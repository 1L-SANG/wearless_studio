"""저장된 SAM2 후보 중 **이 상품**을 자동으로 고른다 (최소 구현, 4ff 판별용).

증거 두 가지만 쓴다.
  1. 같은 상품의 Detail 컷과의 SigLIP 유사도 — Detail 은 원단 접사라 배경이 거의 없고,
     바닥·손·행거와는 닮을 이유가 없다. 마스크는 잘라내고 배경을 중립색으로 지운 뒤
     비교한다(원본 배경이 점수를 지배하지 않게).
  2. 같은 SigLIP 의 텍스트 타워로 '옷' vs '배경/손/행거' 약한 사전지식.

크기·중심·후보 인덱스는 점수에 넣지 않는다 — 4a936543 에서 가장 큰 후보가 **배경**이라는
것이 이미 확인됐다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402
from app.services import embeddings as emb  # noqa: E402

BLOCKED9 = Path("ab_out/cp11_parity_qa/blocked9/artifacts")
SAM_OUT = Path("ab_out/source_sam2")
NEUTRAL = 128
AMBIGUOUS_MARGIN = 0.02

POSITIVE = ["a photo of a piece of clothing", "a garment, a top, a shirt or blouse"]
NEGATIVE = ["a plain wall or floor background", "a human hand or arm",
            "a clothes hanger", "a shop rack or shelf", "an empty backdrop"]


def masked_crop_png(bgr: np.ndarray, mask: np.ndarray) -> bytes:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return b""
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    crop = bgr[y0:y1 + 1, x0:x1 + 1].copy()
    crop[~mask[y0:y1 + 1, x0:x1 + 1]] = NEUTRAL
    ok, buf = cv2.imencode(".png", crop)
    return buf.tobytes() if ok else b""


def text_vecs(model_id: str, phrases: list[str]) -> np.ndarray:
    import torch
    from transformers import AutoModel, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_id)
    mdl = AutoModel.from_pretrained(model_id).eval()
    with torch.no_grad():
        t = tok(phrases, padding="max_length", truncation=True, return_tensors="pt")
        f = mdl.get_text_features(**t)
        f = f / f.norm(dim=-1, keepdim=True)
    return f.cpu().numpy().astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", default="4ff2132f")
    args = ap.parse_args()
    pid = args.pid
    s = load_settings()
    model_id = s.embed_image_model

    src_p = next(iter(sorted((BLOCKED9 / pid).glob("source_*_Front.jpg"))))
    bgr = cv2.imdecode(np.frombuffer(src_p.read_bytes(), np.uint8), cv2.IMREAD_COLOR)

    masks = []
    for mp in sorted((SAM_OUT / pid).glob("cand_*_mask.png")):
        m = cv2.imread(str(mp), cv2.IMREAD_GRAYSCALE)
        if m is not None and m.shape == bgr.shape[:2]:
            masks.append((int(mp.stem.split("_")[1]), m > 0))
    if not masks:
        print("no saved candidates"); return 1

    details = sorted((BLOCKED9 / pid).glob("source_*_Detail.jpg"))
    if not details:
        print("no Detail views for same-product evidence"); return 1

    crops = [masked_crop_png(bgr, m) for _i, m in masks]
    det_bytes = [p.read_bytes() for p in details]
    vecs = emb.embed_images(crops + det_bytes, model_id=model_id)
    cand_v = np.asarray(vecs[:len(crops)], np.float32)
    det_v = np.asarray(vecs[len(crops):], np.float32)

    # 텍스트 사전지식은 **약한 보조 신호**다(§5). 토크나이저가 없으면 그것 없이 간다 —
    # 판별의 본체는 같은 상품 Detail 과의 유사도다.
    try:
        pos, neg = text_vecs(model_id, POSITIVE), text_vecs(model_id, NEGATIVE)
        text_ok = True
    except Exception as exc:                            # noqa: BLE001
        print(f"[warn] text prior unavailable ({type(exc).__name__}) — detail-only ranking")
        pos = neg = None
        text_ok = False

    ranked = []
    for (cid, m), v in zip(masks, cand_v):
        detail_sim = float(np.mean(det_v @ v))
        p = float(np.max(pos @ v)) if text_ok else 0.0
        n = float(np.max(neg @ v)) if text_ok else 0.0
        score = detail_sim + (0.35 * (p - n) if text_ok else 0.0)
        ranked.append({"id": cid, "score": round(score, 4),
                       "detailSim": round(detail_sim, 4),
                       "textGarment": round(p, 4), "textNegative": round(n, 4),
                       "areaFrac": round(float(m.mean()), 4)})
    ranked.sort(key=lambda r: -r["score"])

    margin = ranked[0]["score"] - ranked[1]["score"] if len(ranked) > 1 else None
    state = "SELECTED" if (margin is None or margin >= AMBIGUOUS_MARGIN) else "AMBIGUOUS"
    print(f"state={state} selected={ranked[0]['id'] if state=='SELECTED' else None} "
          f"margin={round(margin,4) if margin is not None else None}")
    for r in ranked:
        print(f"  #{r['id']:>2} score={r['score']:+.4f} detailSim={r['detailSim']:+.4f} "
              f"txt+={r['textGarment']:.3f} txt-={r['textNegative']:.3f} "
              f"area={r['areaFrac']}")

    out = Path("ab_out/source_mask_selection") / pid
    out.mkdir(parents=True, exist_ok=True)
    if state == "SELECTED":
        win = dict(masks)[ranked[0]["id"]]
        cv2.imwrite(str(out / "selected_mask.png"), win.astype(np.uint8) * 255)
        tint = bgr.copy(); tint[win] = (0, 255, 0)
        cv2.imwrite(str(out / "selected.png"),
                    cv2.addWeighted(bgr, 0.7, tint, 0.3, 0))
    (out / "selection.json").write_text(json.dumps(
        {"pid": pid, "state": state, "selectedId": ranked[0]["id"], "margin": margin,
         "embedModel": model_id, "ranking": ranked}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
