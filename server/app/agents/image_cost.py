"""이미지 생성 API 실비 계산 (순수 — I/O 없음).

왜 필요한가: 지금까지 `GeminiImageResult.usage`(usageMetadata)는 받아만 오고 아무도 안 읽었다.
그래서 "완성본 1장에 얼마 쓰나"를 물으면 요금표 × 추정 재시도 횟수로 상한만 말할 수 있었다.
이 모듈은 응답의 실제 토큰 수를 달러로 바꾸고, `image_usage` 가 그걸 DB 에 적는다.

가격 기준: Google Gemini API 공식 요금표(2026-08-04 확인).
  - gemini-3-pro-image  : 입력 $2/1M, 이미지 출력 $120/1M → 1K·2K 1,120tok=$0.134, 4K 2,000tok=$0.24
  - gemini-3.1-flash-image: 입력 $0.5/1M, 이미지 출력 $60/1M → 1K $0.067 / 2K $0.101 / 4K $0.151
usage 가 오면 그 토큰이 1차 근거, 없으면(구버전 응답·모킹) 해상도별 표로 폴백한다 —
어느 쪽을 썼는지 `source` 로 남겨서, 나중에 집계를 볼 때 추정치와 실측치를 섞어 보지 않게 한다.
"""

from dataclasses import dataclass
from typing import Mapping

# 텍스트 출력 단가는 공식 표에 이미지 모델용 항목이 따로 없다. 이미지 응답의 텍스트 파트는
# 수십~수백 토큰이라 총액에서 0.1% 미만이므로 같은 계열 텍스트 모델 단가로 근사한다.
_TEXT_OUT_APPROX = {"pro": 12.0, "flash": 3.0}


@dataclass(frozen=True)
class ModelPrice:
    input_usd_per_mtok: float          # 텍스트·이미지 입력 동일 단가(Gemini)
    output_text_usd_per_mtok: float
    output_image_usd_per_mtok: float
    image_output_tokens: Mapping[str, int]  # 해상도 → 이미지 출력 토큰 (usage 없을 때 폴백)


PRICES: Mapping[str, ModelPrice] = {
    "gemini-3-pro-image": ModelPrice(
        input_usd_per_mtok=2.0,
        output_text_usd_per_mtok=_TEXT_OUT_APPROX["pro"],
        output_image_usd_per_mtok=120.0,
        # 1K 와 2K 가 같은 1,120 토큰인 건 오타가 아니다 — 공식 표가 같은 값이고,
        # 이게 "2K 는 같은 생성물을 크게 낼 뿐"이라는 해석의 근거다(4K 만 2,000 으로 늘어난다).
        image_output_tokens={"1K": 1120, "2K": 1120, "4K": 2000},
    ),
    "gemini-3.1-flash-image": ModelPrice(
        input_usd_per_mtok=0.5,
        output_text_usd_per_mtok=_TEXT_OUT_APPROX["flash"],
        output_image_usd_per_mtok=60.0,
        image_output_tokens={"512": 750, "1K": 1120, "2K": 1680, "4K": 2520},
    ),
    "gemini-2.5-flash-image": ModelPrice(
        input_usd_per_mtok=0.3,
        output_text_usd_per_mtok=_TEXT_OUT_APPROX["flash"],
        output_image_usd_per_mtok=30.0,
        image_output_tokens={"1K": 1290},
    ),
}


@dataclass(frozen=True)
class ImageCost:
    input_tokens: int
    output_text_tokens: int
    output_image_tokens: int
    usd: float | None      # 단가표에 없는 모델이면 None (토큰만 기록)
    source: str            # usage | table | unknown_model | unavailable_no_image


def _modality_split(details: list | None) -> dict[str, int]:
    """usageMetadata 의 *TokensDetails → {modality: tokens}. 형식이 어긋나면 빈 dict."""
    out: dict[str, int] = {}
    for row in details or []:
        if not isinstance(row, dict):
            continue
        modality = str(row.get("modality") or "").upper()
        count = row.get("tokenCount")
        if (modality and isinstance(count, int) and not isinstance(count, bool)
                and count > 0):
            out[modality] = out.get(modality, 0) + count
    return out


def _tokens(value) -> int:
    """API의 깨진/부분 usage 값은 0으로 취급하되 음수·bool은 받지 않는다."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def estimate_cost(
    model: str, image_size: str, usage: dict | None, *, has_image: bool = True,
) -> ImageCost:
    """이 호출 1회의 실비. 실패해도 예외를 던지지 않는다 — 계측이 생성을 막으면 안 된다."""
    price = PRICES.get(model)
    prompt_tokens = 0
    text_out = 0
    image_out = 0
    source = "table"

    if isinstance(usage, dict):
        prompt_tokens = _tokens(usage.get("promptTokenCount"))
        candidates = _tokens(usage.get("candidatesTokenCount"))
        # 사고(thinking) 토큰은 candidates 에 안 잡히는 경우가 있어 따로 더한다 — 텍스트 단가.
        thoughts = _tokens(usage.get("thoughtsTokenCount"))
        split = _modality_split(usage.get("candidatesTokensDetails"))
        if split:
            image_out = split.get("IMAGE", 0)
            # 실측(2026-08-04): details 에 IMAGE 1120 만 오고 candidates 는 1223 이었다 —
            # 차액(103)은 details 에 안 실리는 텍스트 출력이라 여기서 되살린다.
            listed = sum(split.values())
            text_out = (sum(v for k, v in split.items() if k != "IMAGE")
                        + max(candidates - listed, 0) + thoughts)
        elif candidates and has_image:
            # 이미지가 실제 도착했고 모달리티 분해만 없으면 candidates 를 이미지로 본다.
            image_out = candidates
            text_out = thoughts
        else:
            # 텍스트만 온 200 응답의 candidates 를 이미지 토큰으로 오인하지 않는다.
            text_out = candidates + thoughts
        if prompt_tokens or image_out or text_out:
            source = "usage"

    if source != "usage":
        # 이미지가 오지 않았고 usage 도 없으면 실제 과금액은 알 수 없다. 요청 해상도의
        # 장당 이미지 가격을 억지로 붙이면 텍스트-only/깨진 200을 정상 이미지로 계산한다.
        if not has_image:
            return ImageCost(0, 0, 0, None, "unavailable_no_image")
        if price is None:
            return ImageCost(0, 0, 0, None, "unknown_model")
        image_out = price.image_output_tokens.get(image_size.upper(), 0)
        if not image_out:  # 표에 없는 해상도 → 최대값으로 보수 추정
            image_out = max(price.image_output_tokens.values())

    if price is None:
        return ImageCost(prompt_tokens, text_out, image_out, None, "unknown_model")

    usd = (
        prompt_tokens * price.input_usd_per_mtok
        + text_out * price.output_text_usd_per_mtok
        + image_out * price.output_image_usd_per_mtok
    ) / 1_000_000
    return ImageCost(prompt_tokens, text_out, image_out, round(usd, 6), source)
