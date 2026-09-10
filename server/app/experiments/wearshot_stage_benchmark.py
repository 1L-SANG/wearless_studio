"""Measured conditional v2 stages. Default dry-run performs no calls or writes.

Run one explicit case/arm/phase at a time. First PNGs are unreviewed previews.
Receipts are exclusive and immutable; reconcile any failed/uncertain attempt
manually without resubmitting. No controller, queue or storage time enters the
reported action timings. Local references must already be approved by the owner.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from .. import config
from ..agents import cut_generator, model_routing, vision_llm, wearshot_prompt, wearshot_qc, wearshot_runtime as rt
from ..agents.gemini_image import InlineImage
from ..config import load_settings
from . import wearshot_repair_ab as paired, wearshot_replay as local

VERSION = 'wearshot_stage_benchmark_v1'
MODELS = {
    'image2': {'first': 'gpt-image-2', 'repair': 'gpt-image-2'},
    'flare_sunburst': {'first': 'gpt-image-2.5-flare-2026-09-08', 'repair': 'gpt-image-2.5-sunburst-2026-09-08'},
}
PHASES = ('generate-first', 'qc-first', 'conditional-repair', 'qc-final')


def _code_versions():
    return {**paired._code_versions(), **{m.__name__: local._sha(local._read(Path(m.__file__)))
            for m in (paired, config, model_routing)}, 'stageHarness': local._sha(local._read(Path(__file__)))}


def _prepare(path, case):
    raw = local._read(path)
    try:
        manifest = json.loads(raw)
        if (set(manifest) != {'schemaVersion', 'models', 'qcModel', 'qcTimeoutSeconds', 'quality', 'imageSize', 'cases'}
                or type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != 1
                or manifest['models'] != MODELS or manifest['quality'] != 'medium' or manifest['imageSize'] != '2K'
                or manifest['qcTimeoutSeconds'] != 180 or not isinstance(manifest['qcModel'], str)
                or not manifest['qcModel'].startswith('gpt-')):
            raise ValueError('Requires frozen snapshot arms, native 2K medium and a 180 second GPT QC deadline')
        rows = manifest['cases']
        if not isinstance(rows, list) or not rows:
            raise ValueError('Cases must be nonempty')
        ids = [r['id'] for r in rows]
        if (not all(isinstance(i, str) and local._ID.fullmatch(i) for i in ids)
                or len(ids) != len(set(ids)) or (case is not None and case not in ids)):
            raise ValueError('Unsafe, duplicated or missing case ID')
        items = []
        code = _code_versions()
        for row in rows:
            if case is not None and row['id'] != case:
                continue
            if set(row) != {'id', 'generationDeclared', 'contract', 'referencePaths', 'outputSize'} or row['generationDeclared'] is not True:
                raise ValueError('Case requires explicitly declared generation and exact frozen evidence')
            contract = paired._contract(row['contract'], row['referencePaths'], path.parent)
            example = next(r.image for r in contract.references if r.key == contract.example_key)
            rt.validate_output_size(example, row['outputSize'])
            request = dict(harnessVersion=VERSION, caseId=row['id'], manifestSha256=local._sha(raw),
                codeVersions=code, contract=contract.to_dict(), outputSize=row['outputSize'], imageSize='2K',
                quality='medium', outputFormat='png', qcModel=manifest['qcModel'], qcTimeoutSeconds=180, models=MODELS)
            items.append(dict(contract=contract, request=request))
        return manifest, items
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError('Malformed frozen stage manifest') from exc


def _paths(out, item, mode, arm):
    return paired._paths(out, item['request']['caseId'], mode, arm)


def _render_request(item, arm, mode, rendered, **extra):
    return {**item['request'], 'arm': arm, 'mode': mode, 'stageRole': 'first' if mode == 'generate-first' else 'repair',
        'model': MODELS[arm]['first' if mode == 'generate-first' else 'repair'],
        'promptSha256': local._sha(rendered.prompt.encode()),
        'inputs': [{'key': k, **local._decode(i.data, i.mime)} for k, i in zip(rendered.reference_keys, rendered.images)], **extra}


def _generation(item, out, arm, mode, request):
    paths = _paths(out, item, mode, arm)
    receipt, source = paired._read_receipt(paths, request)
    data = local._read(paths['image'])
    output = {'path': str(paths['image']), **local._decode(data)}
    if (receipt.get('output') != output or receipt.get('actualRequest') != paired._actual_request(request)
            or local._sha(local._read(paths['prompt'])) != request['promptSha256']
            or f"{output['width']}x{output['height']}" != request['outputSize'] or output['mime'] != 'image/png'):
        raise ValueError('Candidate, prompt or actual request differs from owning receipt')
    return receipt, source, InlineImage(output['mime'], data)


def _context(item, out, arm, mode):
    contract = item['contract']
    rendered = wearshot_prompt.render_generation(contract)
    first_request = _render_request(item, arm, 'generate-first', rendered)
    context = dict(request=first_request, rendered=rendered, candidate=None, plan=None, decision='generate')
    if mode == 'generate-first':
        return context
    first, source, candidate = _generation(item, out, arm, 'generate-first', first_request)
    qc_request = {**item['request'], 'arm': arm, 'mode': 'qc-first', 'stageRole': 'first-review',
                  'model': item['request']['qcModel'], 'sourceGeneration': source, 'candidateSha256': first['output']['sha256']}
    context.update(request=qc_request, rendered=None, candidate=candidate, first=first)
    if mode == 'qc-first':
        return context
    qc, qc_source = paired._read_receipt(_paths(out, item, 'qc-first', arm), qc_request)
    verdict = qc['verdict']
    checked = wearshot_qc.validate(verdict.get('observations'), contract, candidate)
    if not checked['valid'] or any(verdict.get(k) != v for k, v in checked.items()):
        raise ValueError('First review requires exact valid bound observations')
    allowed = rt.release_allowed(verdict, contract, candidate)
    if qc.get('releaseAllowed') is not allowed:
        raise ValueError('First release decision differs from bound review')
    plan = None
    decision = 'skip_pass' if allowed else 'repair'
    if not allowed:
        try:
            plan = rt.derive_repair_plan(contract, candidate, verdict, known_failures_only=True)
        except ValueError:
            decision = 'hold'
    common = dict(sourceGeneration=source, sourceFirstQc=qc_source, parentImageSha256=first['output']['sha256'], decision=decision)
    rendered = wearshot_prompt.render_repair(contract, plan) if plan else None
    repair_request = (_render_request(item, arm, 'conditional-repair', rendered, repairPlan=plan.to_dict(), **common)
        if plan else {**item['request'], 'arm': arm, 'mode': 'conditional-repair', 'stageRole': 'repair',
                      'model': MODELS[arm]['repair'], **common})
    context.update(request=repair_request, rendered=rendered, plan=plan, decision=decision, qc1=qc)
    if mode == 'conditional-repair':
        return context
    if decision != 'repair':
        raise ValueError('No second candidate: first passed or no safe repair plan')
    repair, source, final = _generation(item, out, arm, 'conditional-repair', repair_request)
    context.update(request={**item['request'], 'arm': arm, 'mode': 'qc-final', 'stageRole': 'final-review',
        'model': item['request']['qcModel'], 'sourceGeneration': source, 'sourceFirstQc': qc_source,
        'candidateSha256': repair['output']['sha256'], 'repairPlan': plan.to_dict()}, candidate=final, repair=repair)
    return context


class _BoundClient(paired._BoundClient):
    async def generate_content_image(self, *args, **kwargs):
        result = await super().generate_content_image(*args, **kwargs)
        self.receipt['providerLatencyMs'] = self.receipt.pop('latencyMs')
        return result


async def run_manifest(manifest_path, output_dir, *, mode='dry-run', case=None, arm=None, settings=None):
    if mode not in ('dry-run', 'report', *PHASES):
        raise ValueError('Unknown stage')
    if mode in PHASES and (case is None or arm not in MODELS):
        raise ValueError('Live stage requires one explicit case and arm')
    if mode in ('dry-run', 'report') and arm is not None:
        raise ValueError('Read-only modes report all arms')
    manifest, items = _prepare(Path(manifest_path).resolve(), case)
    if mode == 'dry-run':
        return [dict(caseId=i['request']['caseId'], status='dry-run', request=i['request']) for i in items]
    out = Path(output_dir).resolve()
    if mode == 'report':
        return _report(items, out)
    item = items[0]
    paths = _paths(out, item, mode, arm)
    paired._unattempted(paths)
    context = _context(item, out, arm, mode)
    request, candidate, plan = context['request'], context['candidate'], context['plan']
    configured = replace(settings or load_settings(), wearshot_generation_model=MODELS[arm]['first'],
        wearshot_repair_model=MODELS[arm]['repair'], wearshot_qc_model=manifest['qcModel'],
        wearshot_qc_timeout_seconds=180, cut_identity_review_model=manifest['qcModel'],
        analysis_model_order='gpt', model_text=manifest['qcModel'], detail_cut_image_size='2K')
    configured = rt.generation_settings(configured)
    out.mkdir(parents=True, exist_ok=True)
    receipt = dict(caseId=case, arm=arm, status='started', request=request, requestSha256=paired._hash(request),
                   startedAt=datetime.now(timezone.utc).isoformat())
    local._exclusive(paths['started'], local._json_bytes(receipt))
    log_filter = local._LocalVisionLogFilter()
    receipt['providerErrors'] = []
    log_filter.errors.set(receipt['providerErrors'])
    vision_llm.logger.addFilter(log_filter)
    interrupted = None
    try:
        if mode == 'conditional-repair' and context['decision'] != 'repair':
            receipt.update(decision=context['decision'], actionMs=0, releaseAllowed=context['decision'] == 'skip_pass')
        elif mode in ('generate-first', 'conditional-repair'):
            rendered = context['rendered']
            local._exclusive(paths['prompt'], rendered.prompt.encode())
            client = _BoundClient(configured, request, rendered, receipt)
            start = perf_counter()
            try:
                if mode == 'generate-first':
                    data, mime = await cut_generator.generate(configured, client, {}, {}, list(rendered.images),
                        wearshot_contract=item['contract'], output_size=request['outputSize'])
                else:
                    data, mime = await cut_generator.repair(configured, client, {}, {}, candidate,
                        wearshot_contract=item['contract'], repair_plan=plan, repair_model=configured.wearshot_repair_model,
                        output_size=request['outputSize'])
            finally:
                receipt['actionMs'] = round((perf_counter() - start) * 1000, 3)
            info = local._decode(data, mime)
            if mime != 'image/png' or f"{info['width']}x{info['height']}" != request['outputSize']:
                raise ValueError('Returned image differs from frozen native canvas')
            receipt.update(output={'path': str(paths['image']), **info}, reviewStatus='unreviewed', decision=context['decision'])
            local._exclusive(paths['image'], data)
        else:
            start = perf_counter()
            try:
                verdict = await rt.review_candidate(configured, item['contract'], candidate, repair_plan=plan)
            finally:
                receipt['actionMs'] = round((perf_counter() - start) * 1000, 3)
            allowed = rt.release_allowed(verdict, item['contract'], candidate, repair_plan=plan)
            if mode == 'qc-final':
                allowed = allowed and wearshot_qc.compare_repair(item['contract'], plan, context['qc1']['verdict'], verdict)
            receipt.update(verdict=verdict, releaseAllowed=allowed)
            if not verdict.get('valid') or verdict.get('provider') != 'gpt' or verdict.get('model') != configured.wearshot_qc_model:
                raise ValueError('Shared v2 judge unavailable or incorrectly bound')
        receipt['status'] = 'completed'
    except BaseException as exc:
        receipt.update(status='cancelled' if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)) else 'failed',
                       error=local._safe_error(exc), releaseAllowed=False)
        if not isinstance(exc, Exception):
            interrupted = exc
    finally:
        vision_llm.logger.removeFilter(log_filter)
        receipt['finishedAt'] = datetime.now(timezone.utc).isoformat()
        receipt['receiptSha256'] = paired._hash(receipt)
        local._exclusive(paths['receipt'], local._json_bytes(receipt))
    if interrupted is not None:
        raise interrupted
    return [receipt]


def _report(items, out):
    rows = []
    for item in items:
        for arm in MODELS:
            row = dict(caseId=item['request']['caseId'], arm=arm, outcome='not_started', stages={})
            timing = dict(withoutQcMs=None, withQcCriticalPathMs=None, manualQueueTimeIncluded=False)
            row['timing'] = timing
            for mode in PHASES:
                paths = _paths(out, item, mode, arm)
                if not paths['started'].exists():
                    break
                try:
                    context = _context(item, out, arm, mode)
                    receipt, _ = paired._read_receipt(paths, context['request'])
                    if mode in ('generate-first', 'conditional-repair') and context['decision'] not in ('skip_pass', 'hold'):
                        _generation(item, out, arm, mode, context['request'])
                except ValueError:
                    row['outcome'] = 'held_or_unreconciled'
                    break
                row['stages'][mode] = receipt
                if mode == 'generate-first':
                    row['outcome'] = 'unreviewed'
                    timing['withoutQcMs'] = receipt['actionMs']
                elif mode == 'qc-first':
                    timing['withQcCriticalPathMs'] = timing['withoutQcMs'] + receipt['verdict']['timing']['totalMs']
                    row['outcome'] = 'released' if receipt['releaseAllowed'] else 'awaiting_conditional_repair'
                elif mode == 'conditional-repair':
                    if context['decision'] != 'repair':
                        row['outcome'] = 'released' if context['decision'] == 'skip_pass' else 'held'
                        break
                    timing['withQcCriticalPathMs'] += receipt['actionMs']
                    row['outcome'] = 'awaiting_final_review'
                else:
                    timing['withQcCriticalPathMs'] += receipt['verdict']['timing']['totalMs']
                    row['outcome'] = 'released' if receipt['releaseAllowed'] else 'held'
            timing['withQcDecisionTimeMs'] = (timing['withQcCriticalPathMs']
                if row['outcome'] in ('released', 'held') else None)
            timing['acceptedResultTimeMs'] = timing['withQcCriticalPathMs'] if row['outcome'] == 'released' else None
            rows.append(row)
    summary = {}
    for arm in MODELS:
        arm_rows = [r for r in rows if r['arm'] == arm]
        counts = {}
        for row in arm_rows:
            counts[row['outcome']] = counts.get(row['outcome'], 0) + 1
        summary[arm] = dict(outcomeCounts=counts)
        for key in ('withoutQcMs', 'withQcDecisionTimeMs', 'acceptedResultTimeMs'):
            values = [r['timing'][key] for r in arm_rows if r['timing'][key] is not None]
            summary[arm][key] = dict(count=len(values), median=median(values) if values else None,
                                    range=[min(values), max(values)] if values else None)
    return dict(cases=rows, summary=summary, timingDefinition=(
                'Measured entrypoint action times; QC total already includes focused review. '
                'withQcDecisionTimeMs includes completed release and hold decisions; '
                'acceptedResultTimeMs includes released results only. Partial critical-path sums are not completed decision times.'),
                excludedOverhead=['CLI startup', 'manifest loading', 'controller pauses', 'other-case queueing', 'receipt/image storage', 'app/R2/DB transport'],
                previewWarning='First-generation PNGs are unreviewed previews, never QC release evidence.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--mode', choices=('dry-run', *PHASES, 'report'), default='dry-run')
    parser.add_argument('--case')
    parser.add_argument('--arm', choices=tuple(MODELS))
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_manifest(args.manifest, args.output_dir, mode=args.mode, case=args.case, arm=args.arm))
    except Exception as exc:
        print(json.dumps(dict(status='failed', error=local._safe_error(exc))))
        return 1
    print(json.dumps(result))
    return int(isinstance(result, list) and any(r['status'] in ('failed', 'cancelled') for r in result))


if __name__ == '__main__':
    raise SystemExit(main())
