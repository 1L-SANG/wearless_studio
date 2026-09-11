"""플래그를 켜도 LoRA 행이 없으면 아무것도 안 바뀐다 — 그 사실을 한 파일에 모아 고정한다.

FACE_IDENTITY_ENABLED=true 로 배포되지만 fm_model_loras 는 아직 비어 있다. 그 상태에서
프롬프트·컷·수요·파드 호출이 전부 이전과 같아야 이 배포가 안전하다.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from app import facemarket
from app.agents import cut_generator, face_identity
from app.services import sam_autoscale
from app.services.face_autoscale import RunpodAutoscaleAdapter, face_demand_snapshot
from conftest import make_settings

REAL = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2026, 9, 11, 3, 0, tzinfo=timezone.utc)


class _Cur:
    def __init__(self, rows):
        self._rows = list(rows)
        self.sql = []

    async def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    async def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self.cur = _Cur(rows)

    def cursor(self):
        return self.cur


def _settings(**kw):
    base = {"gemini_api_key": "x", "r2_bucket": "b", "face_identity_enabled": True}
    base.update(kw)
    return make_settings(**base)


def test_no_lora_row_means_no_face_pass_even_with_the_flag_on():
    """워커가 넘길 근거(spec)가 없으면 얼굴 패스는 걸리지 않는다."""
    spec = {"cutType": "styling", "modelId": REAL, "shot": "full",
            "direction": "front", "faceExposure": "same"}
    assert face_identity.face_identity_from_lora_row(None) is None
    # 가상모델 레지스트리에도 없으면 None — 그게 지금 운영 상태다
    assert cut_generator._face_identity_spec(
        _settings(), dict(spec, modelId="mA"), "top", None) is None


def test_prompt_is_byte_identical_without_profiles():
    """hair·face_shape 값이 없으면 프롬프트가 예전과 한 글자도 다르지 않아야 한다."""
    from app.facemarket_physique import (
        build_body_profile_block, build_face_shape_block, build_hair_block)

    assert build_hair_block(None) == "" and build_hair_block({}) == ""
    assert build_face_shape_block(None) == "" and build_face_shape_block({}) == ""
    assert build_body_profile_block(None) == ""


def test_demand_is_zero_with_no_enabled_lora():
    """켜진 LoRA 가 없으면 착장 컷 잡이 돌아도 수요가 0 — 파드를 켤 이유가 없다."""
    snap = asyncio.run(face_demand_snapshot(_Conn([
        {"t": "fm_model_loras"},
        {"active_face_jobs": 0, "last_face_finished_at": None},
        {"t": "fm_face_warm_pings"},
        {"at": None},
    ])))
    assert (snap.active_sam_jobs, snap.last_upload_at) == (0, None)
    assert sam_autoscale.want_running(snap, idle_minutes=10, now=NOW) is False
    assert sam_autoscale.want_count(snap, idle_minutes=10, now=NOW) == 0


def test_autoscale_on_does_not_touch_runpod_without_demand():
    """FACE_AUTOSCALE=on 이어도 수요 0 이면 RunPod 호출이 0건이다."""
    calls = []

    class _Client:
        def get(self, path):
            calls.append(("GET", path))
            raise AssertionError("수요가 없으면 파드 상태도 볼 이유가 없다")

        def post(self, path, json=None):
            calls.append(("POST", path))
            raise AssertionError("수요가 없으면 파드를 만들지 않는다")

    adapter = RunpodAutoscaleAdapter(
        _settings(face_autoscale="on", face_runpod_api_key="k", face_runpod_pod_id=None),
        client=_Client(), pod_store=None)
    # 등록된 파드가 없다 → discover 는 None, reconciler 는 거기서 멈춘다(아래 want=0 이므로)
    assert asyncio.run(adapter.discover()) is None
    assert calls == []


def test_warm_ping_ignores_models_without_an_enabled_lora():
    """라우트가 그런 모델의 핑을 기록하지 않는다(기록하면 파드가 헛돈다)."""
    import pathlib

    text = pathlib.Path(facemarket.__file__).read_text(encoding="utf-8")
    assert "from fm_model_loras where model_id = %s and enabled and status = 'ready' " in text


# ── 상태 라우트: 모델 단위로 판단한다 ──
def test_status_needs_a_model_to_say_enabled():
    """플래그만 보고 enabled=true 를 주면, LoRA 없는 REAL 프로젝트에서 '준비 중'이 영원히 뜬다."""
    import inspect

    source = inspect.getsource(facemarket.face_render_status)
    assert 'alias="modelId"' in inspect.getsource(facemarket)
    assert "is_real_model_id(model_id)" in source
    assert "and enabled " in source and "status = 'ready'" in source
    # modelId 가 없으면 모델 단위 판단이 불가능하다 → enabled=False 로 남는다
    assert "enabled = False" in source


# ── 상태 라우트 실동작 ──
class _StatusCur:
    """to_regclass → LoRA 조회 순서의 최소 대역."""

    def __init__(self, has_table=True, has_lora=False):
        self._rows = [{"t": "fm_model_loras"} if has_table else {"t": None}]
        if has_table:
            self._rows.append({"x": 1} if has_lora else None)
        self.params = []

    async def execute(self, sql, params=None):
        self.params.append(params)

    async def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _status(monkeypatch, *, flag=True, has_lora=False, model_id=REAL, healthy=True):
    import contextlib
    import types

    cur = _StatusCur(has_lora=has_lora)

    class _C:
        def cursor(self):
            return cur

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _C()

    class _Adapter:
        enabled = True

        async def health_ok(self):
            return healthy

    monkeypatch.setattr(facemarket, "get_conn", fake_conn)
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_settings(face_identity_enabled=flag, facemarket_enabled=True),
        face_autoscaler=types.SimpleNamespace(adapter=_Adapter()))))
    return asyncio.run(facemarket.face_render_status(request, model_id=model_id, user_id="u1")), cur


def test_status_false_for_a_real_model_without_a_lora(monkeypatch):
    """★ 이 버그가 핵심이다 — 파드는 켜질 이유가 없는데 '준비 중'이 영원히 뜨던 경우."""
    body, cur = _status(monkeypatch, has_lora=False)
    assert body == {"ready": False, "enabled": False, "etaMinutes": None}
    assert cur.params[-1] == (REAL,)


def test_status_true_when_the_model_has_an_enabled_lora(monkeypatch):
    body, _ = _status(monkeypatch, has_lora=True, healthy=True)
    assert body["enabled"] is True and body["ready"] is True and body["etaMinutes"] is None


def test_status_reports_eta_while_the_pod_is_still_coming_up(monkeypatch):
    from app.services import face_autoscale

    body, _ = _status(monkeypatch, has_lora=True, healthy=False)
    assert body["enabled"] is True and body["ready"] is False
    assert body["etaMinutes"] == face_autoscale.COLD_START_ETA_MINUTES


def test_status_false_when_the_flag_is_off(monkeypatch):
    body, cur = _status(monkeypatch, flag=False, has_lora=True)
    assert body["enabled"] is False
    assert cur.params == []          # 플래그가 꺼져 있으면 DB 도 안 본다


def test_status_false_for_virtual_models_and_missing_id(monkeypatch):
    for model_id in ("mA", None, ""):
        body, cur = _status(monkeypatch, has_lora=True, model_id=model_id)
        assert body["enabled"] is False, model_id
        assert cur.params == []
