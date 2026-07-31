"""여성 베이스 마네킹 다리 단축 — v1(원본) → v2(다리 0.88배) 재현 스크립트.

**결론: v2 는 채택하지 않았다(2026-07-31 롤백, R2 객체·asset 행 삭제).** 벗은 베이스만 놓고
보면 v2 비율이 낫지만, 실제 착장컷(스트라이프 상의 + 부츠컷 데님)을 나란히 보니 원본 쪽이
나았다 — 옷이 다리를 덮으면 베이스의 과한 다리 길이가 드러나지 않고, 오히려 v2 는 컷 전체가
뭉툭해 보였다. "베이스 단독 비교"로 판단하면 안 된다는 게 이 실험의 교훈이다.
이 스크립트는 원본에서 v2 를 언제든 다시 만들어 낸다(바이트 재현 가능) — 다시 시도한다면
R2 업로드·asset 행 삽입부터 새로 하면 된다.

아래는 그때 잰 값과 방법 — 다시 시도할 사람이 처음부터 재지 않도록 남긴다.

왜(2026-07-31 사용자 관측): 여성 베이스의 다리가 너무 길어 착장컷이 실제 인체로 안 읽혔다.
실측(육안 눈금 검증): 머리끝 y=131, 발바닥 y=1878, 가랑이 y≈970 → 다리비율 52.0%.
리테일 마네킹 최상단 값이고 실제 인체는 45~48%. 0.88배 압축 후 49.1%로 내려온다.

기하 변환만 한다 — 생성 모델을 태우지 않으므로 몸통·얼굴·포즈·조명이 절대 안 변한다:
  · 압축 대상은 **허벅지~종아리(y 970~1790)뿐**. 발목 1790은 하단 폭 프로파일의 최소점이다.
  · 발·그림자(1790~1879)는 원본 배율 유지 — 같이 줄이면 발이 뭉개진다(초안에서 관측).
  · 압축으로 생긴 하단 여백은 원본의 **발 아래 배경 블록을 위로 당겨** 채운다. 단색으로
    칠하면 배경 그라데이션과 톤이 어긋나 밝은 밴드가 보인다(초안에서 관측).

실행:
    cd server && .venv/bin/python -m scripts.shorten_base_legs            # 로컬 파일만 생성
    cd server && .venv/bin/python -m scripts.shorten_base_legs --scale 0.85

산출물은 ab_out/base/ 에 떨어진다. R2 업로드와 assets 행 삽입은 별도 단계
(scripts/seed_mannequin_base.py 가 v2 키를 시드한다) — 이 스크립트는 쓰기를 하지 않는다.
"""
import argparse
import io
import pathlib

from PIL import Image

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

SRC_KEY = "seed/mannequin/base-women-2K.png"
OUT = pathlib.Path(__file__).resolve().parents[1] / "ab_out/base"

# 원본 1344x2016 기준 실측 랜드마크. 다른 원본으로 바꾸면 반드시 다시 재야 한다.
CROTCH = 970   # 가랑이 — 여기부터 아래만 건드린다
ANKLE = 1790   # 발목(하단 폭 최소점)
SHADOW = 1879  # 그림자 끝 = 순수 배경 시작


def shorten(src: Image.Image, scale: float) -> Image.Image:
    w, h = src.size
    limb = src.crop((0, CROTCH, w, ANKLE))
    limb_s = limb.resize((w, int(limb.height * scale)), Image.LANCZOS)
    out = Image.new("RGB", (w, h))
    y = 0
    for part in (src.crop((0, 0, w, CROTCH)), limb_s,
                 src.crop((0, ANKLE, w, SHADOW)), src.crop((0, SHADOW, w, h))):
        out.paste(part, (0, y))
        y += part.height
    if y < h:  # 압축분만큼 남는 하단은 배경 마지막 줄로 연장
        out.paste(out.crop((0, y - 2, w, y)).resize((w, h - y), Image.LANCZOS), (0, y))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=0.88, help="다리 압축 배율(1.0 = 원본)")
    args = ap.parse_args()

    s = load_settings()
    src = Image.open(io.BytesIO(R2Client(s).get_bytes(SRC_KEY))).convert("RGB")
    if (src.width, src.height) != (1344, 2016):
        print(f"❌ 원본 크기가 {src.size} — 랜드마크(CROTCH/ANKLE/SHADOW)를 다시 재야 한다")
        return 1

    out = shorten(src, args.scale)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"base-women-2K-legs{int(args.scale * 100)}.png"
    out.save(path)

    sole = CROTCH + int((ANKLE - CROTCH) * args.scale) + (SHADOW - ANKLE)
    print(f"{path}  다리비율 {(sole - CROTCH) / (sole - 131) * 100:.1f}% (원본 52.0%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
