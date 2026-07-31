"""거울 셀카 원본 → 생성 컷의 로고가 정방향으로 나오는지 실사진 검증 (US-10 수용기준 3).

`verify_mirror_rule.py` 는 **판정기**가 반전을 잡는지를 봤다. 이건 반대쪽 —
`sourceMirrored=True` 신호를 받은 **생성기**가 실제로 글자를 정방향으로 렌더하는지를 본다.
같은 입력·같은 프롬프트로 신호만 껐다 켜서 두 장을 생성하고 나란히 저장한다.

워커의 프롬프트 조립을 그대로 재사용한다(render_mannequin_prompt) — 여기서 다른 경로로
조립하면 실제 출고 경로를 검증한 게 아니게 된다.

실행:
    cd server && .venv/bin/python -m scripts.e2e_mirror_generation \
      --front /tmp/prod_0.jpg [--base-gender women] [--clothing-type top]

비용: Gemini 이미지 생성 2콜(OFF/ON) + image_qc 2콜.
"""
import argparse
import asyncio
import json
import pathlib

from scripts._env import load_env

load_env()

from app.agents import image_qc, mannequin  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.agents.prompts import (  # noqa: E402
    MannequinPromptContext, load_prompt_template, render_mannequin_prompt,
)
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "ab_out/mirror_generation"


def _mime(path: pathlib.Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


async def _base_mannequin(s, gender: str) -> InlineImage:
    """실 베이스 마네킹을 R2 에서 읽는다 — 합성 캔버스로 대체하면 검증이 무의미해진다."""
    import psycopg
    from psycopg.rows import dict_row

    asset_id = (s.base_mannequin_men_asset_id if gender == "men"
                else s.base_mannequin_women_asset_id)
    assert asset_id, "MANNEQUIN_BASE_*_ASSET_ID 미설정"
    with psycopg.connect(s.database_url, row_factory=dict_row) as c, c.cursor() as cur:
        cur.execute("select r2_bucket, r2_key, mime_type from assets where id = %s", (asset_id,))
        row = cur.fetchone()
    assert row, f"베이스 마네킹 에셋 {asset_id} 없음"
    data = R2Client(load_settings(), bucket=row["r2_bucket"]).get_bytes(row["r2_key"])
    return InlineImage(row["mime_type"], data)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--front", required=True, help="거울 셀카 상품 정면 사진")
    ap.add_argument("--base-gender", default="women", choices=["women", "men"])
    ap.add_argument("--clothing-type", default="top")
    args = ap.parse_args()

    s = load_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    front = pathlib.Path(args.front)
    prod = InlineImage(_mime(front), front.read_bytes())
    base = await _base_mannequin(s, args.base_gender)
    gemini = GeminiImageClient(s)
    model = resolve_model(s, "image_high")
    template = load_prompt_template(s)
    manifest = "1. Base mannequin — the canvas to dress (keep it identical)\n2. front view of the garment"
    ctx = MannequinPromptContext(
        clothing_type=args.clothing_type, product_count=1,
        base_gender=args.base_gender, image_manifest=manifest, fit_profile=None)
    product = {"name": "거울셀카 검증 상품", "clothing_type": args.clothing_type}

    rows = []
    for label, analysis in (("OFF", {}), ("ON", {"sourceMirrored": True})):
        prompt = render_mannequin_prompt(template, ctx, product, analysis)
        has_block = "MIRRORED SOURCE PHOTOS" in prompt
        assert has_block == (label == "ON"), f"{label}: 미러 블록 주입 상태가 기대와 다름"
        res = await gemini.generate_content_image(
            model, prompt, [base, prod], s.mannequin_image_size,
            aspect_ratio=s.mannequin_aspect_ratio)
        path = OUT / f"mirror_{label}.png"
        path.write_bytes(res.image)
        p2 = await image_qc.verdict(s, [prod], InlineImage(res.mime, res.image), scored=True)
        rows.append({"label": label, "mirror_block": has_block, "image": str(path),
                     "verdict": p2["verdict"], "mismatches": p2["mismatches"],
                     "product_fidelity": p2["product_fidelity"]})
        print(f"[{label}] 블록={has_block} verdict={p2['verdict']} "
              f"fidelity={p2['product_fidelity']} → {path.name}")
        for m in p2["mismatches"]:
            print(f"    · {m[:130]}")

    (OUT / "results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT}  (두 이미지를 육안 대조: ON 의 글자가 정방향인가)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
