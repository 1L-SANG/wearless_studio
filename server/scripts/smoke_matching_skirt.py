"""매칭 치마(matchingFit v2) 실생성 스모크 — 1콜(1K), 육안 확인용.

주상품=티셔츠(T-A), 매칭=흰색 롱 스트레이트 스커트(WB). 선언 matchingFit
{skirt, silhouette:a_line} → 프롬프트에 치마 실루엣 라인이 들어가고 생성물에서
스커트가 A라인 기미로 표현되는지 확인한다. prod 자원 무접촉(로컬 저장만).

실행: cd server && /Users/daily/Documents/wearless_studio/server/.venv/bin/python -m scripts.smoke_matching_skirt
"""

import asyncio
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # worktree 루트
MAIN = Path("/Users/daily/Documents/wearless_studio")  # .env·이미지·venv 는 본 저장소 것 사용
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

import os  # noqa: E402

for line in (MAIN / "server" / ".env").read_text().splitlines():  # 단순 .env 로더 (import 부작용 회피)
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from app.agents import mannequin  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

PROFILE = {"category": "top", "gender": "women", "source": "seller",
           "axes": {"fit": "slim"}, "version": 2,
           "matchingFit": {"clothingId": "smoke-item", "fitCategory": "skirt",
                           "axes": {"silhouette": "a_line"}}}


async def main():
    s = load_settings()
    r2 = R2Client(s)
    base = InlineImage("image/png", r2.get_bytes("seed/mannequin/base-women-2K.png"))
    tee_dir = MAIN / "spike/input/tee-fb"
    srcs = [InlineImage("image/jpeg", (tee_dir / "1_front.jpeg").read_bytes()),
            InlineImage("image/jpeg", (tee_dir / "2_back.jpeg").read_bytes())]
    skirt = InlineImage("image/png", r2.get_bytes("seed/matching/match_women_bottom_14.png"))

    manifest = ("1. Base mannequin — the canvas to dress (keep it identical)\n"
                "2. front view of the garment\n3. back view of the garment\n"
                "4. matching BOTTOM garment — also dress the mannequin in this, coordinated with the top")
    ctx = mannequin.prompt_context(
        clothing_type="top", product_count=3, base_gender="women",
        image_manifest=manifest, fit_profile=PROFILE, adjusted_axes=())
    prompt = render_mannequin_prompt(
        load_prompt_template(s), ctx,
        product={"name": "매칭 치마 스모크 상의", "clothing_type": "top"},
        analysis={"clothingType": "top", "targetGenders": ["women"]})
    assert "matching skirt silhouette" in prompt, "치마 실루엣 라인 미포함"
    assert "a_line" not in prompt and "smoke-item" not in prompt, "원문 값 유출"
    print("프롬프트 검증 ✓ — 치마 실루엣 고정 문구 포함, 원문 값 미유출")

    g = GeminiImageClient(s)
    res = await g.generate_content_image(
        resolve_model(s, "image_high"), prompt, [base, *srcs, skirt], "1K",
        aspect_ratio="2:3", timeout=300.0)
    out = SERVER / "ab_out" / "smoke_matching_skirt.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(res.image)
    print(f"생성 완료(1K) → {out}")


if __name__ == "__main__":
    asyncio.run(main())
