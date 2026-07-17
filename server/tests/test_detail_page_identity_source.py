"""실존 모델(REAL) 아이덴티티-소스 배선 검증(codex [P1]).

REAL: 셀러가 실존 모델(selectedModelId)+활성 라이선스 → 그리드(face_front,grid_sedcard)가
비공개 face 버킷에서 로드돼 컷에 주입되고, 단일 라이선스 얼굴은 미첨부(이중주입 0), 검증 배지 노출.
REJECTED: 실존 자산 있으나 라이선스 불일치/비활성 → 얼굴 미주입, 배지 없음.
"""

import asyncio
import contextlib
import types
from datetime import datetime, timedelta, timezone

from app.workers import detail_page_job as dpj
from conftest import FakeR2, make_settings, worker_job

GRID_KEY = "facemarket/models/model-1/grid_sedcard.png"
FACE_FRONT_KEY = "facemarket/models/model-1/face_front.png"


def _asset_rows(status="ready"):
    return [
        {"assets_status": status, "view": "face_front",
         "r2_key": FACE_FRONT_KEY, "mime": "image/png", "bucket": "face"},
        {"assets_status": status, "view": "grid_sedcard",
         "r2_key": GRID_KEY, "mime": "image/png", "bucket": "face"},
    ]


def _license_meta(model_id="model-1", status="active", days=30):
    return {"id": "lic-1", "model_id": model_id, "status": status,
            "license_valid_until": datetime.now(timezone.utc) + timedelta(days=days),
            "display_name": "노지운"}


class _Cur:
    def __init__(self, asset_rows, license_meta):
        self._assets = asset_rows
        self._lic = license_meta
        self._sql = ""

    async def execute(self, sql, params=None):
        self._sql = " ".join(sql.split())

    async def fetchone(self):
        if "l.id::text as id" in self._sql and "l.model_id::text as model_id" in self._sql:
            return self._lic          # _load_license_row
        return None                    # _load_license_face → 얼굴 없음(REAL은 그리드로 대체)

    async def fetchall(self):
        if "left join fm_model_assets" in self._sql:
            return self._assets        # resolve_real_model_assets
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, asset_rows, license_meta):
        self._a = asset_rows
        self._l = license_meta

    async def commit(self):
        return None

    def cursor(self):
        return _Cur(self._a, self._l)


class _Pool:
    def __init__(self, asset_rows, license_meta):
        self._a = asset_rows
        self._l = license_meta

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn(self._a, self._l)

        return _cm()


class _FaceR2:
    def __init__(self):
        self.gets = []

    def get_bytes(self, key):
        self.gets.append(key)
        return b"\x89PNG-face-bytes"

    def put_bytes(self, key, data, mime):
        return None

    def delete(self, key):
        return None


def _app(asset_rows, license_meta, face_r2):
    state = types.SimpleNamespace(
        settings=make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        pool=_Pool(asset_rows, license_meta), r2=FakeR2(), r2_face=face_r2,
        gemini=types.SimpleNamespace())
    return types.SimpleNamespace(state=state)


def _patch(monkeypatch, captured):
    async def fake_gp(conn, uid, pid):
        return {"facemarket_license_id": "lic-1", "copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"}]

    async def fake_prod(conn, pid):
        return {"clothing_type": "top",
                "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {"selectedModelId": "model-1"}

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


def test_rejected_when_license_model_mismatch(monkeypatch):
    captured = {}
    face_r2 = _FaceR2()
    _patch(monkeypatch, captured)
    # 라이선스가 다른 모델 → REJECTED → 얼굴 미주입, 배지 없음
    app = _app(_asset_rows(), _license_meta(model_id="other-model"), face_r2)

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert face_r2.gets == []                      # 그리드 미로드
    assert captured["license_notice"] is None      # 배지 없음
