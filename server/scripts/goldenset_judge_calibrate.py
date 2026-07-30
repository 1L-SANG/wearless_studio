"""T3 Step 4 — 구조 judge 캘리브 게이트 (baseline 측정 **전 필수**, codex P1-5).

judge 를 믿기 전에 검증한다: 양품(positive)에 오탐(FP) 없고, 합성 실패모드(negative)를 검출하나.
- positive: 기존 실 마네킹 컷 + 밝기 변형(여전히 양품이어야 함) → 핵심축 fail = FP.
- negative: 각 컷에서 실패모드 합성(qc_calibrate 변형 계승):
    crop_bottom→missing_lower_body, vsquash→broken_proportions, recolor→garment_identity_altered.
- 게이트: FP=0 且 모드별 검출률 ≥ 임계. 미달 → judge 프롬프트 튜닝 후 재실행(같은 코퍼스 아닌
  held-out 로 재검증 권장). 미통과면 baseline 스코어는 '미검증' 표기·결론 제외.

⚠️ 파일럿 한계: 로컬 실컷 3개(전부 top)라 코퍼스 다양성 낮음(변형으로 N 확보). 타종류 positive·
   type-recognition 광역 검증은 baseline 에서 관찰(judge 가 pants/skirt/dress/outer 를 실제로
   식별하는지). 이 게이트는 **구조 검출기(lowerBody/proportions/garmentFidelity)** 검증에 집중.

실행(LOCAL):
  cd server && DATABASE_URL=...54322 GEMINI_API_KEY=... .venv/bin/python -m scripts.goldenset_judge_calibrate
"""
import asyncio
import io
import os
import sys
from pathlib import Path

from scripts._env import load_env

load_env()
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")

import psycopg  # noqa: E402
from PIL import Image, ImageEnhance  # noqa: E402

from app.agents import mannequin_structure_qc as SQ  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "server/ab_out/goldenset_types"
BASE_WOMEN = "seed/mannequin/base-women-2K.png"

# 게이트 임계 (파일럿 — 실컷 3 한계 감안, 보수적 소표본)
FP_MAX = 0                 # 양품 오탐 0건
DETECT_MIN_FRAC = 0.66     # 모드별 검출 ≥ 2/3


def _png(img: Image.Image) -> bytes:
    b = io.BytesIO(); img.convert("RGB").save(b, format="PNG"); return b.getvalue()


def _bg(img: Image.Image) -> tuple:
    px = img.convert("RGB").getpixel((2, 2))
    return px if isinstance(px, tuple) else (255, 255, 255)


def positive_variants(img: Image.Image) -> dict:
    return {"orig": img, "bright+": ImageEnhance.Brightness(img).enhance(1.15),
            "bright-": ImageEnhance.Brightness(img).enhance(0.85)}


def negative_variants(img: Image.Image) -> dict:
    img = img.convert("RGB"); w, h = img.size; bg = Image.new("RGB", img.size, _bg(img))
    out = {}
    # missing_lower_body: 하단 20% 잘라 배경 패딩(발/다리 소실)
    cut = img.crop((0, 0, w, int(h * 0.80))); pad = bg.copy(); pad.paste(cut, (0, 0))
    out["missing_lower_body"] = pad
    # broken_proportions: 세로 70% 압축 후 중앙 배치(인체 비율 왜곡)
    sq = img.resize((w, int(h * 0.70))); canv = bg.copy(); canv.paste(sq, (0, (h - sq.size[1]) // 2))
    out["broken_proportions"] = canv
    # garment_identity_altered: 채널 로테이트(R←G←B) → 색상 크게 변경(형상 유지)
    r, g, b = img.split(); out["garment_identity_altered"] = Image.merge("RGB", (g, b, r))
    return out


async def _fetch_cuts(url: str):
    triples = []
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute("""select mc.project_id::text, a.r2_key from mannequin_cuts mc
                       join assets a on a.id=mc.asset_id order by mc.created_at""")
        for pid, gen_key in cur.fetchall():
            cur.execute("select colors from products where project_id=%s", (pid,))
            col = (cur.fetchone() or [None])[0] or []
            base = next((x for x in col if x.get("isBase")), col[0] if col else None)
            src_key = None
            if base and base.get("images"):
                aid = base["images"][0]["id"]
                cur.execute("select r2_key from assets where id=%s", (aid,))
                r = cur.fetchone(); src_key = r[0] if r else None
            if src_key:
                triples.append((pid, gen_key, src_key))
    return triples


async def main() -> int:
    s = load_settings()
    r2 = R2Client(s)
    url = os.environ["DATABASE_URL"]
    triples = await _fetch_cuts(url)
    if not triples:
        print("실 컷 없음 — 캘리브 코퍼스 부재", file=sys.stderr); return 2
    base_bytes = r2.get_bytes(BASE_WOMEN)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[judge_calibrate] cuts={len(triples)} (positive 변형·negative 합성) 게이트 FP≤{FP_MAX} 검출≥{DETECT_MIN_FRAC:.0%}")

    fp = []                         # 양품인데 핵심축 fail (오탐)
    detect = {m: [0, 0] for m in ("missing_lower_body", "broken_proportions", "garment_identity_altered")}
    type_seen_ok = [0, 0]

    for i, (pid, gen_key, src_key) in enumerate(triples):
        gen = Image.open(io.BytesIO(r2.get_bytes(gen_key)))
        src_bytes = r2.get_bytes(src_key)
        # positives
        for tag, variant in positive_variants(gen).items():
            v = await SQ.judge(s, _png(variant), [src_bytes], base_bytes)
            agg = SQ.aggregate(v, "top")   # 기존 컷 전부 top
            core_fail = any(m in agg["failureModes"] for m in
                            ("missing_lower_body", "broken_proportions", "garment_identity_altered"))
            type_seen_ok[1] += 1
            if SQ.classify_type(v["typeSeen"], "top")["match"] is True:
                type_seen_ok[0] += 1
            if core_fail:
                fp.append((f"{pid[:8]}:{tag}", agg["failureModes"], v["typeSeen"]))
            print(f"  POS {pid[:8]}:{tag:<8} overall={agg['overallPass']} modes={agg['failureModes']} typeSeen={v['typeSeen'][:22]!r}")
        # negatives
        for mode, variant in negative_variants(gen).items():
            v = await SQ.judge(s, _png(variant), [src_bytes], base_bytes)
            agg = SQ.aggregate(v, "top")
            detect[mode][1] += 1
            hit = mode in agg["failureModes"]
            if hit:
                detect[mode][0] += 1
            print(f"  NEG {pid[:8]}:{mode:<24} hit={hit} modes={agg['failureModes']}")

    print("\n== 캘리브 결과 ==")
    print(f"positive {type_seen_ok[1]}건 중 오탐(FP) {len(fp)}건")
    for lbl, modes, ts in fp:
        print(f"  FP {lbl}: {modes} typeSeen={ts!r}")
    print(f"type 식별 정확(top): {type_seen_ok[0]}/{type_seen_ok[1]}")
    gate_ok = len(fp) <= FP_MAX
    for mode, (hit, tot) in detect.items():
        frac = hit / tot if tot else 0
        ok = frac >= DETECT_MIN_FRAC
        gate_ok = gate_ok and ok
        print(f"  검출 {mode:<24} {hit}/{tot} ({frac:.0%}) {'OK' if ok else 'LOW'}")
    print(f"\n게이트: {'통과 — baseline 진행 가능' if gate_ok else '실패 — judge 프롬프트 튜닝 후 held-out 재검증 필요'}")
    return 0 if gate_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
