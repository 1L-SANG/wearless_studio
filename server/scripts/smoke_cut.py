"""컷 생성 live smoke (ADR-0004) — 롤아웃 게이트.

실제 GEMINI_API_KEY로 저장소 테스트 이미지를 옷 레퍼런스 삼아 컷 1장을 생성한다.
① normalize_spec→render_cut_prompt 실입력 통과(미해결 토큰 없음) ② 실서버가 프롬프트를
수용해 이미지 1장을 반환 ③ latency 관측. DB·R2·job 불필요 — Gemini 이미지 1콜(비용 주의).

실행: cd server && .venv/bin/python -m scripts.smoke_cut
      (옵션) --cut mirror|styling|horizon|product --shot full|knee|… --clothing top|bottom
             --front <이미지> --out <저장 경로>
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
    """server/.env → os.environ (미설정 키만). python-dotenv 의존 없이 단순 파싱."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(SERVER / ".env")

from app.agents import cut  # noqa: E402 (env 로드 후 import)
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.config import load_settings  # noqa: E402

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", default="mirror", choices=list(cut.CUT_TYPES))
    ap.add_argument("--shot", default="knee")
    ap.add_argument("--direction", default=None)
    ap.add_argument("--clothing", default="top", choices=["top", "bottom"])
    ap.add_argument("--front", default=str(ROOT / "Test image_front.jpeg"))
    ap.add_argument("--out", default=str(SERVER / "ab_out" / "smoke_cut"))
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = load_settings()
    if not s.gemini_api_key:
        print("GEMINI_API_KEY가 없습니다 (server/.env).", file=sys.stderr)
        return 2

    front = Path(args.front)
    if not front.exists():
        print(f"테스트 이미지가 없습니다: {front}", file=sys.stderr)
        return 2
    img = InlineImage(_MIME.get(front.suffix.lower(), "image/jpeg"), front.read_bytes())

    spec = cut.normalize_spec({
        "cutType": args.cut, "shot": args.shot, "direction": args.direction,
        "colorId": "col1", "spaceGroupId": None,
    })
    product = {"name": "소프트 골지 라운드 니트", "clothing_type": args.clothing}
    analysis = {
        "materials": [{"name": "코튼", "ratio": 60}, {"name": "폴리에스터", "ratio": 40}],
        "fit": "semi_over", "sellingPoints": ["부드러운 촉감"], "targetGenders": ["women"],
    }
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0)
    template = cut.load_cut_template(s)
    prompt = cut.render_cut_prompt(template, spec, product, analysis, args.clothing, manifest)
    print(f"--- spec: {spec}")
    print(f"--- prompt {len(prompt)}자, model={resolve_model(s, s.cut_tier)}")

    gemini = GeminiImageClient(s)
    t0 = time.time()
    res = await gemini.generate_content_image(
        resolve_model(s, s.cut_tier), prompt, [img], s.mannequin_image_size,
        aspect_ratio=s.mannequin_aspect_ratio)
    dt = time.time() - t0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(res.mime, "png")
    out = out_dir / f"{args.cut}_{spec['shot']}_{args.clothing}.{ext}"
    out.write_bytes(res.image)
    print(f"OK — {dt:.1f}s, {len(res.image) // 1024}KB, mime={res.mime}")
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
