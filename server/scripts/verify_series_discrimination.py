"""D축 판정자 변별력 검증 — 낮은 점수를 실제로 낼 수 있는가.

실 생성물 판정이 전부 95~100 이라, 판정자가 진짜 일관성을 재는 건지 그냥 후한 건지
구분이 안 된다. 실컷을 합성 변형(배경 톤·밝기·크롭)해 "확실히 어긋난" 쌍을 만들고,
원본 쌍과 점수 차가 나는지 본다. 차이가 없으면 D축은 눈금 없는 자다.

`app.services.qc` 캘리브레이션이 실패모드를 합성해 검증한 것과 같은 전략이다.

실행:
    cd server && DATABASE_URL=...54322 .venv/bin/python -m scripts.verify_series_discrimination
비용: 변형 종류당 vision 1콜.
"""
import asyncio
import io
import json
import pathlib

from PIL import Image, ImageEnhance

from scripts._env import load_env

load_env()

from app.agents import mannequin_series_qc  # noqa: E402
from app.agents.gemini_image import InlineImage  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "ab_out/series_discrimination"


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _warm(img: Image.Image, amount: float) -> Image.Image:
    """배경 색온도를 따뜻하게 — 셀러가 가장 흔히 느끼는 '톤이 다르다'."""
    r, g, b = img.convert("RGB").split()
    r = r.point(lambda v: min(255, int(v * amount)))
    b = b.point(lambda v: int(v / amount))
    return Image.merge("RGB", (r, g, b))


def _crop_in(img: Image.Image, ratio: float) -> Image.Image:
    """마네킹이 프레임을 더 채우게 — 스케일·여백 불일치."""
    w, h = img.size
    dx, dy = int(w * ratio), int(h * ratio)
    return img.crop((dx, dy, w - dx, h - dy)).resize((w, h), Image.LANCZOS)


VARIANTS = [
    ("동일본(대조군)", lambda im: im),
    ("밝기 -25%", lambda im: ImageEnhance.Brightness(im).enhance(0.75)),
    ("배경 따뜻하게", lambda im: _warm(im, 1.12)),
    ("크롭 확대", lambda im: _crop_in(im, 0.10)),
    ("밝기+톤+크롭 복합", lambda im: _crop_in(_warm(ImageEnhance.Brightness(im).enhance(0.8), 1.1), 0.08)),
]


async def main() -> int:
    import psycopg
    from psycopg.rows import dict_row

    s = load_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(s.database_url, row_factory=dict_row) as c, c.cursor() as cur:
        cur.execute("""
            select a.r2_bucket, a.r2_key from mannequin_cuts mc
            join assets a on a.id = mc.asset_id and a.deleted_at is null
            order by mc.created_at desc limit 2
        """)
        rows = cur.fetchall()
    assert len(rows) == 2, "비교용 컷 2장이 필요하다"
    imgs = [Image.open(io.BytesIO(R2Client(s, bucket=r["r2_bucket"]).get_bytes(r["r2_key"])))
            for r in rows]
    reference, target = imgs[0], imgs[1]
    ref_img = InlineImage("image/png", _png(reference))

    print("기준 컷 1장 vs 변형된 새 컷 — D축이 변형을 잡아내는가\n")
    results = []
    for name, fn in VARIANTS:
        variant = fn(target)
        (OUT / f"{name.replace(' ', '_')}.png").write_bytes(_png(variant))
        out = await mannequin_series_qc.judge(
            s, InlineImage("image/png", _png(variant)), [ref_img])
        results.append({"variant": name, "consistency": out["consistency"],
                        "inconsistencies": out["inconsistencies"]})
        print(f"  {name:20} consistency={out['consistency']:3}  {out['inconsistencies'][:2]}")

    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    base = results[0]["consistency"]
    worst = min(r["consistency"] for r in results[1:])
    print(f"\n대조군 {base} · 변형 최저 {worst} · 격차 {base - worst}")
    if base - worst < 15:
        print("  ⚠️ 변별력 부족 — 판정자가 명백한 불일치를 못 잡는다. D축을 게이트로 쓰면 안 된다.")
    else:
        print("  ✅ 변별력 확인 — 합성 불일치에서 점수가 실제로 내려간다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
