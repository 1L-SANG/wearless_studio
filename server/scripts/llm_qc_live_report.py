"""Phase I/J — one self-contained HTML page and one results JSON, from the run artifacts.

Everything is embedded, so the page can be opened from disk with no server and no network.
Images are re-encoded down to a sane width first: the stripe product renders at 3392x5056 and
four of those would put the page past any reasonable size for something meant to be scrolled.

The colour rule is the report's only editorial decision, and it is a strict one: GREEN means
the authority predicate said this candidate may be consumed, RED means it said no, GREY means
the judge could not tell. A blocked candidate is never labelled FINAL, whatever it looks like.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import pathlib

from PIL import Image

MAX_W = 620
THUMB_W = 420


def data_uri(path: str | pathlib.Path, max_w: int = MAX_W) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, int(im.height * max_w / im.width)))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=84)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def status_class(decision: str, allowed: bool | None = None) -> str:
    if allowed is True:
        return "ok"
    if decision == "PASS":
        return "ok"
    if decision == "FAIL":
        return "bad"
    return "unk"


def checks_table(checks: dict) -> str:
    if not checks:
        return "<p class='muted'>no checks recorded</p>"
    rows = []
    for name, node in checks.items():
        st = node.get("status", "UNVERIFIABLE")
        samples = node.get("sampleStatuses") or []
        sample_txt = (" <span class='muted'>(" + " / ".join(
            s or "?" for s in samples) + ")</span>") if samples else ""
        rows.append(
            f"<tr class='{status_class(st)}'><td class='k'>{html.escape(name)}</td>"
            f"<td class='s'>{html.escape(st)}{sample_txt}</td>"
            f"<td class='e'>{html.escape(node.get('evidence') or '')}</td></tr>")
    return ("<table class='checks'><thead><tr><th>check</th><th>status "
            "<span class='muted'>(per sample)</span></th><th>evidence</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>")


def gates_block(cand: dict) -> str:
    """The property-specific gates, shown apart from the thirteen general checks.

    They are a different question asked of different evidence, and collapsing them into the
    same table is how the stripe failure hid in the first place.
    """
    required = cand.get("requiredGates") or []
    if not required:
        return ""
    statuses = cand.get("gateStatuses") or {}
    results = cand.get("gateResults") or {}
    rows = []
    for gate in required:
        st = statuses.get(gate, "MISSING")
        cls = "ok" if st in ("PASS", "NOT_APPLICABLE") else (
            "bad" if st == "FAIL" else "unk")
        node = results.get(gate) or {}
        samples = node.get("sampleStatuses") or []
        props = node.get("properties") or {}
        detail = "".join(
            f"<tr class='{'ok' if v.get('status') in ('PASS','NOT_APPLICABLE') else ('bad' if v.get('status')=='FAIL' else 'unk')}'>"
            f"<td class='k'>{html.escape(k)}</td><td class='s'>{html.escape(v.get('status',''))}</td>"
            f"<td class='e'><b>source:</b> {html.escape(v.get('sourceObservation') or '')}<br>"
            f"<b>generated:</b> {html.escape(v.get('generatedObservation') or '')}</td></tr>"
            for k, v in props.items())
        rows.append(
            f"<div class='gate {cls}'><h5>{html.escape(gate)} "
            f"<span class='badge {cls}'>{html.escape(st)}</span> "
            f"<span class='muted'>samples {html.escape(str(samples))} · conf "
            f"{node.get('confidence')}</span></h5>"
            f"<p class='muted'>{html.escape((node.get('evidence') or '')[:400])}</p>"
            f"<table class='checks'><tbody>{detail}</tbody></table></div>")
    return ("<div class='gates'><h5>property-specific hard gates "
            "<span class='muted'>(judged on the crops below)</span></h5>"
            + "".join(rows) + "</div>")


def crops_block(cand: dict) -> str:
    crops = cand.get("qcCrops") or []
    if not crops:
        return ""
    figs = "".join(
        f"<figure><img src='{data_uri(c['file'], THUMB_W)}'>"
        f"<figcaption>{html.escape(c['name'])} · {c['width']}x{c['height']}"
        f"</figcaption></figure>" for c in crops)
    return ("<div class='sources'><h5 style='width:100%'>evidence the specialized judge "
            "actually saw</h5>" + figs + "</div>")


def candidate_block(cand: dict, *, is_final_authorized: bool) -> str:
    decision = cand.get("decision", "UNVERIFIABLE")
    allowed = cand.get("allowed")
    cls = status_class(decision, allowed)
    badge = {"ok": "PASS", "bad": "FAIL", "unk": "UNVERIFIABLE"}[cls]
    tag = ""
    if is_final_authorized:
        tag = "<span class='tag final'>FINAL AUTHORIZED</span>"
    elif decision != "PASS":
        tag = "<span class='tag blocked'>BLOCKED — not shippable</span>"
    tech = cand.get("technicalValidation") or {}
    failed = cand.get("failedChecks") or []
    unver = cand.get("unverifiableChecks") or []
    soft = cand.get("softIssues") or []
    reasons = cand.get("failureReasons") or []
    instruction = cand.get("correctionInstruction") or ""
    return f"""
    <div class="cand {cls}">
      <div class="candhead">
        <h4>{html.escape(cand['label'])} <span class="badge {cls}">{badge}</span></h4>
        {tag}
      </div>
      <div class="candbody">
        <div class="candimg"><img src="{data_uri(cand['file'])}" alt="{html.escape(cand['label'])}"></div>
        <div class="candmeta">
          <dl>
            <dt>LLM decision</dt><dd class="{cls}"><b>{html.escape(decision)}</b>
              <span class="muted">(model self-report: {html.escape(cand.get('modelDecision') or '-')},
              agreed: {cand.get('modelAgreed')})</span></dd>
            <dt>samples</dt><dd>{html.escape(str(cand.get('sampleDecisions') or []))}
              <span class="muted">unanimous: {cand.get('sampleAgreement')}</span></dd>
            <dt>confidence</dt><dd>{cand.get('confidence')}</dd>
            <dt>failed hard gates</dt>
              <dd class="{'bad' if failed else ''}">{html.escape(', '.join(failed) or '—')}</dd>
            <dt>unverifiable hard gates</dt>
              <dd class="{'unk' if unver else ''}">{html.escape(', '.join(unver) or '—')}</dd>
            <dt>soft issues</dt><dd>{html.escape(', '.join(soft) or '—')}</dd>
            <dt>specialized gates</dt>
              <dd class="{'bad' if (cand.get('failedGates') or []) else ''}">
                {html.escape(str(cand.get('gateStatuses') or {}) if cand.get('requiredGates') else '— none required')}</dd>
            <dt>failure reasons</dt><dd>{html.escape('; '.join(reasons) or '—')}</dd>
            <dt>technical validation</dt>
              <dd>{tech.get('width')}x{tech.get('height')} {html.escape(tech.get('mime') or '')},
                  decodable={tech.get('decodable')}, aspect ok={tech.get('aspectRatioOk')}
                  {html.escape(', '.join(tech.get('errors') or []) or '')}</dd>
            <dt>provider latency</dt><dd>{cand.get('providerLatencyMs')} ms</dd>
          </dl>
          {'<div class="instr"><h5>correction instruction sent to the image model</h5><pre>'
           + html.escape(instruction) + '</pre></div>' if instruction else ''}
        </div>
      </div>
      {gates_block(cand)}
      {crops_block(cand)}
      <details><summary>all 13 general checks</summary>{checks_table(cand.get('checks') or {})}</details>
    </div>"""


def product_block(p: dict) -> str:
    if p.get("error"):
        return (f"<section class='product'><h2>{html.escape(p.get('label') or p['productKey'])}"
                f"</h2><p class='bad'>ERROR: {html.escape(p['error'])}</p></section>")
    sources = "".join(
        f"<figure><img src='{data_uri(f, THUMB_W)}'><figcaption>SOURCE {html.escape(slot.upper())}"
        f"</figcaption></figure>"
        for slot, f in zip(p["sourceSlots"], p["sourceFiles"]))
    final_label = p.get("finalCandidate")
    cands = "".join(
        candidate_block(c, is_final_authorized=(c["label"] == final_label))
        for c in p["candidates"])
    budget = p.get("imageBudget") or {}
    ready = p.get("readyState")
    ready_cls = "ok" if ready == "READY" else "bad"
    return f"""
    <section class="product">
      <h2>{html.escape(p['label'])}</h2>
      <p class="why">{html.escape(p['why'])}</p>
      <p class="meta">product {html.escape(p['productId'])} · truth: {html.escape(p['truthOrigin'])}
         · pattern {html.escape(str(p.get('truthPatternType')))}
         · generation {html.escape(p['generationModel'])} @ {html.escape(p['imageSize'])}
         {html.escape(p['aspectRatio'])}
         · QC {html.escape(p['qcModel'])} / {html.escape(p['qcPromptVersion'])}
         · {p['qcSamplesPerJudgement']} samples @ temp {p['qcTemperature']}</p>
      <div class="sources">{sources}</div>
      {cands}
      <div class="verdict {ready_cls}">
        <b>{html.escape(ready or '?')}</b>
        {('— ' + html.escape(p.get('authorityReason') or '')) if p.get('authorityReason') else ''}
        · would charge credit: <b>{p.get('wouldChargeCredit')}</b>
        {('(' + html.escape(p.get('billingReason') or '') + ')') if p.get('billingReason') else ''}
        · final authorized candidate: <b>{html.escape(str(final_label))}</b>
      </div>
      <div class="budget">image calls {budget.get('imageCalls')}/{budget.get('maxTotal')}
        (base {budget.get('baseCalls')}, targeted {budget.get('targetedCalls')},
         full regen {budget.get('fullRegenCalls')}, remaining {budget.get('budgetRemaining')})
        · vision provider calls {p.get('visionCallCount')}
        across {p.get('visionJudgements')} judgements</div>
    </section>"""


def frozen_block(frozen: dict) -> str:
    s = frozen["summary"]
    rows = []
    for c in frozen["cases"]:
        cls = "ok" if c.get("correct") else "bad"
        rows.append(
            f"<tr class='{cls}'><td>{html.escape(c['case'])}</td>"
            f"<td>{html.escape(c['expected'])}</td>"
            f"<td>{html.escape(c['decision'])}</td>"
            f"<td>{html.escape(', '.join(c.get('failedGates') or []) or '—')}</td>"
            f"<td>{c.get('confidence')}</td>"
            f"<td>{html.escape(str(c.get('sampleDecisions') or []))}</td>"
            f"<td class='e'>{html.escape(c.get('whatWeSaw') or '')}</td></tr>")
    return f"""
    <section class="product">
      <h2>Phase C — frozen artifacts, real vision QC</h2>
      <p class="meta">{s['correctlyAccepted']}/{s['expectedPass']} faithful cases accepted ·
        {s['correctlyRejected']}/{s['expectedFail']} redesigns rejected ·
        false acceptances: {html.escape(str(s['falseAcceptances']) )} ·
        false rejections: {html.escape(str(s['falseRejections']))} ·
        mismatch control rejected: {s['negativeControlRejected']}</p>
      <table class="checks"><thead><tr><th>case</th><th>expected</th><th>LLM</th>
        <th>failed gates</th><th>conf</th><th>samples</th><th>what we saw in the pixels</th>
        </tr></thead><tbody>{''.join(rows)}</tbody></table>
    </section>"""


CSS = """
:root { --ok:#1a7f37; --bad:#c1121f; --unk:#6b7280; --line:#e5e7eb; }
* { box-sizing: border-box; }
body { margin:0; padding:32px; font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",
       "Helvetica Neue",Arial,"Apple SD Gothic Neo",sans-serif; color:#111; background:#fafafa; }
h1 { font-size:26px; margin:0 0 4px; }
h2 { font-size:20px; margin:0 0 6px; }
h4 { margin:0; font-size:16px; }
h5 { margin:12px 0 4px; font-size:13px; text-transform:uppercase; letter-spacing:.04em; }
.lede { color:#374151; max-width:70ch; margin:0 0 28px; }
section.product { background:#fff; border:1px solid var(--line); border-radius:10px;
                  padding:20px; margin:0 0 26px; }
.why { color:#374151; margin:0 0 6px; }
.meta, .muted { color:#6b7280; font-size:12.5px; }
.meta { margin:0 0 14px; }
.sources { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:18px; }
.sources figure { margin:0; }
.sources img { width:210px; border-radius:6px; border:1px solid var(--line); display:block; }
.sources figcaption { font-size:11.5px; color:#6b7280; margin-top:4px; letter-spacing:.05em; }
.cand { border:2px solid var(--line); border-radius:8px; padding:14px; margin-bottom:14px; }
.cand.ok { border-color:var(--ok); }
.cand.bad { border-color:var(--bad); }
.cand.unk { border-color:var(--unk); }
.candhead { display:flex; align-items:center; gap:12px; margin-bottom:10px; }
.candbody { display:flex; gap:18px; flex-wrap:wrap; }
.candimg img { width:300px; border-radius:6px; border:1px solid var(--line); display:block; }
.candmeta { flex:1 1 380px; min-width:320px; }
.badge { font-size:12px; padding:2px 8px; border-radius:99px; color:#fff; vertical-align:middle; }
.badge.ok { background:var(--ok); } .badge.bad { background:var(--bad); }
.badge.unk { background:var(--unk); }
.tag { font-size:11.5px; padding:3px 9px; border-radius:99px; letter-spacing:.03em; }
.tag.final { background:#dcfce7; color:var(--ok); border:1px solid var(--ok); }
.tag.blocked { background:#fee2e2; color:var(--bad); border:1px solid var(--bad); }
dl { display:grid; grid-template-columns:170px 1fr; gap:3px 12px; margin:0; font-size:13.5px; }
dt { color:#6b7280; } dd { margin:0; }
.ok { color:var(--ok); } .bad { color:var(--bad); } .unk { color:var(--unk); }
tr.ok td.s { color:var(--ok); } tr.bad td.s { color:var(--bad); }
tr.unk td.s { color:var(--unk); }
.instr pre { white-space:pre-wrap; background:#f6f7f9; border:1px solid var(--line);
             border-radius:6px; padding:10px; font-size:12.5px; margin:0; }
details { margin-top:12px; } summary { cursor:pointer; font-size:13px; color:#374151; }
table.checks { width:100%; border-collapse:collapse; margin-top:10px; font-size:12.5px; }
table.checks th, table.checks td { border-bottom:1px solid var(--line); padding:6px 8px;
                                   text-align:left; vertical-align:top; }
table.checks td.k { white-space:nowrap; font-weight:600; }
table.checks td.s { white-space:nowrap; }
.verdict { margin-top:12px; padding:10px 12px; border-radius:6px; background:#f6f7f9;
           font-size:14px; }
.verdict.ok { background:#dcfce7; } .verdict.bad { background:#fee2e2; }
.budget { margin-top:8px; font-size:12.5px; color:#374151; }
.legend { display:flex; gap:16px; margin:0 0 24px; font-size:13px; }
.legend span::before { content:"■ "; }
.gates { margin-top:14px; }
.gate { border-left:4px solid var(--line); padding:8px 12px; margin:10px 0;
        background:#fbfbfc; border-radius:0 6px 6px 0; }
.gate.ok { border-left-color:var(--ok); } .gate.bad { border-left-color:var(--bad); }
.gate.unk { border-left-color:var(--unk); }
.gate h5 { margin:0 0 4px; text-transform:none; letter-spacing:0; font-size:14px; }
"""


def regression_block(reg: dict) -> str:
    s = reg["summary"]
    rows = []
    for c in reg["cases"]:
        cls = "ok" if c.get("correct") else "bad"
        rows.append(
            f"<tr class='{cls}'><td>{html.escape(c['case'])}</td>"
            f"<td>{html.escape(str(c.get('expected')))}</td>"
            f"<td>{html.escape(str(c.get('statuses')))}</td>"
            f"<td class='e'>{html.escape(c.get('seen') or '')}</td></tr>")
    return f"""
    <section class="product">
      <h2>Pattern / material hard-gate regression</h2>
      <p class="meta">{s['correct']}/{s['cases']} correct ·
        the live stripe candidate is now <b>{html.escape(str(s['regressionRejected']))}</b> ·
        positives accepted: {html.escape(str(s['positivesAccepted']))} ·
        wrong: {html.escape(str(s['wrong']))}</p>
      <table class="checks"><thead><tr><th>case</th><th>expected</th><th>gate result</th>
        <th>what the pixels show</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
    </section>"""


def build(live_paths: list[pathlib.Path], frozen_path: pathlib.Path,
          out_html: pathlib.Path, out_json: pathlib.Path,
          regression_path: pathlib.Path | None = None) -> None:
    products = []
    for path in live_paths:
        products.extend(json.loads(path.read_text(encoding="utf-8"))["products"])
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))

    body = "".join(product_block(p) for p in products)
    if regression_path:
        body += regression_block(json.loads(regression_path.read_text(encoding="utf-8")))
    body += frozen_block(frozen)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LLM Vision QC — live end-to-end</title><style>{CSS}</style></head>
<body>
<h1>LLM Vision QC — live end-to-end</h1>
<p class="lede">Real Gemini generation, real vision-LLM comparative QC, real targeted
correction, real budget and authority predicates. Isolated QA mode: nothing was written to the
database, no credit was moved, no cut row was created. Every verdict below is derived from the
thirteen named checks, not from the model's own decision field.</p>
<div class="legend"><span class="ok">PASS / authorized</span>
<span class="bad">FAIL / blocked</span><span class="unk">UNVERIFIABLE</span></div>
{body}
</body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(page, encoding="utf-8")

    # Phase J: one machine-readable record per candidate. No keys, no secrets, no prompts —
    # prompt SHA only, which is enough to prove two runs used the same text.
    rows = []
    for p in products:
        if p.get("error"):
            rows.append({"productId": p.get("productId"), "productKey": p["productKey"],
                         "error": p["error"]})
            continue
        final_label = p.get("finalCandidate")
        for c in p["candidates"]:
            rows.append({
                "productId": p["productId"],
                "productKey": p["productKey"],
                "projectId": p["projectId"],
                "sourceAssetIds": p["sourceAssetIds"],
                "generatedAssetId": None,      # isolated run: no asset row was created
                "generatedFile": c["file"],
                "generatedSha256": c["sha256"],
                "generationModel": p["generationModel"],
                "generationPromptSha256": p["promptSha256"],
                "generationPromptVersion": p["promptVersion"],
                "qcModel": p["qcModel"],
                "qcPromptVersion": p["qcPromptVersion"],
                "qcSamples": c.get("samples"),
                "qcSampleDecisions": c.get("sampleDecisions"),
                "requiredGates": c.get("requiredGates"),
                "gateStatuses": c.get("gateStatuses"),
                "failedGates": c.get("failedGates"),
                "gateResults": c.get("gateResults"),
                "decision": c["decision"],
                "modelSelfDecision": c.get("modelDecision"),
                "checks": c.get("checks"),
                "failureReasons": c.get("failureReasons"),
                "correctionInstruction": c.get("correctionInstruction"),
                "confidence": c.get("confidence"),
                "technicalValidation": c.get("technicalValidation"),
                "imageBudget": p["imageBudget"],
                "visionCallCount": p["visionCallCount"],
                "authorityAllowed": p["authorityAllowed"],
                "authorityReason": p["authorityReason"],
                "wouldChargeCredit": p["wouldChargeCredit"],
                "finalCandidate": final_label,
                "isFinalCandidate": c["label"] == final_label,
            })
    out_json.write_text(json.dumps({
        "mode": "isolated-qa-no-db-writes",
        "frozenArtifactQc": frozen["summary"],
        "candidates": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", nargs="+", required=True)
    ap.add_argument("--frozen", required=True)
    ap.add_argument("--pattern-regression")
    ap.add_argument("--out-html", required=True)
    ap.add_argument("--out-json", required=True)
    args = ap.parse_args()
    build([pathlib.Path(p) for p in args.live], pathlib.Path(args.frozen),
          pathlib.Path(args.out_html), pathlib.Path(args.out_json),
          pathlib.Path(args.pattern_regression) if args.pattern_regression else None)
    print(pathlib.Path(args.out_html).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
