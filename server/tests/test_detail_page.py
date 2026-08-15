import asyncio
import contextlib
import types

import app.routes as routes
from app import repo
from app.workers import detail_page_job as dpj
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job


# ---------- 라우트 ----------

def test_detail_404(client, make_token, monkeypatch):
    async def fake_gp(conn, uid, pid):
        return None
    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/nope/detail-page:generate", headers=auth_headers(make_token))
    assert res.status_code == 404


def test_detail_creates_job_and_reserves(client, make_token, monkeypatch):
    seen = {}

    async def fake_gp(conn, uid, pid):
        return {"id": pid}

    async def fake_eb(conn, pid):
        return []  # 미완료

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai"}, {"id": "b2", "source": "ai"}, {"id": "m", "source": "mine"}]

    async def fake_create_job(conn, **kw):
        seen.update(kw)
        return {"id": "job-dp-1"}, True

    async def fake_reserve(conn, uid, amount):
        seen["reserved"] = amount
        return 100

    async def fake_product(conn, pid):
        # 크레딧 견적의 복제 접기(_duplicate_source_indexes)가 clothing_type을 읽는다.
        return {"clothing_type": "top"}

    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_eb)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert res.status_code == 202, res.text
    assert res.json()["jobId"] == "job-dp-1"
    assert seen["kind"] == "detail_page"
    assert seen["credits_reserved"] == 2  # ai 블록 2개 × storyboardPerCut(1)
    assert seen["reserved"] == 2
    # 예약 시점 단가 스냅샷 — 워커 정산의 단일 기준(정산 불변식)
    assert seen["metadata"]["perCutCost"] == 1
    assert seen["metadata"]["aiCount"] == 2


def test_detail_rejects_saved_bg_example_before_job_or_credit(
    client, make_token, monkeypatch,
):
    calls = {"create_job": 0, "reserve": 0}

    async def fake_gp(conn, uid, pid):
        return {"id": pid}

    async def fake_eb(conn, pid):
        return []

    async def fake_sb(conn, pid):
        return [{
            "id": "b1",
            "source": "ai",
            "exampleId": "ex-bg-1",
            "refScope": "bg",
        }]

    async def fake_create_job(conn, **kw):
        calls["create_job"] += 1
        return {"id": "job-dp-bg"}, True

    async def fake_reserve(conn, uid, amount):
        calls["reserve"] += 1
        return 100

    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_eb)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    res = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "genexample_bg_disabled"
    assert calls == {"create_job": 0, "reserve": 0}


def test_detail_completed_recall(client, make_token, monkeypatch):
    async def fake_gp(conn, uid, pid):
        return {"id": pid}

    async def fake_eb(conn, pid):
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero"}]  # 이미 완료

    async def fake_acct(conn, uid):
        return {"credits": 42}

    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_eb)
    monkeypatch.setattr(routes.repo, "get_account", fake_acct)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert res.status_code == 200
    body = res.json()
    assert body["data"][0]["id"] == "b0" and body["credits"] == 42


# ---------- 워커 (부분 성공 정산) ----------

class _Conn:
    async def commit(self):  # main 워커가 명시 commit 하므로 스텁에도 필요
        return None


class _FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()
        return _cm()


class _FakeR2:
    def get_bytes(self, key):
        return b"\x89PNG-bytes"

    def put_bytes(self, key, data, mime, cache=None):
        return None

    def public_url(self, key):
        # 실제 R2.public_url 미러 — cut_done previewUrl 근거(editor_wait_dev_spec §2-1)
        return f"https://r2.test/{key}"

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/{key}"


class _FakeGemini:
    pass


class _RecordingGemini:
    def __init__(self):
        self.calls = 0

    async def generate_content_image(self, model, prompt, images, image_size, *, aspect_ratio=None):
        self.calls += 1
        return types.SimpleNamespace(image=b"IMG", mime="image/png")


def _app(settings):
    st = types.SimpleNamespace(settings=settings, pool=_FakePool(), r2=_FakeR2(), gemini=_FakeGemini())
    return types.SimpleNamespace(state=st)


def _job(reserved=2, per_cut=1):
    # 라우트 예약 규칙 미러: reserved = ai 블록 수 × per_cut, metadata.perCutCost = 예약 시점
    # 단가 스냅샷(워커 정산의 단일 기준 — 실행 시점 설정·콘티 변동과 무관).
    return {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:tok",
            "credits_reserved": reserved, "metadata": {"perCutCost": per_cut}}


def _settings(**overrides):
    from conftest import make_settings
    return make_settings(gemini_api_key="x", r2_bucket="b", **overrides)


def test_run_detail_page_job_rejects_bg_example_when_pilot_disabled(monkeypatch):
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{
            "id": "bg",
            "source": "ai",
            "exampleId": "ex-bg-1",
            "refScope": "bg",
        }]

    async def fake_failure(conn, **kw):
        captured.update(kw)
        return {"ok": True}

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=1)))

    assert captured["metadata"] == {"error": "genexample_bg_disabled"}
    assert captured["code"] == "genexample_bg_disabled"
    assert captured["reserved"] == 1


def test_run_detail_page_job_reports_space_set_binding_error_without_generation(
    monkeypatch,
):
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [
            {
                "id": "set-1",
                "source": "ai",
                "sectionRole": "fit",
                "contentRole": "coordination",
                "cutType": "styling",
                "direction": "front",
                "shot": "full",
                "spaceGroupId": "ssg1__missing-set__instance-1",
                "spaceSetMemberOrder": 1,
                "exampleId": "missing-member",
            }
        ]

    async def fake_prod(conn, pid):
        return {"clothingType": "top"}

    async def fake_analysis(conn, pid):
        return {"targetGenders": ["women"]}

    def fake_bind(blocks, *, clothing_type, gender):
        raise dpj.space_set_assets.SpaceSetBindingError(
            "unknown_space_set",
            "저장된 공간 세트를 찾을 수 없어요. 세트를 다시 선택해 주세요.",
        )

    async def fake_failure(conn, **kw):
        captured.update(kw)
        return {"ok": True}

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(
        dpj.space_set_assets, "bind_storyboard_space_sets", fake_bind
    )
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=1)))

    assert captured["code"] == "unknown_space_set"
    assert captured["metadata"] == {"error": "unknown_space_set"}
    assert captured["message"] == (
        "저장된 공간 세트를 찾을 수 없어요. 세트를 다시 선택해 주세요."
    )


def test_production_space_set_scene_qc_outage_fails_cut_closed(monkeypatch):
    events = []

    async def fake_generate(*_args, **_kwargs):
        return b"generated", "image/png"

    async def unavailable_scene_qc(*_args, **_kwargs):
        raise dpj.VisionError("qc unavailable")

    async def fake_emit(_pool, _job_id, event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.image_qc, "scene_verdict", unavailable_scene_qc)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    product_image = dpj.InlineImage("image/png", b"product")
    plate = dpj.InlineImage("image/png", b"plate")
    result = asyncio.run(
        dpj._gen_cuts(
            _app(_settings()),
            _job(reserved=1),
            [
                (
                    {"id": "set-cut", "refScope": "pose"},
                    [product_image, plate],
                    "1. PRODUCT\n2. SPACE SET PLATE",
                    False,
                    [product_image],
                    plate,
                    True,
                )
            ],
            {"clothingType": "top"},
            {},
        )
    )

    assert result[:3] == ([], [], 0)
    # 새 이벤트 계약(editor_wait_dev_spec §2-1): 생성 시작(cut_start)과 컷 단위 progress 가
    # 실패 컷에도 둘러싼다 — 대기 화면이 "그리다 실패"를 정직하게 그리는 근거.
    assert events == [
        ("step", {"blockId": "set-cut", "status": "cut_start"}),
        ("step", {"blockId": "set-cut", "status": "cut_failed"}),
        ("progress", {"progress": 80, "phase": "cut", "done": 1, "total": 1}),
    ]


def test_gen_cuts_detail_requires_loaded_detail_manifest(monkeypatch):
    """상품 메타데이터가 아니라 워커가 실제 첨부한 자산으로 게이트한다.

    2026-08-07 개편: 같은 방향 원본이 있으면 구조 확대 모드로 생성하므로,
    게이트가 막는 경우는 '컷 방향 근거(디테일도 원본도)가 전무'할 때다 —
    뒷면 디테일 컷에 앞면 자산만 로드된 상황으로 검증한다."""
    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = _app(_settings())
    app.state.gemini = _RecordingGemini()
    spec = {"id": "detail-1", "cutType": "product", "shot": "detail", "direction": "back"}
    images = [dpj.InlineImage("image/png", b"front")]
    manifest = dpj.cut_generator.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0)

    (cut_results, cut_assets, face_cuts, garment_qcs,
     cut_qcs, page_qc, warnings) = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=1), [(spec, images, manifest, False, images)],
        {"name": "니트", "clothingType": "top"}, {},
    ))

    assert app.state.gemini.calls == 0
    assert cut_results == [] and cut_assets == [] and face_cuts == 0
    assert garment_qcs == [] and cut_qcs == [] and page_qc is None and warnings == []


def test_gen_cuts_detail_reaches_gemini_with_loaded_detail_manifest(monkeypatch):
    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = _app(_settings())
    app.state.gemini = _RecordingGemini()
    spec = {"id": "detail-1", "cutType": "product", "shot": "detail"}
    images = [
        dpj.InlineImage("image/png", b"front"),
        dpj.InlineImage("image/png", b"detail"),
    ]
    manifest = dpj.cut_generator.build_manifest(
        [{"slot": "Front"}, {"slot": "Detail"}],
        has_mannequin=False, has_match=False, mood_count=0,
    )

    (cut_results, cut_assets, face_cuts, garment_qcs,
     cut_qcs, page_qc, warnings) = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=1), [(spec, images, manifest, False, images)],
        {"name": "니트", "clothingType": "top"}, {},
    ))

    assert app.state.gemini.calls == 1
    assert len(cut_results) == len(cut_assets) == 1
    assert face_cuts == 0
    assert garment_qcs == [] and cut_qcs == [] and page_qc is None and warnings == []


def test_run_detail_page_job_partial_success(monkeypatch):
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "styling"},
                {"id": "b2", "source": "ai", "cutType": "product"}]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "a2"},
        ]}]}

    async def fake_analysis(conn, pid):
        return {"suggestedName": "미니멀 코튼 셔츠"}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    call = {"n": 0}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        call["n"] += 1
        if call["n"] == 1:
            return b"IMG", "image/png"     # b1 성공
        raise RuntimeError("gen fail")      # b2 실패 → 빈 슬롯, 미차감

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        captured["cut_results"] = cut_results
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=2)))

    assert captured["charge"] == 1              # 성공 컷 1개 × per_cut(1) — 실패 컷 미차감
    assert len(captured["cut_assets"]) == 1
    assert len(captured["cut_results"]) == 1     # b1만
    assert captured["product_name"] == "미니멀 코튼 셔츠"  # copywriting OFF도 무호출 작명


def test_run_detail_page_job_attaches_matching_garment_to_horizon(monkeypatch):
    captured = {"matching_item_ids": [], "loaded_asset_ids": []}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{
            "id": "fit-with-match", "source": "ai", "sectionRole": "fit",
            "contentRole": "fit", "cutType": "horizon", "shot": "medium",
            "colorId": "col1", "matchIds": ["match-1", "match-2"],
        }]

    async def fake_prod(conn, pid):
        return {"clothingType": "top", "colors": [{
            "id": "col1", "isBase": True,
            "images": [{"slot": "Front", "id": "product-1"}],
        }]}

    async def fake_analysis(conn, pid):
        return {"fitProfile": {
            "category": "top", "gender": "women",
            "axes": {"fit": "regular", "length": None}, "matchCut": "wide",
        }}

    async def fake_matching_asset(conn, matching_item_id, user_id, project_id):
        assert (user_id, project_id) == ("u1", "p1")
        captured["matching_item_ids"].append(matching_item_id)
        return f"{matching_item_id}-asset"

    async def fake_asset(conn, uid, aid):
        captured["loaded_asset_ids"].append(aid)
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        captured["cut_spec"] = cut_spec
        captured["image_data"] = [image.data for image in images]
        captured["manifest"] = manifest
        captured["prompt"] = dpj.cut_generator.build_prompt(
            cut_spec, product, analysis=analysis, manifest=manifest)
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return []

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    class KeyR2:
        def get_bytes(self, key):
            return key.encode()

        def put_bytes(self, key, data, mime, cache=None):
            return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_matching_item_asset", fake_matching_asset)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"), r2=KeyR2())
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["matching_item_ids"] == ["match-1", "match-2"]
    assert captured["loaded_asset_ids"] == [
        "product-1", "match-1-asset", "match-2-asset",
    ]
    assert captured["cut_spec"]["cutType"] == "horizon"
    assert captured["image_data"] == [
        b"k/product-1", b"k/match-1-asset", b"k/match-2-asset",
    ]
    assert captured["manifest"].count(
        "MATCHING — the user-selected coordinating garment"
    ) == 2
    assert "- matching bottom" in captured["prompt"]


def test_run_detail_page_job_separates_detail_manifests_by_direction(monkeypatch):
    """앞·뒤 디테일 블록(같은 색)은 각자 방향의 디테일만 첨부한다 — 캐시 키에 방향 포함 검증."""
    captured = {"manifests": {}}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [
            {"id": "front-detail", "source": "ai", "sectionRole": "product",
             "contentRole": "detail", "cutType": "product", "shot": "detail",
             "direction": "front", "colorId": "base"},
            {"id": "back-detail", "source": "ai", "sectionRole": "product",
             "contentRole": "detail", "cutType": "product", "shot": "detail",
             "direction": "back", "colorId": "base"},
        ]

    async def fake_prod(conn, pid):
        return {"colors": [
            {"id": "base", "name": "레드", "swatchId": "red", "isBase": True, "images": [
                {"slot": "Front", "id": "base-front"},
                {"slot": "Back", "id": "base-back"},
                {"slot": "Detail", "id": "base-detail"},
                {"slot": "BackDetail", "id": "base-backdetail"},
            ]},
        ]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        captured["manifests"][cut_spec["id"]] = manifest
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return []

    async def fake_finalize(conn, **kw):
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=2)))

    front_manifest = captured["manifests"]["front-detail"]
    back_manifest = captured["manifests"]["back-detail"]
    assert "front-side detail close-up" in front_manifest
    assert "back-side detail close-up" not in front_manifest
    assert "back-side detail close-up" in back_manifest
    assert "front-side detail close-up" not in back_manifest


def test_run_detail_page_job_fails_cut_when_any_matching_asset_is_missing(monkeypatch):
    captured = {
        "matching_item_ids": [],
        "loaded_asset_ids": [],
        "generate_calls": 0,
    }

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{
            "id": "fit-with-missing-match",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "fit",
            "cutType": "horizon",
            "shot": "medium",
            "colorId": "col1",
            "matchIds": ["match-1", "match-2"],
        }]

    async def fake_prod(conn, pid):
        return {"clothingType": "top", "colors": [{
            "id": "col1",
            "isBase": True,
            "images": [{"slot": "Front", "id": "product-1"}],
        }]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_matching_asset(conn, matching_item_id, user_id, project_id):
        assert (user_id, project_id) == ("u1", "p1")
        captured["matching_item_ids"].append(matching_item_id)
        return f"{matching_item_id}-asset"

    async def fake_asset(conn, uid, aid):
        captured["loaded_asset_ids"].append(aid)
        if aid == "match-2-asset":
            return None
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def forbidden_generate(*args, **kwargs):
        captured["generate_calls"] += 1
        raise AssertionError("a partially resolved matching outfit must not be generated")

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return {"available": 99}

    async def forbidden_success(conn, **kwargs):
        raise AssertionError("all-cuts-failed path must not finalize success")

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_matching_item_asset", fake_matching_asset)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", forbidden_generate)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", forbidden_success)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["matching_item_ids"] == ["match-1", "match-2"]
    assert captured["loaded_asset_ids"] == [
        "product-1", "match-1-asset", "match-2-asset",
    ]
    assert captured["generate_calls"] == 0
    assert captured["failure"]["code"] == "all_cuts_failed"
    assert captured["failure"]["metadata"] == {
        "error": "all_cuts_failed",
        "requestedCuts": 1,
    }


def test_run_detail_page_job_uses_other_color_detail_and_keeps_normal_color_strict(monkeypatch):
    captured = {"loaded_asset_ids": [], "generated_block_ids": [], "prompts": {}}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [
            {
                "id": "cross-color-detail", "source": "ai", "sectionRole": "product",
                "contentRole": "detail", "cutType": "product", "shot": "detail",
                "colorId": "green",
            },
            {
                "id": "valid-fit", "source": "ai", "sectionRole": "fit",
                "contentRole": "fit", "cutType": "horizon", "shot": "full",
                "colorId": 0,
            },
            {
                "id": "same-color-detail", "source": "ai", "sectionRole": "product",
                "contentRole": "detail", "cutType": "product", "shot": "detail",
                "colorId": "base",
                "_detailColorTransfer": {"targetName": "잘못 저장된 색상"},
            },
        ]

    async def fake_prod(conn, pid):
        return {"colors": [
            {"id": "base", "name": "레드", "swatchId": "red", "isBase": True, "images": [
                {"slot": "Front", "id": "base-front"},
                {"slot": "Detail", "id": "base-detail"},
            ]},
            {"id": "green", "name": "그린", "swatchId": "green", "images": [
                {"slot": "Front", "id": "green-front"},
            ]},
            {"id": "0", "images": [{"slot": "Front", "id": "zero-front"}]},
        ]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        captured["loaded_asset_ids"].append(aid)
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        captured["generated_block_ids"].append(cut_spec["id"])
        captured["prompts"][cut_spec["id"]] = dpj.cut_generator.build_prompt(
            cut_spec, product, analysis=analysis, manifest=manifest)
        captured.setdefault("manifests", {})[cut_spec["id"]] = manifest
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        captured["cut_results"] = cut_results
        return []

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=3)))

    assert captured["loaded_asset_ids"] == [
        "zero-front", "green-front", "base-detail", "base-front", "base-detail",
    ]
    assert captured["generated_block_ids"] == [
        "valid-fit", "cross-color-detail", "same-color-detail",
    ]
    assert [result["blockId"] for result in captured["cut_results"]] == [
        "valid-fit", "cross-color-detail", "same-color-detail",
    ]
    assert "PRODUCT — front-side detail close-up" in captured["manifests"]["cross-color-detail"]
    assert "DETAIL COLORWAY TRANSFER" in captured["prompts"]["cross-color-detail"]
    assert "Target color: 그린 (#3f7a4f)" in captured["prompts"]["cross-color-detail"]
    assert "DETAIL COLORWAY TRANSFER" not in captured["prompts"]["same-color-detail"]
    assert "잘못 저장된 색상" not in captured["prompts"]["same-color-detail"]
    assert len(captured["manifests"]["cross-color-detail"].splitlines()) == 2
    assert captured["manifests"]["valid-fit"] == "1. PRODUCT — front view of the garment"
    assert captured["charge"] == 3


def test_run_detail_page_job_attaches_resolved_examples_with_scoped_manifest(monkeypatch):
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [
            {"id": "all", "source": "ai", "cutType": "styling",
             "exampleId": "ex_styling_top_full_1", "refScope": "all"},
            {"id": "pose", "source": "ai", "cutType": "horizon",
             "exampleId": "ex_horizon_top_full_1", "refScope": "pose"},
            {"id": "mismatch", "source": "ai", "cutType": "styling",
             "exampleId": "ex_wrong_clothing", "refScope": "all"},
            {"id": "unpublished", "source": "ai", "cutType": "horizon",
             "exampleId": "ex_without_bg", "refScope": "bg"},
            {"id": "direction-mismatch", "source": "ai", "cutType": "styling",
             "direction": "front", "exampleId": "ex_back_pose", "refScope": "pose"},
            {"id": "named", "source": "ai", "cutType": "styling", "pose": "walk",
             "exampleId": "ex_styling_top_full_1", "refScope": "pose"},
        ]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_example(settings, example_id, scope="all", clothing_type=None):
        # scope 전달 검증(2026-07-12 누끼 variant): pose 블록은 pose로, 그 외 all
        assert clothing_type == "top"
        return dpj.InlineImage("image/jpeg", f"EXAMPLE:{example_id}:{scope}".encode())

    def fake_example_status(example_id, clothing_type, scope="all"):
        assert clothing_type == "top"
        if example_id == "ex_wrong_clothing":
            return "not_applicable"
        if example_id == "ex_without_bg":
            return "variant_unpublished"
        return "available"

    def fake_pose_compatible(example_id, normalized):
        return example_id != "ex_back_pose"

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None):
        captured[cut_spec["id"]] = {
            "cut_spec": cut_spec,
            "images": images,
            "manifest": manifest,
            "prompt": dpj.cut_generator.build_prompt(
                cut_spec, product, analysis=analysis, manifest=manifest),
        }
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting):
        return []

    async def fake_finalize(conn, **kw):
        captured["finalize"] = kw
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "example_asset_status", fake_example_status)
    monkeypatch.setattr(dpj.cut_generator, "pose_direction_compatible", fake_pose_compatible)
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", fake_example)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(
        _app(_settings(genexample_bg_enabled=True)), _job(reserved=5)))

    for block_id, scope in (("all", "all"), ("pose", "pose")):
        item = captured[block_id]
        assert len(item["images"]) == 2  # PRODUCT 다음에 resolved EXAMPLE 실제 첨부
        assert item["images"][-1].data.startswith(b"EXAMPLE:")
        assert item["images"][-1].data.endswith(f":{scope}".encode())  # scope별 자산(누끼 variant) 선택 검증
    assert "EXAMPLE REFERENCE (scope: all)" in captured["all"]["manifest"]
    assert "POSE CONTROL" in captured["pose"]["manifest"]
    assert "EXAMPLE REFERENCE as art direction" in captured["all"]["prompt"]
    assert "Do not transfer any background, lighting, color grade" in captured["pose"]["prompt"]
    assert "EXAMPLE REFERENCE as art direction" not in captured["pose"]["prompt"]
    assert len(captured["named"]["images"]) == 1
    assert "EXAMPLE REFERENCE" not in captured["named"]["manifest"]
    assert "REFERENCE SCOPE" not in captured["named"]["prompt"]
    for block_id in ("mismatch", "unpublished"):
        assert len(captured[block_id]["images"]) == 1
        assert captured[block_id]["cut_spec"]["exampleId"] is None
        assert "EXAMPLE REFERENCE" not in captured[block_id]["manifest"]
        assert "Composition nuance" not in captured[block_id]["prompt"]
    assert captured["finalize"]["metadata"]["warnings"] == [
        {
            "code": "example_not_applicable", "blockId": "mismatch",
            "exampleId": "ex_wrong_clothing", "clothingType": "top", "refScope": "all",
        },
        {
            "code": "pose_direction_incompatible", "blockId": "direction-mismatch",
            "exampleId": "ex_back_pose", "direction": "front",
        },
        {
            "code": "example_variant_unpublished", "blockId": "unpublished",
            "exampleId": "ex_without_bg", "clothingType": "top", "refScope": "bg",
        },
    ]
    assert "direction-mismatch" not in captured  # preflight에서 빈 슬롯, 생성 호출 0회


def test_run_detail_page_job_attaches_set_plate_and_set_or_flat_pose(monkeypatch):
    captured = {"loads": [], "flatLoads": [], "cuts": {}}
    group_id = "ssg1__set-cafe-01__instance-01"
    storyboard = [
        {
            "id": "set-1",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "coordination",
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "spaceGroupId": group_id,
            "spaceSetMemberOrder": 1,
            "exampleId": "ss_cafe_01",
            "refScope": "all",
            "pose": "arms crossed",
            "spaceVariation": "fixed",
        },
        {
            "id": "set-2",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "coordination",
            "cutType": "styling",
            "direction": "side",
            "shot": "medium",
            "spaceGroupId": group_id,
            "spaceSetMemberOrder": 2,
            "exampleId": "ex-flat-side-medium",
            "refScope": "all",
            "pose": "hands in pockets",
            "spaceVariation": "fixed",
        },
        {
            "id": "dragged-out",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "coordination",
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "exampleId": "ss_drag_pose",
            "refScope": "pose",
            "pose": "auto",
        },
        {
            "id": "standalone-all",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "coordination",
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "exampleId": "ss_all_example",
            "refScope": "all",
            "pose": "auto",
            # 서버 전용 필드를 저장 payload가 위조해도 resolver 판정을 덮지 못해야 한다.
            "_referenceDirectionCompatible": True,
        },
        {
            "id": "mine",
            "source": "mine",
            "sectionRole": "product",
        },
    ]
    set_entry = {
        "setId": "set-cafe-01",
        "spaceVariation": "subtle",
        "representativePlate": {"key": "plate"},
        "members": [],
    }
    members = [
        {
            "exampleId": "ss_cafe_01",
            "order": 1,
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "pose": {"key": "pose-1"},
        },
        {
            "exampleId": "ss_cafe_02",
            "order": 2,
            "cutType": "styling",
            "direction": "side",
            "shot": "medium",
            "pose": {"key": "pose-2"},
        },
    ]
    set_entry["members"] = members

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return storyboard

    async def fake_prod(conn, pid):
        return {
            "clothingType": "top",
            "colors": [
                {
                    "isBase": True,
                    "images": [{"slot": "Front", "id": "product-front"}],
                }
            ],
        }

    async def fake_analysis(conn, pid):
        return {"targetGenders": ["women"]}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    def fake_bind(blocks, *, clothing_type, gender):
        assert clothing_type == "top" and gender == "women"
        captured["boundBlocks"] = [block["id"] for block in blocks]
        return {
            id(blocks[0]): {
                "groupId": group_id,
                "set": set_entry,
                "poseReference": {
                    "source": "space-set",
                    "exampleId": members[0]["exampleId"],
                    "asset": members[0]["pose"],
                },
            },
            id(blocks[1]): {
                "groupId": group_id,
                "set": set_entry,
                "poseReference": {
                    "source": "flat",
                    "exampleId": "ex-flat-side-medium",
                    "asset": None,
                },
            },
        }

    async def fake_set_image(settings, asset, *, role):
        captured["loads"].append((asset["key"], role))
        return dpj.InlineImage("image/png", asset["key"].encode())

    def fake_resolve_example(block, *, clothing_type, gender, scope):
        assert block["exampleId"] in ("ss_drag_pose", "ss_all_example")
        assert clothing_type == "top" and gender == "women"
        expected_scope = (
            "pose" if block["exampleId"] == "ss_drag_pose" else "all"
        )
        assert scope == expected_scope
        return {
            "source": "space-set",
            "exampleId": block["exampleId"],
            "scope": scope,
            "directionCompatible": scope != "all",
            "asset": {
                "key": "pose-drag" if scope == "pose" else "all-example"
            },
        }

    async def fake_flat_image(
        settings, example_id, scope="all", clothing_type=None
    ):
        captured["flatLoads"].append((example_id, scope, clothing_type))
        return dpj.InlineImage("image/png", b"flat-pose")

    async def fake_gen(
        settings,
        gemini,
        cut_spec,
        product,
        images,
        *,
        analysis=None,
        manifest=None,
        **_kwargs,
    ):
        captured["cuts"][cut_spec["id"]] = {
            "images": [image.data for image in images],
            "manifest": manifest,
            "cutSpec": dict(cut_spec),
        }
        return b"IMG", "image/png"

    async def fake_scene(_settings, plate, generated):
        assert plate.data == b"plate"
        return {"verdict": "pass", "mismatches": [], "correctionPrompt": None}

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return []

    async def fake_finalize(conn, **kw):
        captured["finalize"] = kw
        return {"editor_blocks": [], "available": 99}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(
        dpj.space_set_assets, "bind_storyboard_space_sets", fake_bind
    )
    monkeypatch.setattr(
        dpj.space_set_assets, "load_space_set_image", fake_set_image
    )
    monkeypatch.setattr(
        dpj.space_set_assets,
        "resolve_published_example_reference",
        fake_resolve_example,
    )
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", fake_flat_image)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.image_qc, "scene_verdict", fake_scene)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=4)))

    assert captured["loads"] == [
        ("plate", "대표 배경"),
        ("pose-1", "포즈"),
        ("pose-drag", "포즈"),
        ("all-example", "전체 예시"),
    ]
    assert captured["flatLoads"] == [
        ("ex-flat-side-medium", "pose", "top")
    ]
    assert captured["boundBlocks"] == [
        "set-1",
        "set-2",
        "dragged-out",
        "standalone-all",
        "mine",
    ]
    assert captured["cuts"]["set-1"]["images"][-2:] == [b"plate", b"pose-1"]
    assert captured["cuts"]["set-2"]["images"][-2:] == [b"plate", b"flat-pose"]
    assert captured["cuts"]["dragged-out"]["images"][-1:] == [b"pose-drag"]
    assert captured["cuts"]["standalone-all"]["images"][-1:] == [
        b"all-example"
    ]
    for block_id, item in captured["cuts"].items():
        assert "EXAMPLE REFERENCE (scope: bg)" not in item["manifest"]
        assert item["cutSpec"]["pose"] == "auto"
        expected_scope = "all" if block_id == "standalone-all" else "pose"
        assert item["cutSpec"]["refScope"] == expected_scope
        if expected_scope == "pose":
            assert "POSE CONTROL" in item["manifest"]
    assert "EXAMPLE REFERENCE (scope: all)" in (
        captured["cuts"]["standalone-all"]["manifest"]
    )
    assert "source ONLY of scene, lighting, capture tone" in (
        captured["cuts"]["standalone-all"]["manifest"]
    )
    assert captured["cuts"]["standalone-all"]["cutSpec"][
        "_referenceDirectionCompatible"
    ] is False
    for block_id in ("set-1", "set-2"):
        assert (
            captured["cuts"][block_id]["cutSpec"]["spaceVariation"]
            == "subtle"
        )
    assert "SPACE SET PLATE" in captured["cuts"]["set-1"]["manifest"]
    assert "SPACE SET PLATE" in captured["cuts"]["set-2"]["manifest"]
    assert "SPACE SET PLATE" not in captured["cuts"]["dragged-out"]["manifest"]
    assert captured["finalize"]["charge"] == 4


def test_run_detail_page_job_uses_analysis_model_without_mutating_storyboard(monkeypatch):
    captured = {}
    storyboard = [
        {"id": "product", "source": "ai", "cutType": "product", "shot": "ghost"},
        {"id": "person", "source": "ai", "cutType": "horizon", "shot": "full"},
    ]

    class TrackingR2:
        def get_bytes(self, key):
            return key.encode()

        def put_bytes(self, key, data, mime, cache=None):
            return None

        def delete(self, key):
            return None

        def public_url(self, key):
            return f"https://r2.test/{key}"

        def preview_url(self, key, expires=3600):
            return f"https://r2.test/{key}"

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False, "selected_mannequin_id": "A-1"}

    async def fake_sb(conn, pid):
        return storyboard

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {"selectedModelId": "mB"}

    async def fake_cuts(conn, uid, pid):
        return [{"candidate": "A", "version": 1, "asset_id": "man"}]

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    def fake_model_refs(spec, *, require_full_body=False):
        assert require_full_body is True
        if spec["cutType"] == "product":
            return None
        assert require_full_body is True
        return (
            {"key": "seed/models/mB/face_front.webp", "mime": "image/webp"},
            {"key": "seed/models/mB/body_front.png", "mime": "image/jpeg"},
        )

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None):
        captured[cut_spec["id"]] = {
            "spec": cut_spec, "data": [image.data.decode() for image in images],
            "manifest": manifest,
        }
        return b"IMG", "image/png"

    def fake_assemble(saved_storyboard, cut_results, copy_results, product, copywriting):
        captured["assembled_storyboard"] = saved_storyboard
        return []

    async def fake_finalize(conn, **kw):
        return {"editor_blocks": [], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "list_mannequin_cuts", fake_cuts)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "resolve_virtual_model_assets", fake_model_refs)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"), r2=TrackingR2())
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=2)))

    assert captured["person"]["spec"]["modelId"] == "mB"
    assert captured["person"]["data"] == [
        "k/man", "seed/models/mB/face_front.webp",
        "seed/models/mB/body_front.png", "k/a1",
    ]
    assert captured["person"]["manifest"].splitlines()[0].startswith(
        "1. MANNEQUIN — coarse worn-geometry prior"
    )
    assert captured["person"]["manifest"].splitlines()[1].startswith("2. MODEL FACE —")
    assert captured["person"]["manifest"].splitlines()[2].startswith("3. MODEL FULL BODY —")
    assert captured["person"]["manifest"].splitlines()[3] == "4. PRODUCT — front view of the garment"
    assert captured["product"]["data"] == ["k/a1"]
    assert "mannequin" not in captured["product"]["manifest"].lower()
    assert "MODEL" not in captured["product"]["manifest"]
    assert captured["assembled_storyboard"] is not storyboard
    assert [block["id"] for block in captured["assembled_storyboard"]] == ["person", "product"]
    assert [block["contentRole"] for block in captured["assembled_storyboard"]] == [
        "fit", "productOverview",
    ]
    assert all("modelId" not in block and "model_id" not in block for block in storyboard)


def test_run_detail_page_job_partial_charge_uses_reservation_time_price(monkeypatch):
    """정산 불변식 회귀: 부분 성공 단가는 실행 시점 설정이 아니라 예약액에서 역산한다.
    예약(단가 1 × 2블록 = 2) 후 배포로 단가가 5로 올라도, 1컷 성공 = 1 차감(5도 2도 아님)."""
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "styling"},
                {"id": "b2", "source": "ai", "cutType": "product"}]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    call = {"n": 0}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        call["n"] += 1
        if call["n"] == 1:
            return b"IMG", "image/png"
        raise RuntimeError("gen fail")

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    from conftest import make_settings
    inflated = make_settings(gemini_api_key="x", r2_bucket="b", credit_cost_storyboard_per_cut=5)
    asyncio.run(dpj.run_detail_page_job(_app(inflated), _job(reserved=2)))

    assert captured["charge"] == 1  # 스냅샷 단가(perCutCost=1) × 성공 1컷 — 실행 시점 5는 무시


def test_run_detail_page_job_storyboard_growth_never_settles_zero(monkeypatch):
    """정산 불변식 회귀: 예약(2블록×1) 후 콘티 재저장으로 ai 블록이 3개로 늘어도
    정산은 스냅샷 단가 × 성공 컷(예약액 캡)이다 — 예약액÷현재 블록 수 역산이었다면
    블록 수가 예약액을 넘는 순간 단가 0 → 무과금 생성이 된다."""
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):  # 예약 시점(2블록)보다 커진 콘티
        return [{"id": "b1", "source": "ai", "cutType": "styling"},
                {"id": "b2", "source": "ai", "cutType": "product"},
                {"id": "b3", "source": "ai", "cutType": "horizon"}]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        return b"IMG", "image/png"  # 3컷 전부 성공

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=2)))

    assert captured["charge"] == 2  # min(3컷 × 스냅샷 1, 예약 2) — 0이 아니고 예약 초과도 아님


def test_run_detail_page_job_fails_when_all_ai_blocks_lack_garment_truth(monkeypatch):
    # 옷 근거(상품 사진/마네킹) 없이 무드 레퍼런스만 있는 블록은 생성하지 않는다 — 실패·환불.
    # (무드만 첨부하면 모델이 레퍼런스 속 옷을 베끼거나 지어낸다 — ADR-0004 정확성 최우선)
    captured = {}
    calls = {"n": 0}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "styling", "refAssetIds": ["ref1"]}]

    async def fake_prod(conn, pid):
        return {"colors": []}   # 상품 사진 없음 + 마네킹 미선택

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        calls["n"] += 1
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        raise AssertionError("all-cuts-failed must stop before assembly")

    async def fake_finalize(conn, **kw):
        raise AssertionError("all-cuts-failed must not finalize success")

    async def fake_failure(conn, **kw):
        captured["failure"] = kw
        return {"status": "failed"}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=2)))

    assert calls["n"] == 0                       # 생성 호출 자체가 없다
    assert captured["failure"]["code"] == "all_cuts_failed"
    assert captured["failure"]["metadata"] == {
        "error": "all_cuts_failed", "requestedCuts": 1,
    }


def test_run_detail_page_job_copywriting_qc_failure_keeps_original(monkeypatch):
    # copywriting 경로(_gen_copy) 커버 + AG-03 검수 실패 시 원문 유지(except 커버 — NameError 회귀 방지)
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": True}

    async def fake_sb(conn, pid):
        return [{
            "id": "b1", "source": "ai", "sectionRole": "product", "contentRole": "detail",
            "cutType": "horizon",
        }]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "d1"},
        ]}]}

    async def fake_analysis(conn, pid):
        return {"sellingPoints": ["촉감"], "materials": []}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        captured["cut_spec"] = cut_spec
        return b"IMG", "image/png"

    async def fake_copy(settings, **kw):
        captured["copy_kwargs"] = kw
        return {
            "texts": [{"role": "body", "text": "원본 카피"}],
            "productName": "골지 데일리 니트",
        }

    async def fake_review(settings, items, confirmed):
        raise RuntimeError("qc down")  # 검수 실패 → 원문 유지 (except 커버)

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        captured["storyboard"] = storyboard
        captured["copy_results"] = copy_results
        captured["assembled_product_name"] = product.get("name")
        return [{"id": "b0", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.copywriter, "generate", fake_copy)
    monkeypatch.setattr(dpj.copy_qc, "review", fake_review)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=1)))  # ai 블록 1개 × 단가 1
    assert captured["charge"] == 1
    assert captured["copy_kwargs"]["content_role"] == "detail"
    assert captured["copy_kwargs"]["section_role"] == "product"
    assert "block_kind" not in captured["copy_kwargs"]
    assert captured["copy_kwargs"]["include_product_name"] is True
    assert captured["product_name"] == "골지 데일리 니트"
    assert captured["assembled_product_name"] == "골지 데일리 니트"
    assert captured["cut_spec"]["cutType"] == "product"
    assert captured["cut_spec"]["shot"] == "detail"
    assert captured["storyboard"][0]["sectionRole"] == "product"
    assert captured["storyboard"][0]["cutType"] == "product"
    assert captured["storyboard"][0]["shot"] == "detail"
    assert captured["copy_results"] == [{"blockId": "b1", "texts": [{"role": "body", "text": "원본 카피"}]}]


def test_copywriting_off_uses_analysis_name_without_another_llm_call():
    assert dpj._fallback_product_name(
        {"name": "새 상품", "clothingType": "top"},
        {"suggestedName": "미니멀 코튼 셔츠", "subCategory": "shirt"},
    ) == "미니멀 코튼 셔츠"
    assert dpj._fallback_product_name(
        {"name": "", "clothingType": "outer"}, {"suggestedName": None},
    ) == "데일리 아우터"


def test_detail_finalize_updates_product_name_and_project_title_in_same_transaction(monkeypatch):
    statements = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, sql, params=None):
            statements.append((" ".join(sql.split()), params))

        async def fetchone(self):
            return {"id": "j1"}

    class Conn:
        def cursor(self):
            return Cursor()

    async def fake_release(*args, **kwargs):
        return 9

    monkeypatch.setattr(repo, "release_credits", fake_release)
    result = asyncio.run(repo.finalize_detail_page_success(
        Conn(), job_id="j1", lease_token="lease", user_id="u1", project_id="p1",
        editor_blocks=[], cut_assets=[], reserved=0, charge=0, metadata={},
        product_name="골지 데일리 니트",
    ))

    sql = [statement for statement, _params in statements]
    assert any("update products set name = %s where project_id = %s" in s for s in sql)
    assert any("update projects set title = %s where id = %s and user_id = %s" in s for s in sql)
    assert result["available"] == 9


def test_detail_passthrough_ships_the_sellers_original_without_generating(monkeypatch):
    """미세 패턴 상품의 디테일 컷은 셀러 원본을 그대로 싣는다 — 이미지 모델을 부르지 않는다.

    2026-08-01 측정: 줄 하나가 파란 실 2가닥 + 베이지 1가닥인 원단은 4K 에서도 한 주기 14px →
    요소당 2.3px 이라 재현이 불가능하다. 원본이 있는데 다시 그리면 있던 정보를 버리는 셈이다.

    과금은 **생성한 컷 수**(cut_assets)로 매기므로 패스스루는 거기 들어가면 안 된다 —
    호출하지 않은 컷에 크레딧이 붙는다.
    """
    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = _app(_settings())
    app.state.gemini = _RecordingGemini()
    spec = {"id": "detail-1", "cutType": "product", "shot": "detail"}
    images = [dpj.InlineImage("image/png", b"front")]
    original = {"id": "asset-detail-1", "width": 3000, "height": 4000, "slot": "Detail"}

    (cut_results, cut_assets, face_cuts, garment_qcs,
     cut_qcs, page_qc, warnings) = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=1),
        [(spec, images, "manifest", False, images, None, False, original)],
        {"name": "스트라이프 셔츠", "clothingType": "top"}, {},
    ))

    assert app.state.gemini.calls == 0, "패스스루는 이미지 모델을 부르지 않는다"
    assert cut_results == [{
        "blockId": "detail-1",
        "imageUrl": "/v1/assets/asset-detail-1/file",
        "width": 3000, "height": 4000,
    }]
    assert cut_assets == [], "새 asset 을 만들지 않는다 — 과금 단위에 들어가면 안 된다"
    assert face_cuts == 0 and garment_qcs == [] and cut_qcs == []
    assert page_qc is None and warnings == []


def test_run_detail_page_job_emits_copy_first_then_cut_events(monkeypatch):
    """에디터 대기 계약(editor_wait_dev_spec §2-1) — 이벤트 순서와 페이로드.

    ① copy_ready(검수 통과본)는 모든 컷 이벤트보다 앞: 셀러가 컷을 기다리는 동안
       문구를 다듬는 전제. ② cut_done 은 previewUrl(1h presigned)+width/height 를 싣는다
       (asset 행은 finalize 전이라 /file 은 404 — DB 무변경 원칙). ③ 컷 1개 종결마다
       progress(phase=cut, 20→80)가 나간다 — 체크포인트 정지 화면 방지."""
    events = []

    async def fake_gp(conn, uid, pid):
        return {"copywriting": True}

    async def fake_sb(conn, pid):
        return [
            {"id": "b1", "source": "ai", "sectionRole": "styling",
             "contentRole": "hero", "cutType": "styling"},
            {"id": "b2", "source": "ai", "sectionRole": "product",
             "contentRole": "detail", "cutType": "horizon"},
        ]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "d1"},
        ]}]}

    async def fake_analysis(conn, pid):
        return {"sellingPoints": [], "materials": []}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, **_kw):
        return b"IMGDATA", "image/png"

    async def fake_copy(settings, **kw):
        return [{"role": "headline" if kw.get("content_role") == "hero" else "body",
                 "text": "카피"}]

    async def fake_review(settings, items, confirmed):
        return []  # 전건 pass

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return [{"id": "b0", "elements": []}]

    async def fake_finalize(conn, **kw):
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        events.append((et, payload))

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.copywriter, "generate", fake_copy)
    monkeypatch.setattr(dpj.copy_qc, "review", fake_review)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=2)))

    kinds = [(et, p.get("status") or p.get("phase")) for et, p in events]
    # ① 카피가 컷보다 앞 — copy_ready 전부가 첫 cut_* 이벤트보다 먼저다
    first_cut = next(i for i, k in enumerate(kinds) if str(k[1]).startswith("cut"))
    copy_idx = [i for i, k in enumerate(kinds) if k[1] == "copy_ready"]
    assert copy_idx and max(copy_idx) < first_cut
    assert ("progress", "copy") in kinds
    # ② cut_done 페이로드 — previewUrl(presigned 미러)+치수
    dones = [p for et, p in events if et == "step" and p.get("status") == "cut_done"]
    assert {d["blockId"] for d in dones} == {"b1", "b2"}
    for d in dones:
        assert d["previewUrl"].startswith("https://r2.test/")
        # 치수는 미상일 수 있다(assembler 가 2:3 폴백) — 키 존재만 계약
        assert "width" in d and "height" in d
    # ③ 컷 단위 progress — 2컷이면 50, 80 (20+60×n/2)
    cut_prog = [p["progress"] for et, p in events
                if et == "progress" and p.get("phase") == "cut"]
    assert cut_prog == [50, 80]
    # ④ cut_start 는 컷마다 1회
    starts = [p for et, p in events if et == "step" and p.get("status") == "cut_start"]
    assert {s["blockId"] for s in starts} == {"b1", "b2"}
    # ⑤ 조립 국면 전환 이벤트
    assert ("progress", "assemble") in kinds


def test_assembler_wires_source_block_id_and_copy_role():
    """editor_wait_dev_spec §2-3 — 대기 화면 컷 채움·셀러 카피 오버라이드의 매칭 키."""
    storyboard = [{"id": "sb1", "source": "ai", "sectionRole": "styling",
                   "contentRole": "hero", "cutType": "styling"}]
    cut_results = [{"blockId": "sb1", "imageUrl": "/v1/assets/x/file", "width": 880, "height": 1320}]
    copy_results = [{"blockId": "sb1", "texts": [{"role": "headline", "text": "헤드라인"}]}]
    blocks = dpj.page_assembler.assemble(storyboard, cut_results, copy_results, {}, True)
    hero = blocks[0]
    img = next(e for e in hero["elements"] if e["type"] == "image")
    txt = next(e for e in hero["elements"] if e["type"] == "text")
    assert img["sourceBlockId"] == "sb1"
    assert txt["sourceBlockId"] == "sb1" and txt["copyRole"] == "headline"


def test_job_events_poll_returns_json_with_after_cursor(client, make_token, monkeypatch):
    """?poll=1 — SSE 대신 1회 JSON 폴링(editor_wait_dev_spec §2-2).
    EventSource 는 Bearer 헤더를 못 실으므로 대기 화면은 이 분기로 이벤트를 받는다."""
    async def fake_get_job(conn, uid, jid):
        return {"id": jid, "status": "running"}

    seen = {}

    async def fake_list(conn, uid, jid, after):
        seen["after"] = after
        return [
            {"id": 4, "event_type": "step",
             "payload": {"blockId": "b1", "status": "cut_done",
                         "previewUrl": "https://r2.test/k", "width": 880, "height": 1320}},
            {"id": 5, "event_type": "progress",
             "payload": {"progress": 50, "phase": "cut", "done": 1, "total": 2}},
        ]

    monkeypatch.setattr(routes.repo, "get_job", fake_get_job)
    monkeypatch.setattr(routes.repo, "list_job_events", fake_list)
    patch_route_db(monkeypatch, routes)
    res = client.get("/v1/jobs/j1/events?poll=1&after=3", headers=auth_headers(make_token))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert seen["after"] == 3
    body = res.json()
    assert [e["id"] for e in body["events"]] == [4, 5]
    assert body["events"][0]["type"] == "step"
    assert body["events"][0]["payload"]["previewUrl"] == "https://r2.test/k"
