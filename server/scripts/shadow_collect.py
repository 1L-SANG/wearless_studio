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
import json
import os
import pathlib
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.agents import cut_variator, edit_intent_vision  # noqa: E402
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
    row["completed_at"] = time.time()
    return row


async def vision_backfill(args) -> int:
    """이미 생성된 표본에 Vision 관찰만 채운다 — **이미지 생성 호출 0**.

    수집 도중 Vision 이 실패했을 때 이미지를 다시 만들지 않고 관찰만 다시 받는다.
    표본을 버리고 새로 뽑는 것보다 싸고, 같은 결과 이미지를 쓰므로 비교도 정확하다.
    """
    settings = load_settings()
    src_dir = EXAMPLES
    out_dir = pathlib.Path(args.out)
    rows = [json.loads(l) for l in open(args.vision_backfill, encoding="utf-8") if l.strip()]
    todo = [r for r in rows if r.get("output_id")][:args.max_calls]
    print(f"backfill 대상 {len(todo)}건 (이미지 생성 0회, Vision {len(todo)}회, "
          f"추정 ${len(todo) * args.vision_usd:.2f})")
    if args.dry_run:
        return 0

    case_changes = dict(VARY_CASES)
    done = 0
    for n, r in enumerate(todo, 1):
        img_path = out_dir / f"{r['id']}.png"
        src_path = src_dir / r["source"]
        if not img_path.exists() or not src_path.exists():
            print(f"  [{n}] skip (파일 없음) {r['id'][:8]}")
            continue
        changes = case_changes.get(r["case"], [])
        qc, _et, vision_attempted = await observe_and_decide(
            settings, baseline=InlineImage("image/jpeg", src_path.read_bytes()),
            edited=InlineImage("image/png", img_path.read_bytes()),
            changes=changes, timeout=args.timeout)
        r["vision_calls"] = r.get("vision_calls", 0) + vision_attempted
        r["edit_qc_result"] = qc
        r["machine_decision"] = edit_qc_scope.machine_decision(qc, had_output=True)
        r["status"] = r["machine_decision"]
        if (qc.get("vision") or {}).get("meta", {}).get("status") == "ok":
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
        _assert_resumable(samples_path)
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
