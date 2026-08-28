"""FM-31 상세페이지 워커 — FaceMarket 현재 증거 주입 + AI 고지 분기.

제품 핵심("가짜 얼굴의 시대에 진짜를 공급")이 출력에서 성립하는지를 지킨다:
큐에 고정된 모델·라이선스의 현재 얼굴 증거가 컷 생성 입력에 들어가고, 그 사실이 고지에
정확히 반영되며, 라이선스 없는 기존 경로는 한 톨도 변하지 않는다(해커톤 필수 경로).
"""

import asyncio
import contextlib
import types
from datetime import datetime, timedelta, timezone

import httpx

from app import facemarket
from app.agents import identity_source
from app.workers import detail_page_job as dpj
from conftest import FakeR2, make_settings, worker_job

LIC_ID = "11111111-1111-4111-8111-111111111111"
MODEL_ID = "22222222-2222-4222-8222-222222222222"
ENROLLMENT_ID = "33333333-3333-4333-8333-333333333333"
CATEGORY = "상의"
FACE_KEY = "faces/model-1/lic-1.png"
FACE_BYTES = b"\x89PNG-FACE-BYTES"
CURRENT_FACE_KEY = "current/face_front.png"
CURRENT_GRID_KEY = "current/grid_sedcard.png"


def _license_row(status="active", days_left=30, key=FACE_KEY, name="김하늘"):
    return {
        "id": LIC_ID,
        "model_id": MODEL_ID,
        "model_name": "김*늘",
        "face_image_key": key,
        "status": status,
        "display_name": name,
        "license_valid_until": datetime.now(timezone.utc) + timedelta(days=days_left),
        "unit_price": 10000,
        "vc_id": "vc-1",
        "vc_status_uri": None,
        "allowed_use": [CATEGORY],
        "forbidden_use": [],
        "model_status": "verified",
        "assets_status": "ready",
        "current_enrollment_id": ENROLLMENT_ID,
        "license_enrollment_id": ENROLLMENT_ID,
        "enrollment_status": "passed",
        "match_policy_version": "policy-v1",
        "has_face_front": True,
        "has_grid_sedcard": True,
        "assets_current_evidence": True,
    }


def _snapshot_job(*, reserved=1):
    return worker_job(
        {
            "mode": "generate",
            "modelId": MODEL_ID,
            "brandUseCategory": CATEGORY,
            "_facemarket": {"modelId": MODEL_ID, "licenseId": LIC_ID},
        },
        credits_reserved=reserved,
    )


def _patch_snapshot_denial(monkeypatch, row):
    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        assert model_id == MODEL_ID and license_id == LIC_ID
        return row

    async def forbidden_assets(*args, **kwargs):
        raise AssertionError("verifier denial must precede current asset lookup")

    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", forbidden_assets)


def _patch_snapshot_success(monkeypatch, row):
    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        assert model_id == MODEL_ID and license_id == LIC_ID
        return row

    async def fake_assets(conn, model_id, *, enrollment_id, evidence_version):
        assert model_id == MODEL_ID
        assert enrollment_id == ENROLLMENT_ID
        assert evidence_version == "policy-v1"
        return [
            {"key": CURRENT_FACE_KEY, "mime": "image/png", "bucket": "face"},
            {"key": CURRENT_GRID_KEY, "mime": "image/png", "bucket": "face"},
        ]

    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_assets)


class _Cur:
    def __init__(self, row):
        self._row = row
        self._sql = ""

    async def execute(self, sql, params=None):
        self._sql = " ".join(sql.split())
        return None

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        # resolve_real_model_assets(fm_model_assets 조인) → 빈 결과 = 실존 자산 없음 → None → LEGACY.
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    """fm_licenses 조회(커서)까지 흉내내는 커넥션."""

    def __init__(self, row):
        self._row = row

    async def commit(self):
        return None

    def cursor(self):
        return _Cur(self._row)


class _Pool:
    def __init__(self, row):
        self._row = row

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn(self._row)

        return _cm()


class _FaceR2:
    """비공개 얼굴 버킷. 어떤 키로 접근했는지 기록해 버킷 경계를 단언한다."""

    def __init__(self, raises=False):
        self.raises = raises
        self.gets: list[str] = []

    def get_bytes(self, key):
        self.gets.append(key)
        if self.raises:
            raise RuntimeError("no such object")
        return FACE_BYTES

    def put_bytes(self, key, data, mime, cache=None):
        return None

    def delete(self, key):
        return None


class _MainR2(FakeR2):
    """공개 결과 버킷 기록기 — REAL 거부 시 출력이 생기지 않음을 단언한다."""

    def __init__(self, *, fail_delete=False):
        self.puts: list[str] = []
        self.caches: list[str | None] = []
        self.deletes: list[str] = []
        self.objects = set()
        self.fail_delete = fail_delete

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append(key)
        self.caches.append(cache)
        self.objects.add(key)

    def delete(self, key):
        self.deletes.append(key)
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.objects.discard(key)

    def head(self, key):
        return {"size": 1, "mime": "image/png"} if key in self.objects else None


def _app(
    license_row,
    *,
    face_r2=None,
    facemarket_enabled=True,
    with_face_storage=True,
    vc_required=False,
):
    main_r2 = _MainR2()
    state = types.SimpleNamespace(
        settings=make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            facemarket_enabled=facemarket_enabled,
            fm_vc_required=vc_required,
            opendid_holder_url="http://holder" if vc_required else None,
            opendid_holder_hmac_secret="shared-secret" if vc_required else None,
        ),
        pool=_Pool(license_row), r2=main_r2, gemini=types.SimpleNamespace(),
    )
    if with_face_storage:
        state.r2_face = face_r2 or _FaceR2()
    return types.SimpleNamespace(state=state), main_r2


def _patch_inputs(monkeypatch, captured, *, project, storyboard=None, product=None):
    async def fake_gp(conn, uid, pid):
        return project

    async def fake_sb(conn, pid):
        return storyboard or [{"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"}]

    async def fake_prod(conn, pid):
        return product or {"clothing_type": "top",
                           "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *,
                       analysis=None, manifest=None, has_face=False):
        captured.setdefault("calls", []).append(
            {"images": list(images), "manifest": manifest, "has_face": has_face,
             "block": cut_spec.get("id")})
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, *,
                      license_notice=None):
        captured["license_notice"] = license_notice
        captured["cut_results"] = cut_results
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_finalize_failure(conn, **kw):
        captured["failure"] = kw
        return {"status": "failed"}

    async def fake_emit(pool, job_id, et, payload):
        captured.setdefault("events", []).append((et, payload))

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_finalize_failure)
    monkeypatch.setattr(dpj, "_emit", fake_emit)


# ── 라이선스 없는 기존 경로 = 무변경 (해커톤 필수 경로) ──────────────────────
def test_no_license_project_attaches_no_face_and_keeps_default_notice(monkeypatch):
    """절대 금지 항목의 회귀 가드: 라이선스 없는 프로젝트는 얼굴 첨부 0,
    has_face=False, license_notice=None, 정산·차감 그대로."""
    captured = {}
    _patch_inputs(monkeypatch, captured, project={"copywriting": False})  # facemarket_license_id 없음
    app, main_r2 = _app(_license_row())

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    call = captured["calls"][0]
    assert call["has_face"] is False
    assert len(call["images"]) == 1                     # 상품 1장뿐 — 얼굴 미첨부
    assert "MODEL FACE" not in call["manifest"]
    assert captured["license_notice"] is None           # 기존 AI 고지 문구 유지
    assert captured["charge"] == 1                      # 차감 계약 그대로
    assert app.state.r2_face.gets == []                 # 얼굴 버킷 접근조차 없음
    assert main_r2.caches == ["public, max-age=31536000, immutable"]


def test_facemarket_disabled_never_loads_face(monkeypatch):
    # 킬스위치: FACEMARKET_ENABLED=false 면 라이선스가 잠겨 있어도 얼굴 경로 미진입.
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    app, _ = _app(_license_row(), facemarket_enabled=False)

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["calls"][0]["has_face"] is False
    assert captured["license_notice"] is None
    assert app.state.r2_face.gets == []


# ── 얼굴 주입 ────────────────────────────────────────────────────────────────
def test_project_pinned_face_without_snapshot_fails_and_refunds(monkeypatch):
    captured = {"settlements": 0}
    _patch_inputs(
        monkeypatch,
        captured,
        project={"copywriting": False, "facemarket_license_id": LIC_ID},
    )

    async def fake_settlement(*_args, **_kwargs):
        captured["settlements"] += 1

    monkeypatch.setattr(facemarket, "record_license_settlement", fake_settlement)
    app, main_r2 = _app(_license_row())
    app.state.fm_chain = object()

    asyncio.run(dpj.run_detail_page_job(
        app,
        worker_job({"mode": "generate"}, credits_reserved=7),
    ))

    assert captured.get("calls") is None
    assert captured.get("editor_blocks") is None
    assert captured["settlements"] == 0
    assert captured["failure"]["reserved"] == 7
    assert captured["failure"]["code"] == "model_unavailable"
    assert app.state.r2_face.gets == []
    assert main_r2.puts == []


def test_snapshot_real_job_injects_current_evidence_into_cut_input(monkeypatch):
    """큐에 고정된 실존 모델의 현재 증거 2장이 실제 생성 입력에 들어간다."""
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": "later-lock"})
    row = _license_row()
    app, main_r2 = _app(row)
    _patch_snapshot_success(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    call = captured["calls"][0]
    assert call["has_face"] is True
    # 현재 얼굴·그리드가 상품 근거 앞에 매니페스트와 lockstep으로 붙는다.
    assert [im.data for im in call["images"]] == [
        FACE_BYTES,
        FACE_BYTES,
        b"\x89PNG-bytes",
    ]
    assert all(im.mime == "image/png" for im in call["images"][:2])
    assert "MODEL — frontal close-up" in call["manifest"]
    assert "MODEL SHEET" in call["manifest"]
    assert call["manifest"].index("MODEL —") < call["manifest"].index("PRODUCT")
    # 현재 증거는 **비공개** 버킷에서만 — 레거시 얼굴 키와 공개 버킷은 권한이 아니다.
    assert app.state.r2_face.gets == [CURRENT_FACE_KEY, CURRENT_GRID_KEY]
    assert FACE_KEY not in app.state.r2_face.gets
    assert FACE_KEY not in getattr(main_r2, "gets", [])
    assert main_r2.caches == ["private, no-store"]


def test_snapshot_real_job_notice_states_masked_model(monkeypatch):
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": "later-lock"})
    row = _license_row(name="김하늘")
    app, _ = _app(row)
    _patch_snapshot_success(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    # faceCuts/totalCuts 는 고지의 **범위 주장** 근거다 — assembler 가 이 둘로
    # '가상인물 아님'(전 컷) vs '일부 컷' 을 가른다.
    assert captured["license_notice"] == {
        "modelName": "김*늘", "licenseId": LIC_ID, "faceCuts": 1, "totalCuts": 1,
    }


def test_snapshot_real_identity_is_attached_only_to_worn_cuts(monkeypatch):
    # product 컷에는 인물 증거를 붙이지 않는다. 거울샷은 일관성 그리드만 쓰고 배지는 숨긴다.
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": "later-lock"},
                  storyboard=[
                      {"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"},
                      {"id": "b2", "source": "ai", "cutType": "product", "shot": "ghost"},
                      {"id": "b3", "source": "ai", "cutType": "mirror", "shot": "full"},
                  ])
    row = _license_row()
    app, main_r2 = _app(row)
    _patch_snapshot_success(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job(reserved=3)))

    by_block = {c["block"]: c for c in captured["calls"]}
    assert by_block["b1"]["has_face"] is True and len(by_block["b1"]["images"]) == 3
    assert by_block["b2"]["has_face"] is False and len(by_block["b2"]["images"]) == 1
    assert by_block["b3"]["has_face"] is False and len(by_block["b3"]["images"]) == 3
    # 얼굴이 담긴 컷이 하나라도 성공했으므로 고지는 실제 모델 문구
    assert captured["license_notice"] is not None
    # mirror는 얼굴 노출 배지는 꺼져도 REAL identity 두 장을 생성 근거로 쓴다. 따라서
    # 결과 본문과 asset API 모두 styling과 같은 생체 파생물로 취급해야 한다.
    assert main_r2.caches.count("private, no-store") == 2
    assert main_r2.caches.count("public, max-age=31536000, immutable") == 1
    markers = [asset["metadata"]["facemarket_real_derived"] for asset in captured["cut_assets"]]
    assert markers.count(True) == 2
    assert markers.count(False) == 1


# ── verify-before-use 시점 갭 (해지된 얼굴이 생성돼 나가면 회수 불가) ────────
def test_snapshot_real_job_uses_current_evidence_not_legacy_face_key(monkeypatch):
    captured = {}
    _patch_inputs(
        monkeypatch,
        captured,
        project={"copywriting": False, "facemarket_license_id": "later-lock"},
    )
    row = _license_row(key=FACE_KEY)
    app, _ = _app(row)
    _patch_snapshot_success(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    assert app.state.r2_face.gets == [
        CURRENT_FACE_KEY,
        CURRENT_GRID_KEY,
    ]
    assert FACE_KEY not in app.state.r2_face.gets
    assert captured.get("failure") is None


def test_revoked_license_at_worker_time_fails_job_and_refunds(monkeypatch):
    """게이트(요청 시점) 통과 후 해지된 라이선스 — 워커가 재확인해 얼굴을 쓰지 않는다.
    한 번 생성되면 공개 URL 로 나가 회수가 불가능하다."""
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    row = _license_row(status="revoked")
    app, _ = _app(row)
    _patch_snapshot_denial(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    assert captured.get("calls") is None
    assert captured["failure"]["reserved"] == 1
    assert captured["failure"]["code"] == "license_revoked"
    assert app.state.r2_face.gets == []


def test_expired_license_at_worker_time_fails_job_and_refunds(monkeypatch):
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    row = _license_row(days_left=-1)
    app, _ = _app(row)
    _patch_snapshot_denial(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    assert captured.get("calls") is None
    assert captured["failure"]["reserved"] == 1
    assert captured["failure"]["code"] == "license_expired"
    assert app.state.r2_face.gets == []


class _HolderResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_holder_outage_at_worker_time_fails_before_face_read(monkeypatch):
    captured = {}
    _patch_inputs(
        monkeypatch,
        captured,
        project={"copywriting": False, "facemarket_license_id": LIC_ID},
    )

    async def holder_down(*_args, **_kwargs):
        raise httpx.ConnectError("holder down")

    monkeypatch.setattr(facemarket.holder_client, "post", holder_down)
    row = _license_row()
    app, _ = _app(row, vc_required=True)
    _patch_snapshot_denial(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    assert captured.get("calls") is None
    assert captured["failure"]["reserved"] == 1
    assert captured["failure"]["code"] == "holder_unavailable"
    assert app.state.r2_face.gets == []


def test_invalid_or_revoked_vc_at_worker_time_fails_before_face_read(monkeypatch):
    for payload in (
        {"verified": False, "status": "invalid"},
        {"verified": True, "status": "revoked"},
    ):
        captured = {}
        _patch_inputs(
            monkeypatch,
            captured,
            project={"copywriting": False, "facemarket_license_id": LIC_ID},
        )

        async def holder_result(*_args, _payload=payload, **_kwargs):
            return _HolderResponse(_payload)

        monkeypatch.setattr(facemarket.holder_client, "post", holder_result)
        row = _license_row()
        app, _ = _app(row, vc_required=True)
        _patch_snapshot_denial(monkeypatch, row)

        asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

        assert captured.get("calls") is None
        assert captured["failure"]["reserved"] == 1
        assert captured["failure"]["code"] == "license_unverified"
        assert app.state.r2_face.gets == []


# ── 현재 실존 모델 자산 실패 = 무출력·전액 환불 ─────────────────────────────
def test_unavailable_current_real_asset_fails_without_faceless_fallback(monkeypatch):
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": "later-lock"})
    row = _license_row(key=FACE_KEY)
    app, main_r2 = _app(row, face_r2=_FaceR2(raises=True))
    _patch_snapshot_success(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job(reserved=7)))

    assert captured.get("calls") is None
    assert captured.get("license_notice") is None
    assert captured.get("charge") is None
    assert captured["failure"]["code"] == "model_assets_unavailable"
    assert captured["failure"]["reserved"] == 7
    assert main_r2.puts == []


def test_all_face_cuts_failing_fails_the_job_without_false_notice(monkeypatch):
    """얼굴 컷이 전부 실패하면 빈 페이지를 완료하지 않고 실패·환불한다."""
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": "later-lock"})

    async def failing_gen(settings, gemini, cut_spec, product, images, *,
                          analysis=None, manifest=None, has_face=False):
        raise RuntimeError("gen fail")

    monkeypatch.setattr(dpj.cut_generator, "generate", failing_gen)
    row = _license_row()
    app, main_r2 = _app(row)
    _patch_snapshot_success(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    assert "license_notice" not in captured             # 조립 전에 중단 → 허위 고지 없음
    assert captured["failure"]["code"] == "all_cuts_failed"
    assert captured["failure"]["reserved"] == 1        # 실패 종결에서 예약액 전부 환불
    assert main_r2.puts == []


# ── 옷 근거 가드가 얼굴로 우회되지 않는다 (ADR-0004) ─────────────────────────
def test_face_never_bypasses_garment_truth_guard(monkeypatch):
    """상품 사진도 마네킹도 없으면 얼굴이 있어도 생성하지 않는다 — 얼굴을 스킵 표식
    앞이나 빈 리스트에 넣으면 `if not images` 가드가 무력화돼 옷 근거 0으로 생성이 돈다."""
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": "later-lock"},
                  product={"clothing_type": "top", "colors": []})  # 상품 이미지 0 · 마네킹 없음
    row = _license_row()
    app, main_r2 = _app(row)
    _patch_snapshot_success(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    assert captured.get("calls") is None                # 생성 호출 자체가 없어야 한다
    assert "license_notice" not in captured
    assert captured["failure"]["code"] == "all_cuts_failed"
    assert captured["failure"]["reserved"] == 1
    assert main_r2.puts == []


# ── PII: 얼굴 바이트·키가 이벤트에 새지 않는다 ───────────────────────────────
def test_face_bytes_and_key_never_appear_in_job_events(monkeypatch):
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": "later-lock"})
    row = _license_row()
    app, _ = _app(row)
    _patch_snapshot_success(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    blob = repr(captured["events"])
    assert FACE_KEY not in blob
    assert CURRENT_FACE_KEY not in blob
    assert CURRENT_GRID_KEY not in blob
    assert "PNG-FACE-BYTES" not in blob
    assert "faces/" not in blob


def _assert_no_prefinal_output_reference(events):
    blob = repr(events)
    lower_blob = blob.lower()
    assert "previewUrl" not in blob
    assert "/v1/assets/" not in blob
    assert "https://r2.test/" not in blob
    assert "users/" not in blob
    assert "cleanup_intent" not in blob
    assert "intent-" not in blob
    assert "bearer" not in lower_blob
    assert "token" not in lower_blob


def test_real_prefinal_events_hide_original_cut_output_before_late_revoke(monkeypatch):
    captured = {"resolve": 0, "settlement": 0}
    _patch_inputs(
        monkeypatch,
        captured,
        project={"copywriting": False, "facemarket_license_id": "later-lock"},
        storyboard=[{"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"}],
    )

    def row(status):
        return {**_license_row(status=status)}

    async def fake_resolve(conn, model_id, *, license_id=None, **kwargs):
        captured["resolve"] += 1
        assert model_id == MODEL_ID and license_id == LIC_ID
        return row("active" if captured["resolve"] == 1 else "revoked")

    async def fake_verify(app, license_row, **kwargs):
        assert license_row["status"] == "active"

    async def fake_assets(conn, model_id, *, enrollment_id, evidence_version):
        return [
            {"key": CURRENT_FACE_KEY, "mime": "image/png", "bucket": "face"},
            {"key": CURRENT_GRID_KEY, "mime": "image/png", "bucket": "face"},
        ]

    async def fake_lock(conn):
        captured["pre_final_events"] = list(captured["events"])

    async def fake_settlement(*_args, **_kwargs):
        captured["settlement"] += 1

    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_assets)
    monkeypatch.setattr(dpj.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(dpj.facemarket, "record_license_settlement", fake_settlement)

    app, main_r2 = _app(row("active"))
    main_r2.fail_delete = True
    app.state.fm_chain = object()

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job(reserved=7)))

    assert captured["resolve"] == 2
    assert main_r2.puts and main_r2.deletes == main_r2.puts
    assert captured["settlement"] == 0
    assert captured["failure"]["code"] == "license_revoked"
    _assert_no_prefinal_output_reference(captured["pre_final_events"])
    _assert_no_prefinal_output_reference(captured["events"])


def test_real_prefinal_events_hide_duplicate_cut_output_before_late_revoke(monkeypatch):
    captured = {"resolve": 0, "settlement": 0}
    base = {
        "source": "ai",
        "sectionId": "section-a",
        "sectionRole": "studio",
        "cutType": "horizon",
        "shot": "full",
        "direction": "front",
        "pose": "auto",
        "refScope": "all",
    }
    _patch_inputs(
        monkeypatch,
        captured,
        project={"copywriting": False, "facemarket_license_id": "later-lock"},
        storyboard=[{**base, "id": "original"}, {**base, "id": "copy"}],
    )

    def row(status):
        return {**_license_row(status=status)}

    async def fake_resolve(conn, model_id, *, license_id=None, **kwargs):
        captured["resolve"] += 1
        assert model_id == MODEL_ID and license_id == LIC_ID
        return row("active" if captured["resolve"] == 1 else "revoked")

    async def fake_verify(app, license_row, **kwargs):
        assert license_row["status"] == "active"

    async def fake_assets(conn, model_id, *, enrollment_id, evidence_version):
        return [
            {"key": CURRENT_FACE_KEY, "mime": "image/png", "bucket": "face"},
            {"key": CURRENT_GRID_KEY, "mime": "image/png", "bucket": "face"},
        ]

    async def fake_lock(conn):
        captured["pre_final_events"] = list(captured["events"])

    async def fake_settlement(*_args, **_kwargs):
        captured["settlement"] += 1

    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_assets)
    monkeypatch.setattr(dpj.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(dpj.facemarket, "record_license_settlement", fake_settlement)

    app, main_r2 = _app(row("active"))
    main_r2.fail_delete = True
    app.state.fm_chain = object()

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job(reserved=7)))

    dones = [
        payload for event_type, payload in captured["pre_final_events"]
        if event_type == "step" and payload.get("status") == "cut_done"
    ]
    assert [done["blockId"] for done in dones] == ["original", "copy"]
    assert captured["resolve"] == 2
    assert main_r2.puts and main_r2.deletes == main_r2.puts
    assert captured["settlement"] == 0
    assert captured["failure"]["code"] == "license_revoked"
    _assert_no_prefinal_output_reference(captured["pre_final_events"])
    _assert_no_prefinal_output_reference(captured["events"])


def test_successful_real_result_keeps_stable_asset_url_after_finalization(monkeypatch):
    captured = {}
    _patch_inputs(
        monkeypatch,
        captured,
        project={"copywriting": False, "facemarket_license_id": "later-lock"},
    )
    row = _license_row()
    app, _ = _app(row)
    _patch_snapshot_success(monkeypatch, row)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    _assert_no_prefinal_output_reference(captured["events"])
    assert captured["cut_results"][0]["imageUrl"].startswith("/v1/assets/")
    assert captured["cut_results"][0]["imageUrl"].endswith("/file")
    assert captured.get("failure") is None


def test_non_real_detail_cut_done_keeps_preview_url_before_finalize(monkeypatch):
    captured = {}
    _patch_inputs(monkeypatch, captured, project={"copywriting": False})
    app, _ = _app(_license_row())

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    dones = [
        payload for event_type, payload in captured["events"]
        if event_type == "step" and payload.get("status") == "cut_done"
    ]
    assert len(dones) == 1
    assert dones[0]["previewUrl"].startswith("https://r2.test/users/")


def test_detail_final_recheck_revoked_license_deletes_outputs_and_refunds(monkeypatch):
    captured = {"resolve": 0, "settlement": 0}
    _patch_inputs(
        monkeypatch,
        captured,
        project={"copywriting": False},
        storyboard=[{"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"}],
    )

    def row(status):
        return {**_license_row(status=status)}

    async def fake_resolve(conn, model_id, *, license_id=None, **kwargs):
        captured["resolve"] += 1
        assert model_id == MODEL_ID and license_id == LIC_ID
        return row("active" if captured["resolve"] == 1 else "revoked")

    async def fake_verify(app, license_row, **kwargs):
        assert license_row["status"] == "active"

    async def fake_assets(conn, model_id, *, enrollment_id, evidence_version):
        return [
            {"key": CURRENT_FACE_KEY, "mime": "image/png", "bucket": "face"},
            {"key": CURRENT_GRID_KEY, "mime": "image/png", "bucket": "face"},
        ]

    async def fake_settlement(*_args, **_kwargs):
        captured["settlement"] += 1

    async def fake_lock(conn):
        captured["locked"] = True

    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_assets)
    monkeypatch.setattr(dpj.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(dpj.facemarket, "record_license_settlement", fake_settlement)

    app, main_r2 = _app(row("active"))
    app.state.fm_chain = object()
    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job(reserved=7)))

    assert captured["resolve"] == 2
    assert "editor_blocks" not in captured
    assert captured["settlement"] == 0
    assert captured["failure"]["reserved"] == 7
    assert captured["failure"]["code"] == "license_revoked"
    assert main_r2.puts and main_r2.deletes == main_r2.puts


def test_detail_final_recheck_delete_failure_leaves_cleanup_intent(monkeypatch):
    events = []
    captured = {"resolve": 0, "settlement": 0}
    _patch_inputs(
        monkeypatch,
        captured,
        project={"copywriting": False},
        storyboard=[{"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"}],
    )

    def row(status):
        return {**_license_row(status=status)}

    async def fake_resolve(conn, model_id, *, license_id=None, **kwargs):
        captured["resolve"] += 1
        assert model_id == MODEL_ID and license_id == LIC_ID
        return row("active" if captured["resolve"] == 1 else "revoked")

    async def fake_verify(app, license_row, **kwargs):
        assert license_row["status"] == "active"

    async def fake_assets(conn, model_id, *, enrollment_id, evidence_version):
        return [
            {"key": CURRENT_FACE_KEY, "mime": "image/png", "bucket": "face"},
            {"key": CURRENT_GRID_KEY, "mime": "image/png", "bucket": "face"},
        ]

    async def fake_settlement(*_args, **_kwargs):
        captured["settlement"] += 1

    async def fake_lock(conn):
        captured["locked"] = True

    async def fake_intent(conn, **kwargs):
        events.append("intent")
        return "intent-1"

    async def forbidden_clear(conn, intent_id):
        events.append(f"clear:{intent_id}")

    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_assets)
    monkeypatch.setattr(dpj.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(dpj.facemarket, "record_license_settlement", fake_settlement)
    monkeypatch.setattr(
        dpj.repo,
        "create_ai_output_cleanup_intent",
        fake_intent,
        raising=False,
    )
    monkeypatch.setattr(
        dpj.repo,
        "clear_ai_output_cleanup_intent",
        forbidden_clear,
        raising=False,
    )

    app, main_r2 = _app(row("active"))
    main_r2.fail_delete = True
    original_put = main_r2.put_bytes

    def tracking_put(*args, **kwargs):
        events.append("put")
        return original_put(*args, **kwargs)

    main_r2.put_bytes = tracking_put
    app.state.fm_chain = object()

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job(reserved=7)))

    assert events[:2] == ["intent", "put"]
    assert main_r2.puts and main_r2.deletes == main_r2.puts
    assert not any(event.startswith("clear:") for event in events)
    assert "editor_blocks" not in captured
    assert captured["settlement"] == 0
    assert captured["failure"]["code"] == "license_revoked"


def test_detail_cancel_after_put_clears_cleanup_intent_after_confirmed_delete(monkeypatch):
    events = []
    captured = {}
    _patch_inputs(
        monkeypatch,
        captured,
        project={"copywriting": False},
        storyboard=[{"id": "b1", "source": "ai", "cutType": "product", "shot": "ghost"}],
    )

    async def cancelled_success(conn, **kwargs):
        captured["success"] = kwargs
        return None

    async def fake_intent(conn, **kwargs):
        events.append("intent")
        return "intent-1"

    async def fake_clear(conn, intent_id):
        events.append(f"clear:{intent_id}")

    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", cancelled_success)
    monkeypatch.setattr(
        dpj.repo,
        "create_ai_output_cleanup_intent",
        fake_intent,
        raising=False,
    )
    monkeypatch.setattr(
        dpj.repo,
        "clear_ai_output_cleanup_intent",
        fake_clear,
        raising=False,
    )

    app, main_r2 = _app(_license_row(), facemarket_enabled=False)
    original_put = main_r2.put_bytes

    def tracking_put(*args, **kwargs):
        events.append("put")
        return original_put(*args, **kwargs)

    main_r2.put_bytes = tracking_put

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert events[:2] == ["intent", "put"]
    assert main_r2.puts and main_r2.deletes == main_r2.puts
    assert events[-1] == "clear:intent-1"
