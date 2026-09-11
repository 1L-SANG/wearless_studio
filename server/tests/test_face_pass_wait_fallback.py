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
    for memo in (fi._ready_seen, fi._recent_failure, fi._fallback_events):
        memo.clear()
    fi._last_fallback_alert = None
    yield
    for memo in (fi._ready_seen, fi._recent_failure, fi._fallback_events):
        memo.clear()


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
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) == "https://pod-8000.proxy.runpod.net/render"
    assert calls == [HEALTH] and slept == []


def test_waits_while_the_pod_is_coming_up(monkeypatch):
    calls = _probe(monkeypatch, [False, False, True])
    slept = _no_sleep(monkeypatch)
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) == "https://pod-8000.proxy.runpod.net/render"
    assert len(calls) == 3 and len(slept) == 2
    assert slept[0] == fi.FACE_PASS_POLL_SECONDS


def test_gives_up_after_the_budget(monkeypatch):
    _probe(monkeypatch, [False])
    _no_sleep(monkeypatch)
    # 예산 0 → 한 번 보고 포기(대기 코드가 파드를 켜지는 않는다 — 그건 reconciler 몫)
    assert asyncio.run(fi.wait_for_backend(_settings(face_pass_wait_seconds=0), SPEC)) is None


def test_second_cut_of_the_same_job_does_not_wait(monkeypatch):
    calls = _probe(monkeypatch, [True])
    _no_sleep(monkeypatch)
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) == "https://pod-8000.proxy.runpod.net/render"
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) == "https://pod-8000.proxy.runpod.net/render"
    assert len(calls) == 1          # 한 번 뜬 걸 봤으면 다시 묻지 않는다


def test_no_health_address_skips_waiting(monkeypatch):
    calls = _probe(monkeypatch, [False])
    local = fi.FaceIdentitySpec("/local/lora.safetensors", "ohwx man")
    assert asyncio.run(fi.wait_for_backend(_settings(face_identity_backend_url=None), local)) == ""
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
    ({"tries": [{"gate": "identity_low"}], "reason": "gate_failed:identity_lowx3"}, "fallback:gate_failed"),
    ({"tries": [{"gate": "yaw_drift"}], "reason": "gate_failed:yaw_driftx3"}, "fallback:gate_failed"),
    # 얼굴 없음·측면은 렌더 전에 내리는 설계상 건너뜀이다 — 폴백이 아니다(test_face_pass_skip_outcome).
    ({"tries": [], "skipped_reason": "no_face", "reason": "no_face"}, "skipped:no_face"),
    ({"tries": [], "skipped_reason": "yaw", "reason": "yaw"}, "skipped:yaw"),
])
def test_gate_and_skip_reasons(monkeypatch, meta, expected):
    res = fi.FacePassResult(b"ORIG", "image/png", False, meta)
    _, _, outcome = _apply(monkeypatch, result=res)
    assert outcome == {"face_pass": expected}


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


def test_first_alert_is_not_eaten_on_a_freshly_booted_machine(caplog):
    """time.monotonic() 은 부팅 이후 시간이다 — 초깃값을 0.0 으로 두면 갓 뜬 머신에서
    now - 0.0 이 디바운스 창보다 작아 첫 알림이 통째로 먹힌다(2026-09-11 CI 에서 잡혔다)."""
    assert fi._last_fallback_alert is None
    with caplog.at_level(logging.CRITICAL):
        for _ in range(fi.FALLBACK_ALERT_THRESHOLD):
            fi._note_fallback("pod_not_ready")
    assert [r for r in caplog.records if r.levelno == logging.CRITICAL]


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


# ── 파드가 아직 없을 때 / 교체될 때 (url_provider) ──
def _provider(urls):
    """대기 중에 바뀌는 '지금 파드' 를 흉내 낸다. 리스트 순서대로 하나씩 돌려준다."""
    seq = list(urls)

    async def provide():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return provide


def test_waits_while_the_first_pod_is_being_created(monkeypatch):
    """운영은 파드 행이 0개다 — 자동 켜기로 만들어지는 중인 그 컷이 즉시 폴백하면 안 된다."""
    calls = _probe(monkeypatch, [True])
    slept = _no_sleep(monkeypatch)
    provider = _provider([None, None, "https://new-8000.proxy.runpod.net/render"])
    url = asyncio.run(fi.wait_for_backend(_settings(), SPEC, provider))
    assert url == "https://new-8000.proxy.runpod.net/render"
    assert calls == ["https://new-8000.proxy.runpod.net/healthz"]   # 없는 동안엔 찌르지도 않는다
    assert len(slept) == 2                                          # 생길 때까지 기다렸다


def test_pod_swap_during_the_wait_moves_to_the_new_address(monkeypatch):
    """파드가 교체되면(새 id → 새 URL) 대기 중인 컷도 새 주소로 간다."""
    seen = []

    def fake(url, timeout=5.0):
        seen.append(url)
        return "new" in url          # 옛 파드는 끝내 안 뜬다

    monkeypatch.setattr(fi, "_probe_ready", fake)
    _no_sleep(monkeypatch)
    provider = _provider(["https://old-8000.proxy.runpod.net/render",
                          "https://new-8000.proxy.runpod.net/render"])
    url = asyncio.run(fi.wait_for_backend(_settings(), SPEC, provider))
    assert url == "https://new-8000.proxy.runpod.net/render"
    assert seen == ["https://old-8000.proxy.runpod.net/healthz",
                    "https://new-8000.proxy.runpod.net/healthz"]


def test_no_pod_at_all_times_out_as_pod_not_ready(monkeypatch):
    _probe(monkeypatch, [False])
    _no_sleep(monkeypatch)
    provider = _provider([None])
    assert asyncio.run(fi.wait_for_backend(_settings(face_pass_wait_seconds=0), SPEC, provider)) is None


# ── 렌더가 연결 오류면 기억을 지우고 한 번 더 ──
def test_render_connection_error_clears_the_memo_and_retries(monkeypatch):
    fi._ready_seen[HEALTH] = 1e18          # "떠 있다"는 낡은 기억
    probes = _probe(monkeypatch, [True])
    _no_sleep(monkeypatch)
    monkeypatch.setattr(fi, "resolve_backend", lambda s, spec: object())
    results = [fi.FacePassResult(b"ORIG", "image/png", False, {"reason": "backend"}),
               fi.FacePassResult(b"FACE", "image/png", True, {"tries": [{"gate": "ok"}]})]
    monkeypatch.setattr(fi, "run_face_pass", lambda *a, **k: results.pop(0))
    outcome: dict = {}
    image, _ = asyncio.run(fi.apply_face_pass(
        _settings(), b"ORIG", "image/png", SPEC, outcome=outcome))
    assert image == b"FACE" and outcome == {"face_pass": "applied"}
    assert probes == [HEALTH]              # 기억을 버렸으니 다시 확인했다
    assert results == []                   # 렌더를 정확히 두 번 했다


def test_retry_that_fails_again_falls_back(monkeypatch):
    fi._ready_seen[HEALTH] = 1e18
    _probe(monkeypatch, [True])
    _no_sleep(monkeypatch)
    monkeypatch.setattr(fi, "resolve_backend", lambda s, spec: object())
    dead = fi.FacePassResult(b"ORIG", "image/png", False, {"reason": "backend"})
    monkeypatch.setattr(fi, "run_face_pass", lambda *a, **k: dead)
    outcome: dict = {}
    asyncio.run(fi.apply_face_pass(_settings(), b"ORIG", "image/png", SPEC, outcome=outcome))
    assert outcome == {"face_pass": "fallback:backend_error"}


# ── 장애 중에는 컷마다 5분씩 멈추지 않는다 ──
def test_after_a_timeout_the_next_cut_fails_fast(monkeypatch):
    calls = _probe(monkeypatch, [False])
    slept = _no_sleep(monkeypatch)
    settings = _settings(face_pass_wait_seconds=0)
    assert asyncio.run(fi.wait_for_backend(settings, SPEC)) is None     # 첫 컷: 시간 초과
    before = len(calls)
    # 다음 컷은 예산이 남아 있어도 확인 1회만 하고 바로 포기한다
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) is None
    assert len(calls) == before + 1 and slept == []


def test_a_successful_probe_clears_the_failure_memo(monkeypatch):
    fi._recent_failure[HEALTH] = 1e18      # 최근 실패 기억
    _probe(monkeypatch, [True])
    _no_sleep(monkeypatch)
    assert asyncio.run(fi.wait_for_backend(_settings(), SPEC)) == SPEC.backend_url
    assert HEALTH not in fi._recent_failure
