"""`product_fidelity` 에 핏 항목을 넣은 것의 **인과 효과만** 분리 측정.

문제: 오버사이즈 티가 몸에 붙는 미니원피스로 바뀐 컷의 fidelity 가 83→78 이었다. 실루엣이
통째로 바뀌었는데 5점이다. 채점 프롬프트의 fidelity 설명에 색·프린트·길이는 있어도
**핏·여유분이 없었기** 때문이라는 가설.

검증 방법은 거울 규칙 때와 같다 — 같은 이미지·같은 모델에 **규칙 문장만** 껐다 켜서 점수
차이를 본다. 다른 게 같이 바뀌면 무엇이 효과인지 알 수 없다.

실행:
    cd server && DATABASE_URL=... .venv/bin/python -m scripts.verify_fit_fidelity_rule
읽기 전용(생성 없음). ab_out/bust_ab 의 base/v1 쌍을 재사용한다.

⚠️ 프롬프트 **파일을 임시로 바꾸지 않는다**. `image_qc.verdict` 는 호출마다 파일을 읽으므로,
동시에 도는 다른 실행(ab_bust_pass 등)의 점수를 오염시킨다. 모듈의 경로 상수만 프로세스 안에서
갈아끼운다 — 같은 이유로 이 스크립트를 두 개 동시에 돌리면 안 된다.
"""
import argparse
import asyncio
import pathlib

from scripts._env import load_env

load_env()

from app.agents import image_qc  # noqa: E402
from app.agents.gemini_image import InlineImage  # noqa: E402
from app.config import load_settings  # noqa: E402

AB = pathlib.Path(__file__).resolve().parents[1] / "ab_out/bust_ab"
PROMPT = pathlib.Path(image_qc._SCORE_PROMPT_FILE)
_TMP = AB / "_scores_off.txt"      # OFF 팔 전용 임시 프롬프트 (원본은 건드리지 않는다)

# 핏 문장을 빼고 원래대로 되돌린 문단 — OFF 팔.
_WITHOUT_FIT = """- product_fidelity: does the generated garment reproduce the seller's product? Color, pattern,
  print and logo accuracy, neckline, sleeve and hem length, closures, pockets, trims. Every
  mismatch you listed above must be reflected here. A garbled or restyled logo, a changed color,
  or a length that reads as a different product caps this axis below 60."""


def _swap_to_off(text: str) -> str:
    start = text.index("- product_fidelity:")
    end = text.index("- physical_naturalness:")
    return text[:start] + _WITHOUT_FIT + "\n" + text[end:]


async def _score(s, prod_imgs, img: bytes, *, fit_rule: bool, monkey) -> dict:
    monkey(fit_rule)
    return await image_qc.verdict(s, prod_imgs, InlineImage("image/png", img), scored=True)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=3)
    args = ap.parse_args()

    s = load_settings()
    on_text = PROMPT.read_text(encoding="utf-8")
    off_text = _swap_to_off(on_text)

    AB.mkdir(parents=True, exist_ok=True)
    _TMP.write_text(off_text, encoding="utf-8")

    def monkey(fit_rule: bool):
        # 파일이 아니라 **모듈 상수**를 갈아끼운다 — 공유 파일을 건드리면 동시에 도는
        # 다른 실행의 판정까지 바뀐다.
        image_qc._SCORE_PROMPT_FILE = str(PROMPT if fit_rule else _TMP)

    # 상품 원본은 base 컷을 기준으로 삼는다 — 여기서 재는 건 "규칙이 핏 변화를 잡는가"이므로
    # 기준 이미지는 두 팔에서 동일하기만 하면 된다.
    pairs = sorted({p.name.split("_")[0] for p in AB.glob("*_v1.png")})[: args.pairs]
    if not pairs:
        print("ab_out/bust_ab 에 base/v1 쌍이 없다 — ab_bust_pass 를 먼저 돌릴 것")
        return 1

    rows = []
    try:
        for pid in pairs:
            base = (AB / f"{pid}_base.png").read_bytes()
            edited = (AB / f"{pid}_v1.png").read_bytes()
            prod = [InlineImage("image/png", base)]   # 기준 = 편집 전 컷
            off = await _score(s, prod, edited, fit_rule=False, monkey=monkey)
            on = await _score(s, prod, edited, fit_rule=True, monkey=monkey)
            rows.append((pid, off, on))
            print(f"  {pid}  OFF fid={off.get('product_fidelity')} crit={off.get('critical_errors')}")
            print(f"  {pid}  ON  fid={on.get('product_fidelity')} crit={on.get('critical_errors')}")
    finally:
        image_qc._SCORE_PROMPT_FILE = str(PROMPT)
        _TMP.unlink(missing_ok=True)

    deltas = [r[2].get("product_fidelity", 0) - r[1].get("product_fidelity", 0) for r in rows]
    print(f"\n[fit_rule] n={len(rows)} · fidelity 변화 평균 {sum(deltas)/len(deltas):+.1f} "
          f"(개별 {deltas})")
    print("음수면 규칙이 핏 변화를 벌점으로 잡고 있다는 뜻이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
