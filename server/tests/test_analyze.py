import asyncio
import inspect

import app.routes as routes
from app import repo
from app.agents import product_evidence_contract as pec
from app.workers import analyze_job
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job


# ---------- 라우트 ----------

def test_create_job_does_not_infer_a_partial_index_predicate():
    source = inspect.getsource(repo.create_job)
    assert "on conflict do nothing" in source
    assert "on conflict (project_id, kind)" not in source

def test_analyze_route_404_for_unknown_project(client, make_token, monkeypatch):
    async def fake_get_project(conn, uid, pid):
        return None
    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/nope/analyze", headers=auth_headers(make_token))
    assert res.status_code == 404


def test_analyze_route_creates_job(client, make_token, monkeypatch):
    # 이 라우트는 잡을 **둘** 만든다: analyze(응답의 jobId)와 캐노니컬 전처리.
    # 하나의 dict 에 덮어쓰면 뒤엣것만 남아 앞엣것을 검증하지 못한다.
    calls = []

    async def fake_get_project(conn, uid, pid):
        return {"id": pid}

    async def fake_create_job(conn, **kw):
        calls.append(kw)
        return {"id": f"job-{kw['kind']}-1"}, True

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/p1/analyze", headers=auth_headers(make_token))
    assert res.status_code == 202, res.text
    # 응답 jobId 는 **분석 잡**이다 — 전처리 잡이 응답을 가로채면 프론트가 엉뚱한 걸 폴링한다.
    assert res.json()["jobId"] == "job-analyze-1"
    analyze = next(c for c in calls if c["kind"] == "analyze")
    assert analyze["credits_reserved"] == 0  # 무과금
    sam = next(c for c in calls if c["kind"] == "sam_preprocess")
    assert sam["credits_reserved"] == 0      # 전처리도 무과금
    assert calls.index(analyze) < calls.index(sam)


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
        binding = product[pec.INTERNAL_BINDING_KEY]
        assert binding["images"][0]["slot"] == "FRONT"
        assert binding["images"][0]["source"]["sha256"] == binding["images"][0]["analysis"]["sha256"]
        return ({"product": {"clothingType": "top"},
                 "analysis": {"subCategory": "knit", "fit": "regular", "targetGenders": ["women"],
                              "materials": [], "aiSuggestedPoints": [], "suggestedName": "니트"},
                 "intermediate": {
                     "styleTags": ["basic"],
                     "swatchSuggestions": [{"colorGroupId": "base", "swatchId": "navy"}],
                 }}, "gpt")

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"result": kw["result"]}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    async def fake_extract(settings, product, images, slots=None):
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
    assert captured["analysis_payload"]["styleTags"] == ["basic"]  # 재진입 후 tags 랭킹 입력
    assert captured["analysis_payload"]["swatchSuggestions"] == [
        {"colorGroupId": "base", "swatchId": "navy"}
    ]  # 분석 직후·재진입 모두 색 폴백 입력 유지
    assert data["measurements"] == []          # 실측 미산출
    assert "measurements" not in captured["analysis_payload"]  # analyses 저장분엔 measurements 없음
    # AG-08 병렬 결과가 특징을 교체 (2026-07-13)
    assert captured["analysis_payload"]["aiSuggestedPoints"] == ["왼쪽 가슴 로고 자수"]
    # #6: worker 가 provider metadata 를 finalize 로 넘겨야 jobs.metadata 에 저장됨
    assert captured["metadata"]["provider"] == "gpt"
    assert captured["metadata"]["featureProvider"] == "gemini"
    assert captured["metadata"]["promptVersion"] == "v1"


def test_analyze_worker_persists_but_does_not_expose_server_evidence_contract(monkeypatch):
    captured = {}
    contract = {"schemaVersion": 1, "contractSha256": "server-only"}

    async def fake_get_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_get_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_analyze(settings, product, images):
        assert pec.INTERNAL_BINDING_KEY in product
        return ({
            "product": {"clothingType": "top"},
            "analysis": {
                "subCategory": "tshirt",
                "fit": "regular",
                "targetGenders": ["women"],
                "materials": [],
                "aiSuggestedPoints": [],
                "suggestedName": None,
                pec.PERSISTED_KEY: contract,
            },
            "intermediate": {"styleTags": [], "swatchSuggestions": []},
        }, "gemini")

    async def fake_extract(settings, product, images, slots=None):
        return [], "gemini"

    async def fake_finalize(conn, **kwargs):
        captured.update(kwargs)
        return {"result": kwargs["result"]}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    monkeypatch.setattr(analyze_job.repo, "get_product", fake_get_product)
    monkeypatch.setattr(analyze_job.repo, "get_asset_for_user", fake_get_asset)
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", fake_analyze)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", fake_extract)
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_success", fake_finalize)
    monkeypatch.setattr(analyze_job, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="g-x"))
    asyncio.run(analyze_job.run_analyze_job(app, worker_job()))

    assert captured["analysis_payload"][pec.PERSISTED_KEY] == contract
    assert pec.PERSISTED_KEY not in captured["result"]["data"]


def test_ag01_contract_binds_original_and_actual_resized_provider_bytes_in_order(monkeypatch):
    from hashlib import sha256

    captured = {}
    source = [(b"front-original", "image/png"), (b"back-original", "image/png")]

    def fake_shrink(data, mime):
        return b"resized:" + data, "image/jpeg"

    async def fake_analyze(settings, product, images):
        captured["binding"] = product[pec.INTERNAL_BINDING_KEY]
        return ({
            "product": {"clothingType": "top"},
            "analysis": {
                "subCategory": "tshirt", "fit": "regular", "targetGenders": ["women"],
                "materials": [], "aiSuggestedPoints": [], "suggestedName": None,
            },
            "intermediate": {"styleTags": [], "swatchSuggestions": []},
        }, "gemini")

    async def fake_extract(settings, product, images, slots=None):
        return [], "gemini"

    monkeypatch.setattr(analyze_job, "shrink_for_vision", fake_shrink)
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", fake_analyze)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", fake_extract)

    asyncio.run(analyze_job.analyze_image_bytes(
        make_settings(input_consistency="off"),
        source,
        slots=["Front", "Back"],
        persist_confirmed_evidence=True,
    ))

    rows = captured["binding"]["images"]
    assert [row["slot"] for row in rows] == ["FRONT", "BACK"]
    assert rows[0]["source"]["sha256"] == sha256(b"front-original").hexdigest()
    assert rows[0]["source"]["byteLength"] == len(b"front-original")
    assert rows[0]["analysis"]["sha256"] == sha256(b"resized:front-original").hexdigest()
    assert rows[0]["analysis"]["byteLength"] == len(b"resized:front-original")


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

    async def fake_extract(settings, product, images, slots=None):
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

    async def fake_extract(settings, product, images, slots=None):
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

    async def fake_extract(settings, product, images, slots=None):
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


# ---- AG-IC 입력 사진 동일성 배선 (2026-07-31) ----
# 이 축의 위험은 판정 자체가 아니라 **노출 게이팅**이다: shadow 인데 프론트로 새면 캘리브레이션
# 전에 오탐 경고가 셀러에게 나간다. 그래서 모드별 봉투를 직접 검증한다.

def _ic_harness(monkeypatch, verdict_payload, *, judge_raises=False):
    captured = {}

    async def fake_get_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "a1"}, {"slot": "Back", "id": "a2"}]}]}

    async def fake_get_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_analyze(settings, product, images):
        return ({"product": {"clothingType": "top"},
                 "analysis": {"subCategory": "knit", "fit": "regular", "targetGenders": ["women"],
                              "materials": [], "aiSuggestedPoints": [], "suggestedName": "니트"},
                 "intermediate": {"styleTags": [], "swatchSuggestions": []}}, "gemini")

    async def fake_extract(settings, product, images, slots=None):
        return [], "gemini"

    async def fake_judge(settings, images, slots):
        if judge_raises:
            raise RuntimeError("boom")
        assert slots == ["Front", "Back"]   # 슬롯이 이미지와 같은 순서로 전달돼야 한다
        return verdict_payload

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"result": kw["result"]}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    monkeypatch.setattr(analyze_job.repo, "get_product", fake_get_product)
    monkeypatch.setattr(analyze_job.repo, "get_asset_for_user", fake_get_asset)
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", fake_analyze)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", fake_extract)
    monkeypatch.setattr(analyze_job.input_consistency, "judge", fake_judge)
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_success", fake_finalize)
    monkeypatch.setattr(analyze_job, "_emit", fake_emit)
    return captured


_MISMATCH = {"verdict": "mismatch", "confidence": 0.9,
             "offending": [{"index": 2, "slot": "Back", "reason": "지퍼 점퍼예요"}]}


def _run(captured_settings):
    app = fake_worker_app(captured_settings)
    asyncio.run(analyze_job.run_analyze_job(app, worker_job()))


def test_input_consistency_warn_records_verdict_in_metadata(monkeypatch):
    """판정 원문은 metadata 에 남는다 — 사후에 오탐률을 되짚을 유일한 근거다.
    (shadow 모드를 두지 않기로 한 이상, 이 기록처가 없으면 어떤 사후 검증도 불가능하다.)"""
    captured = _ic_harness(monkeypatch, _MISMATCH)
    _run(make_settings(gemini_api_key="g-x", input_consistency="warn"))
    assert captured["metadata"]["inputConsistency"] == _MISMATCH


def test_input_consistency_warn_surfaces_mismatch(monkeypatch):
    captured = _ic_harness(monkeypatch, _MISMATCH)
    _run(make_settings(gemini_api_key="g-x", input_consistency="warn"))
    assert captured["result"]["data"]["inputConsistency"] == _MISMATCH


def test_input_consistency_warn_persists_into_saved_analysis(monkeypatch):
    """저장분에 없으면 새로고침·재진입한 탭에서 경고가 사라져 그대로 통과한다.
    job 결과에만 실렸던 최초 구현의 실제 증상(2026-07-31 로컬 실측)."""
    captured = _ic_harness(monkeypatch, _MISMATCH)
    _run(make_settings(gemini_api_key="g-x", input_consistency="warn"))
    assert captured["analysis_payload"]["inputConsistency"] == _MISMATCH


def test_input_consistency_match_is_recorded_but_never_persisted(monkeypatch):
    """match 는 기록만 되고 저장분에 들어가면 안 된다 — 저장분에 들어가는 순간
    GET /analysis 로 프론트에 새어 멀쩡한 업로드에 모달이 뜬다."""
    match = {"verdict": "match", "confidence": 0.9, "offending": []}
    captured = _ic_harness(monkeypatch, match)
    _run(make_settings(gemini_api_key="g-x", input_consistency="warn"))
    assert captured["metadata"]["inputConsistency"] == match
    assert "inputConsistency" not in captured["analysis_payload"]


def test_input_consistency_warn_stays_silent_on_match(monkeypatch):
    captured = _ic_harness(
        monkeypatch, {"verdict": "match", "confidence": 0.9, "offending": []})
    _run(make_settings(gemini_api_key="g-x", input_consistency="warn"))
    assert "inputConsistency" not in captured["result"]["data"]


def test_input_consistency_off_skips_the_call(monkeypatch):
    async def boom(settings, images, slots):
        raise AssertionError("off 인데 판정을 호출했다")

    captured = _ic_harness(monkeypatch, _MISMATCH)
    monkeypatch.setattr(analyze_job.input_consistency, "judge", boom)
    _run(make_settings(gemini_api_key="g-x", input_consistency="off"))
    assert "inputConsistency" not in captured["metadata"]


def test_input_consistency_failure_does_not_break_analysis(monkeypatch):
    """관찰용 축이 분석 잡을 죽이면 안 된다."""
    captured = _ic_harness(monkeypatch, None, judge_raises=True)
    _run(make_settings(gemini_api_key="g-x", input_consistency="warn"))
    assert captured["clothing_type"] == "top"                       # 분석은 정상 종결
    assert "inputConsistency" not in captured["result"]["data"]


def test_analyze_survives_a_failing_sam_preprocess_enqueue(client, make_token, monkeypatch):
    """전처리 큐잉이 터져도 분석은 202 로 나가야 한다.

    회귀(2026-08-12): 두 잡을 한 트랜잭션에 묶었더니 `sam_preprocess` INSERT 가
    jobs_kind_check 에 걸리는 순간 분석 잡까지 롤백돼 POST /analyze 가 통째로 500 이 됐다.
    보조 인프라는 본 기능을 죽이면 안 된다 — 그 경계를 코드로 잠근다.
    """
    async def fake_get_project(conn, uid, pid):
        return {"id": pid}

    async def fake_create_job(conn, **kw):
        if kw["kind"] == "sam_preprocess":
            raise RuntimeError("jobs_kind_check violation")
        return {"id": "job-analyze-1"}, True

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/p1/analyze", headers=auth_headers(make_token))
    assert res.status_code == 202, res.text
    assert res.json()["jobId"] == "job-analyze-1"
