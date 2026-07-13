"""핏 조정 반영(fidelity) 육안 그리드 — P0 검증 하네스 (fidelity 설계 D5).

같은 입력으로 축 케이스들을 생성해 컨택트시트로 비교한다. **사람 눈이 정본**이며
자동 pass/fail·배포 게이트로 쓰지 않는다. --dry-run 이면 프롬프트 조립까지만(무료).

실행: cd server && .venv/bin/python -m scripts.smoke_mannequin_fit_grid --dry-run
      (유료 실행은 케이스당 Gemini 1콜 — 기본 8케이스, --cases 로 축소 가능)
출력: server/ab_out/fit_grid_<ts>/ (개별 PNG + contact_sheet + prompts)
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

from app.agents import mannequin  # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402

# 8케이스 (설계 D5): 단일축 4 + 하의 유무 교차 + 보고 사례 조합
CASES = [
    ("slim", {"fit": "slim"}, False),
    ("over", {"fit": "over"}, False),
    ("crop_nobottom", {"length": "crop"}, False),
    ("long_nobottom", {"length": "long"}, False),
    ("crop_bottom", {"length": "crop"}, True),
    ("long_bottom", {"length": "long"}, True),
    ("slim_long_bottom", {"fit": "slim", "length": "long"}, True),   # 보고 사례
    ("over_crop_bottom", {"fit": "over", "length": "crop"}, True),
]


def build_prompt(s, axes: dict, with_bottom: bool) -> str:
    profile = {"category": "top", "gender": "women", "source": "seller",
               "axes": axes, "version": 1}
    manifest = "1. Base mannequin — the canvas to dress (keep it identical)\n2. front view of the garment"
    if with_bottom:
        profile["matchCut"] = "wide"
        manifest += "\n3. matching BOTTOM garment — also dress the mannequin in this, coordinated with the top"
    ctx = mannequin.prompt_context(
        clothing_type="top", product_count=1 + (1 if with_bottom else 0),
        base_gender="women", image_manifest=manifest,
        fit_profile=profile, adjusted_axes=tuple(axes.keys()))
    return render_mannequin_prompt(
        load_prompt_template(s), ctx,
        product={"name": "핏 그리드 테스트 상의", "clothing_type": "top"},
        analysis={"clothingType": "top", "targetGenders": ["women"]})


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cases", type=int, default=len(CASES))
    args = ap.parse_args()
    s = load_settings()
    out = SERVER / "ab_out" / "fit_grid_dry"
    out.mkdir(parents=True, exist_ok=True)
    for name, axes, with_bottom in CASES[: args.cases]:
        prompt = build_prompt(s, axes, with_bottom)
        (out / f"prompt_{name}.txt").write_text(prompt)
        print(f"[{name}] prompt {len(prompt)}자"
              + (" · CHANGES ✓" if "CHANGES FOR THIS GENERATION" in prompt else " · CHANGES 없음"))
    if args.dry_run:
        print(f"\n(dry-run) 프롬프트만 저장: {out}")
        return
    print("유료 실행은 실 이미지 입력(베이스·상품·매칭)이 필요 — smoke_mannequin.py 의 로더를 붙여 확장하세요.")


if __name__ == "__main__":
    asyncio.run(main())
