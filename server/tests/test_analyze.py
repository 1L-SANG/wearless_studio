import asyncio

import app.routes as routes
from app.workers import analyze_job
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job


# ---------- 라우트 ----------

def test_analyze_route_404_for_unknown_project(client, make_token, monkeypatch):
    async def fake_get_project(conn, uid, pid):
        return None
    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/nope/analyze", headers=auth_headers(make_token))
    assert res.status_code == 404


def test_analyze_route_creates_job(client, make_token, monkeypatch):
    seen = {}

    async def fake_get_project(conn, uid, pid):
        return {"id": pid}

    async def fake_create_job(conn, **kw):
        seen.update(kw)
        return {"id": "job-analyze-1"}, True

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/p1/analyze", headers=auth_headers(make_token))
    assert res.status_code == 202, res.text
    assert res.json()["jobId"] == "job-analyze-1"
    assert seen["kind"] == "analyze"
    assert seen["credits_reserved"] == 0  # 무과금


def test_analyze_route_idempotent_join(client, make_token, monkeypatch):
    async def fake_get_project(conn, uid, pid):
        return {"id": pid}

    async def fake_create_job(conn, **kw):
        return {"id": "existing-job"}, False  # 활성 합류

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/p1/analyze",
                      headers={**auth_headers(make_token), "Idempotency-Key": "k1"})
    assert res.status_code == 202
    assert res.json()["jobId"] == "existing-job"


def test_analyze_spike_disabled_by_default(client, make_token):
    # 기본 ANALYSIS_SPIKE=off → flag 게이트가 DB 이전에 403 (make_settings 기본값)
    res = client.post("/v1/projects/p1/analyze:spike", headers=auth_headers(make_token))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "spike_disabled"


def test_run_analyze_job_success(monkeypatch):
    captured = {}

    async def fake_get_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_get_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_analyze(settings, product, images):
        assert images and images[0].data == b"\x89PNG-bytes"  # bytes 입력 확인
        return ({"product": {"clothingType": "top"},
                 "analysis": {"subCategory": "knit", "fit": "regular", "targetGenders": ["women"],
                              "materials": [], "aiSuggestedPoints": [], "suggestedName": "니트"},
                 "intermediate": {"styleTags": ["basic"], "swatchSuggestions": []}}, "gpt")

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"result": kw["result"]}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    async def fake_extract(settings, product, images):
        return ["왼쪽 가슴 로고 자수"], "gemini"  # AG-08 성공 → AG-01 points 교체

    monkeypatch.setattr(analyze_job.repo, "get_product", fake_get_product)
    monkeypatch.setattr(analyze_job.repo, "get_asset_for_user", fake_get_asset)
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", fake_analyze)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", fake_extract)
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_success", fake_finalize)
    monkeypatch.setattr(analyze_job, "_emit", fake_emit)

    app = fake_worker_app(make_settings(openai_api_key="sk-x"))
    asyncio.run(analyze_job.run_analyze_job(app, worker_job()))

    assert captured["clothing_type"] == "top"
    data = captured["result"]["data"]
    assert data["clothingType"] == "top"
    assert data["styleTags"] == ["basic"]
    assert data["measurements"] == []          # 실측 미산출
    assert "measurements" not in captured["analysis_payload"]  # analyses 저장분엔 measurements 없음
    # AG-08 병렬 결과가 특징을 교체 (2026-07-13)
    assert captured["analysis_payload"]["aiSuggestedPoints"] == ["왼쪽 가슴 로고 자수"]
    # #6: worker 가 provider metadata 를 finalize 로 넘겨야 jobs.metadata 에 저장됨
    assert captured["metadata"]["provider"] == "gpt"
    assert captured["metadata"]["featureProvider"] == "gemini"
    assert captured["metadata"]["promptVersion"] == "v1"


def test_run_analyze_job_feature_agent_failure_falls_back(monkeypatch):
    """AG-08 실패는 분석을 막지 않는다 — AG-01의 aiSuggestedPoints 유지 (2026-07-13)."""
    from app.agents.vision_llm import VisionError
    captured = {}

    async def fake_get_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_get_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_analyze(settings, product, images):
        return ({"product": {"clothingType": "top"},
                 "analysis": {"subCategory": "knit", "fit": "regular", "targetGenders": [],
                              "materials": [], "aiSuggestedPoints": ["골지 짜임"],
                              "suggestedName": None},
                 "intermediate": {"styleTags": [], "swatchSuggestions": []}}, "gemini")

    async def fake_extract(settings, product, images):
        raise VisionError("특징 발굴 실패")

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"result": kw["result"]}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    monkeypatch.setattr(analyze_job.repo, "get_product", fake_get_product)
    monkeypatch.setattr(analyze_job.repo, "get_asset_for_user", fake_get_asset)
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", fake_analyze)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", fake_extract)
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_success", fake_finalize)
    monkeypatch.setattr(analyze_job, "_emit", fake_emit)

    app = fake_worker_app(make_settings(openai_api_key="sk-x"))
    asyncio.run(analyze_job.run_analyze_job(app, worker_job()))
    assert captured["analysis_payload"]["aiSuggestedPoints"] == ["골지 짜임"]  # 폴백
    assert captured["metadata"]["featureProvider"] is None


def test_run_analyze_job_feature_empty_keeps_ag01_points(monkeypatch):
    """AG-08이 '차별 특징 없음'(빈 배열)을 내면 AG-01 것 유지 — 빈 값으로 덮지 않는다."""
    captured = {}

    async def fake_get_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_get_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_analyze(settings, product, images):
        return ({"product": {"clothingType": "top"},
                 "analysis": {"subCategory": None, "fit": "regular", "targetGenders": [],
                              "materials": [], "aiSuggestedPoints": ["골지 짜임"],
                              "suggestedName": None},
                 "intermediate": {"styleTags": [], "swatchSuggestions": []}}, "gemini")

    async def fake_extract(settings, product, images):
        return [], "gemini"

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"result": kw["result"]}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    monkeypatch.setattr(analyze_job.repo, "get_product", fake_get_product)
    monkeypatch.setattr(analyze_job.repo, "get_asset_for_user", fake_get_asset)
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", fake_analyze)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", fake_extract)
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_success", fake_finalize)
    monkeypatch.setattr(analyze_job, "_emit", fake_emit)

    app = fake_worker_app(make_settings(openai_api_key="sk-x"))
    asyncio.run(analyze_job.run_analyze_job(app, worker_job()))
    assert captured["analysis_payload"]["aiSuggestedPoints"] == ["골지 짜임"]


def test_run_analyze_job_no_images_fails(monkeypatch):
    captured = {}

    async def fake_get_product(conn, pid):
        return {"colors": []}  # 이미지 없음

    async def fake_fail(conn, **kw):
        captured.update(kw)
        return True

    monkeypatch.setattr(analyze_job.repo, "get_product", fake_get_product)
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_failure", fake_fail)

    app = fake_worker_app(make_settings(openai_api_key="sk-x"))
    asyncio.run(analyze_job.run_analyze_job(app, worker_job()))
    assert captured["code"] == "analysis_failed"
    assert "사진" in captured["message"]


def test_run_analyze_job_vision_error_fails(monkeypatch):
    from app.agents.vision_llm import VisionError
    captured = {}

    async def fake_get_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_get_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_analyze(settings, product, images):
        raise VisionError("상품 분석에 실패했어요.")

    async def fake_fail(conn, **kw):
        captured.update(kw)
        return True

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    async def fake_extract(settings, product, images):
        return [], "gemini"  # AG-08은 성공해도 AG-01 실패면 job 실패여야 한다

    monkeypatch.setattr(analyze_job.repo, "get_product", fake_get_product)
    monkeypatch.setattr(analyze_job.repo, "get_asset_for_user", fake_get_asset)
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", fake_analyze)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", fake_extract)
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_failure", fake_fail)
    monkeypatch.setattr(analyze_job, "_emit", fake_emit)

    app = fake_worker_app(make_settings(openai_api_key="sk-x"))
    asyncio.run(analyze_job.run_analyze_job(app, worker_job()))
    assert "실패" in captured["message"]


# ── 분석 비전 입력 축소 (2026-07-07 속도 개선) ─────────────────────────────────


def test_shrink_for_vision_downscales_large_image():
    import os as _os
    from io import BytesIO
    from PIL import Image
    # 압축 안 되는 노이즈로 400KB 초과 원본을 만든다 (단색은 PNG가 너무 작아 skip 분기로 빠짐)
    noise = Image.frombytes("RGB", (2400, 1800), _os.urandom(2400 * 1800 * 3))
    buf = BytesIO()
    noise.save(buf, format="PNG")
    data = buf.getvalue()
    assert len(data) > 400_000
    out, mime = analyze_job.shrink_for_vision(data, "image/png")
    assert mime == "image/jpeg" and len(out) < len(data)
    assert max(Image.open(BytesIO(out)).size) <= 1024


def test_shrink_for_vision_passthrough_small_and_broken():
    small = b"x" * 1000  # 작음 → 원본 유지
    assert analyze_job.shrink_for_vision(small, "image/png") == (small, "image/png")
    broken = b"y" * 500_000  # 크지만 이미지 아님 → 안전 폴백(원본 유지)
    assert analyze_job.shrink_for_vision(broken, "image/png") == (broken, "image/png")
