"""서버 내부 저장은 담당 키만 보낸다. 재분석만 명시적으로 전문을 교체한다."""
import ast
import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock

from app import repo, routes
from app.workers import detail_page_job
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job


def test_feature_copy_route_only_saves_its_key(client, make_token, monkeypatch):
    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={}))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(side_effect=[
        {"fit": "slim", "sellingPoints": ["카라"]}, {"fit": "over", "featureCopy": []},
    ]))
    items = [{"point": "카라", "desc": "카라 디테일을 확인할 수 있습니다."}]
    monkeypatch.setattr(routes.feature_copy, "generate", AsyncMock(return_value=items))
    save = AsyncMock()
    monkeypatch.setattr(repo, "save_analysis", save)
    response = client.post("/v1/projects/p1/feature-copy:draft", headers=auth_headers(make_token))
    assert response.status_code == 200, response.text
    assert save.call_args.args[2] == {"featureCopy": items}


def test_detail_worker_only_saves_generated_copy_key(monkeypatch):
    items = [{"point": "카라", "desc": "카라 디테일을 확인할 수 있습니다."}]
    closure = {"version": 1, "kind": "none", "range": None, "secondary": None,
               "evidenceSlots": ["Front"], "note": None}
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"copywriting": False}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={"name": "상품", "colors": []}))
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(return_value={
        "fit": "slim", "sellingPoints": ["카라"], "frontClosure": closure}))
    monkeypatch.setattr(detail_page_job.feature_copy, "generate", AsyncMock(return_value=items))
    monkeypatch.setattr(detail_page_job, "_emit", AsyncMock())
    monkeypatch.setattr(detail_page_job, "_gen_cuts", AsyncMock(side_effect=RuntimeError("stop after analysis save")))
    monkeypatch.setattr(repo, "finalize_detail_page_failure", AsyncMock(return_value=True))
    save = AsyncMock()
    monkeypatch.setattr(repo, "save_analysis", save)
    asyncio.run(detail_page_job.run_detail_page_job(
        fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b")), worker_job()))
    assert save.await_count == 1
    assert save.call_args.args[2] == {"featureCopy": items}
    assert repo.get_analysis.return_value["frontClosure"] == closure
    assert repo.get_analysis.await_count == 1


def test_custom_deletion_does_not_send_unrelated_fit_profile():
    patch = routes._analysis_without_custom({"fit": "slim", "matchClothing": [],
                                             "fitProfile": {"matchingFit": {"clothingId": "another"}}}, "deleted")
    assert patch == {"matchClothing": []}


def test_every_internal_save_analysis_call_is_in_the_audited_inventory():
    # 저장 경로가 늘면 담당 키와 전문 교체 여부를 함께 검토해야 한다.
    expected = {"save_product": 1, "save_analysis": 1, "draft_feature_copy": 1,
                "add_custom_match_item": 2, "remove_custom_match_item": 1, "run_detail_page_job": 1}
    found = {}
    for module in (routes, detail_page_job):
        tree = ast.parse(Path(module.__file__).read_text())
        for function in (node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)):
            calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr == "save_analysis"
                     and isinstance(node.func.value, ast.Name) and node.func.value.id == "repo"]
            if calls:
                found[function.name] = len(calls)
    assert found == expected


def test_other_internal_sql_writes_keep_owned_keys_or_explicit_full_reanalysis():
    mannequin = inspect.getsource(repo.finalize_mannequin_success)
    assert "jsonb_set(analyses.payload, '{fitProfile}', excluded.payload->'fitProfile', true)" in mannequin
    reanalysis = inspect.getsource(repo.finalize_analyze_success)
    assert "재분석 시 최신 결과 반영" in reanalysis
    assert "on conflict (project_id) do update set payload = excluded.payload" in reanalysis
    for writer, key in ((repo.save_confirmed_gpt_product_evidence, "confirmedGptProductEvidence"),
                        (repo.save_detail_recommendations, "detailRecommendations"),
                        (repo.replace_garment_color_evidence, "garmentColorEvidence")):
        assert f"jsonb_set(payload, '{{{key}}}'" in inspect.getsource(writer)
    assert "payload = payload - 'garmentColorEvidence'" in inspect.getsource(repo.save_product)
