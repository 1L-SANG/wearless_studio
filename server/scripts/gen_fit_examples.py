"""핏 예시 타일 누락분 생성 — fitExampleImages.js 갭 목록의 일회성 채움 (2026-08-01 WS3).

예시 = 셀러 옷과 무관한 "중립 마네킹 예시". 기존 36장의 톤을 그대로 따른다:
  · 흰 마네킹 + 옅은 회색 스튜디오, 3/4 각도, **해당 카테고리 옷만** 입음
  · 성별 규칙(사용자 지시 2026-08-01): -men- 조합은 남성 베이스, -women- 은 여성 베이스,
    -any- 는 기존 any 파일과 동일하게 **여성 베이스**(outer-any-fit-slim 실물 확인)
  · 옷은 기존 세트와 동일 계열 — men top=검정 포켓 티, men pants=연한 워시드 진,
    skirt=아이보리 드로스트링 스커트, dress=검정 반팔 원피스, outer=차콜 블레이저

실행:
    cd server && .venv/bin/python -m scripts.gen_fit_examples [--only pants-men-cut-slim]
원본 PNG 를 ab_out/fit_examples/ 에 저장한다 — 사람이 확인 후 300x447 jpg 로 변환해
public/assets/fit-examples/ 에 배치하고 fitExampleImages.js FILES 에 등록한다(별도 단계).
비용: 조합당 이미지 호출 1회(총 11회, 사용자 승인).
"""
import argparse
import asyncio
import pathlib

from scripts._env import load_env

load_env()

from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "ab_out/fit_examples"

_BASE_KEY = {"women": "seed/mannequin/base-women-2K.png", "men": "seed/mannequin/base-men-2K.png"}

# (파일 키, 베이스 성별, 옷+핏 지시). 지시는 기존 세트의 옷 계열 + 카탈로그 promptEn 서술.
COMBOS = [
    ("pants-men-cut-slim", "men",
     "a pair of light washed-blue jeans, slim cut — close to the leg from thigh to ankle with a "
     "narrow clean line, not skin-tight"),
    ("pants-men-cut-straight", "men",
     "a pair of light washed-blue jeans, straight cut — the leg falls straight from the thigh to "
     "the hem with the same width top to bottom"),
    ("top-men-fit-semi_over", "men",
     "a plain black short-sleeve t-shirt with a small chest pocket, semi-oversized fit — relaxed "
     "volume with a mildly dropped shoulder, clearly roomier than regular but not fully oversized"),
    ("skirt-women-length-mini", "women",
     "an ivory drawstring skirt, mini length — hem ends above mid-thigh"),
    ("skirt-women-length-midi", "women",
     "an ivory drawstring skirt, midi length — hem falls between the knee and mid-calf"),
    ("skirt-women-length-long", "women",
     "an ivory drawstring skirt, long length — hem reaches the lower calf to ankle"),
    ("dress-women-silhouette-a_line", "women",
     "a plain black short-sleeve dress, A-line silhouette — fitted through the bodice, the skirt "
     "widens gradually from the waist in a clean A shape"),
    ("dress-women-length-midi", "women",
     "a plain black short-sleeve dress, midi length — hem falls between the knee and mid-calf"),
    ("outer-any-fit-regular", "women",
     "a charcoal single-breasted blazer worn open over the bare mannequin, regular fit — natural "
     "shoulder line with light, even ease through the body"),
    ("outer-any-fit-semi_over", "women",
     "a charcoal single-breasted blazer worn open over the bare mannequin, semi-oversized fit — "
     "relaxed volume with a mildly dropped shoulder and roomier body"),
    # top-men length 3종(2026-08-01 추가) — WS2 매칭 상의 조정 스텝이 남성 하의 상품에서
    # 쓰는 예시. 여성만 있고 남성이 없어 정합 테스트가 잡았다.
    ("top-men-length-crop", "men",
     "a plain black short-sleeve t-shirt with a small chest pocket, cropped length — short hem "
     "ending around the high waist, clearly above the hip"),
    ("top-men-length-basic", "men",
     "a plain black short-sleeve t-shirt with a small chest pocket, standard length — hem ends "
     "around the hip line"),
    ("top-men-length-long", "men",
     "a plain black short-sleeve t-shirt with a small chest pocket, long length — hem extends "
     "clearly below the hips"),
    ("outer-any-length-basic", "women",
     "a charcoal single-breasted blazer worn open over the bare mannequin, standard length — hem "
     "ends around the hip line"),
]

_PROMPT = """Dress the mannequin in image 1 in exactly ONE garment: {garment}.

This is a neutral fit-example tile for a clothing app. Rules:
- The mannequin wears ONLY this garment — nothing else. Bare mannequin surface everywhere else.
- Keep the SAME mannequin body, pose, camera framing and plain light-grey studio background as
  image 1. Do not change the mannequin.
- Plain solid fabric, no logos, no prints, no patterns — the tile must read as a neutral example.
- The garment's fit and length must clearly show the described characteristics; that is the whole
  point of this image.
- Studio product-photo lighting, clean and soft."""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[], help="특정 키만 생성")
    args = ap.parse_args()

    s = load_settings()
    r2 = R2Client(s)
    gemini = GeminiImageClient(s)
    OUT.mkdir(parents=True, exist_ok=True)

    bases = {g: InlineImage("image/png", r2.get_bytes(k)) for g, k in _BASE_KEY.items()}
    todo = [c for c in COMBOS if not args.only or c[0] in args.only]
    print(f"[gen_fit_examples] {len(todo)}건 생성 → {OUT}")
    ok = 0
    for key, gender, garment in todo:
        try:
            res = await gemini.generate_content_image(
                resolve_model(s, "image_high"),
                _PROMPT.format(garment=garment),
                [bases[gender]], "1K", aspect_ratio="2:3")
            (OUT / f"{key}.png").write_bytes(res.image)
            ok += 1
            print(f"  ✅ {key}  ({gender} base, {len(res.image)//1024}KB)")
        except Exception as e:  # 한 장 실패가 나머지를 막지 않는다
            print(f"  ❌ {key}: {type(e).__name__} {str(e)[:120]}")
    print(f"\n{ok}/{len(todo)} 완료. 확인 후 변환·배치는 별도 단계.")
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
