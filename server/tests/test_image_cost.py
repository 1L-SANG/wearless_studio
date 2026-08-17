"""이미지 실비 계산·계측 (app/agents/image_cost.py, app/image_usage.py).

가격표가 바뀌면 여기가 먼저 깨져야 한다 — 공식 요금표의 장당 단가(1K/2K $0.134, 4K $0.24)를
앵커로 박아 둔다. 계측 쪽은 "어떤 실패도 생성을 막지 않는다"가 핵심 계약이라 그것만 본다.
"""

import asyncio
import logging

import pytest

from app.agents.image_cost import estimate_cost
from app import image_usage

PRO = "gemini-3-pro-image"
FLASH = "gemini-3.1-flash-image"
GPT_IMAGE_2 = "gpt-image-2"


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


def test_text_only_response_without_split_does_not_count_image_tokens():
    cost = estimate_cost(
        PRO, "1K", {"promptTokenCount": 10, "candidatesTokenCount": 80},
        has_image=False,
    )
    assert cost.source == "usage"
    assert cost.output_image_tokens == 0
    assert cost.output_text_tokens == 80
    assert cost.usd == pytest.approx((10 * 2 + 80 * 12) / 1_000_000)


def test_explicit_image_tokens_are_kept_even_when_no_inline_image_was_delivered():
    cost = estimate_cost(PRO, "1K", _usage(prompt=10, image=1120), has_image=False)
    assert cost.source == "usage"
    assert cost.output_image_tokens == 1120
    assert cost.usd is not None


def test_no_image_and_no_usage_is_unknown_not_full_image_table_price():
    cost = estimate_cost(PRO, "4K", None, has_image=False)
    assert cost.source == "unavailable_no_image"
    assert cost.usd is None
    assert cost.output_image_tokens == 0


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


def test_gpt_image_2_actual_usage_matches_the_sealed_experiment_receipt():
    """선정 실험의 실제 Images API usage: text 2,376 + image 7,463 + output 1,372."""
    cost = estimate_cost(GPT_IMAGE_2, "1024x1536", {
        "input_tokens": 9839,
        "input_tokens_details": {"text_tokens": 2376, "image_tokens": 7463},
        "output_tokens": 1372,
        "total_tokens": 11211,
        "output_tokens_details": {"image_tokens": 1372, "text_tokens": 0},
    })
    assert cost.source == "usage"
    assert cost.input_tokens == 9839
    assert cost.output_image_tokens == 1372
    # 2376*$5/M + 7463*$8/M + 1372*$30/M
    assert cost.usd == pytest.approx(0.112744, abs=1e-9)


def test_gpt_image_2_snapshot_uses_the_same_rate_card():
    usage = {
        "input_tokens": 9839,
        "input_tokens_details": {"text_tokens": 2376, "image_tokens": 7463},
        "output_tokens": 1372,
        "output_tokens_details": {"image_tokens": 1372},
    }
    assert estimate_cost("gpt-image-2-2026-04-21", "2336x3504", usage).usd == 0.112744


def test_gpt_image_2_applies_cache_rates_only_with_modality_evidence():
    cost = estimate_cost(GPT_IMAGE_2, "2336x3504", {
        "input_tokens": 3000,
        "input_tokens_details": {
            "text_tokens": 1000,
            "image_tokens": 2000,
            "text_tokens_details": {"cached_tokens": 400},
            "image_tokens_details": {"cached_tokens": 500},
        },
        "output_tokens": 100,
        "output_tokens_details": {"image_tokens": 100},
    })
    # 600*$5 + 400*$1.25 + 1500*$8 + 500*$2 + 100*$30 = $0.0195
    assert cost.usd == pytest.approx(0.0195, abs=1e-9)


def test_gpt_image_2_without_usage_is_not_given_a_made_up_4k_table_price():
    cost = estimate_cost(GPT_IMAGE_2, "2336x3504", None)
    assert cost.source == "unavailable_usage"
    assert cost.usd is None


@pytest.mark.parametrize("usage", [
    {
        "input_tokens": -1,
        "input_tokens_details": {"text_tokens": 0, "image_tokens": 1},
        "output_tokens": 1,
        "output_tokens_details": {"image_tokens": 1},
    },
    {
        "input_tokens": 10,
        "input_tokens_details": {"text_tokens": 4, "image_tokens": 5},
        "output_tokens": 1,
        "output_tokens_details": {"image_tokens": 1},
    },
    {
        "input_tokens": 10,
        "input_tokens_details": {"text_tokens": 4, "image_tokens": 6},
        "output_tokens": 2,
        "output_tokens_details": {"image_tokens": 1},
    },
    {
        "input_tokens": 10,
        "input_tokens_details": {
            "text_tokens": 4,
            "image_tokens": 6,
            "text_tokens_details": {"cached_tokens": 5},
        },
        "output_tokens": 1,
        "output_tokens_details": {"image_tokens": 1},
    },
])
def test_gpt_image_2_invalid_usage_fails_closed_instead_of_underbilling(usage):
    cost = estimate_cost(GPT_IMAGE_2, "2336x3504", usage)
    assert cost.source == "invalid_usage"
    assert cost.usd is None
    assert (cost.input_tokens, cost.output_text_tokens, cost.output_image_tokens) == (0, 0, 0)


def test_gpt_image_2_ambiguous_aggregate_cache_fails_closed():
    cost = estimate_cost(GPT_IMAGE_2, "2336x3504", {
        "input_tokens": 10,
        "input_tokens_details": {
            "text_tokens": 4,
            "image_tokens": 6,
            "cached_tokens": 3,
        },
        "output_tokens": 1,
        "output_tokens_details": {"image_tokens": 1},
    })
    assert cost.source == "invalid_usage"
    assert cost.usd is None


def test_gpt_image_2_zero_aggregate_cache_is_not_ambiguous():
    cost = estimate_cost(GPT_IMAGE_2, "2336x3504", {
        "input_tokens": 10,
        "input_tokens_details": {
            "text_tokens": 4,
            "image_tokens": 6,
            "cached_tokens": 0,
        },
        "output_tokens": 1,
        "output_tokens_details": {"image_tokens": 1},
    })
    assert cost.source == "usage"
    assert cost.usd == pytest.approx((4 * 5 + 6 * 8 + 1 * 30) / 1_000_000)


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


def test_drain_waits_for_pending_usage_tasks():
    finished = []

    async def scenario():
        async def insert():
            await asyncio.sleep(0)
            finished.append(True)

        task = asyncio.create_task(insert())
        image_usage._tasks.add(task)
        task.add_done_callback(image_usage._tasks.discard)
        await image_usage.drain(timeout_seconds=1)
        assert task.done()

    asyncio.run(scenario())
    assert finished == [True]
    assert not image_usage._tasks
