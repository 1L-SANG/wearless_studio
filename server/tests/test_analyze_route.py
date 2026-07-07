"""PL-1 분석 라우트·워커 오케스트레이션 테스트 (pl1_analysis_agent_spec §10.2).

프로젝트 관례(순수 함수 + DB-less)에 따라 SQL은 repo 몽키패치로 대체하고,
라우트 분기·워커 흐름(재시도·게이트·finalize 인자)을 검증한다.
SQL 레벨(지문 가드·병합 upsert)은 live smoke + 물리 검증이 담당(§10.3·§11).
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.repo as repo
from app.agents import analysis
from app.agents.gemini_text import GeminiJsonResult, GeminiTextError
from app.main import create_app
from app.workers.analyze_job import run_analyze_job
from tests.conftest import make_settings


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


class FakePool:
    def connection(self):
        @asynccontextmanager
        async def _cm():
            yield SimpleNamespace(commit=_noop)
        return _cm()


async def _noop(*a, **k):
    return None


def _img(id, slot):
    return {"id": id, "slot": slot, "src": f"/v1/assets/{id}/file"}


PRODUCT = {
    "id": "prd1", "project_id": "prj1", "name": "골지 니트", "clothing_type": None,
    "colors": [
        {"id": "col_base", "isBase": True, "swatchId": None,
         "images": [_img("a1", "Front"), _img("a2", "Detail")]},
        {"id": "col_2", "isBase": False, "swatchId": None, "images": [_img("b1", "Front")]},
    ],
}


@pytest.fixture()
def rig(keypair, monkeypatch):
    """TestClient + fake pool + repo 몽키패치 기본 세트. 테스트가 개별 함수를 덮어쓴다."""
    private_key, public_key = keypair
    app = create_app(make_settings())
    app.state.jwt_key_resolver = lambda token: public_key
    app.state.pool = FakePool()

    calls = {"create_job": []}

    async def get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def get_product(conn, project_id):
        return dict(PRODUCT)

    async def get_analysis(conn, project_id):
        return {}

    async def get_last_analyze_fingerprint(conn, project_id):
        return None

    async def get_account(conn, user_id):
        return {"name": "u", "avatar": "", "credits": 42, "plan": "basic"}

    async def create_job(conn, **kwargs):
        calls["create_job"].append(kwargs)
        return {"id": "job-1", "status": "pending"}, True

    async def count_recent_analyze_jobs(conn, user_id, window_seconds):
        return 0  # 기본: 상한 미도달 (테스트가 개별 override)

    async def has_active_analyze_job(conn, user_id, project_id):
        return False  # 기본: 진행 중 job 없음 (테스트가 개별 override)

    for name, fn in [
        ("get_project", get_project), ("get_product", get_product),
        ("get_analysis", get_analysis),
        ("get_last_analyze_fingerprint", get_last_analyze_fingerprint),
        ("get_account", get_account), ("create_job", create_job),
        ("count_recent_analyze_jobs", count_recent_analyze_jobs),
        ("has_active_analyze_job", has_active_analyze_job),
    ]:
        monkeypatch.setattr(repo, name, fn)

    return SimpleNamespace(app=app, client=TestClient(app), calls=calls,
                           monkeypatch=monkeypatch)


# ── 라우트 (§5.1·§5.2) ──


def test_analyze_requires_front(rig, make_token, monkeypatch):
    async def no_front(conn, project_id):
        return {**PRODUCT, "colors": [{"id": "c", "isBase": True,
                                       "images": [_img("x", "Back")]}]}
    monkeypatch.setattr(repo, "get_product", no_front)
    res = rig.client.post("/v1/projects/prj1/analysis:analyze", headers=_auth(make_token))
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "missing_front_photo"
    assert rig.calls["create_job"] == []


def test_analyze_first_call_202(rig, make_token):
    res = rig.client.post("/v1/projects/prj1/analysis:analyze", headers=_auth(make_token))
    assert res.status_code == 202
    assert res.json() == {"jobId": "job-1"}
    (job,) = rig.calls["create_job"]
    assert job["kind"] == "analyze" and job["credits_reserved"] == 0
    assert job["metadata"]["fingerprint"] == analysis.input_fingerprint(PRODUCT)
    assert job["metadata"]["agentId"] == "AG-01"


def test_analyze_same_fingerprint_returns_existing(rig, make_token, monkeypatch):
    edited = {"fit": "slim", "materials": [{"name": "면", "ratio": 100}],
              "sellingPoints": ["직접 쓴 특징"]}

    async def existing_payload(conn, project_id):
        return dict(edited)

    async def last_fp(conn, project_id):
        return analysis.input_fingerprint(PRODUCT)

    monkeypatch.setattr(repo, "get_analysis", existing_payload)
    monkeypatch.setattr(repo, "get_last_analyze_fingerprint", last_fp)
    res = rig.client.post("/v1/projects/prj1/analysis:analyze", headers=_auth(make_token))
    assert res.status_code == 200
    body = res.json()
    assert body["credits"] == 42
    assert body["data"]["projectId"] == "prj1"
    assert body["data"]["sellingPoints"] == ["직접 쓴 특징"]  # 사용자 편집 보존
    assert rig.calls["create_job"] == []  # 재분석 없음


def test_analyze_changed_fingerprint_new_job(rig, make_token, monkeypatch):
    async def existing_payload(conn, project_id):
        return {"fit": "slim"}

    async def stale_fp(conn, project_id):
        return "다른-지문"

    monkeypatch.setattr(repo, "get_analysis", existing_payload)
    monkeypatch.setattr(repo, "get_last_analyze_fingerprint", stale_fp)
    res = rig.client.post("/v1/projects/prj1/analysis:analyze", headers=_auth(make_token))
    assert res.status_code == 202
    assert len(rig.calls["create_job"]) == 1


def test_analyze_rate_limited_429(rig, make_token, monkeypatch):
    async def over_limit(conn, user_id, window_seconds):
        return 30  # 기본 상한 == 30 → 도달
    monkeypatch.setattr(repo, "count_recent_analyze_jobs", over_limit)
    res = rig.client.post("/v1/projects/prj1/analysis:analyze", headers=_auth(make_token))
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "rate_limited"
    assert rig.calls["create_job"] == []  # job 미생성


def test_analyze_rate_limit_exempts_active_job_join(rig, make_token, monkeypatch):
    # 상한 초과여도 진행 중 job이 있으면 재호출은 합류(새 Gemini 작업 없음) — 429 아님 (멱등 ①)
    async def over_limit(conn, user_id, window_seconds):
        return 999
    async def has_active(conn, user_id, project_id):
        return True
    monkeypatch.setattr(repo, "count_recent_analyze_jobs", over_limit)
    monkeypatch.setattr(repo, "has_active_analyze_job", has_active)
    res = rig.client.post("/v1/projects/prj1/analysis:analyze", headers=_auth(make_token))
    assert res.status_code == 202
    assert len(rig.calls["create_job"]) == 1  # 합류 경로 진입 (create_job이 처리)


def test_analyze_rate_limit_skips_fingerprint_reuse(rig, make_token, monkeypatch):
    # 상한 초과여도 같은 사진 재제출(fingerprint 재사용·무비용)은 200 반환 — 제한 대상 아님
    async def over_limit(conn, user_id, window_seconds):
        return 999
    async def existing_payload(conn, project_id):
        return {"fit": "slim"}
    async def last_fp(conn, project_id):
        return analysis.input_fingerprint(PRODUCT)
    monkeypatch.setattr(repo, "count_recent_analyze_jobs", over_limit)
    monkeypatch.setattr(repo, "get_analysis", existing_payload)
    monkeypatch.setattr(repo, "get_last_analyze_fingerprint", last_fp)
    res = rig.client.post("/v1/projects/prj1/analysis:analyze", headers=_auth(make_token))
    assert res.status_code == 200
    assert res.json()["data"]["fit"] == "slim"


def test_get_analysis_route(rig, make_token, monkeypatch):
    res = rig.client.get("/v1/projects/prj1/analysis", headers=_auth(make_token))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "analysis_not_found"

    async def payload(conn, project_id):
        return {"fit": "slim"}

    async def product_with_type(conn, project_id):
        return {**PRODUCT, "clothing_type": "top"}

    monkeypatch.setattr(repo, "get_analysis", payload)
    monkeypatch.setattr(repo, "get_product", product_with_type)
    res = rig.client.get("/v1/projects/prj1/analysis", headers=_auth(make_token))
    assert res.status_code == 200
    assert res.json() == {"projectId": "prj1", "clothingType": "top", "fit": "slim"}


# ── 워커 (§6.6) ──


RAW_OK = {
    "inputVerdict": "ok", "clothingType": "top", "subCategory": "knit",
    "targetGenders": ["women"], "fit": "semi_over",
    "materials": [{"name": "면", "ratio": 100}],
    "aiSuggestedPoints": ["넉넉한 라운드 넥"],
    "suggestedName": "소프트 골지 라운드 니트",
    "swatchSuggestions": [{"colorGroupId": "col_base", "swatchId": "ivory"}],
    "styleTags": ["basic"],
}

MATCHING_ITEMS = [
    {"id": "m1", "name": "슬랙스", "gender": "women", "clothing_type": "bottom",
     "is_active": True, "color_brightness": 80, "sort_order": 1,
     "thumb_key": "seed/m1_thumb.webp", "image_key": "seed/m1.webp"},
    {"id": "m2", "name": "청바지", "gender": "unisex", "clothing_type": "bottom",
     "is_active": True, "color_brightness": 60, "sort_order": 2,
     "thumb_key": "seed/m2_thumb.webp", "image_key": None},
    {"id": "m3", "name": "썸네일없음", "gender": "women", "clothing_type": "bottom",
     "is_active": True, "color_brightness": 90, "sort_order": 0, "thumb_key": None},
]


class FakeGemini:
    def __init__(self, results):
        self.results = list(results)  # GeminiJsonResult | Exception 순차 소비
        self.calls = []

    async def generate_json(self, model, system, user_text, images, schema, **kw):
        self.calls.append({"user_text": user_text, "images": len(images), "kw": kw})
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _worker_rig(monkeypatch, gemini, product=None):
    finals = {"success": [], "failure": []}

    async def get_product(conn, project_id):
        return dict(product or PRODUCT)

    async def get_asset_for_user(conn, user_id, asset_id):
        return {"id": asset_id, "mime_type": "image/jpeg", "r2_key": f"up/{asset_id}.jpg"}

    async def list_active_matching_items(conn):
        return [dict(i) for i in MATCHING_ITEMS]

    async def finalize_success(conn, **kwargs):
        finals["success"].append(kwargs)
        return {"data": {}, "available": 42}

    async def finalize_failure(conn, **kwargs):
        finals["failure"].append(kwargs)
        return True

    for name, fn in [
        ("get_product", get_product), ("get_asset_for_user", get_asset_for_user),
        ("list_active_matching_items", list_active_matching_items),
        ("finalize_analyze_success", finalize_success),
        ("finalize_analyze_failure", finalize_failure),
    ]:
        monkeypatch.setattr(repo, name, fn)

    r2 = SimpleNamespace(get_bytes=lambda key: b"img", public_url=lambda k: f"https://r2/{k}")
    app = SimpleNamespace(state=SimpleNamespace(
        settings=make_settings(), pool=FakePool(), r2=r2, gemini_text=gemini))
    job = {"id": "job-1", "user_id": "user-1", "project_id": "prj1", "lease_token": "lt-1"}
    return app, job, finals


def _ok_result(data=None):
    return GeminiJsonResult(data=data or dict(RAW_OK), latency_ms=1234,
                            usage={"totalTokenCount": 999})


def test_worker_success_finalize(monkeypatch):
    gemini = FakeGemini([_ok_result()])
    app, job, finals = _worker_rig(monkeypatch, gemini)
    asyncio.run(run_analyze_job(app, job))

    assert finals["failure"] == []
    (call,) = finals["success"]
    assert call["clothing_type"] == "top"
    assert call["actual_fingerprint"] == analysis.input_fingerprint(PRODUCT)
    assert call["swatch_suggestions"] == [{"colorGroupId": "col_base", "swatchId": "ivory"}]
    payload = call["payload"]
    assert payload["fit"] == "semi_over" and payload["sellingPoints"] == []
    assert payload["selectedModelId"] == "mA"
    # M-01: thumb_key 없는 m3 제외, 밝기순 m1(80)→m2(60) → main/sub
    assert [c["id"] for c in payload["matchCandidates"]] == ["m1", "m2"]
    assert payload["matchCandidates"][0]["thumb"] == "https://r2/seed/m1_thumb.webp"
    assert payload["matchSelections"] == [
        {"clothingId": "m1", "role": "main"}, {"clothingId": "m2", "role": "sub"}]
    meta = call["metadata"]
    assert meta["fingerprint"] == call["actual_fingerprint"]
    assert meta["attempts"] == 1 and meta["model"] == "gemini-3.5-flash"
    # 유저 텍스트: 매니페스트 + PRODUCT CONTEXT(상품명), 이미지 3장 첨부
    assert gemini.calls[0]["images"] == 3
    assert "Product name: 골지 니트" in gemini.calls[0]["user_text"]


def test_worker_rejects_not_clothing(monkeypatch):
    gemini = FakeGemini([_ok_result({**RAW_OK, "inputVerdict": "not_clothing"})])
    app, job, finals = _worker_rig(monkeypatch, gemini)
    asyncio.run(run_analyze_job(app, job))
    assert finals["success"] == []
    (call,) = finals["failure"]
    assert "의류를 인식하지 못했어요" in call["message"]
    assert call["metadata"]["error"] == "input_rejected:not_clothing"


def test_worker_rejects_unusable_photo(monkeypatch):
    # 의류지만 사진 상태가 AI 입력 불가 수준 → 범용 촬영 가이드 문구 (사용자 결정 2026-07-03)
    gemini = FakeGemini([_ok_result({**RAW_OK, "inputVerdict": "unusable_photo"})])
    app, job, finals = _worker_rig(monkeypatch, gemini)
    asyncio.run(run_analyze_job(app, job))
    assert finals["success"] == []
    (call,) = finals["failure"]
    assert "밝은 곳에서" in call["message"] and "배경" in call["message"]
    assert call["metadata"]["error"] == "input_rejected:unusable_photo"


def test_worker_retry_then_success(monkeypatch):
    gemini = FakeGemini([GeminiTextError("Gemini 500: boom"), _ok_result()])
    app, job, finals = _worker_rig(monkeypatch, gemini)
    asyncio.run(run_analyze_job(app, job))
    (call,) = finals["success"]
    assert call["metadata"]["attempts"] == 2
    # 2차 시도에는 실패 사유 피드백이 유저 텍스트에 주입된다 (§2.3)
    assert "PREVIOUS ATTEMPT WAS REJECTED" in gemini.calls[1]["user_text"]
    assert "PREVIOUS ATTEMPT" not in gemini.calls[0]["user_text"]


def test_worker_validation_error_retries(monkeypatch):
    gemini = FakeGemini([_ok_result({**RAW_OK, "fit": "loose"}), _ok_result()])
    app, job, finals = _worker_rig(monkeypatch, gemini)
    asyncio.run(run_analyze_job(app, job))
    assert finals["failure"] == []
    assert finals["success"][0]["metadata"]["attempts"] == 2


def test_worker_all_attempts_fail(monkeypatch):
    gemini = FakeGemini([GeminiTextError("x"), GeminiTextError("y")])
    app, job, finals = _worker_rig(monkeypatch, gemini)
    asyncio.run(run_analyze_job(app, job))
    assert finals["success"] == []
    (call,) = finals["failure"]
    assert call["message"] == "상품 분석에 실패했어요. 다시 시도해 주세요."
    assert call["metadata"]["attempts"] == 2


def test_worker_no_images_fails(monkeypatch):
    gemini = FakeGemini([])
    app, job, finals = _worker_rig(
        monkeypatch, gemini,
        product={**PRODUCT, "colors": []})
    asyncio.run(run_analyze_job(app, job))
    (call,) = finals["failure"]
    assert "상품 사진을 찾을 수 없어요" in call["message"]
    assert gemini.calls == []  # Gemini 미호출


def test_worker_unconfigured_gemini(monkeypatch):
    app, job, finals = _worker_rig(monkeypatch, None)
    app.state.gemini_text = None
    asyncio.run(run_analyze_job(app, job))
    (call,) = finals["failure"]
    assert call["metadata"]["error"] == "gemini_text_unconfigured"
