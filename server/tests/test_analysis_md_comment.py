"""셀러 선택 메모는 저장만 하고 생성 프롬프트에는 넣지 않는다."""
import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from app.agents import copywriter, copy_qc, feature_copy, prompts, selling_points
from app.workers.detail_page_job import _fallback_product_name
from conftest import auth_headers
from test_analysis_server_owned_fields import _Conn


@pytest.fixture
def saved_analysis(monkeypatch):
    conn = _Conn({"mdComment": "기존 메모", "sellingPoints": ["기존 특징"]})

    @contextlib.asynccontextmanager
    async def connection(_request):
        yield conn

    monkeypatch.setattr(routes, "get_conn", connection)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={}))
    return conn


def save(client, make_token, payload, merge):
    return client.patch(
        f"/v1/projects/p1/analysis?merge={int(merge)}",
        headers=auth_headers(make_token), json=payload,
    )


@pytest.mark.parametrize("merge", [False, True])
@pytest.mark.parametrize("value", [123, False, [], {}, ["메모"]])
def test_rejects_non_string_before_writing(client, make_token, saved_analysis, merge, value):
    response = save(client, make_token, {"mdComment": value}, merge)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_md_comment"
    assert response.json()["error"]["message"] == "MD 코멘트를 다시 확인해 주세요."
    assert saved_analysis.calls == []


def test_replace_rejects_null(client, make_token, saved_analysis):
    response = save(client, make_token, {"mdComment": None}, False)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_md_comment"
    assert saved_analysis.calls == []


@pytest.mark.parametrize("merge", [False, True])
def test_rejects_more_than_300_trimmed_characters(client, make_token, saved_analysis, merge):
    response = save(client, make_token, {"mdComment": "  " + "옷" * 301 + "\n"}, merge)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "md_comment_too_long"
    assert response.json()["error"]["message"] == "MD 코멘트는 300자까지 쓸 수 있어요."
    assert saved_analysis.calls == []


@pytest.mark.parametrize("merge", [False, True])
@pytest.mark.parametrize("value, expected", [
    ("  가을 니트\n소매를 접어 입어요.  ", "가을 니트\n소매를 접어 입어요."),
    ("\n\t  ", ""),
    ("  " + "옷" * 300 + "  ", "옷" * 300),
])
def test_saves_trimmed_string_and_allows_300(client, make_token, saved_analysis, merge, value, expected):
    response = save(client, make_token, {"mdComment": value}, merge)
    assert response.status_code == 200, response.text
    assert response.json()["mdComment"] == expected
    assert saved_analysis.saved["mdComment"] == expected


def test_merge_null_removes_only_md_comment(client, make_token, saved_analysis):
    response = save(client, make_token, {"mdComment": None}, True)
    assert response.status_code == 200, response.text
    assert "mdComment" not in response.json()
    assert saved_analysis.saved == {"sellingPoints": ["기존 특징"]}


def test_unrelated_merge_keeps_md_comment(client, make_token, saved_analysis):
    response = save(client, make_token, {"sellingPoints": ["다듬은 특징"]}, True)
    assert response.status_code == 200, response.text
    assert response.json()["mdComment"] == "기존 메모"


def test_replacement_keeps_existing_rule_for_omitted_seller_keys(client, make_token, saved_analysis):
    response = save(client, make_token, {"fit": "regular"}, False)
    assert response.status_code == 200, response.text
    assert "mdComment" not in response.json()
    assert "sellingPoints" not in response.json()


MEMO = "MD_ONLY_셀러가쓴메모_이문구는생성에넘기지않음"
PRODUCT = {"name": "가을 니트", "clothingType": "top", "colors": []}
ANALYSIS = {"sellingPoints": ["라운드넥"], "aiSuggestedPoints": ["라운드넥"],
            "materials": [{"name": "코튼", "ratio": 100}], "subCategory": "knit"}
BLOCKS = [{"id": "b1", "source": "ai", "cutType": "styling", "contentRole": "coordination", "shot": "full"}]


@pytest.mark.parametrize("builder", [
    lambda analysis: copywriter.build_prompt(BLOCKS, PRODUCT, analysis),
    lambda analysis: feature_copy.build_prompt(analysis["sellingPoints"], PRODUCT, analysis),
    lambda analysis: copy_qc.build_prompt([{"textId": "b1:headline", "text": "가을 니트"}], analysis),
    lambda analysis: _fallback_product_name(PRODUCT, analysis),
])
def test_copy_and_product_name_ignore_md_comment(builder):
    original = builder(ANALYSIS)
    with_memo = builder({**ANALYSIS, "mdComment": MEMO})
    assert with_memo == original
    assert MEMO not in with_memo


@pytest.mark.parametrize("canon", ["off", "on"])
def test_image_prompt_and_selling_point_guidance_ignore_md_comment(canon):
    context = prompts.MannequinPromptContext(clothing_type="top", product_count=1, base_gender="women")
    template = "Make a ${clothingType} photo for ${baseGender}. ${imageManifest}"
    original = prompts.render_mannequin_prompt(template, context, PRODUCT, ANALYSIS, seller_canon=canon)
    with_memo = prompts.render_mannequin_prompt(template, context, PRODUCT,
                                               {**ANALYSIS, "mdComment": MEMO}, seller_canon=canon)
    assert with_memo == original
    assert MEMO not in with_memo
    assert selling_points.canonicalize(ANALYSIS["sellingPoints"]) == selling_points.canonicalize(
        {**ANALYSIS, "mdComment": MEMO}["sellingPoints"])


def test_generation_modules_do_not_read_the_storage_only_key():
    app = Path(__file__).resolve().parents[1] / "app"
    paths = list((app / "agents").glob("*.py")) + list((app / "workers").glob("*.py"))
    paths += list((app / "services").glob("matching*.py"))
    for path in paths:
        assert "mdComment" not in path.read_text(), path.name
