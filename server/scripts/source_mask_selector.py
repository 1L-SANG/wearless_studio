"""SOURCE-FIRST Phase 1.8 — **어느 마스크가 이 상품인가** 를 자동으로 고른다.

왜 필요한가
-----------
SAM2 는 후보를 잘 만든다. 문제는 고르기다. 실측:
  · 4a936543(평면 촬영): 가장 큰 그럴듯한 후보는 **배경 판**이다. "제일 큰 것"을 고르면
    배경이 뽑힌다.
  · 4a936543: 옷이 몸통/좌소매/우소매로 **쪼개진다**. 마스크 1개 = 옷 1벌이 아니다.
  · 2dc913d6: 최적 후보가 **팔뚝까지** 삼킨다.
그래서 크기·중심·인덱스는 권위가 될 수 없다.

무엇을 쓰는가
-------------
같은 상품의 **다른 뷰**가 서로를 지지하는가로 고른다. 원본 사진끼리 비교하면 배경끼리도
닮아서(같은 매장·같은 판) 엉뚱한 후보가 보상을 받는다. 그래서 비교는 **마스크 단위**로만
한다 — 후보를 잘라내고 배경을 중립색으로 지운 뒤 임베딩한다.

기존 인프라만 쓴다: `app.services.embeddings`(SigLIP). provider 호출 0회.
텍스트 사전지식은 같은 SigLIP 의 텍스트 타워로 **약한 보조 신호**로만 쓴다(§5).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402
from app.services import embeddings as emb  # noqa: E402

from scripts.source_sam2_spike import (  # noqa: E402
    BLOCKED9,
    MODEL_ID,
    candidate_stats,
    dedupe,
    plausible,
    sam2_candidates,
)

CACHE = Path("ab_out/source_sam2_cache")
SELECTOR_VERSION = "product_mask_selector_v1"
CROP_VERSION = "masked_crop_neutral_v1"

#: 중립 배경. 결정론이고 옷 픽셀은 그대로 둔다(§2).
NEUTRAL = 128

#: 약한 보조 신호일 뿐 — 단독 선택자가 아니다(§5).
POSITIVE_TEXT = [
    "a photo of a piece of clothing",
    "a garment, a top, a shirt or blouse",
]
NEGATIVE_TEXT = [
    "a plain wall or floor background",
    "a human hand or arm",
    "a clothes hanger",
    "a shop rack or shelf",
    "an empty backdrop surface",
]

#: 최종 판단 여유. 1등과 2등이 이보다 가까우면 강제로 고르지 않는다(§12).
AMBIGUOUS_MARGIN = 0.02


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def masked_crop(bgr: np.ndarray, mask: np.ndarray) -> bytes:
    """마스크 밖을 중립색으로 지우고 bbox 로 자른다. 옷 픽셀은 손대지 않는다."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return b""
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    crop = bgr[y0:y1 + 1, x0:x1 + 1].copy()
    m = mask[y0:y1 + 1, x0:x1 + 1]
    crop[~m] = NEUTRAL
    ok, buf = cv2.imencode(".png", crop)
    return buf.tobytes() if ok else b""


def load_or_segment(model, processor, device, path: Path, slot: str) -> list[dict]:
    """뷰 하나의 후보들. SHA 로 캐시 — 선택자를 여러 번 돌려도 SAM 은 한 번만(§13)."""
    raw = path.read_bytes()
    sha = _sha(raw)
    key = CACHE / f"{sha}.npz"
    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if key.exists():
        z = np.load(key)
        masks = [z[k].astype(bool) for k in sorted(z.files)]
    else:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        masks = dedupe(sam2_candidates(model, processor, rgb, device))
        CACHE.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(key, **{f"m{i:03d}": m for i, m in enumerate(masks)})
    out = []
    for i, m in enumerate(masks):
        st = candidate_stats(m)
        ok, why = plausible(st)
        out.append({"slot": slot, "asset": path.name, "sourceSha": sha, "id": i,
                    "mask": m, "bgr": bgr, "stats": st,
                    "plausible": ok, "reject": why or None})
    return out


def embed_candidates(cands: list[dict], model_id: str) -> None:
    live = [c for c in cands if c["plausible"]]
    crops = [masked_crop(c["bgr"], c["mask"]) for c in live]
    keep = [(c, b) for c, b in zip(live, crops) if b]
    if not keep:
        return
    vecs = emb.embed_images([b for _c, b in keep], model_id=model_id)
    for (c, _b), v in zip(keep, vecs):
        c["vec"] = np.asarray(v, np.float32)


def siglip_text_vectors(model_id: str, phrases: list[str]) -> np.ndarray | None:
    """같은 SigLIP 의 텍스트 타워 — 이미지 임베딩과 같은 공간이라야 비교가 성립한다.

    SigLIP 토크나이저는 protobuf 를 요구한다. 이 환경엔 없다(2026-08-10 확인). 텍스트
    사전지식은 애초에 **약한 보조 신호**일 뿐이므로, 의존성을 새로 끌어오는 대신 None 을
    돌려주고 호출측이 detail-only 랭킹으로 강등된다 — 원 커밋(5977e71)이 기록한 그 동작이다.
    """
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
        tok = AutoTokenizer.from_pretrained(model_id)
        mdl = AutoModel.from_pretrained(model_id).eval()
        with torch.no_grad():
            t = tok(phrases, padding="max_length", truncation=True, return_tensors="pt")
            f = mdl.get_text_features(**t)
            f = f / f.norm(dim=-1, keepdim=True)
        return f.cpu().numpy().astype(np.float32)
    except Exception:                       # noqa: BLE001 — 보조 신호의 부재는 실패가 아니다
        return None


def select(product: dict, model_id: str, target_slot: str = "Front") -> dict:
    """뷰 간 지지 + 약한 사전지식으로 순위를 매긴다. 크기·중심은 약한 사전지식일 뿐.

    `target_slot` 은 **어느 뷰의 후보를 고를 것인가** 다. 원 구현은 Front 로 고정돼 있었는데,
    증강 스파이크는 Front·Back·Detail 각각에 마스크가 필요하다. 점수식은 한 글자도 바뀌지
    않는다 — 같은 함수를 다른 뷰에 겨눌 뿐이고, 나머지 뷰는 그대로 교차 지지로 쓰인다.
    """
    by_slot: dict[str, list[dict]] = {}
    for c in product["candidates"]:
        if c.get("vec") is not None:
            by_slot.setdefault(c["slot"], []).append(c)

    front = by_slot.get(target_slot, [])
    others = [c for s, lst in by_slot.items() if s != target_slot for c in lst]

    pos = siglip_text_vectors(model_id, POSITIVE_TEXT)
    neg = siglip_text_vectors(model_id, NEGATIVE_TEXT)
    text_prior = pos is not None and neg is not None

    ranked = []
    for c in front:
        v = c["vec"]
        # A. 교차 뷰 지지 — 같은 상품의 다른 뷰 후보 중 가장 잘 맞는 것들의 평균.
        sims = sorted((float(v @ o["vec"]) for o in others), reverse=True)
        cross = float(np.mean(sims[:3])) if sims else 0.0
        support = int(sum(1 for s in sims if s > 0.55))
        # B/E. 약한 보조 신호. 텍스트 타워가 없으면 0 — 즉 cross-view 지지만으로 순위가 난다.
        p = float(np.max(pos @ v)) if text_prior else 0.0
        n = float(np.max(neg @ v)) if text_prior else 0.0
        st = c["stats"]
        area = st.get("areaFrac", 0.0)
        # 크기는 **약한** 사전지식이다 — 과하게 크거나 작은 것만 살짝 깎는다.
        size_prior = -0.05 if (area > 0.6 or area < 0.05) else 0.0
        score = 1.0 * cross + 0.35 * (p - n) + size_prior
        ranked.append({
            "id": c["id"], "slot": c["slot"], "score": round(score, 4),
            "crossViewSim": round(cross, 4), "multiViewSupport": support,
            "textGarment": round(p, 4), "textNegative": round(n, 4),
            "sizePrior": size_prior, "stats": st,
        })
    ranked.sort(key=lambda r: -r["score"])

    state = "NO_VALID_PRODUCT_MASK"
    chosen = None
    if ranked:
        if len(ranked) == 1 or (ranked[0]["score"] - ranked[1]["score"]) >= AMBIGUOUS_MARGIN:
            state, chosen = "SELECTED", ranked[0]["id"]
        else:
            state = "AMBIGUOUS"
    return {"state": state, "selectedId": chosen, "ranking": ranked,
            "targetSlot": target_slot, "textPriorAvailable": text_prior,
            "margin": (round(ranked[0]["score"] - ranked[1]["score"], 4)
                       if len(ranked) > 1 else None)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pids", default="4ff2132f,2dc913d6,4a936543")
    ap.add_argument("--out", default="ab_out/source_mask_selection")
    args = ap.parse_args()

    s = load_settings()
    model_id = s.embed_image_model
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from transformers import Sam2Model, Sam2Processor
    from scripts.source_sam2_spike import _device
    device = _device()
    processor = Sam2Processor.from_pretrained(MODEL_ID)
    sam = Sam2Model.from_pretrained(MODEL_ID).to(device).eval()

    results = []
    for pid in [x.strip() for x in args.pids.split(",") if x.strip()]:
        d = BLOCKED9 / pid
        views = []
        for p in sorted(d.glob("source_*")):
            slot = p.stem.split("_")[-1]
            views.append((p, slot))
        cands: list[dict] = []
        for p, slot in views:
            cands.extend(load_or_segment(sam, processor, device, p, slot))
        embed_candidates(cands, model_id)
        product = {"pid": pid, "candidates": cands}
        sel = select(product, model_id)

        n_live = sum(1 for c in cands if c.get("vec") is not None)
        print(f"\n=== {pid} ===  views={len(views)} candidates={len(cands)} "
              f"embedded={n_live}")
        print(f"  state={sel['state']} selected={sel['selectedId']} "
              f"margin={sel['margin']}")
        for r in sel["ranking"][:5]:
            print(f"   #{r['id']:>2} score={r['score']:+.4f} cross={r['crossViewSim']:.3f} "
                  f"support={r['multiViewSupport']} txt+={r['textGarment']:.3f} "
                  f"txt-={r['textNegative']:.3f} area={r['stats'].get('areaFrac')}")

        # 선택 결과 시각화
        if sel["selectedId"] is not None:
            pick = next(c for c in cands
                        if c["slot"] == "Front" and c["id"] == sel["selectedId"])
            from scripts.source_sam2_spike import overlay
            pd = out / pid
            pd.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(pd / "selected.png"), overlay(pick["bgr"], pick["mask"]))
            cv2.imwrite(str(pd / "selected_mask.png"),
                        pick["mask"].astype(np.uint8) * 255)
        results.append({"pid": pid, "selectorVersion": SELECTOR_VERSION,
                        "cropVersion": CROP_VERSION, "embedModel": model_id,
                        **sel})
        (out / "selection.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"\nwrote {out}/selection.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
