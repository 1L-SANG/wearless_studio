"""핏 반영(fidelity) 전 카테고리 실생성 검증 캠페인 — Codex ultra 설계(M1~M4) 실행 러너.

설계 정본: documents/mannequin_fit_fidelity_plan.md + scratchpad codex-campaign-matrix.
36 arms(생성 1콜/arm) + vision 자동 판정 1콜/arm. 배치 1(14)·2(12)·3(10), retry 금지.
사람 스팟체크(실패 전부 + 통과 20%)는 오퍼레이터가 별도 수행 — 이 스크립트는 자동판정까지.

실행: cd server && .venv/bin/python -m scripts.fit_fidelity_campaign --batch 1
산출: server/ab_out/fit_campaign/<armId>.png + results.jsonl (판정·해시 포함)
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

from app.agents import mannequin  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.agents.vision_llm import analyze_with_fallback  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

GEN = ROOT / "outputs/coor_matching/generated_v2"
FIT = ROOT / "public/assets/fit-examples"

# 입력 약호 (설계 M1 — 실파일 검증됨)
SRC = {
    "T-A": [ROOT / "spike/input/tee-fb/1_front.jpeg", ROOT / "spike/input/tee-fb/2_back.jpeg"],
    "T-B": [GEN / "men_top/09_무난_원_포켓_롱_슬리브_헨리_티셔츠_(멜란지그레이)_3752.png"],
    "T-C": [GEN / "men_top/07_폴로_멀티_핀_스트라이프_폴로_셔츠_(블루)_3750.png"],
    "T-D": [GEN / "women_top/02_무난_스트라이프_롱_슬리브_티셔츠_(화이트)_2216.png"],
    "P-A": [GEN / "women_bottom/03_무난_켄톤_플라이_스트레이트_데님_팬츠_(워시드블루)_3283.png"],
    "P-B": [GEN / "women_bottom/05_무난_릴랙스드_플루이드_와이드_레그_트라우저_(그레이)_cos.png"],
    "P-C": [GEN / "men_bottom/05_무난_스트레이트_컷_샌드_워시드_진스_(아이스블루)_3538.png"],
    "P-D": [GEN / "men_bottom/12_무난_스카치_울_테이퍼드_팬츠_블랙_ptry1275.png"],
    "P-E": [GEN / "women_bottom/08_무난_빅_포켓_와이드레그_스웻팬츠_(블랙)_1411.png"],
    "S-A": [ROOT / "public/assets/matching/bottom/women-bottom-14.png"],
    "S-B": [GEN / "women_bottom/12_치마_새틴_랩_스커트_(블랙)_cos.png"],
    "S-C": [GEN / "women_bottom/15_치마_랩_플리츠_스커트_(그레이)_mixxo12692.png"],
    "D-A": [FIT / "dress-women-silhouette-fit_and_flare.jpg"],
    "D-B": [FIT / "dress-women-length-long.jpg"],
    "D-C": [FIT / "dress-women-silhouette-h_line.jpg"],
    "O-A": [FIT / "outer-any-length-long.jpg"],
    "O-B": [FIT / "outer-any-fit-slim.jpg"],
}
BASE_R2 = {"women": "seed/mannequin/base-women-2K.png", "men": "seed/mannequin/base-men-2K.png"}
WB_R2 = "seed/matching/match_women_bottom_14.png"
CLOTHING_TYPE = {"top": "top", "pants": "bottom", "skirt": "bottom", "dress": "dress", "outer": "outer"}
PRODUCT_NAME = {"top": "핏 검증 상의", "pants": "핏 검증 팬츠", "skirt": "핏 검증 스커트",
                "dress": "핏 검증 원피스", "outer": "핏 검증 아우터"}

def A(aid, cat, gender, src, axes, adjusted, wb):
    return {"id": aid, "category": cat, "gender": gender, "src": src,
            "axes": axes, "adjusted": adjusted, "with_bottom": wb}

ARMS = [
    # 배치 1 — top/pants 단일축 극단 + men (14)
    A("T01", "top", "women", "T-A", {"fit": "tight"}, ["fit"], False),
    A("T02", "top", "women", "T-A", {"fit": "over"}, ["fit"], False),
    A("T03", "top", "men", "T-B", {"length": "crop"}, ["length"], False),
    A("T04", "top", "men", "T-B", {"length": "long"}, ["length"], False),
    A("T05", "top", "men", "T-C", {"fit": "slim"}, ["fit"], False),
    A("T06", "top", "men", "T-C", {"fit": "over"}, ["fit"], False),
    A("P01", "pants", "women", "P-A", {"cut": "skinny"}, ["cut"], False),
    A("P02", "pants", "women", "P-A", {"cut": "wide"}, ["cut"], False),
    A("P03", "pants", "women", "P-B", {"length": "above_ankle"}, ["length"], False),
    A("P04", "pants", "women", "P-B", {"length": "below_ankle"}, ["length"], False),
    A("P05", "pants", "men", "P-C", {"cut": "slim"}, ["cut"], False),
    A("P06", "pants", "men", "P-C", {"cut": "wide"}, ["cut"], False),
    A("P07", "pants", "men", "P-D", {"length": "above_ankle"}, ["length"], False),
    A("P08", "pants", "men", "P-D", {"length": "below_ankle"}, ["length"], False),
    # 배치 2 — skirt 실상품 + dress 대용 (12)
    A("S01", "skirt", "women", "S-A", {"length": "mini"}, ["length"], False),
    A("S02", "skirt", "women", "S-A", {"length": "long"}, ["length"], False),
    A("S03", "skirt", "women", "S-B", {"silhouette": "h_line"}, ["silhouette"], False),
    A("S04", "skirt", "women", "S-B", {"silhouette": "mermaid"}, ["silhouette"], False),
    A("S05", "skirt", "women", "S-C", {"length": "mini", "silhouette": "h_line"}, ["length", "silhouette"], False),
    A("S06", "skirt", "women", "S-C", {"length": "long", "silhouette": "mermaid"}, ["length", "silhouette"], False),
    A("D01", "dress", "women", "D-A", {"length": "mini"}, ["length"], False),
    A("D02", "dress", "women", "D-A", {"length": "long"}, ["length"], False),
    A("D03", "dress", "women", "D-B", {"silhouette": "h_line"}, ["silhouette"], False),
    A("D04", "dress", "women", "D-B", {"silhouette": "mermaid"}, ["silhouette"], False),
    A("D05", "dress", "women", "D-C", {"length": "mini", "silhouette": "fit_and_flare"}, ["length", "silhouette"], False),
    A("D06", "dress", "women", "D-C", {"length": "long", "silhouette": "mermaid"}, ["length", "silhouette"], False),
    # 배치 3 — withBottom·복합·outer (10)
    A("T07", "top", "women", "T-D", {"length": "ultra_crop"}, ["length"], True),
    A("T08", "top", "women", "T-D", {"length": "long"}, ["length"], True),
    A("P09", "pants", "women", "P-E", {"cut": "straight", "length": "above_ankle"}, ["cut", "length"], False),
    A("P10", "pants", "women", "P-E", {"cut": "wide", "length": "below_ankle"}, ["cut", "length"], False),
    A("O01", "outer", "women", "O-A", {"fit": "slim"}, ["fit"], False),
    A("O02", "outer", "women", "O-A", {"fit": "over"}, ["fit"], False),
    A("O03", "outer", "women", "O-B", {"length": "crop_short"}, ["length"], False),
    A("O04", "outer", "women", "O-B", {"length": "long"}, ["length"], False),
    A("O05", "outer", "women", "O-B", {"fit": "slim", "length": "crop_short"}, ["fit", "length"], True),
    A("O06", "outer", "women", "O-B", {"fit": "over", "length": "long"}, ["fit", "length"], True),
]
BATCHES = {1: [a["id"] for a in ARMS[:14]], 2: [a["id"] for a in ARMS[14:26]], 3: [a["id"] for a in ARMS[26:]]}

JUDGE_PROMPT = """You are a strict visual QA judge for a garment-on-mannequin generation.
You receive SOURCE PRODUCT images, an optional MATCHING BOTTOM image, one GENERATED image (always the LAST image), and ARM_SPEC JSON.
Judge only visible evidence. Do not infer a landmark that is cropped, hidden, or ambiguous.
For every axis in ARM_SPEC.axes, compare the generated main garment against that axis's exact observableTarget.
The target is absolute, not merely "different from source"; if the source already satisfies it, do not require a change.
For outer fit, assess shoulder-seam placement and body/sleeve air volume as separate visible cues; do not infer a natural shoulder seam or low ease merely because a jacket looks structured or padded.
Set visible=false and pass=false when the required landmark or full outline is not visible.
The mannequin is barefoot; never require footwear to satisfy an observable target.
For identityPass, compare color, pattern, fabric appearance, construction, seams, neckline, trims, hardware, logos, and prints. Exempt only changes necessarily caused by declared axes. If withBottom=true, the matching bottom must be present and preserve its visible identity.
For undeclaredAxesPreserved, judge every catalog axis absent from ARM_SPEC.axes against the source. Return null when reliable comparison is impossible, and true when no undeclared axis exists.
Do not reward photorealism or plausibility when a specified landmark is missed.
Return JSON only, matching the schema exactly. Describe observedLandmark factually; never copy the target without checking the image.

ARM_SPEC:
"""

JUDGE_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["armId", "identityPass", "axisPass", "undeclaredAxesPreserved", "mismatches"],
    "properties": {
        "armId": {"type": "string"},
        "identityPass": {"type": "boolean"},
        "axisPass": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["axis", "target", "pass", "observedLandmark", "visible"],
            "properties": {"axis": {"enum": ["fit", "length", "cut", "silhouette"]},
                           "target": {"type": "string"}, "pass": {"type": "boolean"},
                           "observedLandmark": {"type": "string"}, "visible": {"type": "boolean"}}}},
        "undeclaredAxesPreserved": {"type": ["boolean", "null"]},
        "mismatches": {"type": "array", "items": {"type": "string"}}}}


def sniff_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"\x89PNG":
        return "image/png"
    return "image/jpeg"


def load_local(path: Path) -> InlineImage:
    data = path.read_bytes()
    return InlineImage(sniff_mime(data), data)  # 확장자 아닌 sniff (M4 — png 확장자에 JPEG 바이트 존재)


def build_prompt_for(s, arm) -> str:
    from app.agents.fit_axes import AXIS_OBSERVABLES
    profile = {"category": arm["category"], "gender": arm["gender"], "source": "seller",
               "axes": arm["axes"], "version": 1}
    n_src = len(SRC[arm["src"]])
    lines = ["1. Base mannequin — the canvas to dress (keep it identical)"]
    for i in range(n_src):
        lines.append(f"{i+2}. {'front view of the garment' if i == 0 else 'back view of the garment'}")
    if arm["with_bottom"]:
        lines.append(f"{n_src+2}. matching BOTTOM garment — also dress the mannequin in this, coordinated with the top")
    ctx = mannequin.prompt_context(
        clothing_type=CLOTHING_TYPE[arm["category"]], product_count=n_src + (1 if arm["with_bottom"] else 0),
        base_gender=arm["gender"], image_manifest="\n".join(lines),
        fit_profile=profile, adjusted_axes=tuple(arm["adjusted"]))
    return render_mannequin_prompt(
        load_prompt_template(s), ctx,
        product={"name": PRODUCT_NAME[arm["category"]], "clothing_type": CLOTHING_TYPE[arm["category"]]},
        analysis={"clothingType": CLOTHING_TYPE[arm["category"]], "targetGenders": [arm["gender"]]})


async def run_arm(s, r2, out_dir: Path, arm, sem, rejudge: bool = False) -> dict:
    from app.agents.fit_axes import AXIS_OBSERVABLES
    rec = {"armId": arm["id"], "category": arm["category"], "gender": arm["gender"],
           "axes": arm["axes"], "withBottom": arm["with_bottom"], "src": arm["src"],
           "rejudge": rejudge}
    try:
        async with sem:
            srcs = [load_local(p) for p in SRC[arm["src"]]]
            wb = None
            if arm["with_bottom"]:
                wb = InlineImage("image/png", await asyncio.to_thread(r2.get_bytes, WB_R2))
            out = out_dir / f"{arm['id']}.png"
            if rejudge:  # 기존 이미지 재판정만 — 판정 프롬프트 강화 효과를 생성과 격리 검증
                gen_img = load_local(out)
            else:
                base = InlineImage("image/png", await asyncio.to_thread(r2.get_bytes, BASE_R2[arm["gender"]]))
                prompt = build_prompt_for(s, arm)
                rec["prompt_hash"] = hashlib.sha256(prompt.encode()).hexdigest()[:16]
                g = GeminiImageClient(s)
                images = [base, *srcs] + ([wb] if wb else [])
                t0 = time.time()
                res = await g.generate_content_image(resolve_model(s, s.mannequin_tier), prompt, images,
                                                     s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
                rec["gen_seconds"] = round(time.time() - t0, 1)
                out.write_bytes(res.image)
                gen_img = InlineImage(res.mime, res.image)
            rec["output"] = str(out)
            rec["output_hash"] = hashlib.sha256(gen_img.data).hexdigest()[:16]
            # 자동 판정 (M2)
            spec = {"armId": arm["id"], "withBottom": arm["with_bottom"],
                    "axes": [{"axis": ax, "value": v,
                              "observableTarget": AXIS_OBSERVABLES[(arm["category"], ax, v)]}
                             for ax, v in arm["axes"].items()]}
            judge_prompt = JUDGE_PROMPT + json.dumps(spec, ensure_ascii=False)
            judge_imgs = [*srcs] + ([wb] if wb else []) + [gen_img]
            raw, provider = await analyze_with_fallback(s, judge_prompt, judge_imgs, JUDGE_SCHEMA)
            rec["judge"] = raw
            rec["judge_provider"] = provider
            ap = raw.get("axisPass", [])
            rec["auto_pass"] = bool(raw.get("identityPass")) and ap and all(x.get("pass") and x.get("visible") for x in ap)
    except Exception as e:  # retry 금지(M4) — 실패는 기록만
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
        rec["auto_pass"] = None
    return rec


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, choices=[1, 2, 3])
    ap.add_argument("--arms", help="쉼표구분 armId (프롬프트 수정 후 재실행용, 예: P04,P08)")
    ap.add_argument("--rejudge", help="쉼표구분 armId — 재생성 없이 기존 이미지 재판정만 (판정 프롬프트 격리검증)")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    if not args.batch and not args.arms and not args.rejudge:
        ap.error("--batch, --arms 또는 --rejudge 필요")
    s = load_settings()
    r2 = R2Client(s)
    out_dir = SERVER / "ab_out" / "fit_campaign"
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = args.rejudge.split(",") if args.rejudge else (args.arms.split(",") if args.arms else BATCHES[args.batch])
    arms = [a for a in ARMS if a["id"] in ids]
    label = f"재판정 {args.rejudge}" if args.rejudge else (f"재실행 {args.arms}" if args.arms else f"배치 {args.batch}")
    print(f"{label}: {len(arms)} arms → {out_dir}")
    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*[run_arm(s, r2, out_dir, a, sem, rejudge=bool(args.rejudge)) for a in arms])
    with open(out_dir / "results.jsonl", "a") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ok = sum(1 for r in results if r["auto_pass"])
    fail = [r["armId"] for r in results if r["auto_pass"] is False]
    err = [r["armId"] for r in results if r["auto_pass"] is None]
    print(f"\n자동판정: PASS {ok}/{len(results)} · FAIL {fail or '없음'} · ERROR {err or '없음'}")
    for r in results:
        mark = "✅" if r["auto_pass"] else ("⚠️" if r["auto_pass"] is None else "❌")
        detail = r.get("error") or "; ".join(
            f"{x['axis']}={'P' if x['pass'] else 'F'}({x['observedLandmark'][:40]})"
            for x in (r.get("judge", {}).get("axisPass") or []))
        print(f" {mark} {r['armId']} [{r['category']}/{r['gender']}] {detail[:110]}")


if __name__ == "__main__":
    asyncio.run(main())
