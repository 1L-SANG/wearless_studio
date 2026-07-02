"""AG-01 live smoke (pl1_analysis_agent_spec §10.3) — 롤아웃 게이트.

실제 GEMINI_API_KEY로 저장소의 테스트 이미지를 분석해 ① §3.2 스키마 준수(AnalysisRaw 통과)
② responseJsonSchema·thinkingLevel 실서버 수용(폴백 로그 관측) ③ latency·토큰을 확인한다.
DB·R2 불필요 — Gemini 호출 1회. 비용: Flash 1콜.

실행: cd server && .venv/bin/python -m scripts.smoke_analysis
      (옵션) --front <이미지> --back <이미지> --name <상품명> --thinking low|medium|high
"""

import argparse
import asyncio
import json
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

from app.agents import analysis  # noqa: E402 (env 로드 후 import)
from app.agents.gemini_text import GeminiTextClient  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.agents.gemini_image import InlineImage  # noqa: E402

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--front", default=str(ROOT / "Test image_front.jpeg"))
    ap.add_argument("--back", default=str(ROOT / "Test image_back.jpeg"))
    ap.add_argument("--name", default="")
    ap.add_argument("--thinking", default=None, choices=["low", "medium", "high"])
    args = ap.parse_args()

    s = load_settings()
    if not s.gemini_api_key:
        sys.exit("GEMINI_API_KEY 미설정 — server/.env 확인")
    model = resolve_model(s, "text")
    thinking = args.thinking or s.analysis_thinking_level

    # 가짜 product(색상 그룹 1개) — 매니페스트·후처리 검증용
    specs, images = [], []
    for slot, path in (("Front", args.front), ("Back", args.back)):
        p = Path(path)
        if not p.exists():
            print(f"skip {slot}: {p} 없음")
            continue
        specs.append({"colorGroupId": "col_smoke", "isBase": True, "slot": slot,
                      "assetId": f"smoke-{slot}"})
        images.append(InlineImage(_MIME.get(p.suffix.lower(), "image/jpeg"), p.read_bytes()))
    if not images:
        sys.exit("테스트 이미지 없음")

    product = {"name": args.name,
               "colors": [{"id": "col_smoke", "isBase": True, "swatchId": None, "images": []}]}
    user_text = analysis.build_user_text(analysis.build_manifest(specs), args.name)
    system = analysis.load_analysis_prompt(s)

    print(f"model={model} thinking={thinking} images={len(images)}")
    client = GeminiTextClient(s)
    t0 = time.perf_counter()
    res = await client.generate_json(
        model, system, user_text, images, analysis.RESPONSE_SCHEMA,
        thinking_level=thinking, timeout=s.analysis_timeout_seconds)
    wall = int((time.perf_counter() - t0) * 1000)

    raw = analysis.AnalysisRaw.model_validate(res.data)  # ← §3.2 스키마 게이트
    post = analysis.postprocess(raw, product)

    print(f"\n== RAW ({res.latency_ms}ms api / {wall}ms wall) ==")
    print(json.dumps(res.data, ensure_ascii=False, indent=2))
    print("\n== POSTPROCESSED ==")
    print(json.dumps({"clothing_type": post["clothing_type"], **post["payload_base"],
                      "swatchSuggestions": post["swatch_suggestions"],
                      "styleTags": post["style_tags"]}, ensure_ascii=False, indent=2))
    print(f"\nusage={res.usage}")
    print("PASS — 스키마 준수·후처리 통과 (폴백 경고 로그가 없으면 "
          "responseJsonSchema·thinkingLevel 모두 실서버 수용)")


if __name__ == "__main__":
    asyncio.run(main())
