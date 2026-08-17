import asyncio
import base64
from types import SimpleNamespace

import httpx
import pytest

from app.agents import gemini_image


def settings():
    return SimpleNamespace(
        gemini_api_key="test-key",
        openai_api_key="openai-test-key",
        vertex_project=None,
        vertex_location="global",
    )


def test_transport_error_is_wrapped_as_gemini_error(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError, match="ConnectError") as raised:
        asyncio.run(
            client.generate_content_image(
                "gemini-3-pro-image",
                "prompt",
                [gemini_image.InlineImage("image/png", b"image")],
                "1K",
            )
        )

    assert isinstance(raised.value.__cause__, httpx.ConnectError)


def _wire_response(monkeypatch, response):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return response

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)


def test_malformed_200_is_recorded_once_before_raising(monkeypatch):
    class Response:
        status_code = 200
        text = "not-json"

        def json(self):
            raise ValueError("bad json")

    recorded = []
    _wire_response(monkeypatch, Response())
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    with pytest.raises(gemini_image.GeminiError, match="응답 형식 오류"):
        asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
            "gemini-3-pro-image", "prompt", [], "4K"))

    assert len(recorded) == 1
    assert recorded[0]["usage"] is None
    assert recorded[0]["has_image"] is False


def test_unexpected_candidates_shape_keeps_available_usage(monkeypatch):
    usage = {"promptTokenCount": 7}
    response = SimpleNamespace(
        status_code=200,
        text="ok",
        json=lambda: {"usageMetadata": usage, "candidates": {"bad": "shape"}},
    )
    recorded = []
    _wire_response(monkeypatch, response)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    with pytest.raises(gemini_image.GeminiError, match="candidates is not a list"):
        asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
            "gemini-3-pro-image", "prompt", [], "1K"))

    assert len(recorded) == 1
    assert recorded[0]["usage"] == usage and recorded[0]["has_image"] is False


def test_text_only_200_records_usage_without_claiming_an_image(monkeypatch):
    usage = {"promptTokenCount": 10, "candidatesTokenCount": 20}
    response = SimpleNamespace(
        status_code=200,
        text="ok",
        json=lambda: {"usageMetadata": usage, "candidates": [
            {"content": {"parts": [{"text": "no image"}]}}
        ]},
    )
    recorded = []
    _wire_response(monkeypatch, response)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    with pytest.raises(gemini_image.GeminiError, match="이미지 없음"):
        asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
            "gemini-3-pro-image", "prompt", [], "1K"))

    assert len(recorded) == 1
    assert recorded[0]["usage"] == usage and recorded[0]["has_image"] is False


def test_image_200_records_exactly_once_and_returns_image(monkeypatch):
    payload = base64.b64encode(b"image-bytes").decode()
    response = SimpleNamespace(
        status_code=200,
        text="ok",
        json=lambda: {"usageMetadata": {"promptTokenCount": 1}, "candidates": [
            {"content": {"parts": [{"inlineData": {
                "data": payload, "mimeType": "image/png"
            }}]}}
        ]},
    )
    recorded = []
    _wire_response(monkeypatch, response)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    result = asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
        "gemini-3-pro-image", "prompt", [], "1K"))

    assert result.image == b"image-bytes"
    assert len(recorded) == 1 and recorded[0]["has_image"] is True


def _counting_client(monkeypatch, raise_exc, sleeps):
    calls = {"n": 0}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            raise raise_exc

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    monkeypatch.setattr(gemini_image.asyncio, "sleep", fake_sleep)
    return calls


def test_read_timeout_is_not_retried_so_a_charged_image_is_not_paid_for_twice(monkeypatch):
    """읽기 타임아웃 = 요청은 이미 도착했고 답만 늦는 상태.

    다시 보내면 프로바이더가 이미 만든(=과금된) 이미지를 한 장 더 만들고, 그 추가 호출은
    비용 원장(image_usage_events)에도 안 남는다. 그래서 재시도하지 않는다(2026-08-16 리뷰).
    """
    sleeps: list[float] = []
    calls = _counting_client(monkeypatch, httpx.ReadTimeout("slow"), sleeps)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError, match="ReadTimeout"):
        asyncio.run(
            client.generate_content_image(
                "gemini-3-pro-image",
                "prompt",
                [gemini_image.InlineImage("image/png", b"image")],
                "1K",
            )
        )

    assert calls["n"] == 1, "한 번만 보낸다 — 재시도하면 같은 컷을 두 번 과금한다"
    assert sleeps == []


def test_connect_error_is_retried_because_the_provider_never_received_the_request(monkeypatch):
    """연결 자체가 안 선 경우는 프로바이더가 요청을 받지도 못했다 — 재시도해도 이중 과금이 없다."""
    sleeps: list[float] = []
    calls = _counting_client(monkeypatch, httpx.ConnectError("offline"), sleeps)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError, match="ConnectError"):
        asyncio.run(
            client.generate_content_image(
                "gemini-3-pro-image",
                "prompt",
                [gemini_image.InlineImage("image/png", b"image")],
                "1K",
            )
        )

    assert calls["n"] == 3, "3회까지 시도"
    assert sleeps == [5, 10], "5초 → 10초 백오프"


def test_read_timeout_is_marked_billable_so_the_worker_does_not_resend(monkeypatch):
    """아래층이 "다시 보내지 않는다"고 판단한 실패는 위층도 알아야 한다.

    표식이 없으면 워커의 컷 재시도가 같은 요청을 한 번 더 보내 같은 컷을 두 번 과금한다
    (2026-08-17 리뷰 — 두 층이 서로 무효로 만들던 문제).
    """
    sleeps: list[float] = []
    _counting_client(monkeypatch, httpx.ReadTimeout("slow"), sleeps)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError) as raised:
        asyncio.run(client.generate_content_image(
            "gemini-3-pro-image", "prompt",
            [gemini_image.InlineImage("image/png", b"image")], "1K"))
    assert raised.value.billable is True


def test_connect_error_is_not_billable(monkeypatch):
    """연결이 안 섰으면 그림도 안 나왔다 — 위층이 다시 시도해도 된다."""
    sleeps: list[float] = []
    _counting_client(monkeypatch, httpx.ConnectError("offline"), sleeps)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError) as raised:
        asyncio.run(client.generate_content_image(
            "gemini-3-pro-image", "prompt",
            [gemini_image.InlineImage("image/png", b"image")], "1K"))
    assert raised.value.billable is False


@pytest.mark.parametrize(
    "status, expect_calls, expect_billable",
    [(500, 3, False), (503, 3, False), (502, 1, True), (504, 1, True), (400, 1, False)],
)
def test_5xx_retry_is_narrowed_to_backend_rejections(monkeypatch, status, expect_calls, expect_billable):
    """502/504 는 게이트웨이가 답을 못 받은 것 — 모델은 이미 그렸을 수 있어 다시 안 보낸다."""
    calls = {"n": 0}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            calls["n"] += 1
            return SimpleNamespace(status_code=status, text="err", json=lambda: {})

    async def fake_sleep(_seconds): return None
    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    monkeypatch.setattr(gemini_image.asyncio, "sleep", fake_sleep)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError) as raised:
        asyncio.run(client.generate_content_image(
            "gemini-3-pro-image", "prompt",
            [gemini_image.InlineImage("image/png", b"image")], "1K"))
    assert calls["n"] == expect_calls
    assert raised.value.billable is expect_billable


def test_billable_failures_are_written_to_the_usage_ledger(monkeypatch):
    """과금됐을 수 있는 실패도 원장에 남아야 누수액을 잴 수 있다(2026-08-17 검증)."""
    recorded = []
    monkeypatch.setattr(gemini_image.image_usage, "record",
                        lambda **kw: recorded.append(kw))
    sleeps: list[float] = []
    _counting_client(monkeypatch, httpx.ReadTimeout("slow"), sleeps)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError):
        asyncio.run(client.generate_content_image(
            "gemini-3-pro-image", "prompt",
            [gemini_image.InlineImage("image/png", b"image")], "1K"))
    assert len(recorded) == 1
    assert recorded[0]["model"] == "gemini-3-pro-image"
    assert recorded[0]["has_image"] is False
    assert recorded[0]["usage"] is None


def test_non_billable_failures_are_not_written(monkeypatch):
    """연결이 안 선 실패는 그림도 안 나왔다 — 원장에 남기면 비용이 부풀려진다."""
    recorded = []
    monkeypatch.setattr(gemini_image.image_usage, "record",
                        lambda **kw: recorded.append(kw))
    sleeps: list[float] = []
    _counting_client(monkeypatch, httpx.ConnectError("offline"), sleeps)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError):
        asyncio.run(client.generate_content_image(
            "gemini-3-pro-image", "prompt",
            [gemini_image.InlineImage("image/png", b"image")], "1K"))
    assert recorded == []


def test_openai_branch_carries_the_same_billable_contract():
    """이미지 모델을 gpt-image 로 돌려도 이중 과금 방어가 유지된다."""
    import inspect

    source = inspect.getsource(gemini_image.GeminiImageClient._openai_generate)
    assert "billable = not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))" in source
    assert "billable = res.status_code in (502, 504)" in source
    assert "_record_unbilled_failure(" in source


@pytest.mark.parametrize(
    "ratio,expected",
    [("2:3", "1024x1536"), ("3:2", "1536x1024"), ("1:1", "1024x1024")],
)
def test_openai_1k_keeps_the_existing_canvas_mapping(ratio, expected):
    assert gemini_image.GeminiImageClient._openai_size("1K", ratio) == expected


def test_openai_gpt_image_2_uses_exact_2_by_3_4k_recipe(monkeypatch):
    posted = {}
    usage = {
        "input_tokens": 9839,
        "input_tokens_details": {"text_tokens": 2376, "image_tokens": 7463},
        "output_tokens": 1372,
        "output_tokens_details": {"text_tokens": 0, "image_tokens": 1372},
    }
    response = SimpleNamespace(
        status_code=200,
        text="ok",
        json=lambda: {
            "data": [{"b64_json": base64.b64encode(b"gpt-image").decode()}],
            "usage": usage,
        },
    )

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            posted.update(url=url, **kwargs)
            return response

    recorded = []
    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    result = asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
        "gpt-image-2-2026-04-21",
        "prompt",
        [gemini_image.InlineImage("image/png", b"reference")],
        "4K",
        aspect_ratio="2:3",
    ))

    assert result.image == b"gpt-image"
    assert posted["url"] == "https://api.openai.com/v1/images/edits"
    assert posted["data"] == {
        "model": "gpt-image-2-2026-04-21",
        "prompt": "prompt",
        "size": "2336x3504",
        "quality": "medium",
        "output_format": "png",
        "n": "1",
    }
    assert "input_fidelity" not in posted["data"]
    assert len(recorded) == 1
    assert recorded[0]["image_size"] == "2336x3504"
    assert recorded[0]["usage"] == usage
    assert recorded[0]["has_image"] is True


def test_confirmed_openai_profile_preserves_original_multipart_bytes_and_mime(
    monkeypatch,
):
    posted = {}
    response = SimpleNamespace(
        status_code=200,
        text="ok",
        json=lambda: {
            "data": [{"b64_json": base64.b64encode(b"gpt-image").decode()}],
            "usage": {},
        },
    )

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            posted.update(url=url, **kwargs)
            return response

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: None)
    historical_jpeg = b"\xff\xd8historical-mannequin-bytes\xff\xd9"
    historical_png = b"\x89PNG\r\n\x1a\nhistorical-sheet-bytes"

    asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
        "gpt-image-2-2026-04-21",
        "prompt",
        [
            gemini_image.InlineImage("image/jpeg", historical_jpeg),
            gemini_image.InlineImage("image/png", historical_png),
        ],
        "4K",
        aspect_ratio="2:3",
        openai_preserve_input_bytes=True,
    ))

    assert posted["files"] == [
        ("image[]", ("ref0.jpg", historical_jpeg, "image/jpeg")),
        ("image[]", ("ref1.png", historical_png, "image/png")),
    ]


def test_confirmed_openai_profile_rejects_unsupported_mime_before_network(
    monkeypatch,
):
    calls = {"n": 0}

    class Client:
        def __init__(self, *args, **kwargs):
            calls["n"] += 1

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)

    with pytest.raises(gemini_image.GeminiError, match="input MIME unsupported"):
        asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
            "gpt-image-2-2026-04-21",
            "prompt",
            [gemini_image.InlineImage("image/gif", b"gif")],
            "4K",
            aspect_ratio="2:3",
            openai_preserve_input_bytes=True,
        ))

    assert calls["n"] == 0


def test_openai_malformed_200_keeps_available_usage_in_ledger(monkeypatch):
    usage = {"input_tokens": 10, "output_tokens": 20}
    response = SimpleNamespace(
        status_code=200,
        text="ok",
        json=lambda: {"data": [], "usage": usage},
    )
    recorded = []
    _wire_response(monkeypatch, response)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    with pytest.raises(gemini_image.GeminiError, match="OpenAI 200 응답 형식 오류"):
        asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
            "gpt-image-2", "prompt", [], "4K", aspect_ratio="2:3"))

    assert len(recorded) == 1
    assert recorded[0]["usage"] == usage
    assert recorded[0]["has_image"] is False


@pytest.mark.parametrize(
    "error,expected_billable",
    [(httpx.ConnectError("offline"), False), (httpx.ReadTimeout("slow"), True)],
)
def test_openai_transport_is_one_shot_without_hidden_retry(
    monkeypatch, error, expected_billable,
):
    calls = {"n": 0}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            raise error

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: None)

    with pytest.raises(gemini_image.GeminiError) as raised:
        asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
            "gpt-image-2-2026-04-21",
            "prompt",
            [gemini_image.InlineImage("image/png", b"reference")],
            "4K",
            aspect_ratio="2:3",
        ))

    assert calls["n"] == 1
    assert raised.value.billable is expected_billable
