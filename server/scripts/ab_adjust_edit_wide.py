"""편집 조정 확장 검증 — B(편집+QC)·C(편집만) × 전 카테고리·여러 핏·기장·복합축.

사용자 기준(2026-07-31): "원단 톤이 아니라 이미지 자체가 일관돼야" → 기존 축반영·정체성에
**장면 일관성 판정**(기준 컷 vs 결과: 마네킹·포즈·카메라·배경·조명·비조정 속성 동일 여부) 추가.

실행: cd server && .venv/bin/python -m scripts.ab_adjust_edit_wide
출력: ab_out/adjust_ab_wide/<case>_{B,C}.png + results.jsonl + 요약 (전 콜 1K)
"""

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

from scripts.fit_fidelity_campaign import ARMS, SRC, load_local  # noqa: E402
from app.agents import mannequin_fit_qc  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.mannequin_adjust import (  # noqa: E402
    build_adjust_directives, build_adjust_manifest, render_adjust_prompt,
)
from app.agents.model_routing import resolve_model  # noqa: E402
from app.agents.vision_llm import analyze_with_fallback  # noqa: E402
from app.config import load_settings  # noqa: E402

# (기준 arm, 조정) — 카테고리·축·방향(확대/축소) 커버 + 복합축 2건
CASES = [
    ("T02", {"fit": "slim"}),                            # top women: over → slim (축소)
    ("T03", {"length": "long"}),                         # top men: crop → long (확대)
    ("T05", {"fit": "semi_over"}),                       # top men polo: slim → semi_over
    ("P01", {"cut": "wide"}),                            # pants women: skinny → wide
    ("P03", {"length": "below_ankle"}),                  # pants women: above → below
    ("P05", {"cut": "wide"}),                            # pants men: slim → wide
    ("S01", {"length": "long"}),                         # skirt: mini → long
    ("S03", {"silhouette": "mermaid"}),                  # skirt: h_line → mermaid
    ("D01", {"length": "midi"}),                         # dress: mini → midi
    ("D03", {"silhouette": "fit_and_flare"}),            # dress: h_line → fit&flare
    ("O04", {"length": "crop_short"}),                   # outer: long → crop (난제 축소방향)
    ("T01", {"fit": "semi_over", "length": "long"}),     # 복합축 top
    ("P02", {"cut": "straight", "length": "above_ankle"}),  # 복합축 pants
]
OUT = SERVER / "ab_out" / "adjust_ab_wide"

CONSIST_PROMPT = """You compare two studio mannequin photos: IMAGE 1 = the ORIGINAL cut, IMAGE 2 = an ADJUSTED cut where ONLY these garment aspects were intentionally changed: ${adjusted}.
Judge whether everything ELSE is the same scene. Check separately:
- scene: same mannequin body and head, same pose, same camera framing, same plain background, same lighting.
- garment: for the SAME garment(s), every attribute NOT listed above is unchanged — color, pattern, fabric texture, logos, prints, construction details, and unlisted fit/length/cut/silhouette aspects.
Ignore differences that are a physically necessary consequence of the listed changes (e.g., new fabric folds from a different fit, more or less visible mannequin skin because a hem moved).
Judge only visible evidence. Return JSON only.
"""

CONSIST_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["sceneConsistent", "garmentConsistent", "differences"],
                  "properties": {"sceneConsistent": {"type": "boolean"},
                                 "garmentConsistent": {"type": "boolean"},
                                 "differences": {"type": "array", "items": {"type": "string"}}}}


def _target_profile(arm, adjust):
    return {"category": arm["category"], "gender": arm["gender"], "source": "seller",
            "axes": {**arm["axes"], **adjust}, "version": 2}


async def _timed(coro):
    t0 = time.time()
    return await coro, round(time.time() - t0, 1)


async def _consistency(s, baseline, result, adjusted_desc):
    prompt = CONSIST_PROMPT.replace("${adjusted}", adjusted_desc)
    raw, _ = await analyze_with_fallback(
        s, prompt, [baseline, InlineImage(result.mime, result.image)], CONSIST_SCHEMA)
    return {"sceneConsistent": bool(raw.get("sceneConsistent")),
            "garmentConsistent": bool(raw.get("garmentConsistent")),
            "differences": [str(d)[:200] for d in (raw.get("differences") or [])][:6]}


async def run_case(s, g, arm_id, adjust, sem):
    async with sem:
        arm = next(a for a in ARMS if a["id"] == arm_id)
        profile = _target_profile(arm, adjust)
        srcs = [load_local(p) for p in SRC[arm["src"]]]
        baseline = load_local(SERVER / "ab_out" / "fit_campaign" / f"{arm_id}.png")
        model = resolve_model(s, "image_high")
        directives = build_adjust_directives(profile, tuple(adjust.keys()))
        prompt = render_adjust_prompt(directives, build_adjust_manifest(len(srcs), False))
        adjusted_desc = ", ".join(f"{k} → {v}" for k, v in adjust.items())
        spec = mannequin_fit_qc.declared_axis_spec(profile)
        rows = []
        for label, with_qc in (("B_편집+QC", True), ("C_편집만", False)):
            try:
                gen, t_gen = await _timed(g.generate_content_image(
                    model, prompt, [baseline, *srcs], "1K",
                    aspect_ratio=s.mannequin_aspect_ratio, timeout=300.0))
                times = {"gen": t_gen}
                selected, retried = gen, False
                v, t_j = await _timed(mannequin_fit_qc.verdict(
                    s, srcs, InlineImage(gen.mime, gen.image), profile))
                times["judge" if with_qc else "measure_only_judge"] = t_j
                if with_qc:
                    failed = mannequin_fit_qc.failed_axis_specs(spec, v)
                    if failed:
                        instruction = mannequin_fit_qc.build_edit_instruction(failed)
                        ed, t_r = await _timed(g.generate_content_image(
                            model, instruction, [InlineImage(gen.mime, gen.image)], "1K",
                            aspect_ratio=s.mannequin_aspect_ratio, timeout=300.0))
                        times["retry_edit"] = t_r
                        v2, t_rj = await _timed(mannequin_fit_qc.verdict(
                            s, srcs, InlineImage(ed.mime, ed.image), profile))
                        times["rejudge"] = t_rj
                        if mannequin_fit_qc.edit_improves(v, v2):
                            selected, v, retried = ed, v2, True
                        else:
                            retried = True
                cons, t_c = await _timed(_consistency(s, baseline, selected, adjusted_desc))
                times["consistency_judge"] = t_c  # 측정용 — 흐름 시간엔 미포함
                out = OUT / f"{arm_id}_{label}.png"
                out.write_bytes(selected.image)
                flow = sum(t for k, t in times.items()
                           if k not in ("measure_only_judge", "consistency_judge"))
                axis_ok = all(x["pass"] and x["visible"] for x in v["axisPass"])
                rows.append({"case": arm_id, "adjust": adjust, "arm": label,
                             "output": str(out), "times_sec": times,
                             "flow_total_sec": round(flow, 1), "axis_pass": axis_ok,
                             "identity_pass": v["identityPass"], "qc_retry_fired": retried,
                             "scene_consistent": cons["sceneConsistent"],
                             "garment_consistent": cons["garmentConsistent"],
                             "consistency_diffs": cons["differences"], "judge": v})
                print(f"  {arm_id} {label}: {flow:.0f}s · 축 {'✅' if axis_ok else '❌'}"
                      f" · 정체성 {'✅' if v['identityPass'] else '❌'}"
                      f" · 장면일관 {'✅' if cons['sceneConsistent'] else '❌'}"
                      f" · 의류일관 {'✅' if cons['garmentConsistent'] else '❌'}"
                      f"{' · 교정발화' if retried else ''}", flush=True)
            except Exception as e:
                rows.append({"case": arm_id, "adjust": adjust, "arm": label,
                             "error": f"{type(e).__name__}: {e}"[:300]})
                print(f"  ⚠️ {arm_id} {label} 실패: {type(e).__name__}", flush=True)
        return rows


async def main():
    s = load_settings()
    g = GeminiImageClient(s)
    OUT.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(3)
    all_rows = []
    results = await asyncio.gather(*[run_case(s, g, aid, adj, sem) for aid, adj in CASES])
    for rows in results:
        all_rows.extend(rows)
    with open(OUT / "results.jsonl", "w") as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("\n=== 요약 ===", flush=True)
    for label in ("B_편집+QC", "C_편집만"):
        rs = [r for r in all_rows if r.get("arm") == label and "error" not in r]
        errs = [r for r in all_rows if r.get("arm") == label and "error" in r]
        if not rs:
            continue
        avg = sum(r["flow_total_sec"] for r in rs) / len(rs)
        def cnt(k):
            return sum(1 for r in rs if r[k])
        print(f"  {label}: n={len(rs)} 평균 {avg:.0f}s · 축 {cnt('axis_pass')}/{len(rs)}"
              f" · 정체성 {cnt('identity_pass')}/{len(rs)}"
              f" · 장면일관 {cnt('scene_consistent')}/{len(rs)}"
              f" · 의류일관 {cnt('garment_consistent')}/{len(rs)}"
              f" · 교정발화 {cnt('qc_retry_fired')}회"
              + (f" · 오류 {len(errs)}" if errs else ""), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
