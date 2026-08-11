"""SOURCE-FIRST Phase 1.7 — 실제 소스 사진에서 **대상 의류 마스크**를 얻는다.

왜 필요한가
-----------
기존 소스 마스크는 순환이었다: Vision landmark → panel poly → 그 poly 로 제한한 전경.
그래서 그 마스크에서 기하를 뽑으면 결국 Vision 좌표를 되읽는 것이었다. 배경 차분만으로는
매장 사진에서 프레임의 88.9% 가 전경으로 잡힌다(4ff2132f 실측).

이 스파이크는 사전학습 SAM2 로 **후보 마스크들**을 만들고, 그중 무엇이 대상 상품인지
같은 상품의 다른 뷰로 고른다. 학습·파인튜닝 없음. 운영 배선 없음.

자동 프롬프트
-------------
transformers 의 SAM2 는 point/box 프롬프트를 받는다. 사용자가 점을 찍게 할 수는 없으므로
(제품에 사람 검수 단계가 없다) **균일 격자 점**을 자동으로 넣는다 — 공식 AMG 도 같은
원리다. 손으로 그린 박스는 쓰지 않는다.

provider 호출 0회. Gemini 생성 0회.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

BLOCKED9 = Path("ab_out/cp11_parity_qa/blocked9/artifacts")

MODEL_ID = "facebook/sam2.1-hiera-tiny"
#: 격자 점 개수(한 변). 8×8=64 프롬프트 — CPU 에서 실용적이면서 소매·몸통을 따로 잡기에 충분.
GRID = 8
#: 후보 중복 제거 임계(IoU).
DEDUPE_IOU = 0.80


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter / union) if union else 0.0


def candidate_stats(mask: np.ndarray) -> dict:
    h, w = mask.shape
    area = int(mask.sum())
    ys, xs = np.nonzero(mask)
    if area == 0:
        return {"areaFrac": 0.0}
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    border = int(mask[0].sum() + mask[-1].sum() + mask[:, 0].sum() + mask[:, -1].sum())
    perim = 2 * (h + w)
    n_lab, _ = cv2.connectedComponents(mask.astype(np.uint8))
    return {
        "areaFrac": round(area / (h * w), 4),
        "bbox": [x0, y0, x1 - x0 + 1, y1 - y0 + 1],
        "bboxFillFrac": round(area / max(1, (x1 - x0 + 1) * (y1 - y0 + 1)), 3),
        "borderTouchFrac": round(border / perim, 4),
        "components": int(n_lab - 1),
        "aspectHW": round((y1 - y0 + 1) / max(1, (x1 - x0 + 1)), 3),
    }


def plausible(st: dict) -> tuple[bool, str]:
    """§5 — 명백히 나쁜 후보만 보수적으로 거른다. 한 표본으로 옷 전용 임계를 새기지 않는다."""
    a = st.get("areaFrac", 0.0)
    if a < 0.02:
        return False, "too_small"
    if a > 0.75:
        return False, "near_full_frame"
    if st.get("borderTouchFrac", 0) > 0.55:
        return False, "border_dominated"
    if st.get("aspectHW", 1) > 6 or st.get("aspectHW", 1) < 0.12:
        return False, "impossible_aspect"
    return True, ""


@torch.no_grad()
def sam2_candidates(model, processor, rgb: np.ndarray, device: str) -> list[np.ndarray]:
    """균일 격자 점 프롬프트로 후보 마스크를 모은다(자동 — 사용자 입력 없음)."""
    h, w = rgb.shape[:2]
    xs = np.linspace(w / (GRID + 1), w - w / (GRID + 1), GRID)
    ys = np.linspace(h / (GRID + 1), h - h / (GRID + 1), GRID)
    points = [[[float(x), float(y)]] for y in ys for x in xs]

    masks: list[np.ndarray] = []
    batch = 16
    for i in range(0, len(points), batch):
        chunk = points[i:i + batch]
        inputs = processor(
            images=[rgb] * len(chunk),
            input_points=[[p] for p in chunk],
            input_labels=[[[1]] for _ in chunk],
            return_tensors="pt").to(device)
        out = model(**inputs, multimask_output=True)
        post = processor.post_process_masks(
            out.pred_masks, inputs["original_sizes"])
        for per_image, score in zip(post, out.iou_scores):
            m = per_image[0] if per_image.ndim == 4 else per_image
            m = m.cpu().numpy()
            s = score.detach().cpu().numpy().reshape(-1)
            for j in range(m.shape[0]):
                if s[j] < 0.7:
                    continue
                masks.append(m[j].astype(bool))
    return masks


def dedupe(masks: list[np.ndarray]) -> list[np.ndarray]:
    kept: list[np.ndarray] = []
    for m in sorted(masks, key=lambda x: -x.sum()):
        if all(_iou(m, k) < DEDUPE_IOU for k in kept):
            kept.append(m)
    return kept


def overlay(bgr: np.ndarray, mask: np.ndarray, colour=(0, 255, 0)) -> np.ndarray:
    out = bgr.copy()
    tint = out.copy()
    tint[mask] = colour
    out = cv2.addWeighted(out, 0.7, tint, 0.3, 0)
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, cnts, -1, colour, max(2, bgr.shape[1] // 300))
    return out


def contact_sheet(bgr: np.ndarray, masks: list[np.ndarray], cols: int = 6) -> np.ndarray:
    if not masks:
        return bgr.copy()
    tiles = [cv2.resize(overlay(bgr, m), (bgr.shape[1] // 4, bgr.shape[0] // 4))
             for m in masks]
    rows = []
    for i in range(0, len(tiles), cols):
        row = tiles[i:i + cols]
        while len(row) < cols:
            row.append(np.zeros_like(tiles[0]))
        rows.append(np.hstack(row))
    return np.vstack(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", default="4ff2132f")
    ap.add_argument("--out", default="ab_out/source_sam2")
    args = ap.parse_args()

    out = Path(args.out) / args.pid
    out.mkdir(parents=True, exist_ok=True)

    src = next(iter(sorted((BLOCKED9 / args.pid).glob("source_*_Front.jpg"))), None)
    if src is None:
        print("no front source"); return 1
    raw = src.read_bytes()
    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    from transformers import Sam2Model, Sam2Processor
    device = _device()
    print(f"[sam2] device={device} model={MODEL_ID}", flush=True)
    processor = Sam2Processor.from_pretrained(MODEL_ID)
    model = Sam2Model.from_pretrained(MODEL_ID).to(device).eval()

    cands = sam2_candidates(model, processor, rgb, device)
    print(f"[sam2] raw candidates: {len(cands)}", flush=True)
    kept = dedupe(cands)
    print(f"[sam2] after dedupe: {len(kept)}", flush=True)

    records = []
    plausible_masks = []
    for i, m in enumerate(kept):
        st = candidate_stats(m)
        ok, why = plausible(st)
        records.append({"id": i, **st, "plausible": ok, "reject": why or None})
        if ok:
            plausible_masks.append((i, m))
    print(f"[sam2] plausible: {len(plausible_masks)} / {len(kept)}", flush=True)

    cv2.imwrite(str(out / "all_candidates.png"), contact_sheet(bgr, kept))
    cv2.imwrite(str(out / "plausible_candidates.png"),
                contact_sheet(bgr, [m for _i, m in plausible_masks]))
    for i, m in plausible_masks[:12]:
        cv2.imwrite(str(out / f"cand_{i:02d}.png"), overlay(bgr, m))
        cv2.imwrite(str(out / f"cand_{i:02d}_mask.png"), m.astype(np.uint8) * 255)

    (out / "candidates.json").write_text(json.dumps(
        {"pid": args.pid, "sourceSha": _sha(raw), "model": MODEL_ID,
         "grid": GRID, "records": records}, ensure_ascii=False, indent=2))
    print(f"[sam2] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
