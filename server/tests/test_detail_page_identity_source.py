"""실존 모델(REAL) 아이덴티티-소스 배선 검증(codex [P1]).

REAL: 셀러가 실존 모델(selectedModelId)+활성 라이선스 → 그리드(face_front,grid_sedcard)가
비공개 face 버킷에서 로드돼 컷에 주입되고, 단일 라이선스 얼굴은 미첨부(이중주입 0), 검증 배지 노출.
REJECTED: 선택한 실존 모델에 검증된 라이선스가 없으면 자산 조회 전에 잡 전체 실패.
"""

import asyncio
import contextlib
import types
from datetime import datetime, timedelta, timezone

from app import facemarket
from app.workers import detail_page_job as dpj
from conftest import FakeR2, make_settings, worker_job

GRID_KEY = "facemarket/models/11111111-1111-1111-1111-111111111111/grid_sedcard.png"
FACE_FRONT_KEY = "facemarket/models/11111111-1111-1111-1111-111111111111/face_front.png"


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
            "display_name": "노지운", "unit_price": 100}


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


def _app(asset_rows, license_meta, face_r2):
    state = types.SimpleNamespace(
        settings=make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
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

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    # 그리드 2장이 비공개 face 버킷에서 로드됨
    assert GRID_KEY in face_r2.gets
    assert FACE_FRONT_KEY in face_r2.gets
    # 검증 배지(실존 모델) 노출
    assert captured["license_notice"] is not None
    assert captured["license_notice"]["licenseId"] == "lic-1"
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

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

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

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert app.state.pool.asset_queries == 0
    assert face_r2.gets == []
    assert captured.get("calls") is None
    assert captured.get("success") is None
    assert captured.get("settlements") is None
    assert captured["failure"]["reserved"] == 1


def test_product_only_selected_real_model_does_not_require_face_license(monkeypatch):
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

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert app.state.pool.asset_queries == 0
    assert face_r2.gets == []
    assert captured["calls"]
    assert captured["success"]["charge"] == 1
    assert captured.get("failure") is None
