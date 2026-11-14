"""Part copy stays in the existing one-call job path, separate from seller features."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app import repo
from app.workers import detail_page_job as dpj
from app.agents import feature_copy, page_part_copy
from conftest import fake_worker_app, make_settings, worker_job


def board():
    return [
        {"id": "worn", "taxonomyVersion": 4, "source": "mine", "ownImages": ["/worn.jpg"],
         "sectionId": "wear", "pageSectionKey": "emo.wear_1", "pageSectionKind": "wear",
         "pageSectionUsage": "styling", "pageDesignId": "P02-D01", "slotKey": None},
        {"id": "points", "taxonomyVersion": 4, "source": "text", "sectionId": "points",
         "pageSectionKey": "emo.points", "pageSectionKind": "points", "pageDesignId": "PT-BELOW-CARD-01",
         "cutType": None, "contentRole": None, "sectionRole": None, "slotKey": None,
         "pointItems": [
             {"kind": "detail", "sourceBlockId": "detail"},
             {"kind": "wornCrop", "sourceBlockId": "worn", "bodyPart": "shoulder", "label": "드롭 숄더",
              "cropRegion": [0.1, 0.1, 0.4, 0.2]},
             {"kind": "wornCrop", "sourceBlockId": "gone", "bodyPart": "length", "label": "삭제된 기장",
              "cropRegion": [0.1, 0.1, 0.4, 0.2]},
         ]},
        {"id": "detail", "taxonomyVersion": 4, "source": "ai", "sectionId": "points",
         "pageSectionKey": "emo.points", "pageSectionKind": "points", "pageDesignId": "PT-BELOW-CARD-01",
         "slotKey": "points.detail1", "cutType": "product", "shot": "detail", "direction": "front",
         "detailTargetId": "button"},
    ]


def analysis():
    return {"sellingPoints": ["단추", "유연한 원단", "단추"], "fit": "regular",
            "detailRecommendations": {"candidates": [
                {"id": "button", "label": "단추"}, {"id": "unselected", "label": "선택하지 않은 포켓"}]},
            "featureCopy": [{"point": "유연한 원단", "desc": "저장된 원단 설명입니다."}],
            "partDescriptionCopy": [{"point": "드롭 숄더", "desc": "저장된 어깨 설명입니다."}]}


def run_job(monkeypatch, *, enabled=True, result=None, failure=False, persist_failure=False, real_generator=False):
    original = analysis()
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"copywriting": enabled}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={"name": "셔츠", "colors": []}))
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=board()))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(return_value=original))
    if real_generator:
        generate = AsyncMock(return_value=(result or {}, "fake-provider"))
        monkeypatch.setattr(feature_copy, "complete_json", generate)
    else:
        generate = AsyncMock(return_value=result or {},
                             side_effect=RuntimeError("copy unavailable") if failure else None)
        monkeypatch.setattr(dpj.page_part_copy, "generate", generate)
    monkeypatch.setattr(dpj, "_emit", AsyncMock())
    monkeypatch.setattr(dpj, "_gen_cuts", AsyncMock(return_value=([
        {"blockId": "detail", "imageUrl": "/detail.jpg", "width": 600, "height": 900}], [], 0, [], None, [])))
    saved = AsyncMock(side_effect=RuntimeError("save unavailable") if persist_failure else None)
    monkeypatch.setattr(repo, "save_analysis", saved)
    monkeypatch.setattr(repo, "finalize_detail_page_success", AsyncMock(return_value={"editor_blocks": [], "available": 1}))
    failure_save = AsyncMock(return_value=True)
    monkeypatch.setattr(repo, "finalize_detail_page_failure", failure_save)
    assembled = {}

    def assemble(_storyboard, _cuts, _copy, _product, copywriting, **kwargs):
        assembled.update(analysis=deepcopy(kwargs["analysis"]), copywriting=copywriting)
        return []

    monkeypatch.setattr(dpj.page_assembler, "assemble", assemble)
    asyncio.run(dpj.run_detail_page_job(
        fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b")), worker_job()))
    assert not failure_save.called, failure_save.call_args
    return generate, saved, assembled, original


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("persist_failure", [False, True])
def test_one_request_names_only_selected_parts_and_updates_current_assembly(monkeypatch, persist_failure, enabled):
    seller = {"point": "단추", "desc": "단추 여밈을 확인할 수 있습니다."}
    button = {"point": "단추", "desc": "단추가 더해진 여밈으로\n차분한 옷차림을 완성해요"}
    shoulder = {"point": "드롭 숄더", "desc": "어깨 아래로 내려온 절개라\n상체 윤곽이 한결 느긋해요"}
    generated = {"featureCopy": [seller], "partDescriptionCopy": [button, shoulder,
                 {"point": "선택하지 않은 포켓", "desc": "반환되어도 버릴 문구입니다."}]}
    call, saved, assembled, original = run_job(monkeypatch, enabled=enabled, result=generated, persist_failure=persist_failure)
    assert call.await_count == 1
    assert call.call_args.args[1:3] == (["단추", "유연한 원단"], ["단추", "드롭 숄더"])
    assert call.call_args.kwargs["timeout_seconds"] == 120
    expected_features = [{"point": "유연한 원단", "desc": "저장된 원단 설명입니다."}, seller]
    expected_parts = [shoulder, button]
    assert saved.call_args.args[2] == {"featureCopy": expected_features, "partDescriptionCopy": expected_parts}
    assert assembled["analysis"]["featureCopy"] == expected_features
    assert assembled["analysis"]["partDescriptionCopy"] == expected_parts
    assert assembled["copywriting"] is True
    assert original == analysis(), "input analysis snapshot must not be mutated"


def test_partial_response_keeps_missing_stored_part_and_seller_copy(monkeypatch):
    seller = {"point": "단추", "desc": "단추 여밈을 확인할 수 있습니다."}
    part = {"point": "단추", "desc": "단추가 더해진 여밈으로\n차분한 옷차림을 완성해요"}
    generated = {"featureCopy": [seller], "partDescriptionCopy": [part]}
    call, saved, assembled, _ = run_job(monkeypatch, result=generated)
    assert call.await_count == 1
    assert assembled["analysis"]["partDescriptionCopy"] == [analysis()["partDescriptionCopy"][0], part]
    assert assembled["analysis"]["featureCopy"][0] == analysis()["featureCopy"][0]
    assert set(saved.call_args.args[2]) == {"featureCopy", "partDescriptionCopy"}


@pytest.mark.parametrize("failure", [False, True])
def test_empty_or_failed_response_keeps_both_stored_fields(monkeypatch, failure):
    call, saved, assembled, _ = run_job(monkeypatch, failure=failure)
    assert call.await_count == 1
    assert not saved.called
    assert assembled["analysis"]["featureCopy"] == analysis()["featureCopy"]
    assert assembled["analysis"]["partDescriptionCopy"] == analysis()["partDescriptionCopy"]


@pytest.mark.parametrize("enabled", [False, True])
def test_real_feature_generator_uses_one_fake_provider_call_for_all_unknown_labels(monkeypatch, enabled):
    result = {"featureCopy": [{"point": "유연한 원단", "desc": "원단의 유연한 형태를 확인할 수 있습니다."}],
              "partDescriptionCopy": [
                  {"point": "단추", "desc": "단추가 더해진 여밈으로\n차분한 옷차림을 완성해요"},
                  {"point": "드롭 숄더", "desc": "어깨 아래로 내려온 절개라\n상체 윤곽이 한결 느긋해요"}]}
    provider, saved, assembled, _ = run_job(monkeypatch, enabled=enabled, result=result, real_generator=True)
    assert provider.await_count == 1
    prompt = provider.call_args.args[1]
    assert "- 유연한 원단" in prompt and "- 드롭 숄더" in prompt
    assert prompt.split("PAGE PARTS (data):\n", 1)[1].strip() == "- 단추\n- 드롭 숄더"
    assert "선택하지 않은 포켓" not in prompt and "삭제된 기장" not in prompt
    assert {item["point"] for item in assembled["analysis"]["featureCopy"]} == {"유연한 원단", "단추"}
    assert {item["point"] for item in assembled["analysis"]["partDescriptionCopy"]} == {"단추", "드롭 숄더"}
    seller_button = next(item["desc"] for item in assembled["analysis"]["featureCopy"] if item["point"] == "단추")
    part_button = next(item["desc"] for item in assembled["analysis"]["partDescriptionCopy"] if item["point"] == "단추")
    assert seller_button == "단추 디테일을 확인할 수 있습니다."
    assert part_button == result["partDescriptionCopy"][0]["desc"]
    assert seller_button != part_button
    assert saved.await_count == 1


def test_part_labels_follow_selected_card_order_and_append_legacy_detail_photos():
    storyboard = board()
    storyboard[-1]["detailTargetId"] = "unselected"
    extra = {**storyboard[-1], "id": "extra", "slotKey": "points.detail2", "detailTargetId": "button"}
    storyboard.append(extra)
    assert page_part_copy.part_labels(storyboard, analysis()) == ["선택하지 않은 포켓", "드롭 숄더", "단추"]
    storyboard[1].pop("pointItems")
    assert page_part_copy.part_labels(storyboard, analysis()) == ["선택하지 않은 포켓", "단추"]


def test_missing_candidate_and_removed_worn_source_do_not_request_unrelated_labels():
    storyboard = board()
    storyboard[-1]["detailTargetId"] = "missing-candidate"
    storyboard.pop(0)
    assert page_part_copy.part_labels(storyboard, analysis()) == []


def test_bad_or_unrequested_response_rows_cannot_clear_saved_descriptions(monkeypatch):
    malformed = [None, {"point": [], "desc": "bad"}, {"point": "단추", "desc": " "},
                 {"point": "드롭 숄더", "desc": None}, {"point": "미요청 부위", "desc": "버릴 설명입니다."}]
    _call, saved, assembled, _ = run_job(monkeypatch, result={"featureCopy": malformed, "partDescriptionCopy": malformed})
    assert not saved.called
    assert assembled["analysis"]["featureCopy"] == analysis()["featureCopy"]
    assert assembled["analysis"]["partDescriptionCopy"] == analysis()["partDescriptionCopy"]
