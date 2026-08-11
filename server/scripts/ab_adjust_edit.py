"""조정 방식 3-way A/B — 현행(재생성+QC) vs 편집+QC vs 편집만. 소요시간 구간별 계측.

기준 컷 = fidelity 캠페인 검증 산출물(ab_out/fit_campaign/*.png — 프로필 기지).
같은 기준 컷·같은 조정값을 세 방식에 공통 적용해 품질·시간을 나란히 비교한다.

  A 현행    : 베이스+원본사진에서 재생성(mannequin_generate_v1) → QC 판정 → 미달 시 교정 편집 1회
  B 편집+QC : 현재 컷+원본사진 편집(mannequin_adjust_v2)      → QC 판정 → 미달 시 교정 편집 1회
  C 편집만  : B와 같은 편집 1콜, QC 생략 (품질 채점은 측정용으로만 별도 수행 — 시간 미포함)

실행: cd server && .venv/bin/python -m scripts.ab_adjust_edit
출력: ab_out/adjust_ab/<case>_{A,B,C}.png + results.jsonl + 요약표 (전 콜 1K 강제)
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

from scripts.fit_fidelity_campaign import (  # noqa: E402
    ARMS, BASE_R2, SRC, build_prompt_for, load_local,
)
from app.agents import mannequin_fit_qc  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.mannequin_adjust import (  # noqa: E402
    build_adjust_directives, build_adjust_manifest, render_adjust_prompt,
)
from app.agents.model_routing import resolve_model  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

# (기준 arm, 조정: 축→새 값) — 기준 컷의 알려진 값에서 반대 극단으로
CASES = [
    ("T01", {"fit": "over"}),        # slim 티셔츠 → 오버핏
    ("T04", {"length": "crop"}),     # long 헨리 → 크롭
    ("P02", {"cut": "skinny"}),      # wide 데님 → 스키니
    ("S02", {"length": "mini"}),     # long 스커트 → 미니
]
OUT = SERVER / "ab_out" / "adjust_ab"


def _target_profile(arm, adjust: dict) -> dict:
    axes = {**arm["axes"], **adjust}
    return {"category": arm["category"], "gender": arm["gender"], "source": "seller",
            "axes": axes, "version": 2}


async def _timed(coro):
    t0 = time.time()
    res = await coro
    return res, round(time.time() - t0, 1)


async def _qc_and_correct(s, g, model, srcs, image, profile):
    """프로덕션 동작 미러: 판정 → 미달 축 교정 편집 1회 → 재판정. (선택결과, 시간dict, 판정)"""
    times = {}
    spec = mannequin_fit_qc.declared_axis_spec(profile)
    v1, times["judge"] = await _timed(mannequin_fit_qc.verdict(
        s, srcs, InlineImage(image.mime, image.image), profile))
    failed = mannequin_fit_qc.failed_axis_specs(spec, v1)
    if not failed:
        return image, times, v1, False
    instruction = mannequin_fit_qc.build_edit_instruction(failed)
    edited, times["retry_edit"] = await _timed(g.generate_content_image(
        model, instruction, [InlineImage(image.mime, image.image)], "1K",
        aspect_ratio=s.mannequin_aspect_ratio, timeout=300.0))
    v2, times["rejudge"] = await _timed(mannequin_fit_qc.verdict(
        s, srcs, InlineImage(edited.mime, edited.image), profile))
    if mannequin_fit_qc.edit_improves(v1, v2):
        return edited, times, v2, True
    return image, times, v1, True


async def run_case(s, g, r2, arm_id, adjust):
    arm = next(a for a in ARMS if a["id"] == arm_id)
    profile = _target_profile(arm, adjust)
    srcs = [load_local(p) for p in SRC[arm["src"]]]
    current = load_local(SERVER / "ab_out" / "fit_campaign" / f"{arm_id}.png")
    model = resolve_model(s, "image_high")  # 사용자 지정: 조정은 Gemini 3 Pro
    rows = []

    # ---- A 현행: 베이스에서 재생성 + QC ----
    base = InlineImage("image/png", r2.get_bytes(BASE_R2[arm["gender"]]))
    arm_a = dict(arm, axes=profile["axes"], adjusted=list(adjust.keys()))
    prompt_a = build_prompt_for(s, arm_a)
    gen_a, t_gen_a = await _timed(g.generate_content_image(
        model, prompt_a, [base, *srcs], "1K", aspect_ratio=s.mannequin_aspect_ratio,
        timeout=300.0))
    sel_a, times_a, v_a, retried_a = await _qc_and_correct(s, g, model, srcs, gen_a, profile)
    rows.append(("A_현행", sel_a, {"gen": t_gen_a, **times_a}, v_a, retried_a))

    # ---- B/C 공용 편집 1콜 ----
    directives = build_adjust_directives(profile, tuple(adjust.keys()))
    assert directives, f"{arm_id}: 지시문 조립 실패"
    manifest = build_adjust_manifest(len(srcs), False)
    prompt_edit = render_adjust_prompt(directives, manifest)
    edit_b, t_edit_b = await _timed(g.generate_content_image(
        model, prompt_edit, [current, *srcs], "1K", aspect_ratio=s.mannequin_aspect_ratio,
        timeout=300.0))
    sel_b, times_b, v_b, retried_b = await _qc_and_correct(s, g, model, srcs, edit_b, profile)
    rows.append(("B_편집+QC", sel_b, {"gen": t_edit_b, **times_b}, v_b, retried_b))

    edit_c, t_edit_c = await _timed(g.generate_content_image(
        model, prompt_edit, [current, *srcs], "1K", aspect_ratio=s.mannequin_aspect_ratio,
        timeout=300.0))
    spec = mannequin_fit_qc.declared_axis_spec(profile)
    v_c, t_measure = await _timed(mannequin_fit_qc.verdict(   # 측정용 채점 — C 시간엔 미포함
        s, srcs, InlineImage(edit_c.mime, edit_c.image), profile))
    rows.append(("C_편집만", edit_c, {"gen": t_edit_c, "measure_only_judge": t_measure}, v_c, False))

    results = []
    for label, img, times, verdict, retried in rows:
        out = OUT / f"{arm_id}_{label}.png"
        out.write_bytes(img.image)
        flow_time = sum(v for k, v in times.items() if k != "measure_only_judge")
        axis_ok = all(x["pass"] and x["visible"] for x in verdict["axisPass"])
        results.append({
            "case": arm_id, "adjust": adjust, "arm": label, "output": str(out),
            "times_sec": times, "flow_total_sec": round(flow_time, 1),
            "axis_pass": axis_ok, "identity_pass": verdict["identityPass"],
            "qc_retry_fired": retried,
            "judge": verdict})
        print(f"  {arm_id} {label}: 총 {flow_time:.0f}s {times} · 축반영 {'✅' if axis_ok else '❌'}"
              f" · 정체성 {'✅' if verdict['identityPass'] else '❌'}"
              f"{' · 교정발화' if retried else ''}")
    return results


async def main():
    s = load_settings()
    g = GeminiImageClient(s)
    r2 = R2Client(s)
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for arm_id, adjust in CASES:
        print(f"[{arm_id}] 조정 {adjust}")
        try:
            all_rows.extend(await run_case(s, g, r2, arm_id, adjust))
        except Exception as e:
            print(f"  ⚠️ {arm_id} 실패: {type(e).__name__}: {e}")
            all_rows.append({"case": arm_id, "adjust": adjust, "arm": "ERROR",
                             "error": f"{type(e).__name__}: {e}"[:300]})
    with open(OUT / "results.jsonl", "w") as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 요약
    print("\n=== 방식별 요약 (성공 케이스) ===")
    for label in ("A_현행", "B_편집+QC", "C_편집만"):
        rs = [r for r in all_rows if r.get("arm") == label]
        if not rs:
            continue
        avg = sum(r["flow_total_sec"] for r in rs) / len(rs)
        ok = sum(1 for r in rs if r["axis_pass"])
        idok = sum(1 for r in rs if r["identity_pass"])
        print(f"  {label}: 평균 {avg:.0f}s · 축반영 {ok}/{len(rs)} · 정체성 {idok}/{len(rs)}")


if __name__ == "__main__":
    asyncio.run(main())
