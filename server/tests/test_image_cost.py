"""이미지 실비 계산·계측 (app/agents/image_cost.py, app/image_usage.py).

가격표가 바뀌면 여기가 먼저 깨져야 한다 — 공식 요금표의 장당 단가(1K/2K $0.134, 4K $0.24)를
앵커로 박아 둔다. 계측 쪽은 "어떤 실패도 생성을 막지 않는다"가 핵심 계약이라 그것만 본다.
"""

import logging

import pytest

from app.agents.image_cost import estimate_cost
from app import image_usage

PRO = "gemini-3-pro-image"
FLASH = "gemini-3.1-flash-image"


def _usage(prompt: int, image: int, text: int = 0) -> dict:
    return {
        "promptTokenCount": prompt,
        "candidatesTokenCount": image + text,
        "candidatesTokensDetails": [
            {"modality": "IMAGE", "tokenCount": image},
            {"modality": "TEXT", "tokenCount": text},
        ],
    }


# ---------- 요금표 앵커 ----------

@pytest.mark.parametrize("size,expected", [("1K", 0.134), ("2K", 0.134), ("4K", 0.24)])
def test_pro_table_matches_official_per_image_price(size, expected):
    """usage 가 없으면 해상도별 공식 장당 단가가 나온다(입력 0 기준)."""
    cost = estimate_cost(PRO, size, None)
    assert cost.source == "table"
    assert cost.usd == pytest.approx(expected, abs=0.001)


def test_flash_is_cheaper_than_pro_at_every_size():
    for size in ("1K", "2K", "4K"):
        assert estimate_cost(FLASH, size, None).usd < estimate_cost(PRO, size, None).usd


def test_pro_1k_and_2k_cost_the_same():
    """공식 표가 같은 1,120 토큰이다 — 2K 승급은 돈이 같고 4K 만 실제로 비싸다."""
    assert estimate_cost(PRO, "1K", None).usd == estimate_cost(PRO, "2K", None).usd


def test_unknown_size_falls_back_to_most_expensive():
    """표에 없는 해상도는 과소계상하지 않는다(비용 경보가 늦게 울리는 쪽이 더 나쁘다)."""
    assert estimate_cost(PRO, "8K", None).usd == estimate_cost(PRO, "4K", None).usd


# ---------- 실제 응답 토큰 ----------

def test_usage_tokens_beat_the_table():
    cost = estimate_cost(PRO, "1K", _usage(prompt=3000, image=2000, text=50))
    assert cost.source == "usage"
    assert cost.input_tokens == 3000
    assert cost.output_image_tokens == 2000
    # 3000*2 + 50*12 + 2000*120 = 246,600 (/1M)
    assert cost.usd == pytest.approx(0.2466, abs=1e-4)


def test_usage_without_modality_split_treats_candidates_as_image():
    cost = estimate_cost(PRO, "1K", {"promptTokenCount": 0, "candidatesTokenCount": 1120})
    assert cost.source == "usage"
    assert cost.usd == pytest.approx(0.1344, abs=1e-4)


def test_candidates_beyond_the_modality_split_are_billed_as_text():
    """실측 응답 형태: details 엔 IMAGE 1120 만 오고 candidatesTokenCount 는 1223 이다."""
    cost = estimate_cost(PRO, "1K", {
        "promptTokenCount": 10, "candidatesTokenCount": 1223,
        "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}],
        "thoughtsTokenCount": 106,
    })
    assert cost.output_image_tokens == 1120
    assert cost.output_text_tokens == 1223 - 1120 + 106
    # 1120*120 + 209*12 + 10*2 = 134,400 + 2,508 + 20 (/1M)
    assert cost.usd == pytest.approx(0.136928, abs=1e-5)


def test_thinking_tokens_are_billed_as_text():
    plain = estimate_cost(PRO, "1K", {"promptTokenCount": 0, "candidatesTokenCount": 1120})
    thinking = estimate_cost(PRO, "1K", {
        "promptTokenCount": 0, "candidatesTokenCount": 1120, "thoughtsTokenCount": 500})
    assert thinking.usd > plain.usd


def test_input_images_are_billed():
    """레퍼런스 이미지를 많이 넣는 경로(마네킹+상품+모델그리드)가 비싸지는 걸 잡는다."""
    few = estimate_cost(PRO, "1K", _usage(prompt=600, image=1120))
    many = estimate_cost(PRO, "1K", _usage(prompt=4000, image=1120))
    assert many.usd > few.usd


def test_unknown_model_records_tokens_without_price():
    cost = estimate_cost("some-new-model", "1K", _usage(prompt=100, image=1000))
    assert cost.usd is None and cost.source == "unknown_model"
    assert cost.output_image_tokens == 1000


def test_malformed_usage_does_not_raise():
    for bad in ({"promptTokenCount": None}, {"candidatesTokensDetails": "nope"}, {}, None):
        assert estimate_cost(PRO, "1K", bad).usd is not None


# ---------- 계측 ----------

def test_record_without_pool_only_logs(caplog):
    image_usage.configure(pool=None, persist=True)
    with caplog.at_level(logging.INFO, logger="wearless.image_usage"):
        image_usage.record(model=PRO, image_size="4K", usage=_usage(2000, 2000),
                           latency_ms=1234)
    assert "image_usage" in caplog.text and "usd=0.24" in caplog.text


def test_record_never_raises_even_when_cost_calc_explodes(monkeypatch, caplog):
    """계측 실패가 이미지 생성을 깨뜨리면 안 된다 — 유일하게 지켜야 할 계약."""
    monkeypatch.setattr(image_usage, "estimate_cost",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    image_usage.record(model=PRO, image_size="1K", usage=None, latency_ms=1)  # 예외 없음


def test_job_scope_attaches_context_and_restores():
    assert image_usage._ctx.get().job_id is None
    with image_usage.job_scope(job_id="job-1", user_id="u-1", stage="mannequin"):
        ctx = image_usage._ctx.get()
        assert (ctx.job_id, ctx.user_id, ctx.stage) == ("job-1", "u-1", "mannequin")
        with image_usage.job_scope(stage="cut_generate"):  # 중첩은 지정 필드만 덮어쓴다
            inner = image_usage._ctx.get()
            assert inner.job_id == "job-1" and inner.stage == "cut_generate"
    assert image_usage._ctx.get().job_id is None
