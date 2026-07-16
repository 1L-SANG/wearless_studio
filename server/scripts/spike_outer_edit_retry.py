"""아우터 극단 축 — '편집 재시도' 스파이크 (P1 QC 재시도의 편집형 변주 검증).

가설: 실패한 생성 이미지를 입력으로 주고 축 지시만 담은 편집 호출을 하면,
정체성은 입력 이미지가 지고 지시 예산 전부가 형태 변경에 쓰여 극단 축이 반영된다.

대상: 캠페인 잔존 실패 O01(slim)·O02(over)·O05(crop_short). 1콜 편집 + 1콜 판정/건.
실행: cd server && .venv/bin/python -m scripts.spike_outer_edit_retry
출력: ab_out/fit_campaign/<armId>_edit.png + edit_retry_results.jsonl
"""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

from scripts.fit_fidelity_campaign import (  # noqa: E402
    ARMS, JUDGE_PROMPT, JUDGE_SCHEMA, SRC, load_local,
)
from app.agents.fit_axes import AXIS_OBSERVABLES  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.agents.vision_llm import analyze_with_fallback  # noqa: E402
from app.config import load_settings  # noqa: E402

OUT = SERVER / "ab_out" / "fit_campaign"

TAIL = (" Change NOTHING else — keep the same garment color, fabric, buttons, lapels and details, "
        "the same mannequin, pose, camera framing, plain background, and bare feet. "
        "Output ONE photorealistic image.")

EDIT = {
    "O01": ("Re-tailor the jacket in this photo to a SLIM fit: move both shoulder seams up to the "
            "mannequin's natural shoulder points and remove the excess width through the body and "
            "sleeves so the jacket follows the torso with minimal ease." + TAIL),
    "O02": ("Make the jacket in this photo clearly OVERSIZED: drop both shoulder seams visibly below "
            "the shoulder points and add generous air volume through the body and sleeves, like a "
            "much larger size worn on the same mannequin." + TAIL),
    "O05": ("Shorten the jacket in this photo so its entire hem ends at the mannequin's natural "
            "waist, clearly above the skirt's waistband, like a cropped version of the same jacket."
            + TAIL),
}


async def run_one(s, arm_id: str) -> dict:
    arm = next(a for a in ARMS if a["id"] == arm_id)
    rec = {"armId": arm_id, "mode": "edit_retry", "axes": arm["axes"]}
    failed = load_local(OUT / f"{arm_id}.png")
    g = GeminiImageClient(s)
    res = await g.generate_content_image(
        resolve_model(s, s.mannequin_tier), EDIT[arm_id], [failed],
        s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
    out = OUT / f"{arm_id}_edit.png"
    out.write_bytes(res.image)
    rec["output"] = str(out)
    rec["output_hash"] = hashlib.sha256(res.image).hexdigest()[:16]
    spec = {"armId": arm_id, "withBottom": arm["with_bottom"],
            "axes": [{"axis": ax, "value": v,
                      "observableTarget": AXIS_OBSERVABLES[(arm["category"], ax, v)]}
                     for ax, v in arm["axes"].items()]}
    srcs = [load_local(p) for p in SRC[arm["src"]]]
    raw, provider = await analyze_with_fallback(
        s, JUDGE_PROMPT + json.dumps(spec, ensure_ascii=False),
        [*srcs, InlineImage(res.mime, res.image)], JUDGE_SCHEMA)
    rec["judge"] = raw
    ap = raw.get("axisPass", [])
    rec["auto_pass"] = bool(raw.get("identityPass")) and ap and all(
        x.get("pass") and x.get("visible") for x in ap)
    return rec


async def main():
    s = load_settings()
    results = []
    for aid in ("O01", "O02", "O05"):
        try:
            r = await run_one(s, aid)
        except Exception as e:
            r = {"armId": aid, "mode": "edit_retry", "error": f"{type(e).__name__}: {e}"[:300],
                 "auto_pass": None}
        results.append(r)
        print(f"{'✅' if r['auto_pass'] else ('⚠️' if r['auto_pass'] is None else '❌')} {aid}: "
              + (r.get("error") or "; ".join(
                  f"{x['axis']}={'P' if x['pass'] else 'F'}({x['observedLandmark'][:60]})"
                  for x in r["judge"]["axisPass"])
                  + f" identity={r['judge']['identityPass']}"))
    with open(OUT / "edit_retry_results.jsonl", "a") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
