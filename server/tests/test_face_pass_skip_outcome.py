"""설계상 건너뜀(yaw·no_face·too_small)은 폴백이 아니다 — "skipped:<사유>" 로 적고 재대기·재실행·알림을 하지 않는다.

실측(2026-09-11 잡 6c270b84, 옆모습 컷 blk_ref4): prepare_image 가 yaw 로 건너뛰면 tries 가 비어
apply_face_pass 가 "렌더 자체 실패"로 오판했다 → 파드 기억 삭제 → 다시 대기 → 다시 실행(또 yaw) →
원장에 fallback:backend_error → CRITICAL 알림 카운트(+1). 진짜 사유는 api 로그 meta.reason 에만 남았다.
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


def _skipped(reason: str) -> fi.FacePassResult:
    """run_face_pass 가 prepare_image 단계에서 건너뛴 결과 — 실제 코드가 만드는 모양 그대로(tries 없음)."""
    return fi.FacePassResult(b"ORIG", "image/png", False, {
        "applied": False, "fallback": True, "attempts": 0, "seed": None, "tries": [],
        "skipped_reason": reason, "reason": reason, "pose_risk": False,
    })


def _run(monkeypatch, results):
    """파드는 떠 있고(기억 있음), run_face_pass 는 대본대로 답한다. 프로브·렌더 호출 수를 센다."""
    fi._ready_seen[HEALTH] = 1e18
    probes = []
    monkeypatch.setattr(fi, "_probe_ready", lambda url, timeout=5.0: probes.append(url) or True)

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(fi.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(fi, "resolve_backend", lambda s, spec: object())
    renders = []

    def fake_run(*_a, **_k):
        renders.append(1)
        return results.pop(0) if len(results) > 1 else results[0]   # 대본이 다하면 마지막 답을 반복

    monkeypatch.setattr(fi, "run_face_pass", fake_run)
    outcome: dict = {}
    image, mime = asyncio.run(fi.apply_face_pass(_settings(), b"ORIG", "image/png", SPEC, outcome=outcome))
    return image, mime, outcome, probes, renders


@pytest.mark.parametrize("reason", ["yaw", "no_face", "too_small"])
def test_design_skip_is_recorded_as_skipped_not_backend_error(monkeypatch, reason):
    image, mime, outcome, _, _ = _run(monkeypatch, [_skipped(reason)])
    assert (image, mime) == (b"ORIG", "image/png")
    assert outcome == {"face_pass": f"skipped:{reason}"}


def test_design_skip_does_not_forget_the_pod_or_render_again(monkeypatch):
    _, _, _, probes, renders = _run(monkeypatch, [_skipped("yaw")])
    assert renders == [1]              # 한 번 보고 끝 — 같은 그림을 다시 돌려도 같은 yaw 다
    assert probes == []                # "떠 있다"는 기억을 지우지 않았다(다시 대기하지 않았다)
    assert HEALTH in fi._ready_seen


def test_design_skip_does_not_count_toward_the_pod_alert(monkeypatch, caplog):
    with caplog.at_level(logging.CRITICAL):
        for _ in range(fi.FALLBACK_ALERT_THRESHOLD + 1):
            _run(monkeypatch, [_skipped("yaw")])
    assert fi._fallback_events == []
    assert [r for r in caplog.records if r.levelno == logging.CRITICAL] == []


def test_connection_error_still_retries_once_and_falls_back_as_backend_error(monkeypatch):
    """연결 오류(예외로 끝나 tries 없음)는 기존대로 — 기억을 지우고 한 번 더 기다렸다 재실행한다."""
    dead = fi.FacePassResult(b"ORIG", "image/png", False, {"tries": [], "reason": "error:ConnectError"})
    _, _, outcome, probes, renders = _run(monkeypatch, [dead, dead])
    assert renders == [1, 1] and probes == [HEALTH]
    assert outcome == {"face_pass": "fallback:backend_error"}
    assert len(fi._fallback_events) == 1
