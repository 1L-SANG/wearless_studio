"""AG-01 live smoke — 실 provider(vision_llm 폴백 체인)로 분석 1콜: 지연·판정·특징 확인.

2026-07-07 재작성: 옛 agents.analysis/gemini_text(삭제됨) → product_analyst 경유로 갱신.
--no-shrink 로 이미지 축소를 끄면 축소 유/무 지연 A/B 가 된다 (속도 개선 실측용).

실행: cd server && .venv/bin/python -m scripts.smoke_analysis
      (옵션) --front <이미지> --back <이미지> --name <상품명> --no-shrink
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

from app.agents import product_analyst  # noqa: E402 (env 로드 후 import)
from app.agents.gemini_image import InlineImage  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.workers.analyze_job import shrink_for_vision  # noqa: E402

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--front", default=str(ROOT / "Test image_front.jpeg"))
    ap.add_argument("--back", default=str(ROOT / "Test image_back.jpeg"))
    ap.add_argument("--name", default="")
    ap.add_argument("--no-shrink", action="store_true", help="이미지 축소 없이 원본 전송 (A/B용)")
    args = ap.parse_args()

    s = load_settings()
    if not (s.gemini_api_key or s.openai_api_key):
        sys.exit("분석 AI 키 미설정 — server/.env 확인")

    images, total_in, total_out = [], 0, 0
    for path in (args.front, args.back):
        p = Path(path)
        if not p.exists():
            print(f"skip: {p} 없음")
            continue
        raw = p.read_bytes()
        mime = _MIME.get(p.suffix.lower(), "image/jpeg")
        data, mime = (raw, mime) if args.no_shrink else shrink_for_vision(raw, mime)
        total_in += len(raw)
        total_out += len(data)
        images.append(InlineImage(mime, data))
    if not images:
        sys.exit("테스트 이미지 없음")

    print(f"order={s.analysis_model_order} thinking={s.analysis_thinking_level} "
          f"images={len(images)} bytes {total_in:,} → {total_out:,}"
          f"{' (no-shrink)' if args.no_shrink else ''}")

    product = {"name": args.name}
    t0 = time.perf_counter()
    distributed, provider = await product_analyst.analyze(s, product, images)
    wall = int((time.perf_counter() - t0) * 1000)

    print(f"\n== RESULT (provider={provider}, {wall}ms wall) ==")
    print(json.dumps(distributed, ensure_ascii=False, indent=2))
    a = distributed["analysis"]
    checks = [
        ("clothingType 판정", bool(distributed["product"].get("clothingType"))),
        ("소재 채워짐 (판독 또는 카테고리 기본값)", bool(a.get("materials"))),
        ("특징 ≤2 (개조식 서버 가드 통과분)", len(a.get("aiSuggestedPoints", [])) <= 2),
        ("실측 없음", "measurements" not in a),
    ]
    ok = all(c for _, c in checks)
    for name, c in checks:
        print(f"  {'✅' if c else '❌'} {name}")
    print(f"\n{'PASS' if ok else 'FAIL'} — {wall}ms")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
