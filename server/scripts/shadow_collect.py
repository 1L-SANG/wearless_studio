"""Shadow 표본 수집 — 실 provider 호출 (Phase 3 P0-C 9/N, 사용자 승인 필요).

승인 없이 돌리지 않는다. 승인 후에도 다음을 지킨다:
  · shadow 전용 — 판정 결과로 아무것도 막거나 지우지 않는다
  · 운영 DB·운영 R2 무접촉. 산출물은 파일뿐(행·이미지 모두 지정한 디렉터리)
  · 호출 상한(전체/파이프라인/edit type) + 비용 상한 + 요청 timeout
  · provider 원문 응답 미저장 — edit_qc_result 요약과 관찰 결과만
  · 입력은 레포에 들어 있는 예시 이미지만. 운영 사용자 데이터는 쓰지 않는다

사람의 판단(accepted/rejected)은 여기서 만들지 않는다. 그건 사람이 실제로 비교
화면을 보고 눌러야 생기는 데이터고, 대신 눌러 주면 그 순간 캘리브레이션 근거가
아니라 조작이 된다. 이 스크립트는 기계 쪽 분포만 채운다.

    python scripts/shadow_collect.py --dry-run
    python scripts/shadow_collect.py --per-pipeline 30 --budget-usd 12

산출물은 **파일**이다(samples.jsonl + 결과 PNG). DB 에 쓰지 않는다 — 운영 DB 를
건드릴 경로를 아예 만들지 않는 편이 "안 쓴다"는 약속보다 튼튼하다. 집계는
shadow_report.py --jsonl 로 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.agents import cut_variator, edit_intent_vision  # noqa: E402
from app.agents.edit_intent_vision import PROMPT_VERSION as VISION_PROMPT_VERSION  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.services import edit_intent_qc, edit_qc_scope, editor_vary  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "public" / "assets" / "fit-examples"

# 수집 축 — edit type 별로 상한을 따로 둔다(한 축이 예산을 다 먹지 않게).
VARY_CASES = [
    ("bg_only", [{"type": "bg", "value": "밝은 스튜디오 배경"}]),
    ("shot", [{"type": "shot", "value": "전신"}]),
    ("direction", [{"type": "direction", "value": "측면"}]),
    ("pose", [{"type": "pose", "value": "자연스러운 서 있는 자세"}]),
    ("bg_and_shot", [{"type": "bg", "value": "회색 배경"}, {"type": "shot", "value": "상반신"}]),
]


async def observe_and_decide(settings, *, baseline, edited, changes, timeout):
    """Vision 1회 + 정량 판정. 운영과 동일 계약(require_vision=True)."""
    scope = editor_vary.semantic_scope(changes)
    edit_type = editor_vary.edit_type_for(changes)
    observation, meta, attempted = None, None, 1
    try:
        observation, meta = await asyncio.wait_for(edit_intent_vision.observe(
            settings, baseline=baseline, edited=edited, edit_type=edit_type,
            adjustments={"changes": changes},
            allowed_scope=edit_qc_scope.vision_scope(scope),
            source_refs=None), timeout)
    except Exception as e:                                    # noqa: BLE001
        meta = edit_intent_vision.failure_meta(e)
    qc = edit_intent_qc.evaluate(
        baseline_bgr=_bgr(baseline.data), edited_bgr=_bgr(edited.data),
        edit_type=edit_type, allowed_scope=edit_qc_scope.qc_allowed_scope(),
        target_ratio=None, vision=observation, require_vision=True,
        semantic_scope=scope, extra_entailed=editor_vary.entailed_metrics(changes))
    # 관찰 원문이 아니라 정규화 관찰 + 계측 메타만 남긴다.
    qc["vision"] = {"observation": observation, "meta": meta}
    return qc, edit_type, attempted


# ── fingerprint ────────────────────────────────────────────────────────────
# run-level 과 per-case 를 나눈다. 이걸 안 나눴던 게 직전 결함이다: generation
# prompt 는 case 마다 다른 게 **정상**인데 run 동일성 키에 넣어 두니, 멀쩡한
# multi-case 데이터셋이 "provenance 가 섞였다"고 거부됐다.
#
# run-level = 실험 조건(모델·템플릿·정책·case 집합·공통 설정). 이게 다르면 다른 실험이다.
# per-case  = 그 조건 아래 case 마다 실제로 렌더링된 프롬프트. case 별로 달라야 정상이다.

# 이 중 하나라도 달라지면 새 dataset 을 요구한다. codeCommit 은 여기 없다 —
# 보조 근거일 뿐이라 스냅샷 비교를 대신할 수 없고, 대신하게 두면 dirty working tree
# 에서 프롬프트만 바뀐 경우를 놓친다.
RUN_FINGERPRINT_KEYS = (
    "generationModel", "generationTemplateSha256",
    "visionPromptTemplateVersion", "visionTemplateSha256",
    "qcPolicyVersion", "caseSetSha256", "imageSize", "aspectRatio",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def _code_commit() -> str | None:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def normalized_cases(cases=None) -> list[dict]:
    """case 정의의 정본 표현. 이름·edit type·정규화 changes 까지 포함한다."""
    out = []
    for name, changes in (cases if cases is not None else VARY_CASES):
        out.append({"case": name,
                    "editType": editor_vary.edit_type_for(changes),
                    "changes": editor_vary.validate_changes(changes)})
    return sorted(out, key=lambda c: c["case"])


def case_set_sha256(cases=None) -> str:
    """case 집합 전체의 해시 — 추가·삭제·변경 어느 쪽이든 값이 바뀐다."""
    return _sha(_canonical(normalized_cases(cases)))


def run_fingerprint(prepared, *, cases=None) -> dict:
    """실험 조건 스냅샷. provider 를 부르지 않고 prepare 결과만으로 만든다."""
    return {
        "generationModel": prepared.model,
        "generationTemplateSha256": cut_variator.template_sha256(),
        "visionPromptTemplateVersion": VISION_PROMPT_VERSION,
        "visionTemplateSha256": edit_intent_vision.template_sha256(),
        "qcPolicyVersion": edit_qc_scope.QC_POLICY_VERSION,
        "caseSetSha256": case_set_sha256(cases),
        "imageSize": prepared.image_size,
        "aspectRatio": getattr(prepared, "aspect_ratio", None),
    }


def case_fingerprint(prepared, *, case_name: str, changes: list) -> dict:
    """이 case 가 **실제로** 어떤 프롬프트를 냈는지."""
    return {
        "case": case_name,
        "editType": editor_vary.edit_type_for(changes),
        "changes": editor_vary.validate_changes(changes),
        "generationPromptSha256": _sha(prepared.prompt.encode()),
    }


def _provenance(prepared, *, case_name: str, changes: list, attempt: int,
                source_bytes: bytes, output_bytes: bytes,
                vision_meta: dict | None = None) -> dict:
    """표본 1건이 **무엇으로** 만들어졌는지 그 자리에서 찍는다.

    나중에 기억으로 채우면 그건 provenance 가 아니라 추정이다. 모델이 바뀌었는지
    프롬프트가 바뀌었는지 모르는 표본은 다른 수집분과 합칠 수 없고, 합칠 수 없는
    데이터로 임계값을 정하면 그 임계값의 근거도 없다.
    """
    vm = vision_meta or {}
    return {
        "sourceSha256": _sha(source_bytes),
        "outputSha256": _sha(output_bytes),
        "run": run_fingerprint(prepared),
        "case": case_fingerprint(prepared, case_name=case_name, changes=changes),
        # 편의를 위한 평면 사본 — 정본은 run/case 다.
        "generationModel": prepared.model,
        "generationPromptSha256": _sha(prepared.prompt.encode()),
        "generationTemplateSha256": cut_variator.template_sha256(),
        "visionPromptTemplateVersion": VISION_PROMPT_VERSION,
        "visionTemplateSha256": edit_intent_vision.template_sha256(),
        # 실제 provider 로 나간 프롬프트의 해시(템플릿 해시가 아니다).
        "visionPromptSha256": vm.get("promptSha256"),
        "visionProvider": vm.get("provider"),
        "visionStatus": vm.get("status"),
        "qcPolicyVersion": edit_qc_scope.QC_POLICY_VERSION,
        "codeCommit": _code_commit(),
        "imageSize": prepared.image_size,
        "aspectRatio": getattr(prepared, "aspect_ratio", None),
        "callAttemptIndex": attempt,
    }


def _refuse(msg: str):
    raise SystemExit(f"REFUSING: {msg} 새 --dataset-id 로 모으세요.")


def _prepare_only(settings, cases=None):
    """provider 를 부르지 않고 현재 조건을 계산한다 — prepare() 까지만 실행."""
    src = _sources(1)[0]
    img = InlineImage("image/jpeg", src.read_bytes())
    cases = cases if cases is not None else VARY_CASES
    per_case, run = {}, None
    for name, changes in cases:
        prep = cut_variator.prepare(settings, img, changes, None)
        per_case[name] = case_fingerprint(prep, case_name=name, changes=changes)
        run = run or run_fingerprint(prep, cases=cases)
    return run, per_case


def _assert_resumable(samples_path: pathlib.Path, settings=None) -> None:
    """resume 은 **같은 실험 조건**일 때만 허용한다.

    조건이 바뀐 뒤 이어 붙이면 한 데이터셋 안에 서로 다른 실험이 섞이고, 행마다
    표시가 없으니 나중에 분리할 수 없다. 반대로 case 마다 프롬프트가 다른 것은
    정상이므로 그걸로 거부하면 멀쩡한 데이터셋을 못 이어 모은다.
    """
    if not samples_path.exists():
        return
    rows = [json.loads(l) for l in samples_path.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    # 실패 row 는 출력이 없어 provenance 증거가 아니다.
    done = [r for r in rows if r.get("output_id")]
    if not done:
        return
    provs = [r.get("provenance") or {} for r in done]
    if any(not p.get("run") or not p.get("case") for p in provs):
        _refuse("기존 표본에 run/case fingerprint 가 없어 같은 조건인지 확인할 수 없어요.")

    runs = {_canonical({k: p["run"].get(k) for k in RUN_FINGERPRINT_KEYS}) for p in provs}
    if len(runs) > 1:
        _refuse("기존 파일에 이미 서로 다른 실험 조건이 섞여 있어요.")
    prev_run = json.loads(runs.pop())

    now_run, now_cases = _prepare_only(settings or load_settings())
    for k in RUN_FINGERPRINT_KEYS:
        if prev_run.get(k) != now_run.get(k):
            _refuse(f"실행 조건 불일치 ({k}: {prev_run.get(k)!r} != {now_run.get(k)!r}).")

    prev_cases: dict[str, dict] = {}
    for p in provs:
        c = p["case"]
        seen = prev_cases.setdefault(c["case"], c)
        if seen.get("generationPromptSha256") != c.get("generationPromptSha256"):
            _refuse(f"같은 case 안에서 프롬프트가 서로 달라요 ({c['case']}).")
    for name, prev in prev_cases.items():
        cur = now_cases.get(name)
        if cur is None:
            _refuse(f"기존 case 가 지금 정의에 없어요 ({name}).")
        if prev.get("generationPromptSha256") != cur.get("generationPromptSha256"):
            _refuse(f"case 프롬프트가 바뀌었어요 ({name}).")
        if _canonical(prev.get("changes")) != _canonical(cur.get("changes")):
            _refuse(f"case 정의가 바뀌었어요 ({name}).")

    prev_commit = provs[0].get("codeCommit")
    now_commit = _code_commit()
    if prev_commit and now_commit and prev_commit != now_commit:
        # 보조 근거다 — 스냅샷이 전부 같으면 커밋이 달라도 같은 실험으로 본다.
        print(f"  주의: codeCommit 이 다릅니다 ({prev_commit[:8]} → {now_commit[:8]}). "
              "모델·템플릿·프롬프트·정책은 모두 일치해 resume 을 허용합니다.")


def _sources(limit: int) -> list[pathlib.Path]:
    if not EXAMPLES.is_dir():
        raise SystemExit(f"예시 이미지가 없어요: {EXAMPLES}")
    files = sorted(p for p in EXAMPLES.iterdir() if p.suffix.lower() in (".jpg", ".png"))
    if not files:
        raise SystemExit("예시 이미지가 비어 있어요")
    return [files[i % len(files)] for i in range(limit)]


def _bgr(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


async def _one_sample(settings, gemini, *, src_path, case_name, changes, timeout,
                      out_dir) -> dict:
    """표본 1건 = 생성 1회 + (선택) Vision 1회. 실패해도 행은 남긴다(실패도 데이터)."""
    raw = src_path.read_bytes()
    mime = "image/png" if src_path.suffix.lower() == ".png" else "image/jpeg"
    source = InlineImage(mime, raw)
    started = time.time()
    row = {"id": str(uuid.uuid4()), "case": case_name, "source": src_path.name,
           "source_kind": "editor_asset",
           "edit_type": editor_vary.edit_type_for(changes),
           "created_at": started, "completed_at": None,
           "status": "failed", "output_id": None, "edit_qc_result": None,
           "image_calls": 0, "vision_calls": 0}

    prepared = cut_variator.prepare(settings, source, changes, None)
    try:
        row["image_calls"] = 1
        res = await asyncio.wait_for(cut_variator.execute(gemini, prepared), timeout)
        edited_bytes, edited_mime = res.image, res.mime
        row["provider_latency_ms"] = res.latency_ms
    except Exception as e:                                    # noqa: BLE001
        # 원문을 남기지 않는다 — 타입만.
        row["edit_qc_result"] = {"error": "generation_failed",
                                 "category": type(e).__name__}
        row["completed_at"] = time.time()
        return row

    out_path = out_dir / f"{row['id']}.png"
    out_path.write_bytes(edited_bytes)
    row["output_id"] = str(uuid.uuid4())

    qc, _et, vision_attempted = await observe_and_decide(
        settings, baseline=source, edited=InlineImage(edited_mime, edited_bytes),
        changes=changes, timeout=timeout)
    row["vision_calls"] = vision_attempted
    row["edit_qc_result"] = qc
    row["machine_decision"] = edit_qc_scope.machine_decision(qc, had_output=True)
    row["status"] = row["machine_decision"]      # 워크플로 상태 == 판정 (수집기는 잡이 없다)
    # provenance 는 QC 이후에 만든다 — 실제 나간 Vision 프롬프트 해시가 meta 에 있고,
    # 그걸 다시 조립하면 기록과 요청이 갈라진다.
    row["provenance"] = _provenance(
        prepared, case_name=case_name, changes=changes, attempt=1,
        source_bytes=raw, output_bytes=edited_bytes,
        vision_meta=(qc.get("vision") or {}).get("meta") or {})
    row["completed_at"] = time.time()
    return row


async def vision_backfill(args) -> int:
    """이미 생성된 표본에 Vision 관찰만 채운다 — **이미지 생성 호출 0**.

    수집 도중 Vision 이 실패했을 때 이미지를 다시 만들지 않고 관찰만 다시 받는다.
    표본을 버리고 새로 뽑는 것보다 싸고, 같은 결과 이미지를 쓰므로 비교도 정확하다.
    """
    settings = load_settings()
    src_dir = EXAMPLES
    # 결과 PNG 는 samples.jsonl 옆에 있다. --out 을 다시 조합하면 dataset 디렉터리가
    # 빠져 "파일 없음"으로 전부 skip 된다.
    samples_file = pathlib.Path(args.vision_backfill).resolve()
    out_dir = samples_file.parent
    rows = [json.loads(l) for l in samples_file.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    todo = [r for r in rows if r.get("output_id")][:args.max_calls]
    print(f"backfill 대상 {len(todo)}건 (이미지 생성 0회, Vision {len(todo)}회, "
          f"추정 ${len(todo) * args.vision_usd:.2f})")
    if args.dry_run:
        return 0

    case_changes = dict(VARY_CASES)
    now_run, _now_cases = _prepare_only(settings)

    # provider 를 부르기 **전에** 조건을 맞춰 본다. 조건이 다른데 backfill 하면 QC 결과는
    # 새 조건, provenance 는 옛 조건을 가리키는 상태가 만들어지고 — 그 데이터는 무엇으로
    # 잰 값인지 아무도 말할 수 없다. 안전한 기본은 거부하고 새 dataset 을 요구하는 것.
    for r in todo:
        prov = r.get("provenance") or {}
        run = prov.get("run")
        if not run:
            _refuse(f"기존 표본에 run fingerprint 가 없어요 ({r.get('id')}).")
        for k in ("visionTemplateSha256", "visionPromptTemplateVersion", "qcPolicyVersion"):
            if run.get(k) != now_run.get(k):
                _refuse(f"Vision/QC 조건 불일치 ({k}: {run.get(k)!r} != "
                        f"{now_run.get(k)!r}) — backfill 로 덮을 수 없어요.")
        img_path = out_dir / f"{r['id']}.png"
        src_path = src_dir / str(r.get("source") or "")
        if not img_path.exists() or _sha(img_path.read_bytes()) != prov.get("outputSha256"):
            _refuse(f"결과 이미지가 바뀌었어요 ({r.get('id')}).")
        if not src_path.exists() or _sha(src_path.read_bytes()) != prov.get("sourceSha256"):
            _refuse(f"원본 이미지가 바뀌었어요 ({r.get('id')}).")
    print(f"  조건·이미지 일치 확인 완료 — backfill 대상 {len(todo)}건")

    done = 0
    for n, r in enumerate(todo, 1):
        img_path = out_dir / f"{r['id']}.png"
        src_path = src_dir / r["source"]
        changes = case_changes.get(r["case"], [])
        qc, _et, vision_attempted = await observe_and_decide(
            settings, baseline=InlineImage("image/jpeg", src_path.read_bytes()),
            edited=InlineImage("image/png", img_path.read_bytes()),
            changes=changes, timeout=args.timeout)
        r["vision_calls"] = r.get("vision_calls", 0) + vision_attempted
        r["edit_qc_result"] = qc
        r["machine_decision"] = edit_qc_scope.machine_decision(qc, had_output=True)
        r["status"] = r["machine_decision"]
        vmeta = (qc.get("vision") or {}).get("meta") or {}
        # QC 와 provenance 가 같은 실행을 가리키도록 함께 갱신하고, 덮어쓴 게 아니라
        # **다시 잰 것**임을 lineage(visionBackfilledAt)로 남긴다.
        prov = r["provenance"]
        prov["visionBackfilledAt"] = time.time()
        prov["visionProvider"] = vmeta.get("provider")
        prov["visionStatus"] = vmeta.get("status")
        prov["visionPromptTemplateVersion"] = now_run["visionPromptTemplateVersion"]
        prov["visionTemplateSha256"] = now_run["visionTemplateSha256"]
        prov["visionPromptSha256"] = vmeta.get("promptSha256")
        prov["qcPolicyVersion"] = now_run["qcPolicyVersion"]
        if vmeta.get("status") == "ok":
            done += 1
        vision = qc["vision"]
        print(f"  [{n}/{len(todo)}] {r['case']:<12} {r['status']:<16} "
              f"vision={vision['meta'].get('status')}", flush=True)

    dest = pathlib.Path(args.vision_backfill).with_name("samples_vision.jsonl")
    with dest.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"\nVision 성공 {done}/{len(todo)} → {dest}")
    return 0


async def run(args) -> int:
    settings = load_settings()
    if not getattr(settings, "gemini_api_key", None):
        raise SystemExit("GEMINI_API_KEY 가 없어요.")
    # 데이터셋마다 디렉터리를 나눈다. 같은 파일에 다른 run 을 이어 붙이면 모델·커밋·
    # 프롬프트가 다른 표본이 한 데이터셋으로 섞이고, 그건 나중에 분리할 수 없다.
    out_dir = pathlib.Path(args.out) / args.dataset_id
    samples_path = out_dir / "samples.jsonl"
    if out_dir.exists() and any(out_dir.iterdir()):
        if not args.resume:
            raise SystemExit(
                f"REFUSING: {out_dir} 가 비어 있지 않아요. --dataset-id 를 바꾸거나, "
                "같은 조건으로 이어 모으려면 --resume 를 주세요.")
        _assert_resumable(samples_path, settings)
    out_dir.mkdir(parents=True, exist_ok=True)

    budget_per_sample = args.image_usd + args.vision_usd
    max_by_budget = (int(args.budget_usd / budget_per_sample)
                     if budget_per_sample > 0 else args.per_pipeline * len(VARY_CASES))
    per_case = max(1, args.per_pipeline // len(VARY_CASES))
    planned = min(args.per_pipeline, per_case * len(VARY_CASES), max_by_budget,
                  args.max_calls)

    plan = []
    srcs = _sources(planned + len(VARY_CASES))
    i = 0
    for case_name, changes in VARY_CASES:
        for _ in range(per_case):
            if len(plan) >= planned:
                break
            plan.append((srcs[i % len(srcs)], case_name, changes))
            i += 1

    est = len(plan) * budget_per_sample
    print(f"계획: {len(plan)}건 (case당 {per_case}), 이미지 {len(plan)}회, "
          f"Vision {len(plan)}회, 추정 ${est:.2f}")
    print(f"입력: {EXAMPLES} (레포 예시 이미지, 운영 사용자 데이터 아님)")
    print(f"출력: {out_dir} (파일 전용 — R2·DB 미사용)")
    if args.dry_run:
        print("dry-run — provider 호출 0")
        return 0

    gemini = GeminiImageClient(settings)
    rows = []
    for n, (src, case_name, changes) in enumerate(plan, 1):
        row = await _one_sample(settings, gemini, src_path=src, case_name=case_name,
                                changes=changes, timeout=args.timeout, out_dir=out_dir)
        rows.append(row)
        print(f"  [{n}/{len(plan)}] {case_name:<12} {row['status']:<16} "
              f"{src.name[:28]}", flush=True)
        samples_path.open("a", encoding="utf-8").write(
            json.dumps(row, ensure_ascii=False, default=str) + "\n")

    img_attempted = sum(r.get("image_calls", 0) for r in rows)
    vis_attempted = sum(r.get("vision_calls", 0) for r in rows)
    spent = img_attempted * args.image_usd + vis_attempted * args.vision_usd
    print(f"\n완료: {len(rows)}건, 이미지 시도 {img_attempted}회, "
          f"Vision 시도 {vis_attempted}회, 추정 ${spent:.2f} (시도 기준 — 실패도 과금)")
    print("사람의 판단(accepted/rejected)은 비어 있습니다 — confusion matrix 는 "
          "사람이 실제로 검수한 뒤에만 채워집니다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 3 shadow 표본 수집 (실 provider 호출)")
    ap.add_argument("--per-pipeline", type=int, default=30)
    ap.add_argument("--max-calls", type=int, default=60, help="전체 생성 호출 상한")
    ap.add_argument("--budget-usd", type=float, default=12.0)
    ap.add_argument("--image-usd", type=float, default=0.15)
    ap.add_argument("--vision-usd", type=float, default=0.003)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--out", default="/tmp/shadow-samples")
    ap.add_argument("--dataset-id", required=True,
                    help="데이터셋 식별자 — 출력 디렉터리이자 라벨 결합 키")
    ap.add_argument("--resume", action="store_true",
                    help="같은 provenance 일 때만 이어서 수집")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vision-backfill", help="기존 samples.jsonl 에 Vision 만 다시 채운다")
    args = ap.parse_args()
    if args.vision_backfill:
        return asyncio.run(vision_backfill(args))
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
