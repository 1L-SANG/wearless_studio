"""Seller sentences and freshly generated two-line part descriptions stay separate."""
import asyncio

import pytest

from app.agents import feature_copy, page_part_copy
from conftest import make_settings


PART = "단추가 더해진 여밈으로\n차분한 옷차림을 완성해요"
SELLER = "단추 디테일을 확인할 수 있습니다."


def test_same_label_keeps_dictionary_seller_copy_and_generates_part_in_one_completion(monkeypatch):
    calls = []

    async def complete(settings, prompt, schema, *, timeout_seconds=None):
        calls.append((prompt, schema, timeout_seconds))
        return ({"featureCopy": [{"point": "잔잔한 워싱", "desc": "워싱 색감이 은은하게 드러납니다."}],
                 "partDescriptionCopy": [{"point": "단추", "desc": PART}]}, "offline-fake")

    monkeypatch.setattr(feature_copy, "complete_json", complete)
    result = asyncio.run(page_part_copy.generate(
        make_settings(), ["단추", "잔잔한 워싱"], ["단추", "단추"], {}, {}, timeout_seconds=7.5))

    assert result == {
        "featureCopy": [{"point": "단추", "desc": SELLER},
                        {"point": "잔잔한 워싱", "desc": "워싱 색감이 은은하게 드러납니다."}],
        "partDescriptionCopy": [{"point": "단추", "desc": PART}],
    }
    assert len(calls) == 1
    prompt, schema, timeout = calls[0]
    assert timeout == 7.5
    assert prompt.split("SELLER HIGHLIGHTS (data):\n", 1)[1].split("PAGE PARTS (data):", 1)[0].strip() == "- 잔잔한 워싱"
    assert prompt.split("PAGE PARTS (data):\n", 1)[1].strip() == "- 단추"
    assert set(schema["required"]) == {"featureCopy", "partDescriptionCopy"}


def test_dictionary_known_part_without_seller_still_requests_fresh_copy(monkeypatch):
    calls = []

    async def complete(*args, **kwargs):
        calls.append(1)
        return ({"featureCopy": [], "partDescriptionCopy": [{"point": "단추", "desc": PART}]}, "offline-fake")

    monkeypatch.setattr(feature_copy, "complete_json", complete)
    result = asyncio.run(page_part_copy.generate(make_settings(), [], ["단추"], {}, {}))
    assert result == {"featureCopy": [], "partDescriptionCopy": [{"point": "단추", "desc": PART}]}
    assert calls == [1]


@pytest.mark.parametrize("bad", [None, [], "wrong shape", {"featureCopy": None, "partDescriptionCopy": {}},
                                   {"partDescriptionCopy": [{"point": "단추", "desc": SELLER}]}])
def test_malformed_output_retains_only_seller_dictionary_without_retry(monkeypatch, bad):
    calls = []

    async def complete(*args, **kwargs):
        calls.append(1)
        return bad, "offline-fake"

    monkeypatch.setattr(feature_copy, "complete_json", complete)
    result = asyncio.run(page_part_copy.generate(make_settings(), ["단추"], ["단추"], {}, {}))
    assert result == {"featureCopy": [{"point": "단추", "desc": SELLER}], "partDescriptionCopy": []}
    assert calls == [1]


def test_provider_failure_never_substitutes_seller_dictionary_for_parts(monkeypatch):
    calls = []

    async def complete(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("offline failure")

    monkeypatch.setattr(feature_copy, "complete_json", complete)
    assert asyncio.run(page_part_copy.generate(make_settings(), ["단추"], ["단추"], {}, {})) == {
        "featureCopy": [{"point": "단추", "desc": SELLER}], "partDescriptionCopy": []}
    assert calls == [1]


def test_no_parts_and_only_known_seller_features_need_no_completion(monkeypatch):
    calls = []

    async def complete(*args, **kwargs):
        calls.append(1)
        return {}, "offline-fake"

    monkeypatch.setattr(feature_copy, "complete_json", complete)
    assert asyncio.run(page_part_copy.generate(make_settings(), ["단추"], [], {}, {})) == {
        "featureCopy": [{"point": "단추", "desc": SELLER}], "partDescriptionCopy": []}
    assert asyncio.run(page_part_copy.generate(make_settings(), [], [], {}, {})) == {
        "featureCopy": [], "partDescriptionCopy": []}
    assert calls == []


def test_no_parts_delegates_to_the_unchanged_seller_request_contract(monkeypatch):
    calls = []

    async def complete(_settings, prompt, schema, *, timeout_seconds=None):
        calls.append((prompt, schema))
        return {"items": [{"point": "잔잔한 워싱", "desc": "워싱 색감이 은은하게 드러납니다."}]}, "offline-fake"

    monkeypatch.setattr(feature_copy, "complete_json", complete)
    assert asyncio.run(page_part_copy.generate(make_settings(), ["잔잔한 워싱"], [], {}, {})) == {
        "featureCopy": [{"point": "잔잔한 워싱", "desc": "워싱 색감이 은은하게 드러납니다."}],
        "partDescriptionCopy": []}
    assert len(calls) == 1 and set(calls[0][1]["properties"]) == {"items"}


@pytest.mark.parametrize("desc", [
    SELLER, PART.replace("\n", " "), PART + "\n한 줄 더 있어요", "\n" + PART,
    "단추가 더해진 여밈이에요\n차분한 옷차림을 완성해요",
    "단추가 더해진 여밈으로\n차분한 옷차림을 완성합니다.",
    "가" * 23 + "로\n차분한 옷차림을 완성해요",
    "단추가 더해진 여밈으로\n" + "가" * 23 + "해요",
    "통기성이 좋은 소재로\n오래 입어도 쾌적해요",
    "도톰하게 짜인 원단으로\n바람을 막아 따뜻해요",
    "완벽한 단추 여밈으로\n차분한 옷차림을 완성해요",
    "단추가 더해진 여밈으로\n부드러운 촉감이 편안해요",
    "단추가 더해진 여밈으로\n100% 만족할 수 있어요",
    "<b>단추가 더해진 여밈으로</b>\n차분한 옷차림을 완성해요",
    "단추가 더해진\t여밈으로\n차분한 옷차림을 완성해요",
    "단추가 더해진 여밈으로\n차분한\u200b 옷차림을 완성해요",
    "단추가 더해진 여밈으로\n차분한 옷차림을 완성해요!", 123, None,
    "바람이 안 들어오는 원단으로\n쌀쌀한 날에도 입기 좋아요",
    "보들보들한 소재로\n맨살에 입어도 좋아요",
    "단추가 있어요. 여밈으로\n차분한 옷차림을 완성해요",
])
def test_parts_reject_wrong_line_structure_register_claims_or_unsafe_text(desc):
    assert page_part_copy.validate_parts([{"point": "단추", "desc": desc}], ["단추"]) == {}


def test_parts_keep_exact_newline_and_map_sanitized_label_back_to_original():
    rows = [{"point": "롤업 소매", "desc": "접어 올린 소매 마감으로\n손목이 드러나 산뜻해요"},
            {"point": "미요청", "desc": PART}, None, {"point": [], "desc": PART}]
    assert page_part_copy.validate_parts(rows, ["롤업  소매"]) == {
        "롤업  소매": "접어 올린 소매 마감으로\n손목이 드러나 산뜻해요"}


@pytest.mark.parametrize("ending", ["옷차림이 단정해 보여요", "옷의 윤곽이 깔끔하게 잡혀요",
                                     "옷차림에 포인트가 돼요", "옆모습의 포인트예요"])
def test_parts_accept_natural_contracted_polite_endings(ending):
    desc = f"단추가 더해진 여밈으로\n{ending}"
    assert page_part_copy.validate_parts([{"point": "단추", "desc": desc}], ["단추"]) == {"단추": desc}


def test_partition_does_not_cross_contaminate_same_label_or_clear_missing_saved_rows():
    stored = {"featureCopy": [{"point": "기존", "desc": "기존 특징입니다."}],
              "partDescriptionCopy": [{"point": "기존", "desc": "기존 부위입니다."}]}
    generated = {"featureCopy": [{"point": "단추", "desc": SELLER}],
                 "partDescriptionCopy": [{"point": "단추", "desc": PART}]}
    assert page_part_copy.partition_copy(generated, ["단추"], ["단추"], stored) == {
        "featureCopy": [stored["featureCopy"][0], {"point": "단추", "desc": SELLER}],
        "partDescriptionCopy": [stored["partDescriptionCopy"][0], {"point": "단추", "desc": PART}],
    }
