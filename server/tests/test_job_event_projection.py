"""SSE 잡 이벤트 표면 — `qcScores` 를 좁혀 놓고 이벤트로 흘리면 좁힌 의미가 없다.

이 파일이 막는 것
-----------------
`public_qc_scores` 는 최종 페이로드에서 원본 자산 id·원본 바이트 해시·라우팅 정책을
없앤다. 그런데 같은 정보가 `hybrid_stripe_model` 같은 이벤트로 SSE 에 그대로 나가고
있었다(`StripeModel.summary()` 가 `source_asset_id`·`source_sha256` 를 싣는다). 직렬화
지점이 셋인데 둘만 좁히면 좁힌 것이 아니다.

기록은 그대로 둔다 — `job_events` 의 내부 진단 가치는 유지하고, **나가는 자리에서만**
좁힌다.

왜 여기만 denylist 인가
-----------------------
이벤트 타입이 수십 가지고 모양이 제각각이라 allowlist 로 하면 새 진단 이벤트가 전부 빈
껍데기가 된다. 대신 민감한 **이름**을 재귀로 지우고, 아래 전수 시험으로 그 목록이
워커가 실제로 내보내는 것을 덮는지 확인한다. denylist 는 새 필드에 대해 열려 있으므로
그 시험이 없으면 이 방어는 시간이 지나면서 조용히 무너진다.
"""

import inspect

import pytest

from app.services.public_qc_projection import (
    _EVENT_SENSITIVE_KEYS,
    public_job_event_payload,
)


def test_the_stripe_model_event_no_longer_carries_source_identity():
    """실제로 샜던 이벤트 — `**model.summary()` 가 자산 id 와 원본 해시를 실었다."""
    payload = {"ok": True, "source_slot": "front", "axis": "vertical",
               "period_px": 24.0, "confidence": 0.95,
               "source_asset_id": "a-1", "source_sha256": "deadbeef" * 8}
    out = public_job_event_payload(payload)
    assert "source_asset_id" not in out
    assert "source_sha256" not in out
    # 진단 가치는 남는다 — 좁히는 것이 목적이지 비우는 것이 목적이 아니다.
    assert out["period_px"] == 24.0
    assert out["axis"] == "vertical"
    assert out["source_slot"] == "front"


def test_sensitive_keys_are_removed_at_any_depth():
    """중첩 안에 숨어 있으면 지워지지 않는 구현이 흔하다."""
    payload = {"step": "hybrid", "detail": {"inner": {"source_sha256": "x",
                                                      "keep": 1}},
               "list": [{"assetId": "a-2", "kept": True}]}
    out = public_job_event_payload(payload)
    assert out["detail"]["inner"] == {"keep": 1}
    assert out["list"][0] == {"kept": True}


def test_routing_policy_does_not_ship_on_the_event_stream():
    payload = {"pipelinePolicy": {"lane": "fast"}, "modelTier": "pro",
               "generationPath": "regen", "truthPackageId": "t-1", "candidate": "A"}
    out = public_job_event_payload(payload)
    for leaked in ("pipelinePolicy", "modelTier", "generationPath", "truthPackageId"):
        assert leaked not in out, leaked
    assert out["candidate"] == "A"


def test_non_dict_payloads_pass_through():
    assert public_job_event_payload(None) is None
    assert public_job_event_payload("plain") == "plain"
    assert public_job_event_payload([1, 2]) == [1, 2]


def test_projection_does_not_mutate_the_stored_event():
    """기록은 그대로 둔다 — 내부 진단이 이 투영 때문에 사라지면 안 된다."""
    payload = {"source_sha256": "x", "keep": {"inner": 1}}
    out = public_job_event_payload(payload)
    assert payload["source_sha256"] == "x", "원본이 변형됐다"
    assert out is not payload
    assert out["keep"] is not payload["keep"]


def test_deeply_nested_payloads_do_not_recurse_forever():
    node = {"leaf": 1}
    for _ in range(40):
        node = {"child": node, "source_sha256": "x"}
    out = public_job_event_payload(node)      # 터지지 않아야 한다
    assert "source_sha256" not in out


# ── 전수: 워커가 내보내는 민감 키가 목록 밖에 있지 않은가 ────────────────────
#: 민감하다고 판단하는 이름의 **패턴**. 워커 소스에서 이 패턴에 걸리는 키를 전부 찾아
#: `_EVENT_SENSITIVE_KEYS` 가 덮는지 본다.
_SENSITIVE_PATTERNS = ("sha256", "asset_id", "assetid")


def test_no_sensitive_key_emitted_by_the_worker_escapes_the_denylist():
    """denylist 의 완전성 — 새 민감 필드가 조용히 새는 것을 막는다.

    워커가 이벤트로 내보내는 키워드 인자와 dict 리터럴 키를 훑어, 민감 패턴에 걸리는
    이름이 전부 `_EVENT_SENSITIVE_KEYS` 에 있는지 확인한다. 없으면 목록에 넣거나,
    민감하지 않은 이유를 여기 적어야 한다.
    """
    import re

    from app.services.hybrid_composite import types
    from app.workers import mannequin_job

    lowered = {k.lower() for k in _EVENT_SENSITIVE_KEYS}
    found: set[str] = set()
    for module in (mannequin_job, types):
        src = inspect.getsource(module)
        for match in re.finditer(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']\s*:', src):
            found.add(match.group(1))
        for match in re.finditer(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:self\.)?', src):
            found.add(match.group(1))

    sensitive = {k for k in found
                 if any(p in k.lower() for p in _SENSITIVE_PATTERNS)}
    assert sensitive, "민감 패턴을 하나도 못 찾았다 — 이 시험이 헛돌고 있다"
    escaped = {k for k in sensitive if k.lower() not in lowered}
    assert not escaped, (
        f"denylist 를 벗어난 민감 키: {sorted(escaped)} — "
        "`_EVENT_SENSITIVE_KEYS` 에 넣거나 민감하지 않은 이유를 적어라")


def test_the_sse_route_actually_applies_the_projection():
    """한 곳만 좁히면 다른 곳으로 그대로 나간다 — 배선을 구조로 고정한다."""
    from app import routes
    src = inspect.getsource(routes)
    assert "public_job_event_payload(e[\"payload\"])" in src
    assert 'json.dumps(e["payload"]' not in src


@pytest.mark.parametrize("key", sorted(_EVENT_SENSITIVE_KEYS))
def test_every_listed_key_is_actually_removed(key):
    """목록에 적어 놓고 실제로 안 지워지는 일이 없도록."""
    assert key not in public_job_event_payload({key: "x", "keep": 1})
