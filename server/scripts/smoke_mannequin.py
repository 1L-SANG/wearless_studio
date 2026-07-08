"""마네킹 재생성 live smoke — fitProfile(matchCut 포함) 프롬프트 반영 게이트.

워커(mannequin_job)와 동일 경로로 프롬프트를 조립해 ① fit profile 블록(축 + matching
bottom 라인)이 실제 템플릿과 함께 렌더되는지 ② 실서버가 [베이스, 상품, 매칭하의] 3이미지
+ 프롬프트로 컷 1장을 반환하는지 확인한다. 베이스 마네킹은 DB→R2에서 실제 시드를 읽는다.
DB 쓰기·job 없음 — Gemini 이미지 1콜(비용 주의). --dry-run 이면 프롬프트 조립까지만(무료).

실행: cd server && .venv/bin/python -m scripts.smoke_mannequin [--dry-run]
      (옵션) --match-cut wide|skinny|slim|straight|bootcut --fit over|regular|…
             --front <상의 이미지> --match <하의 이미지> --out <저장 경로>
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root
SERVER = ROOT / "server"


def _load_env(path: Path):
    """server/.env → os.environ (미설정 키만). smoke_cut 과 동일한 단순 파싱."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(SERVER / ".env")

from app.agents import mannequin  # noqa: E402 (env 로드 후 import)
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402
from app.workers.mannequin_job import _build_manifest  # noqa: E402

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

_DEFAULT_MATCH = (ROOT / "outputs/coor_matching/generated_v2/women_bottom"
                  / "03_무난_켄톤_플라이_스트레이트_데님_팬츠_(워시드블루)_3283.png")


def _inline(path: Path) -> InlineImage:
    return InlineImage(_MIME.get(path.suffix.lower(), "image/jpeg"), path.read_bytes())


def _load_base_mannequin(s) -> InlineImage:
    """DB assets에서 여성 베이스 시드의 r2_key를 찾아 R2에서 실바이트를 읽는다."""
    import psycopg

    asset_id = s.base_mannequin_women_asset_id
    if not asset_id:
        raise SystemExit("MANNEQUIN_BASE_WOMEN_ASSET_ID가 없습니다 (server/.env).")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute(
            "select r2_key, mime_type from assets where id = %s", (asset_id,)
        ).fetchone()
    if not row:
        raise SystemExit(f"베이스 마네킹 asset({asset_id})이 DB에 없습니다.")
    key, mime = row
    return InlineImage(mime or "image/png", R2Client(s).get_bytes(key))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--match-cut", default="wide",
                    choices=["skinny", "slim", "straight", "bootcut", "wide"])
    ap.add_argument("--fit", default="regular")
    ap.add_argument("--front", default=str(ROOT / "Test image_front.jpeg"))
    ap.add_argument("--match", default=str(_DEFAULT_MATCH))
    ap.add_argument("--out", default=str(SERVER / "ab_out" / "smoke_mannequin"))
    ap.add_argument("--dry-run", action="store_true", help="프롬프트 조립까지만 (Gemini 콜 없음)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = load_settings()
    if not s.gemini_api_key:
        print("GEMINI_API_KEY가 없습니다 (server/.env).", file=sys.stderr)
        return 2

    front, match = Path(args.front), Path(args.match)
    for p in (front, match):
        if not p.exists():
            print(f"테스트 이미지가 없습니다: {p}", file=sys.stderr)
            return 2

    # 워커와 동일 경로: effective_fit_profile → manifest → prompt_context → render.
    analysis = {
        "targetGenders": ["women"],
        "sellingPoints": ["부드러운 촉감"],
        "fitProfile": {
            "category": "top", "gender": "women",
            "axes": {"fit": args.fit, "length": None},
            "matchCut": args.match_cut, "source": "seller",
        },
    }
    product = {"name": "소프트 골지 라운드 니트", "clothing_type": "top"}
    fit_profile = mannequin.effective_fit_profile(analysis, has_match_image=True)
    prod_assets = [{"slot": "Front"}]
    manifest = _build_manifest(prod_assets, has_match=True)
    template = load_prompt_template(s)
    ctx = mannequin.prompt_context(
        clothing_type="top", product_count=2, base_gender="women",
        image_manifest=manifest, fit_profile=fit_profile)
    prompt = render_mannequin_prompt(template, ctx, product, analysis)

    fit_block = [l for l in prompt.splitlines()
                 if l.startswith("- ") or l.startswith("FIT PROFILE")]
    print("--- FIT PROFILE 블록:")
    print("\n".join(fit_block[:6]))
    if "matching bottom" not in prompt:
        print("FAIL — matchCut 라인이 프롬프트에 없습니다.", file=sys.stderr)
        return 1
    print(f"--- prompt {len(prompt)}자, model={resolve_model(s, s.mannequin_tier)}, "
          f"matchCut={args.match_cut}")
    if args.dry_run:
        print("OK (dry-run) — 프롬프트 조립 통과, Gemini 콜 생략")
        return 0

    base_img = _load_base_mannequin(s)
    images = [base_img, _inline(front), _inline(match)]  # 워커와 동일 순서(manifest 정합)
    gemini = GeminiImageClient(s)
    t0 = time.time()
    res = await gemini.generate_content_image(
        resolve_model(s, s.mannequin_tier), prompt, images, s.mannequin_image_size,
        aspect_ratio=s.mannequin_aspect_ratio)
    dt = time.time() - t0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(res.mime, "png")
    out = out_dir / f"matchcut_{args.match_cut}_fit_{args.fit}.{ext}"
    out.write_bytes(res.image)
    print(f"OK — {dt:.1f}s, {len(res.image) // 1024}KB, mime={res.mime}")
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
