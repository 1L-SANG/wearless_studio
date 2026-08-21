"""실존 모델(REAL) 아이덴티티-소스 배선 검증(codex [P1]).

REAL: 셀러가 실존 모델(selectedModelId)+활성 라이선스 → 그리드(face_front,grid_sedcard)가
비공개 face 버킷에서 로드돼 컷에 주입되고, 단일 라이선스 얼굴은 미첨부(이중주입 0), 검증 배지 노출.
REJECTED: 선택한 실존 모델에 검증된 라이선스가 없으면 자산 조회 전에 잡 전체 실패.
"""

import asyncio
import contextlib
import types
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import facemarket
from app.agents import identity_source
from app.workers import detail_page_job as dpj
from conftest import FakeR2, make_settings, worker_job

GRID_KEY = "facemarket/models/11111111-1111-1111-1111-111111111111/grid_sedcard.png"
FACE_FRONT_KEY = "facemarket/models/11111111-1111-1111-1111-111111111111/face_front.png"
MODEL_ID = "11111111-1111-1111-1111-111111111111"
LICENSE_ID = "33333333-3333-3333-3333-333333333333"
ENROLLMENT_ID = "22222222-2222-2222-2222-222222222222"
CATEGORY = "일반 여성 의류"


def _snapshot_payload(*, model_id=MODEL_ID, license_id=LICENSE_ID):
    return {
        "mode": "generate",
        "modelId": MODEL_ID,
        "brandUseCategory": CATEGORY,
        "_facemarket": {"modelId": model_id, "licenseId": license_id},
    }


def _runtime_row(**overrides):
    row = {
        "id": LICENSE_ID,
        "model_id": MODEL_ID,
        "model_name": "홍*동",
        "status": "active",
        "license_valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "unit_price": 100,
        "vc_id": "vc-1",
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
    row.update(overrides)
    return row


def _asset_rows(status="ready"):
    enrollment_id = "22222222-2222-2222-2222-222222222222"
    return [
        {"model_status": "verified", "assets_status": status,
         "current_enrollment_id": enrollment_id, "view": "face_front",
         "r2_key": FACE_FRONT_KEY, "mime": "image/png", "bucket": "face",
         "source_enrollment_id": enrollment_id, "evidence_version": "policy-v1"},
        {"model_status": "verified", "assets_status": status,
         "current_enrollment_id": enrollment_id, "view": "grid_sedcard",
         "r2_key": GRID_KEY, "mime": "image/png", "bucket": "face",
         "source_enrollment_id": enrollment_id, "evidence_version": "policy-v1"},
    ]


def _license_meta(model_id="11111111-1111-1111-1111-111111111111", status="active", days=30):
    return {"id": "lic-1", "model_id": model_id, "status": status,
            "license_valid_until": datetime.now(timezone.utc) + timedelta(days=days),
            "display_name": "노지운", "unit_price": 100, "vc_id": "vc-1",
            "vc_status_uri": None}


class _Cur:
    def __init__(self, pool):
        self._pool = pool
        self._sql = ""
        self._params = ()

    async def execute(self, sql, params=None):
        self._sql = " ".join(sql.split())
        self._params = params or ()
        if "left join fm_model_assets" in self._sql:
            self._pool.asset_queries += 1

    async def fetchone(self):
        license_meta = self._pool.license_meta
        if "from fm_licenses where id" in self._sql:
            return license_meta
        if "from fm_licenses where model_id" in self._sql:
            if license_meta and str(license_meta["model_id"]) == str(self._params[0]):
                return license_meta
            return None
        if "l.id::text as id" in self._sql and "l.model_id::text as model_id" in self._sql:
            return license_meta       # _load_license_row
        if "select model_id::text as model_id, unit_price" in self._sql:
            if license_meta:
                return {
                    "model_id": license_meta["model_id"],
                    "unit_price": license_meta["unit_price"],
                }
            return None
        return None                    # _load_license_face → 얼굴 없음(REAL은 그리드로 대체)

    async def fetchall(self):
        if "left join fm_model_assets" in self._sql:
            return self._pool.asset_rows  # resolve_real_model_assets
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, pool):
        self._pool = pool

    async def commit(self):
        return None

    def cursor(self):
        return _Cur(self._pool)


class _Pool:
    def __init__(self, asset_rows, license_meta):
        self.asset_rows = asset_rows
        self.license_meta = license_meta
        self.asset_queries = 0

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn(self)

        return _cm()


class _FaceR2:
    def __init__(self):
        self.gets = []

    def get_bytes(self, key):
        self.gets.append(key)
        return b"\x89PNG-face-bytes"

    def put_bytes(self, key, data, mime, cache=None):
        return None

    def delete(self, key):
        return None


def _app(asset_rows, license_meta, face_r2, *, vc_required=False):
    state = types.SimpleNamespace(
        settings=make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            facemarket_enabled=True,
            fm_vc_required=vc_required,
            opendid_holder_url="http://holder" if vc_required else None,
            opendid_holder_hmac_secret="shared-secret" if vc_required else None,
        ),
        pool=_Pool(asset_rows, license_meta), r2=FakeR2(), r2_face=face_r2,
        gemini=types.SimpleNamespace())
    return types.SimpleNamespace(state=state)


def _patch(monkeypatch, captured, *, project=None, analysis=None, storyboard=None):
    async def fake_gp(conn, uid, pid):
        return project or {"facemarket_license_id": "lic-1", "copywriting": False}

    async def fake_sb(conn, pid):
        if storyboard is not None:
            return storyboard
        return [{"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"}]

    async def fake_prod(conn, pid):
        return {"clothing_type": "top",
                "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        if analysis is not None:
            return analysis
        return {"selectedModelId": "11111111-1111-1111-1111-111111111111"}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *,
                       analysis=None, manifest=None, has_face=False):
        captured.setdefault("calls", []).append(
            {"n_images": len(images), "has_face": has_face, "manifest": manifest})
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, *,
                      license_notice=None):
        captured["license_notice"] = license_notice
        return [{"id": "b0", "kind": "hook", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured["success"] = kw
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_finalize_failure(conn, **kw):
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
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_finalize_failure)
    monkeypatch.setattr(dpj, "_emit", fake_emit)


def test_real_source_injects_grid_from_face_bucket_and_shows_badge(monkeypatch):
    captured = {}
    face_r2 = _FaceR2()
    _patch(monkeypatch, captured)
    app = _app(_asset_rows(), _license_meta(), face_r2)

    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        return _runtime_row()

    async def fake_verify(app, row, **kwargs):
        return None

    async def fake_assets(conn, model_id, *, enrollment_id, evidence_version):
        return [
            {"key": FACE_FRONT_KEY, "mime": "image/png", "bucket": "face"},
            {"key": GRID_KEY, "mime": "image/png", "bucket": "face"},
        ]

    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_assets)

    asyncio.run(dpj.run_detail_page_job(
        app, worker_job(_snapshot_payload(), credits_reserved=1)
    ))

    # 그리드 2장이 비공개 face 버킷에서 로드됨
    assert GRID_KEY in face_r2.gets
    assert FACE_FRONT_KEY in face_r2.gets
    # 검증 배지(실존 모델) 노출
    assert captured["license_notice"] is not None
    assert captured["license_notice"]["licenseId"] == LICENSE_ID
    # 생성 호출에 그리드 2장 포함(그리드가 아이덴티티 앵커)
    assert captured["calls"] and captured["calls"][0]["n_images"] >= 2


def test_locked_license_model_mismatch_fails_before_real_asset_resolution(monkeypatch):
    captured = {}
    face_r2 = _FaceR2()
    _patch(monkeypatch, captured)
    app = _app(_asset_rows(), _license_meta(model_id="other-model"), face_r2)
    app.state.fm_chain = object()

    async def fake_settlement(*_args, **_kwargs):
        captured.setdefault("settlements", []).append(_kwargs)

    monkeypatch.setattr(facemarket, "record_license_settlement", fake_settlement)

    asyncio.run(dpj.run_detail_page_job(
        app,
        worker_job(
            _snapshot_payload(model_id="44444444-4444-4444-4444-444444444444"),
            credits_reserved=1,
        ),
    ))

    assert app.state.pool.asset_queries == 0
    assert face_r2.gets == []
    assert captured.get("calls") is None
    assert captured.get("success") is None
    assert captured.get("settlements") is None
    assert captured["failure"]["reserved"] == 1


def test_selected_real_model_without_license_fails_before_real_asset_resolution(monkeypatch):
    captured = {}
    face_r2 = _FaceR2()
    _patch(
        monkeypatch,
        captured,
        project={"copywriting": False},
        analysis={"selectedModelId": "11111111-1111-1111-1111-111111111111"},
    )
    app = _app(_asset_rows(), None, face_r2)
    app.state.fm_chain = object()

    async def fake_settlement(*_args, **_kwargs):
        captured.setdefault("settlements", []).append(_kwargs)

    monkeypatch.setattr(facemarket, "record_license_settlement", fake_settlement)

    async def missing_license(conn, model_id, *, license_id=None):
        return None

    monkeypatch.setattr(facemarket, "resolve_model_license", missing_license)

    asyncio.run(dpj.run_detail_page_job(
        app, worker_job(_snapshot_payload(), credits_reserved=1)
    ))

    assert app.state.pool.asset_queries == 0
    assert face_r2.gets == []
    assert captured.get("calls") is None
    assert captured.get("success") is None
    assert captured.get("settlements") is None
    assert captured["failure"]["reserved"] == 1


def test_product_only_queued_real_model_does_not_require_face_assets(monkeypatch):
    captured = {}
    face_r2 = _FaceR2()
    _patch(
        monkeypatch,
        captured,
        project={"copywriting": False},
        analysis={"selectedModelId": "11111111-1111-1111-1111-111111111111"},
        storyboard=[
            {
                "id": "product",
                "source": "ai",
                "cutType": "product",
                "shot": "ghost",
            }
        ],
    )
    app = _app(_asset_rows(), None, face_r2)

    asyncio.run(dpj.run_detail_page_job(
        app, worker_job(_snapshot_payload(), credits_reserved=1)
    ))

    assert app.state.pool.asset_queries == 0
    assert face_r2.gets == []
    assert captured["calls"]
    assert captured["success"]["charge"] == 1
    assert captured.get("failure") is None


@pytest.mark.parametrize(
    ("analysis", "license_status", "vc_required"),
    [
        ({"selectedModelId": "mB"}, "revoked", False),
        ({"selected_model_id": "mB"}, "active", True),
    ],
    ids=("revoked-old-license", "holder-outage-snake-alias"),
)
def test_virtual_worn_selection_ignores_stale_facemarket_license(
    monkeypatch,
    analysis,
    license_status,
    vc_required,
):
    captured = {}
    calls = {"verify": 0, "face": 0, "assets": 0, "settlement": 0}
    face_r2 = _FaceR2()
    _patch(monkeypatch, captured, analysis=analysis)
    app = _app(
        _asset_rows(),
        _license_meta(status=license_status),
        face_r2,
        vc_required=vc_required,
    )
    app.state.fm_chain = object()

    original_verify = facemarket.verify_license
    original_asset_resolver = identity_source.resolve_real_model_assets

    async def tracked_verify(*args, **kwargs):
        calls["verify"] += 1
        return await original_verify(*args, **kwargs)

    async def tracked_asset_resolver(*args, **kwargs):
        calls["assets"] += 1
        return await original_asset_resolver(*args, **kwargs)

    async def holder_down(*_args, **_kwargs):
        raise httpx.ConnectError("holder down")

    async def tracked_settlement(*_args, **_kwargs):
        calls["settlement"] += 1

    monkeypatch.setattr(facemarket, "verify_license", tracked_verify)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", tracked_asset_resolver)
    monkeypatch.setattr(facemarket, "record_license_settlement", tracked_settlement)
    if vc_required:
        monkeypatch.setattr(facemarket.holder_client, "post", holder_down)

    model_id = analysis.get("selectedModelId") or analysis.get("selected_model_id")
    asyncio.run(dpj.run_detail_page_job(
        app,
        worker_job({"mode": "generate", "modelId": model_id,
                    "brandUseCategory": None}, credits_reserved=1),
    ))

    assert calls == {"verify": 0, "face": 0, "assets": 0, "settlement": 0}
    assert face_r2.gets == []
    assert app.state.pool.asset_queries == 0
    assert captured["calls"]
    assert captured["license_notice"] is None
    assert captured["success"]["charge"] == 1
    assert captured.get("failure") is None


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing_snapshot", "model_unavailable"),
        ("mismatched_snapshot", "model_unavailable"),
        ("revoked", "license_revoked"),
        ("holder", "holder_unavailable"),
        ("stale_evidence", "model_enrollment_unavailable"),
        ("missing_refs", "model_assets_unavailable"),
        ("r2_failure", "model_assets_unavailable"),
    ],
)
def test_real_worker_denials_refund_without_output_or_settlement(
    monkeypatch, case, expected_code
):
    captured = {"settlements": 0, "resolver": 0, "verify": 0, "assets": 0}
    _patch(
        monkeypatch,
        captured,
        analysis={"selectedModelId": "different-mutable-model"},
    )
    face_r2 = _FaceR2()
    if case == "r2_failure":
        def fail_read(key):
            face_r2.gets.append(key)
            raise RuntimeError("private object unavailable")
        face_r2.get_bytes = fail_read
    app = _app([], None, face_r2)

    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        captured["resolver"] += 1
        return _runtime_row()

    async def fake_verify(app, row, **kwargs):
        captured["verify"] += 1
        if case in {"revoked", "holder", "stale_evidence"}:
            raise facemarket._err(expected_code, "blocked", status=503 if case == "holder" else 409)

    async def fake_assets(conn, model_id, *, enrollment_id, evidence_version):
        captured["assets"] += 1
        assert enrollment_id == ENROLLMENT_ID and evidence_version == "policy-v1"
        if case == "missing_refs":
            return None
        return [
            {"key": FACE_FRONT_KEY, "mime": "image/png", "bucket": "face"},
            {"key": GRID_KEY, "mime": "image/png", "bucket": "face"},
        ]

    async def fake_settlement(*args, **kwargs):
        captured["settlements"] += 1

    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_assets)
    monkeypatch.setattr(facemarket, "record_license_settlement", fake_settlement)
    app.state.fm_chain = object()

    payload = _snapshot_payload()
    if case == "missing_snapshot":
        payload.pop("_facemarket")
    elif case == "mismatched_snapshot":
        payload["_facemarket"]["modelId"] = "44444444-4444-4444-4444-444444444444"
    asyncio.run(dpj.run_detail_page_job(
        app,
        worker_job(payload, credits_reserved=7),
    ))

    assert captured.get("calls") is None
    assert captured.get("success") is None
    assert captured["settlements"] == 0
    assert captured["failure"]["reserved"] == 7
    assert captured["failure"]["code"] == expected_code
    if case in {"revoked", "holder", "stale_evidence"}:
        assert face_r2.gets == [] and captured["assets"] == 0
    if case in {"missing_snapshot", "mismatched_snapshot"}:
        assert captured["resolver"] == 0


def test_real_worker_snapshot_wins_and_notice_is_masked(monkeypatch):
    captured = {}
    _patch(
        monkeypatch,
        captured,
        analysis={"selectedModelId": "different-mutable-model"},
    )
    app = _app([], None, _FaceR2())

    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        assert model_id == MODEL_ID and license_id == LICENSE_ID
        return _runtime_row()

    async def fake_verify(app, row, **kwargs):
        assert kwargs == {"model_id": MODEL_ID, "brand_use_category": CATEGORY}

    async def fake_assets(conn, model_id, *, enrollment_id, evidence_version):
        return [
            {"key": FACE_FRONT_KEY, "mime": "image/png", "bucket": "face"},
            {"key": GRID_KEY, "mime": "image/png", "bucket": "face"},
        ]

    async def fake_settlement(app, **kwargs):
        captured["settlement"] = kwargs

    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_assets)
    monkeypatch.setattr(facemarket, "record_license_settlement", fake_settlement)
    app.state.fm_chain = object()

    asyncio.run(dpj.run_detail_page_job(
        app,
        worker_job(_snapshot_payload(), credits_reserved=1),
    ))

    assert captured["license_notice"]["modelName"] == "홍*동"
    assert captured["license_notice"]["licenseId"] == LICENSE_ID
    assert captured["settlement"] == {
        "payment_key": "job:j1",
        "license_id": LICENSE_ID,
        "model_id": MODEL_ID,
        "total": 100,
        "job_id": "j1",
    }
    assert captured.get("failure") is None
