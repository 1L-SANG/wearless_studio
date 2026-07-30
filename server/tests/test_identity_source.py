"""아이덴티티-소스 상태머신 + 실존 자산 resolve 검증(codex [P1])."""

import asyncio
import contextlib

from app.agents.identity_source import resolve_real_model_assets, select_source


def test_real_requires_active_license():
    # 실자산 있으나 라이선스 없음 → REJECTED(조용한 폴백 금지)
    assert select_source(selected_model_id="m1", license_row=None,
                         has_real_assets=True, has_license_face=False) == "REJECTED"


def test_real_requires_matching_model():
    # 라이선스는 있으나 다른 모델 → REJECTED
    assert select_source(selected_model_id="m1",
                         license_row={"model_id": "m2", "status": "active"},
                         has_real_assets=True, has_license_face=False) == "REJECTED"


def test_real_with_active_matching_license():
    assert select_source(selected_model_id="m1",
                         license_row={"model_id": "m1", "status": "active"},
                         has_real_assets=True, has_license_face=True) == "REAL"


def test_virtual_no_license_needed():
    assert select_source(selected_model_id="mA", license_row=None,
                         has_real_assets=False, has_license_face=False) == "VIRTUAL"


def test_legacy_face_only():
    assert select_source(selected_model_id=None, license_row=None,
                         has_real_assets=False, has_license_face=True) == "LEGACY"


def test_none_when_nothing():
    assert select_source(selected_model_id=None, license_row=None,
                         has_real_assets=False, has_license_face=False) == "NONE"


# ── resolve_real_model_assets ──
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, sql, params=None):
        return None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cur(self._rows)


_FM_UUID = "11111111-1111-1111-1111-111111111111"  # 실존 모델 id 는 uuid (fm_models.id)


def _run(rows):
    return asyncio.run(resolve_real_model_assets(_Conn(rows), _FM_UUID))


class _ExplodingConn:
    """가상모델 id 가 DB 에 닿으면 실패하는 감시 스텁."""

    def cursor(self):
        raise AssertionError("non-UUID model id must not reach the fm_models query")


def test_resolve_virtual_model_id_skips_db_and_returns_none():
    # 가상모델 id(mA 등)는 UUID 가 아니다 — fm_models.id(uuid) 쿼리에 그대로 바인딩하면
    # psycopg InvalidTextRepresentation 으로 상세페이지 잡 전체가 죽는다(2026-07-29 prod 재현:
    # facemarket_enabled=true + 가상모델 선택 조합에서 progress 5 즉사).
    for vid in ("mA", "mB", "mC", "model-1", ""):
        assert asyncio.run(resolve_real_model_assets(_ExplodingConn(), vid)) is None


def test_resolve_ready_returns_two_refs_face_bucket():
    rows = [
        {"assets_status": "ready", "view": "face_front",
         "r2_key": "facemarket/models/m1/face_front.png", "mime": "image/png", "bucket": "face"},
        {"assets_status": "ready", "view": "grid_sedcard",
         "r2_key": "facemarket/models/m1/grid_sedcard.png", "mime": "image/png", "bucket": "face"},
    ]
    refs = _run(rows)
    assert refs is not None and len(refs) == 2
    assert refs[0]["key"].endswith("face_front.png") and refs[0]["bucket"] == "face"
    assert refs[1]["key"].endswith("grid_sedcard.png")


def test_resolve_not_ready_returns_none():
    rows = [{"assets_status": "building", "view": None, "r2_key": None, "mime": None, "bucket": None}]
    assert _run(rows) is None


def test_resolve_missing_view_returns_none():
    rows = [
        {"assets_status": "ready", "view": "face_front",
         "r2_key": "k", "mime": "image/png", "bucket": "face"},
    ]  # grid_sedcard 없음
    assert _run(rows) is None
