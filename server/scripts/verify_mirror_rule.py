"""거울 반전 QC 규칙 전후 검증 (플랜 Phase 1 — US-3 수용기준).

2026-07-30 A/B 에서 관측된 오판정을 그대로 재현한다: 셀러 원본이 거울 셀카라 로고·숫자가
반전돼 있는데, 구 프롬프트는 '원본과 일치'만 봐서 **반전 보존본에 pass** 를 주고 정방향
교정본에는 retry 를 줬다(correctionPrompt 가 "exact mirrored numbers '201' 을 유지하라").

같은 이미지 쌍에 (a) 현행 프롬프트 (b) MIRRORED SOURCE 규칙을 제거한 프롬프트 를 각각
적용해 판정을 비교한다 — 규칙이 실제로 판정을 뒤집는지 확인하는 것이 목적이다.

실행:
    cd server && .venv/bin/python -m scripts.verify_mirror_rule

입력은 2026-07-30 산출물(레포 밖 /tmp, ab_out/refeval)이라 경로를 인자로 받는다.
"""
import argparse
import asyncio
import json
import pathlib
import re

from scripts._env import load_env

load_env()

from app.agents import image_qc  # noqa: E402
from app.agents.gemini_image import InlineImage  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.agents.vision_llm import analyze_with_fallback  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "ab_out/mirror_rule"

# MIRRORED SOURCE 블록을 통째로 들어낸 '구 프롬프트' 재현 — 규칙의 인과 효과만 분리한다.
_MIRROR_BLOCK = re.compile(
    r"\nMIRRORED SOURCE PHOTOS.*?(?=\n- verdict \"pass\")", re.DOTALL)


def _strip_mirror_rule(prompt: str) -> str:
    stripped = _MIRROR_BLOCK.sub("\n", prompt)
    assert "MIRRORED SOURCE" not in stripped, "규칙 제거 실패 — 프롬프트 구조가 바뀌었다"
    return stripped


def _img(path: pathlib.Path) -> InlineImage:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return InlineImage(mime, path.read_bytes())


async def _judge(s, prompt: str, prods: list[InlineImage], gen: InlineImage) -> dict:
    raw, _provider = await analyze_with_fallback(
        s, prompt, [*prods, gen], image_qc.qc_schema())
    return image_qc.validate(raw)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", nargs="+", required=True, help="셀러 원본(거울 셀카) 경로")
    ap.add_argument("--generated", nargs="+", required=True,
                    help="생성컷 경로 — 반전 보존본/정방향 교정본 섞어서")
    args = ap.parse_args()

    s = load_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    prods = [_img(pathlib.Path(p)) for p in args.product]
    cur_prompt = image_qc.build_prompt(len(prods))
    old_prompt = _strip_mirror_rule(cur_prompt)

    rows = []
    for g in args.generated:
        path = pathlib.Path(g)
        gen = _img(path)
        before, after = await asyncio.gather(
            _judge(s, old_prompt, prods, gen),
            _judge(s, cur_prompt, prods, gen),
        )
        rows.append({"image": path.name, "before": before, "after": after})
        flip = "" if before["verdict"] == after["verdict"] else "  ← 판정 뒤집힘"
        print(f"{path.name:24} 규칙없음={before['verdict']:5} 규칙있음={after['verdict']:5}{flip}")
        for label, v in (("  before", before), ("  after ", after)):
            for m in v["mismatches"]:
                print(f"{label} · {m[:120]}")

    (OUT / "results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
