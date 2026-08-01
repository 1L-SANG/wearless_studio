"""Shadow 표본 수집 — 실 provider 호출 (Phase 3 P0-C 9/N, 사용자 승인 필요).

승인 없이 돌리지 않는다. 승인 후에도 다음을 지킨다:
  · shadow 전용 — 판정 결과로 아무것도 막거나 지우지 않는다
  · 운영 DB·운영 R2 무접촉. 행은 disposable DB 에, 이미지는 임시 디렉터리에
  · 호출 상한(전체/파이프라인/edit type) + 비용 상한 + 요청 timeout
  · provider 원문 응답 미저장 — edit_qc_result 요약과 관찰 결과만
  · 입력은 레포에 들어 있는 예시 이미지만. 운영 사용자 데이터는 쓰지 않는다

사람의 판단(accepted/rejected)은 여기서 만들지 않는다. 그건 사람이 실제로 비교
화면을 보고 눌러야 생기는 데이터고, 대신 눌러 주면 그 순간 캘리브레이션 근거가
아니라 조작이 된다. 이 스크립트는 기계 쪽 분포만 채운다.

    python scripts/shadow_collect.py --dsn postgres://…/rehearsal --dry-run
    python scripts/shadow_collect.py --dsn postgres://…/rehearsal \
        --per-pipeline 30 --budget-usd 12 --image-usd 0.15 --vision-usd 0.003
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
from app.services import edit_intent_qc, editor_vary  # noqa: E402

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


def _local_only(dsn: str) -> None:
    """운영 DB 로 잘못 쓰는 사고를 시작 전에 막는다."""
    bad = ("pooler.supabase.com", "amazonaws.com", "supabase.co")
    if any(b in dsn for b in bad):
        raise SystemExit(f"REFUSING: 운영으로 보이는 DSN 입니다 — {dsn.split('@')[-1][:40]}")


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
                      out_dir, use_vision) -> dict:
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

    vision = None
    scope = editor_vary.semantic_scope(changes)
    if use_vision:
        row["vision_calls"] = 1
        try:
            obs, meta = await asyncio.wait_for(edit_intent_vision.observe(
                settings, baseline=source, edited=InlineImage(edited_mime, edited_bytes),
                edit_type=row["edit_type"], adjustments={"changes": changes},
                allowed_scope=scope), timeout)
            vision = {"observation": obs, "meta": meta}
        except Exception as e:                                # noqa: BLE001
            vision = {"meta": edit_intent_vision.failure_meta(e)}

    qc = edit_intent_qc.evaluate(
        baseline_bgr=_bgr(raw), edited_bgr=_bgr(edited_bytes),
        edit_type=row["edit_type"], allowed_scope=scope, target_ratio=None,
        vision=vision, require_vision=False, semantic_scope=scope,
        extra_entailed=editor_vary.entailed_metrics(changes))
    qc["vision"] = vision                        # 관찰 + 계측 메타(원문 아님)
    row["edit_qc_result"] = qc
    row["status"] = {"pass": "pass", "review": "review_required",
                     "review_required": "review_required",
                     "reject": "reject"}.get(str(qc.get("decision")), "review_required")
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
        scope = editor_vary.semantic_scope(changes)
        try:
            obs, meta = await asyncio.wait_for(edit_intent_vision.observe(
                settings, baseline=InlineImage("image/jpeg", src_path.read_bytes()),
                edited=InlineImage("image/png", img_path.read_bytes()),
                edit_type=r["edit_type"], adjustments={"changes": changes},
                allowed_scope=scope), args.timeout)
            vision = {"observation": obs, "meta": meta}
            done += 1
        except Exception as e:                                # noqa: BLE001
            vision = {"meta": edit_intent_vision.failure_meta(e)}
        r["vision_calls"] = 1
        qc = edit_intent_qc.evaluate(
            baseline_bgr=_bgr(src_path.read_bytes()), edited_bgr=_bgr(img_path.read_bytes()),
            edit_type=r["edit_type"], allowed_scope=scope, target_ratio=None,
            vision=vision, require_vision=False, semantic_scope=scope,
            extra_entailed=editor_vary.entailed_metrics(changes))
        qc["vision"] = vision
        r["edit_qc_result"] = qc
        r["status"] = {"pass": "pass", "review": "review_required",
                       "review_required": "review_required",
                       "reject": "reject"}.get(str(qc.get("decision")), "review_required")
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
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    budget_per_sample = args.image_usd + (args.vision_usd if not args.no_vision else 0)
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
          f"Vision {0 if args.no_vision else len(plan)}회, 추정 ${est:.2f}")
    print(f"입력: {EXAMPLES} (레포 예시 이미지, 운영 사용자 데이터 아님)")
    print(f"출력: {out_dir} (R2 미사용)  DB: {args.dsn or '(미기록)'}")
    if args.dry_run:
        print("dry-run — provider 호출 0")
        return 0

    gemini = GeminiImageClient(settings)
    rows = []
    for n, (src, case_name, changes) in enumerate(plan, 1):
        row = await _one_sample(settings, gemini, src_path=src, case_name=case_name,
                                changes=changes, timeout=args.timeout, out_dir=out_dir,
                                use_vision=not args.no_vision)
        rows.append(row)
        print(f"  [{n}/{len(plan)}] {case_name:<12} {row['status']:<16} "
              f"{src.name[:28]}", flush=True)
        (out_dir / "samples.jsonl").open("a", encoding="utf-8").write(
            json.dumps(row, ensure_ascii=False, default=str) + "\n")

    spent = sum(r["image_calls"] for r in rows) * args.image_usd + \
        sum(r["vision_calls"] for r in rows) * args.vision_usd
    print(f"\n완료: {len(rows)}건, 이미지 {sum(r['image_calls'] for r in rows)}회, "
          f"Vision {sum(r['vision_calls'] for r in rows)}회, 추정 ${spent:.2f}")
    print("사람의 판단(accepted/rejected)은 비어 있습니다 — confusion matrix 는 "
          "사람이 실제로 검수한 뒤에만 채워집니다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 3 shadow 표본 수집 (실 provider 호출)")
    ap.add_argument("--dsn", help="disposable DB DSN (운영이면 거부). 없으면 파일만 기록")
    ap.add_argument("--per-pipeline", type=int, default=30)
    ap.add_argument("--max-calls", type=int, default=60, help="전체 생성 호출 상한")
    ap.add_argument("--budget-usd", type=float, default=12.0)
    ap.add_argument("--image-usd", type=float, default=0.15)
    ap.add_argument("--vision-usd", type=float, default=0.003)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--out", default="/tmp/shadow-samples")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vision-backfill", help="기존 samples.jsonl 에 Vision 만 다시 채운다")
    args = ap.parse_args()
    if args.dsn:
        _local_only(args.dsn)
    if args.vision_backfill:
        return asyncio.run(vision_backfill(args))
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
