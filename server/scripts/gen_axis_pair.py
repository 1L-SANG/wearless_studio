"""T2 P1-2 (B) — 통제 A/B 한 쌍 생성 + pairwise 심판 검증 (judge 골드셋 positive 레그).

한 상품에 length 축만 A(crop)↔B(long)로 선언해 2컷 생성 → mannequin_pairwise_qc.judge 로
"어느 쪽이 더 긴가" 판정 → expected(long이 더 김)와 대조. **judge 자체검증용** (P2 인과측정 아님 —
그건 실 :regenerate 경로). 백엔드 gpt-image-2(Gemini Pro 쿼터 우회). 저장: ab_out/axis_pair/.

실행: cd server && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \\
        OPENAI_API_KEY=... .venv/bin/python -m scripts.gen_axis_pair --project <uuid> \\
        --axis length --value-a crop --value-b long
"""
import argparse
import asyncio
import base64
import sys
from pathlib import Path

from scripts._env import load_env

load_env()

import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.agents import mannequin, mannequin_pairwise_qc as PQ  # noqa: E402
from app.agents.gemini_image import InlineImage  # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402


async def _gpt_image(s, prompt, images, size="1024x1536"):
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
    files = [("image[]", (f"i{i}.{ext.get(im.mime,'png')}", im.data, im.mime)) for i, im in enumerate(images)]
    async with httpx.AsyncClient(timeout=240) as c:
        r = await c.post("https://api.openai.com/v1/images/edits",
                         headers={"Authorization": f"Bearer {s.openai_api_key}"},
                         data={"model": "gpt-image-2", "prompt": prompt[:32000], "size": size, "n": "1"},
                         files=files)
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:300]}")
    return base64.b64decode(r.json()["data"][0]["b64_json"])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--axis", default="length")
    ap.add_argument("--value-a", default="crop")
    ap.add_argument("--value-b", default="long")
    ap.add_argument("--out", default="ab_out/axis_pair")
    args = ap.parse_args()
    s = load_settings()
    if not s.openai_api_key:
        print("OPENAI_API_KEY 없음 (spike/.env 키 주입 필요)", file=sys.stderr)
        return 2
    url = s.database_url
    with psycopg.connect(url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("select payload from analyses where project_id=%s limit 1", (args.project,))
        analysis = (cur.fetchone() or {}).get("payload") or {}
        cur.execute("select clothing_type from products where project_id=%s", (args.project,))
        clothing_type = (cur.fetchone() or {}).get("clothing_type") or "top"
        cur.execute("select r2_key, mime_type from assets where project_id=%s and source='upload' "
                    "and deleted_at is null order by created_at", (args.project,))
        uploads = cur.fetchall()
    if not uploads:
        print("업로드 상품 이미지 없음", file=sys.stderr)
        return 2
    gender = mannequin.select_base_gender(analysis)
    base_id = s.base_mannequin_women_asset_id if gender == "women" else s.base_mannequin_men_asset_id
    r2 = R2Client(s, bucket=s.r2_bucket)
    with psycopg.connect(url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("select r2_key, mime_type from assets where id=%s", (base_id,))
        b = cur.fetchone()
    base_img = InlineImage(b["mime_type"] or "image/png", r2.get_bytes(b["r2_key"]))
    prod_imgs = [InlineImage(u["mime_type"] or "image/jpeg", r2.get_bytes(u["r2_key"])) for u in uploads]
    template = load_prompt_template(s)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    async def gen(value, tag):
        # length 축만 선언한 seller fit_profile (다른 축은 미선언 → 사진 근거)
        fit_profile = {"category": clothing_type if clothing_type in
                       ("top", "outer", "pants", "skirt", "dress") else "top",
                       "gender": gender, "source": "seller", "version": 1,
                       "axes": {args.axis: value}}
        ctx = mannequin.prompt_context(clothing_type=clothing_type, product_count=len(prod_imgs),
                                       base_gender=gender, image_manifest="", fit_profile=fit_profile,
                                       adjusted_axes=(args.axis,))
        prompt = render_mannequin_prompt(template, ctx, {"name": "", "clothing_type": clothing_type}, analysis)
        data = await _gpt_image(s, prompt, [base_img, *prod_imgs])
        path = out_dir / f"{args.project[:8]}_{args.axis}_{value}.png"
        path.write_bytes(data)
        print(f"  [{tag}] {args.axis}={value} → {path.name}")
        return data, path

    print(f"[gen_axis_pair] {args.axis}: {args.value_a} vs {args.value_b} (gpt-image-2)")
    a_data, a_path = await gen(args.value_a, "A")
    b_data, b_path = await gen(args.value_b, "B")

    # pairwise 심판: LEFT=A, RIGHT=B. expected = A/B 축값 매핑.
    cat = fit_cat = (clothing_type if clothing_type in ("top", "outer", "pants", "skirt", "dress") else "top")
    verdict = await PQ.judge(s, a_data, b_data, args.axis, timeout=60)
    score = PQ.score_pair(verdict, cat, args.axis, args.value_a, args.value_b)
    print(f"[judge] verdict={verdict}")
    print(f"[score] {score}")
    print(f"[expected] LEFT(A={args.value_a}) vs RIGHT(B={args.value_b}) → "
          f"more={PQ.expected_more_side(cat, args.axis, args.value_a, args.value_b)}")
    print(f"저장: {a_path} / {b_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
