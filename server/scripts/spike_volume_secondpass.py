"""스파이크 — 2패스로 마네킹 체형 볼륨을 살릴 수 있는지 계측 판정.

배경: 베이스 마네킹을 볼륨 있는 것으로 바꿔 넣어도(1패스 단독) 결과 컷의 몸이 표준으로 정규화된다.
실측 결과 가슴아래 폭 차이가 베이스끼리 +12.2% → 옷 입히면 +2.4%(루즈)/+1.4%(피트)로 줄고,
GPT images/edits 는 부호까지 반대로 나왔다. 반면 seed_mannequin_matrix.py 처럼 **"볼륨만 바꿔라"가
단독 과제**일 때는 모델이 정확히 수행한다.

그래서 검증할 가설: 옷 입힌 컷을 만든 뒤 그 컷에 **볼륨 변경만** 지시하는 2패스를 붙이면
체감 가능한 차이가 나온다.

설계: 1패스는 체형과 무관하므로(기본 베이스 사용) 반복분을 slim/volume 이 공유한다.
  1패스 N회 → 각 결과에 slim 지시 / volume 지시 2패스 → 2N장. 같은 1패스 이미지에서 갈라지므로
  1패스 확률 변동이 상쇄되고 2패스 효과만 남는다.
  호출 수 = N + 2N (N=3 이면 9회).

프로덕션 경로 미접촉: DB·R2 는 읽기만, 산출물은 --outdir 로컬 파일만. 크레딧·프로젝트 무영향.

실행:
  cd server && .venv/bin/python -m scripts.spike_volume_secondpass \\
      --project <uuid> --user <uuid> [--job <uuid>] --repeats 3 --outdir ./spike_out
"""
import argparse
import asyncio
import io
import json
import os
import sys

from scripts._env import load_env

load_env()

import numpy as np  # noqa: E402
import psycopg  # noqa: E402
from PIL import Image  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app import repo  # noqa: E402
from app.agents import mannequin  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402
from app.workers.mannequin_job import _build_manifest  # noqa: E402

# 볼륨 지시 문구 — 고정 상수만 보간한다(셀러 문자열 미유입, seed_mannequin_matrix 규약과 동일).
#
# 가슴 전용(2026-07-30 사용자 결정): 1차 스파이크에서 torso·hips 를 함께 지시했더니 몸 전체가
# 부풀어 "뚱뚱해" 보였다. 힙 축은 폐기하고 흉부만 바꾼다.
#
# 컵 사이즈 앵커링(2026-07-30 2차): "더 크게/작게" 같은 상대 표현은 변화폭이 너무 작았다.
# 모델이 강한 사전지식을 가진 컵 사이즈로 절대 목표를 준다. 기준선(첨부 이미지)이 B컵임을
# 함께 명시해 상대 변화량까지 고정한다. regular=B컵은 베이스 그대로이므로 2패스를 돌리지 않는다.
#
# 강화 3차(2026-07-30): 컵 사이즈만으로는 변화폭이 여전히 작았다. 크기 목표에 더해
# "얼마나 앞으로 나오는가"를 물리적 배수로 못 박는다.
_VOLUME_EN = {
    "slim": ("an A CUP — essentially FLAT. There is no bust apex at all: the chest is a "
             "straight plane from the collarbone down to the waist"),
    "volume": ("a LARGE, FULL D CUP — dramatically bigger than the B cup in the attached "
               "image. The bust must project forward from the chest wall roughly TWICE as far "
               "as it does now, and it must be the widest point of the whole torso"),
    # 캘리브레이션 후보(2026-07-30 4차): 여성 기본 마네킹에 상시 적용할 크기를 고른다.
    # D컵/2배는 뚜렷하지만 기본값으로는 과하다는 판단 → 그 아래 두 단계를 재본다.
    "mid_c": ("a full C CUP — clearly bigger than the B cup in the attached image. The bust "
              "must project forward from the chest wall roughly 1.5 times as far as it does now"),
    "mid_cd": ("a full C-to-D CUP — clearly bigger than the B cup in the attached image. The "
               "bust must project forward from the chest wall roughly 1.75 times as far as it "
               "does now"),
}

# 2패스 프롬프트. prompts/mannequin_adjust_v1.txt 의 "한 차원만 바꾸고 나머지 동결" 형태를 따르되,
# 그 템플릿의 "몸을 동결하라"는 규칙은 여기선 정반대여야 하므로 명시적으로 뒤집는다
# (몸 볼륨이 곧 요청된 변경). 스파이크 전용 — 프로덕션 템플릿은 건드리지 않는다.
_PASS2_PROMPT = """<role>
You are retouching a studio mannequin photo. You have exactly ONE job: change the mannequin's BUST SIZE.
</role>

<input image>
The single attached image is the CURRENT mannequin cut. Its bust is a B cup.
</input image>

<THE ONE CHANGE YOU MUST MAKE>
Rebuild the mannequin's chest so that it is ${volumeTarget}.

This change must be UNMISTAKABLE. Someone holding your output next to the attached image must
see a different bust size instantly, without looking for it. A subtle, tasteful, or conservative
change is a FAILURE of this task — deliberately go far enough that the difference is obvious at
a glance from across a room. Err on the side of too much rather than too little.
</THE ONE CHANGE YOU MUST MAKE>

<how the garment must report the new bust>
The mannequin is clothed, so the fabric is the ONLY way the bust size is visible. Redraw the
fabric so it physically reports the new chest:
- The fabric is pushed outward and TENTS over the bust apex instead of hanging flat.
- Below the bust, the fabric falls AWAY from the stomach into shadow, rather than continuing
  straight down the body.
- The front button placket, the chest pockets and any seams curve as they pass over the bust —
  they are not flat panels lying on a flat plane.
- For a larger bust: the widest point of the torso silhouette moves UP to the bust line, and the
  fabric shows visible tension and shadow there.
- For a flat bust: the fabric falls in a straight line from the shoulders with no apex, no
  tenting, and no shadow under the chest.
</how the garment must report the new bust>

<keep identical>
Everything except the bust and the fabric draping over it is unchanged from the attached image:
waist width, hip width, stomach, shoulders, arms, neck, legs, height and overall body weight;
pose, camera angle, framing, distance, crop and background; and the garment's color, pattern,
fabric, seams, neckline, hem height, length, buttons, pockets and prints. The mannequin must NOT
read as heavier, wider or larger overall — the waist and hips in particular stay exactly as they
are. Only the bust differs.
</keep identical>

<output format>
- Output ONE brand-new, FULLY OPAQUE, photorealistic e-commerce studio PHOTOGRAPH of the SAME mannequin wearing the SAME garment, with ONLY the bust size changed.
- The mannequin and garments must be solid and opaque — NEVER translucent, faded, washed-out, or ghosted, and NOT a see-through overlay of the input photo.
- FULL BODY in frame, from the top of the head down to the feet — nothing cropped. Portrait orientation.
- Output EXACTLY ONE image — no grid, no collage, no multiple views, no panels, no text, and no human skin, face, or hair.
</output format>

<critical rules>
- The BUST SIZE is the requested change. Do NOT preserve the original chest; changing it is the entire point of this edit.
- If you are unsure how far to go, go FURTHER. Under-shooting this change is the single most common failure and is not acceptable.
- The waist and hips are NOT the requested change — their width must be identical to the attached image.
- Use ONLY the attached image as the base — do not fabricate new garment details, and do not change the garment's identity.
</critical rules>"""

# 1패스 주입용 가슴 블록 — 2패스 없이 생성 한 번으로 되는지 검증한다(--pass1-bust).
# 1패스에서 앞서 실패한 문구들은 전부 "image 1 의 몸을 보존하라"는 *보존* 요구였고, 상품 사진
# ground-truth 룰과 경쟁해서 졌다. 여기서는 베이스가 고정이므로 *절대 목표*를 준다 — 경쟁 상대가
# 없다. 2패스에서 실제로 먹혔던 요소(물리적 배수·천 레벨 관측·과소변화=실패 선언)를 그대로 옮긴다.
_PASS1_BUST_BLOCK = """

BUST (applies to the dressed mannequin in your output):
- The base mannequin in image 1 has a small B-cup chest. Your output must instead show ${volumeTarget}.
- This is not optional and must be UNMISTAKABLE at a glance. A subtle or conservative bust is a FAILURE — err on the side of too much rather than too little.
- The garment is the only way the bust is visible, so the fabric must report it: the fabric TENTS over the bust apex instead of hanging flat, it falls AWAY from the stomach below the bust, and the front placket, chest pockets and seams curve as they pass over the bust rather than lying on a flat plane. The widest point of the torso silhouette sits at the bust line.
- Only the bust differs from image 1. The waist, hips, stomach, shoulders, arms, neck, legs, height and overall body weight are unchanged — the mannequin must NOT read as heavier or larger overall."""

# 실루엣 측정 지점 — 몸 높이(머리끝~발끝) 기준 비율
_MEASURE = {0.22: "가슴", 0.30: "가슴아래", 0.38: "허리", 0.50: "힙"}
_JUDGE_AT = 0.30  # 판정 기준 지점 (베이스끼리 차이가 가장 큰 곳)
_BASE_DELTA_PCT = 12.2  # 옷 없는 베이스끼리의 가슴아래 폭 차이(실측) — 통과선의 기준
_PASS_RATIO = 0.5  # 베이스 차이의 절반 이상이면 통과
# 가슴 전용 판정(2026-07-30): 허리가 같이 굵어지면 "뚱뚱해진" 것이지 가슴이 커진 게 아니다.
# 허리 변화가 이 폭 안에 있어야 통과 — 1차 스파이크의 실패 모드를 잡는 가드.
_WAIST_AT = 0.38
_WAIST_TOL_PCT = 3.0
# 힙(0.50)은 그 높이에 마네킹 손이 내려와 실루엣에 잡혀 측정 신뢰 불가 — 판정에서 제외한다.


def _widths(data: bytes) -> dict | None:
    """실루엣 폭을 몸 높이로 정규화해 지점별로 반환. 배경이 균일 밝은 회색인 전제."""
    im = Image.open(io.BytesIO(data)).convert("L")
    a = np.asarray(im, dtype=np.int16)
    edges = np.concatenate([a[:20, :].ravel(), a[-20:, :].ravel(),
                            a[:, :20].ravel(), a[:, -20:].ravel()])
    bg = int(np.median(edges))
    mask = a < (bg - 8)
    rows = np.where(mask.sum(axis=1) > mask.shape[1] * 0.01)[0]
    if len(rows) < 2:
        return None
    top, bot = int(rows[0]), int(rows[-1])
    h = bot - top
    if h <= 0:
        return None
    out = {}
    for frac in _MEASURE:
        y = int(top + h * frac)
        xs = np.where(mask[y])[0]
        out[frac] = float((xs[-1] - xs[0]) / h) if len(xs) > 1 else 0.0
    return out


async def _load_inputs(s, conn, project: str, user: str, job: str | None):
    product = await repo.get_product(conn, project) or {}
    analysis = await repo.get_analysis(conn, project) or {}
    job_payload = {}
    if job:
        async with conn.cursor() as cur:
            await cur.execute("select payload from jobs where id = %s", (job,))
            row = await cur.fetchone()
        job_payload = (row or {}).get("payload") or {}

    gender = mannequin.select_base_gender(
        analysis,
        product.get("clothing_type") or product.get("clothingType"),
    )
    # 1패스는 체형과 무관 — 현행 기본 베이스를 쓴다(반복분을 slim/volume 이 공유).
    base_id = (s.base_mannequin_men_asset_id if gender == "men"
               else s.base_mannequin_women_asset_id)
    base_asset = await repo.get_asset_for_user(conn, user, base_id)
    prod_assets = []
    for slot, aid in mannequin.base_color_images(product):
        a = await repo.get_asset_for_user(conn, user, aid)
        if a:
            a = dict(a); a["slot"] = slot
            prod_assets.append(a)
    match_asset = None
    match_id = mannequin.main_match_item_id(analysis)
    if match_id:
        m_aid = await repo.get_matching_item_asset(conn, match_id, user, project)
        if m_aid:
            match_asset = await repo.get_asset_for_user(conn, user, m_aid)
    return product, analysis, job_payload, gender, base_asset, prod_assets, match_asset


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--job", default=None, help="fitProfile 스냅샷 출처(없으면 analysis)")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--reuse-pass1", default=None,
                    help="이전 실행 디렉터리 — r{i}_pass1.jpg 를 재사용해 1패스 호출을 아낀다")
    ap.add_argument("--cells", default="slim,volume",
                    help=f"돌릴 문구 키 (쉼표 구분). 가능: {','.join(_VOLUME_EN)}")
    ap.add_argument("--pass2-tier", default="image_high", choices=("image_high", "image_light"),
                    help="2패스 모델 티어. image_light=Flash(저렴) — 비용 절감 검증용. "
                         "1패스는 항상 image_high(Pro) 유지(AG-04 사용자 결정)")
    ap.add_argument("--pass1-bust", default=None, metavar="CELL",
                    help="2패스 대신 1패스 프롬프트에 가슴 지시를 주입해 한 번에 생성 "
                         "(비용 절반 — 2패스 없이 되는지 검증)")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    cells = [c.strip() for c in args.cells.split(",") if c.strip()]
    unknown = [c for c in cells if c not in _VOLUME_EN]
    if unknown:
        print(f"알 수 없는 cell: {unknown} (가능: {list(_VOLUME_EN)})")
        return 1

    os.makedirs(args.outdir, exist_ok=True)
    s = load_settings()
    r2 = R2Client(s)
    gemini = GeminiImageClient(s)
    model = resolve_model(s, "image_high")           # 1패스 — Pro 고정(AG-04 사용자 결정)
    model2 = resolve_model(s, args.pass2_tier)       # 2패스 — 비용 검증용으로 티어 선택 가능

    async with await psycopg.AsyncConnection.connect(s.database_url, row_factory=dict_row) as conn:
        (product, analysis, job_payload, gender,
         base_asset, prod_assets, match_asset) = await _load_inputs(
            s, conn, args.project, args.user, args.job)

    if not base_asset or not prod_assets:
        print(f"입력 부족: base={bool(base_asset)} prod={len(prod_assets)}")
        return 1

    base_img = InlineImage(base_asset["mime_type"], r2.get_bytes(base_asset["r2_key"]))
    prod_imgs = [InlineImage(a["mime_type"], r2.get_bytes(a["r2_key"])) for a in prod_assets]
    match_img = (InlineImage(match_asset["mime_type"], r2.get_bytes(match_asset["r2_key"]))
                 if match_asset else None)

    snap = job_payload.get("fitProfileSnapshot") or {}
    fit_profile = snap.get("profile") if snap else analysis.get("fitProfile")
    adjusted = tuple(a for a in (snap.get("adjustedAxes") or []) if isinstance(a, str))

    ctx = mannequin.prompt_context(
        clothing_type=(product.get("clothing_type") or product.get("clothingType") or "상의"),
        product_count=len(prod_imgs) + (1 if match_img else 0),
        base_gender=gender,
        image_manifest=_build_manifest(prod_assets, match_img is not None),
        fit_profile=fit_profile, adjusted_axes=adjusted)
    pass1_prompt = render_mannequin_prompt(load_prompt_template(s), ctx, product, analysis)
    pass1_images = [base_img, *prod_imgs] + ([match_img] if match_img else [])

    if args.pass1_bust:
        if args.pass1_bust not in _VOLUME_EN:
            print(f"알 수 없는 cell: {args.pass1_bust}")
            return 1
        pass1_prompt += _PASS1_BUST_BLOCK.replace("${volumeTarget}", _VOLUME_EN[args.pass1_bust])
        print(f"1패스 가슴 주입 모드 — cell={args.pass1_bust}, 2패스 없음")
        print(f"   호출 {args.repeats}회 (2패스 방식의 절반)\n")
        for i in range(1, args.repeats + 1):
            r = await gemini.generate_content_image(
                model, pass1_prompt, pass1_images,
                s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
            p = os.path.join(args.outdir, f"r{i}_pass1bust.jpg")
            with open(p, "wb") as f:
                f.write(r.image)
            print(f"   저장 {os.path.basename(p)} ({len(r.image) // 1024}KB)")
        print("\n1패스 주입 결과는 육안 비교 — 좌우 실루엣 폭은 3/4 각도 가슴 돌출을 못 잡는다.")
        return 0

    print(f"1패스 model={model} / 2패스 model={model2} ({args.pass2_tier})")
    print(f"size={s.mannequin_image_size} aspect={s.mannequin_aspect_ratio}")
    print(f"1패스 입력 이미지 {len(pass1_images)}장  gender={gender}")
    print(f"호출 예정: 1패스 {args.repeats}회 + 2패스 {args.repeats * 2}회 = "
          f"{args.repeats * 3}회\n")

    rows = []
    for i in range(1, args.repeats + 1):
        print(f"── 반복 {i}/{args.repeats} ──")
        p1_path = os.path.join(args.outdir, f"r{i}_pass1.jpg")
        reuse_src = (os.path.join(args.reuse_pass1, f"r{i}_pass1.jpg")
                     if args.reuse_pass1 else None)
        if reuse_src and os.path.exists(reuse_src):
            # 1패스는 체형과 무관하므로 이전 실행분을 재사용해 호출을 아낀다.
            with open(reuse_src, "rb") as f:
                p1_bytes = f.read()
            if os.path.abspath(reuse_src) != os.path.abspath(p1_path):
                with open(p1_path, "wb") as f:
                    f.write(p1_bytes)
            p1_mime = "image/jpeg"
            print(f"   1패스 재사용 {reuse_src} ({len(p1_bytes) // 1024}KB) — 호출 안 함")
        else:
            r1 = await gemini.generate_content_image(
                model, pass1_prompt, pass1_images,
                s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
            p1_bytes, p1_mime = r1.image, r1.mime
            with open(p1_path, "wb") as f:
                f.write(p1_bytes)
            print(f"   1패스 저장 {os.path.basename(p1_path)} ({len(p1_bytes) // 1024}KB)")

        per_cell = {}
        for cell in cells:
            prompt2 = _PASS2_PROMPT.replace("${volumeTarget}", _VOLUME_EN[cell])
            r2res = await gemini.generate_content_image(
                model2, prompt2, [InlineImage(p1_mime, p1_bytes)],
                s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
            p2_path = os.path.join(args.outdir, f"r{i}_pass2_{cell}.jpg")
            with open(p2_path, "wb") as f:
                f.write(r2res.image)
            w = _widths(r2res.image)
            per_cell[cell] = w
            print(f"   2패스 {cell:6s} 저장 {os.path.basename(p2_path)}  "
                  f"측정={'ok' if w else '실패'}")

        w1 = _widths(p1_bytes)
        rows.append({"repeat": i, "pass1": w1, **per_cell})

    # ── 판정 ──
    print("\n" + "=" * 72)
    print("측정 — 몸 높이로 정규화한 실루엣 폭, volume 대비 slim 차이(%)")
    print("=" * 72)
    if not {"slim", "volume"} <= set(cells):
        # 캘리브레이션 모드(임의 문구 비교) — 자동 판정 대신 이미지 산출만 한다.
        # 좌우 실루엣 폭은 3/4 각도의 가슴 돌출을 못 잡는다는 게 확인돼 있어(2026-07-30),
        # 크기 선택은 육안으로 한다.
        print("\n캘리브레이션 모드 — 자동 판정 생략. 생성된 이미지를 육안 비교하라.")
        with open(os.path.join(args.outdir, "result.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "calibration", "cells": cells, "rows": rows},
                      f, ensure_ascii=False, indent=2)
        return 0

    deltas, waist_deltas = [], []
    for r in rows:
        sl, vo = r["slim"], r["volume"]
        if not sl or not vo:
            print(f"  반복 {r['repeat']}: 측정 실패 — 판정 제외")
            continue
        parts = []
        for frac, label in _MEASURE.items():
            d = (vo[frac] - sl[frac]) / sl[frac] * 100 if sl[frac] else 0.0
            note = "(측정불가)" if frac == 0.50 else ""
            parts.append(f"{label} {d:+.1f}%{note}")
            if frac == _JUDGE_AT:
                deltas.append(d)
            if frac == _WAIST_AT:
                waist_deltas.append(d)
        print(f"  반복 {r['repeat']}: " + "  ".join(parts))

    threshold = _BASE_DELTA_PCT * _PASS_RATIO
    verdict = "판정 불가 (측정 실패)"
    passed = False
    med = waist_med = None
    if deltas:
        med = float(np.median(deltas))
        same_sign = all(d > 0 for d in deltas)
        waist_med = float(np.median([abs(d) for d in waist_deltas])) if waist_deltas else 0.0
        waist_ok = waist_med <= _WAIST_TOL_PCT
        passed = med >= threshold and same_sign and waist_ok
        print(f"\n  [1] 가슴 변화 — 판정 지점 {_MEASURE[_JUDGE_AT]}")
        print(f"      반복별 = {['%+.1f%%' % d for d in deltas]}")
        print(f"      중위값 {med:+.1f}%  vs 통과선 +{threshold:.1f}% "
              f"(베이스끼리 {_BASE_DELTA_PCT}% 의 {_PASS_RATIO:.0%})  → "
              f"{'OK' if med >= threshold else 'NG'}")
        print(f"      부호 일관(전부 +) = {same_sign}  → {'OK' if same_sign else 'NG'}")
        print(f"  [2] 허리 유지 — 가슴만 커졌는가 (전신 비대화 방지)")
        print(f"      반복별 = {['%+.1f%%' % d for d in waist_deltas]}")
        print(f"      |중위값| {waist_med:.1f}%  vs 허용 {_WAIST_TOL_PCT:.1f}%  → "
              f"{'OK' if waist_ok else 'NG — 몸 전체가 커졌다'}")
        verdict = ("통과 — 가슴만 커진다" if passed else
                   "실패 — 가슴 변화 부족" if not (med >= threshold and same_sign) else
                   "실패 — 허리까지 굵어져 전신이 커진다")
    print(f"\n  >>> {verdict}")

    with open(os.path.join(args.outdir, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"mode": "bust_only", "rows": rows,
                   "bust_deltas": deltas, "bust_median": med,
                   "waist_deltas": waist_deltas, "waist_abs_median": waist_med,
                   "threshold_pct": threshold, "waist_tol_pct": _WAIST_TOL_PCT,
                   "passed": passed, "verdict": verdict},
                  f, ensure_ascii=False, indent=2)
    print(f"  결과 json: {os.path.join(args.outdir, 'result.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
