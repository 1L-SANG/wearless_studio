"""SAM 단일 추론 슬롯의 보조 우선순위 — 동시 직접 요청에서도 셀러 작업이 먼저다.

2026-08-18. 상품 등록→바로 생성 흐름에서 업로드 누끼(sam_preprocess)·코디 누끼
(matching_cutout)·톤 마스크(worn-garment)가 몇 분 안에 전부 도착한다. 슬롯이 도착순(FIFO)
이면 셀러가 화면 앞에서 기다리는 톤 마스크가 아무도 안 기다리는 전처리 뒤에 줄을 서고,
클라이언트 타임아웃(90초)을 넘겨 unavailable 로 죽는다 — 실사고의 절반이 이 줄서기였다.

운영의 주 대기열은 DB claim 순서다. 이 슬롯은 다중 레플리카의 동시 직접 요청에서도
한 번에 하나라는 메모리 상한과 우선순위를 지키는 2차 방어다.
"""

import asyncio
import inspect
from contextlib import asynccontextmanager

from sam_service import api as sapi


# ── 슬롯 자체의 순서 규칙 ────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


async def _ordering(first_pri, second_pri):
    slot = sapi.PrioritySlot()
    order = []

    async def use(name, pri):
        async with slot.hold(pri):
            order.append(name)

    async with slot.hold(5):                    # 슬롯 점유 중에 둘이 줄을 선다
        t1 = asyncio.create_task(use("first-arrival", first_pri))
        await asyncio.sleep(0)                  # first 가 확실히 먼저 줄 선다
        t2 = asyncio.create_task(use("second-arrival", second_pri))
        await asyncio.sleep(0)
    await asyncio.gather(t1, t2)
    return order


def test_a_waiting_worn_garment_request_overtakes_queued_preprocess():
    """늦게 왔어도 우선순위가 높으면(숫자가 작으면) 먼저 든다."""
    assert _run(_ordering(first_pri=10, second_pri=0)) == \
        ["second-arrival", "first-arrival"]


def test_equal_priority_stays_first_come_first_served():
    """같은 급끼리는 도착순 — 우선순위가 같은 잡들 사이의 기아를 막는다."""
    assert _run(_ordering(first_pri=10, second_pri=10)) == \
        ["first-arrival", "second-arrival"]


def test_release_with_an_empty_queue_frees_the_slot():
    """줄이 비면 슬롯이 실제로 풀린다 — 다음 단독 요청이 즉시 든다."""
    async def scenario():
        slot = sapi.PrioritySlot()
        async with slot.hold(0):
            pass
        async with slot.hold(10):               # 여기서 멈추면 슬롯이 안 풀린 것
            return True
    assert _run(scenario())


# ── 엔드포인트 배선: 어느 쪽이 급행인가 ─────────────────────────────────────

def test_worn_garment_is_declared_ahead_of_canonical_preprocess():
    """급행은 톤 마스크다 — 셀러가 화면 앞에서 기다리는 유일한 SAM 작업이다."""
    assert sapi.PRIORITY_WORN_GARMENT < sapi.PRIORITY_CANONICAL


def test_priority_slot_docs_do_not_claim_background_callers_retry():
    """PrioritySlot은 다중 레플리카 방어이며 존재하지 않는 호출자 재시도를 전제하지 않는다."""
    docs = inspect.getdoc(sapi.PrioritySlot) or ""
    assert "retried by their own callers" not in docs
    assert "database claim order" in docs


def test_the_worn_endpoint_actually_uses_the_express_priority(monkeypatch):
    recorded = []

    class _RecSlot:
        @asynccontextmanager
        async def hold(self, priority):
            recorded.append(priority)
            yield

    class _Source:
        def fetch(self, _key):
            return b"bytes", "image/jpeg"

        def head(self, _key):
            return None

        def put(self, *_a, **_k):
            return None

    async def get_segmenter(_mid):
        return object()

    from sam_service.worn_garment import WornGarmentMask
    monkeypatch.setattr(sapi, "_INFERENCE_SLOT", _RecSlot())
    monkeypatch.setattr(sapi.model_registry, "get_segmenter", get_segmenter)
    monkeypatch.setattr(sapi.worn_garment, "produce",
                        lambda *_a, **_k: WornGarmentMask(
                            png=b"p", width=4, height=4, area_frac=0.1, candidates=1,
                            plausible_candidates=1, selected_score=1.0, evidence=0.9,
                            m2m=True, source_sha256="a" * 64))

    class _Req:
        sourceKey = "k1"
        baseKey = "k2"
        clothingType = "top"
        subCategory = None
        matchingSide = None
        productKey = None

    class _Settings:
        model_id = ""

    out = asyncio.run(sapi._segment_worn_one(_Req(), _Source(), _Settings()))
    assert out["status"] == "ready", out
    assert recorded == [sapi.PRIORITY_WORN_GARMENT]
