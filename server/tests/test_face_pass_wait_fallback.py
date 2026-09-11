"""파드가 꺼져 있으면 기다렸다가 렌더하고, 그래도 안 뜨면 gpt-image 얼굴로 내보낸다.

폴백 자체는 원래 계약이다(run_face_pass 는 예외를 던지지 않는다). 문제는 **언제** 폴백하느냐였다:
파드는 필요할 때만 켜지는데(콜드 ≈2분, reconciler 60초 주기) 첫 렌더가 즉시 실패하면
가끔 쓰는 셀러는 사실상 항상 생성 모델 얼굴을 받는다. 그래서 렌더 전에 파드를 기다린다.
"""

import asyncio
import logging

import pytest

from app.agents import face_identity as fi
from conftest import make_settings

SPEC = fi.FaceIdentitySpec("facemarket/loras/m/v1.safetensors", "ohwx man",
                           backend_url="https://pod-8000.proxy.runpod.net/render")
HEALTH = "https://pod-8000.proxy.runpod.net/healthz"


@pytest.fixture(autouse=True)
def _reset():
    fi._ready_seen.clear()
    fi._fallback_events.clear()
    fi._last_fallback_alert = 0.0
    yield
    fi._ready_seen.clear()
    fi._fallback_events.clear()


def _settings(**kw):
    base = {"gemini_api_key": "x", "r2_bucket": "b", "face_identity_enabled": True,
            "face_pass_wait_seconds": 300}
    base.update(kw)
    return make_settings(**base)


def _probe(monkeypatch, results):
    """_probe_ready 를 대본대로 답하게 한다. 호출 URL 을 기록한다."""
    calls = []

    def fake(url, timeout=5.0):
        calls.append(url)
        return results[min(len(calls) - 1, len(results) - 1)]

    monkeypatch.setattr(fi, "_probe_ready", fake)
    return calls


def _no_sleep(monkeypatch):
    """폴링 간격을 실제로 기다리지 않는다 — 대기 횟수만 센다."""
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(fi.asyncio, "sleep", fake_sleep)
    return slept


# ── 대기 ──
def test_ready_pod_renders_without_waiting(monkeypatch):
    calls = _probe(monkeypatch, [True])
    slept = _no_sleep(monkeypatch)
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) is True
    assert calls == [HEALTH] and slept == []


def test_waits_while_the_pod_is_coming_up(monkeypatch):
    calls = _probe(monkeypatch, [False, False, True])
    slept = _no_sleep(monkeypatch)
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) is True
    assert len(calls) == 3 and len(slept) == 2
    assert slept[0] == fi.FACE_PASS_POLL_SECONDS


def test_gives_up_after_the_budget(monkeypatch):
    _probe(monkeypatch, [False])
    _no_sleep(monkeypatch)
    # 예산 0 → 한 번 보고 포기(대기 코드가 파드를 켜지는 않는다 — 그건 reconciler 몫)
    assert asyncio.run(fi.wait_for_backend(_settings(face_pass_wait_seconds=0), SPEC)) is False


def test_second_cut_of_the_same_job_does_not_wait(monkeypatch):
    calls = _probe(monkeypatch, [True])
    _no_sleep(monkeypatch)
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) is True
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) is True
    assert len(calls) == 1          # 한 번 뜬 걸 봤으면 다시 묻지 않는다


def test_no_health_address_skips_waiting(monkeypatch):
    calls = _probe(monkeypatch, [False])
    local = fi.FaceIdentitySpec("/local/lora.safetensors", "ohwx man")
    assert asyncio.run(fi.wait_for_backend(_settings(face_identity_backend_url=None), local)) is True
    assert calls == []


# ── 결과 기록 ──
def _apply(monkeypatch, *, ready=True, backend=object(), result=None):
    monkeypatch.setattr(fi, "resolve_backend", lambda s, spec: backend)
    monkeypatch.setattr(fi, "_probe_ready", lambda url, timeout=5.0: ready)
    _no_sleep(monkeypatch)
    if result is not None:
        monkeypatch.setattr(fi, "run_face_pass", lambda *a, **k: result)
    outcome: dict = {}
    image, mime = asyncio.run(fi.apply_face_pass(
        _settings(face_pass_wait_seconds=0), b"ORIG", "image/png", SPEC, outcome=outcome))
    return image, mime, outcome


def test_applied_outcome(monkeypatch):
    ok = fi.FacePassResult(b"FACE", "image/png", True, {"tries": [{"gate": "ok"}]})
    image, _, outcome = _apply(monkeypatch, result=ok)
    assert image == b"FACE" and outcome == {"face_pass": "applied"}


def test_pod_not_ready_falls_back_to_the_generated_face(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("파드가 안 떴으면 렌더를 부르지 않는다")

    monkeypatch.setattr(fi, "run_face_pass", boom)
    image, mime, outcome = _apply(monkeypatch, ready=False)
    assert (image, mime) == (b"ORIG", "image/png")
    assert outcome == {"face_pass": "fallback:pod_not_ready"}


def test_backend_error_is_its_own_reason(monkeypatch):
    # run_face_pass 가 예외를 삼키고 tries 없이 돌아온 경우 = 렌더 자체가 안 됐다
    dead = fi.FacePassResult(b"ORIG", "image/png", False, {"reason": "backend"})
    image, _, outcome = _apply(monkeypatch, result=dead)
    assert image == b"ORIG" and outcome == {"face_pass": "fallback:backend_error"}


def test_missing_backend_is_backend_error(monkeypatch):
    image, _, outcome = _apply(monkeypatch, backend=None)
    assert image == b"ORIG" and outcome == {"face_pass": "fallback:backend_error"}


@pytest.mark.parametrize("meta,expected", [
    ({"tries": [{"gate": "identity_low"}], "reason": "gate_failed:identity_lowx3"}, "gate_failed"),
    ({"tries": [{"gate": "yaw_drift"}], "reason": "gate_failed:yaw_driftx3"}, "gate_failed"),
    ({"tries": [{}], "reason": "no_face"}, "no_face"),
    ({"tries": [{}], "reason": "yaw"}, "no_face"),
])
def test_gate_and_no_face_reasons(monkeypatch, meta, expected):
    res = fi.FacePassResult(b"ORIG", "image/png", False, meta)
    _, _, outcome = _apply(monkeypatch, result=res)
    assert outcome == {"face_pass": f"fallback:{expected}"}


# ── 알림 ──
def test_three_pod_failures_in_the_window_alert_once(caplog):
    with caplog.at_level(logging.CRITICAL):
        for _ in range(3):
            fi._note_fallback("pod_not_ready")
    criticals = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(criticals) == 1
    assert "gpt-image" in criticals[0].getMessage()
    caplog.clear()
    with caplog.at_level(logging.CRITICAL):
        for _ in range(5):
            fi._note_fallback("backend_error")
    assert [r for r in caplog.records if r.levelno == logging.CRITICAL] == []   # 디바운스


def test_two_failures_do_not_alert(caplog):
    with caplog.at_level(logging.CRITICAL):
        fi._note_fallback("pod_not_ready")
        fi._note_fallback("backend_error")
    assert [r for r in caplog.records if r.levelno == logging.CRITICAL] == []


def test_picture_problems_never_alert(caplog):
    """게이트 실패·얼굴 없음은 그림 문제다 — 운영이 손댈 게 없으니 알리지 않는다."""
    with caplog.at_level(logging.CRITICAL):
        for _ in range(10):
            fi._note_fallback("gate_failed")
            fi._note_fallback("no_face")
    assert [r for r in caplog.records if r.levelno == logging.CRITICAL] == []
    assert fi._fallback_events == []


def test_alert_line_matches_the_slack_filter(caplog):
    """Slack 필터는 " CRITICAL " 만 잡는다(log-slack-alerts.yml)."""
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("wearless.face_identity")
    logger.addHandler(handler)
    try:
        for _ in range(3):
            fi._note_fallback("pod_not_ready")
    finally:
        logger.removeHandler(handler)
    assert " CRITICAL " in stream.getvalue()


# ── 가상 모델은 영향 0 ──
def test_virtual_models_have_no_face_pass_path():
    """근거는 fm_model_loras 하나다 — JSON 레지스트리 경로는 삭제됐다."""
    assert not hasattr(fi, "face_identity_from_registry_entry")
    from app.agents import cut_generator

    settings = _settings()
    spec = {"cutType": "styling", "modelId": "mA", "shot": "full",
            "direction": "front", "faceExposure": "same"}
    assert cut_generator._face_identity_spec(settings, spec, "top", None) is None
