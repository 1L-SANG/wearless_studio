from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import cx_identity
from app.config import load_settings
from conftest import make_settings


def _birth_yyyymmdd(years_ago: int) -> str:
    """오늘로부터 정확히 `years_ago` 년 전 생일 — 만 나이 경계를 결정적으로 만든다."""
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        birth_date = today.replace(year=today.year - years_ago)
    except ValueError:
        # 오늘이 2/29(윤년)인 드문 경우의 안전한 폴백.
        birth_date = today.replace(year=today.year - years_ago, day=28)
    return birth_date.strftime("%Y%m%d")


def _settings(monkeypatch, **env):
    for key in (
        "FM_IDENTITY_METHODS", "FM_ENROLLMENT_REVIEW", "FM_OACX_SIMPLE_AUTH_CONTRACT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_settings()


def test_identity_methods_default_is_mid_only(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.fm_identity_methods == ("mid",)


def test_identity_methods_parses_csv(monkeypatch):
    settings = _settings(monkeypatch, FM_IDENTITY_METHODS="mid,simple_auth")
    assert settings.fm_identity_methods == ("mid", "simple_auth")


def test_identity_methods_rejects_unknown_value(monkeypatch):
    # 오타가 조용히 통과해 인증 수단이 통째로 사라지는 일을 막는다.
    settings = _settings(monkeypatch, FM_IDENTITY_METHODS="mid,bogus")
    assert settings.fm_identity_methods == ("mid",)


def test_identity_methods_fallback_to_mid_when_all_unknown(monkeypatch):
    # 오타가 조용히 통과해 인증 수단이 통째로 사라지는 일을 막는다.
    # FM_IDENTITY_METHODS="bogus" 일 때 결과는 ("mid",) 이어야 한다.
    settings = _settings(monkeypatch, FM_IDENTITY_METHODS="bogus")
    assert settings.fm_identity_methods == ("mid",)


def test_review_default_is_simple_auth_only(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.fm_enrollment_review == "simple_auth_only"


def test_simple_auth_contract_default_is_disabled(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.fm_oacx_simple_auth_contract == "disabled"


def test_simple_auth_contract_blocked_when_disabled():
    settings = make_settings(fm_oacx_simple_auth_contract="disabled")
    with pytest.raises(cx_identity.OacxBiometricError):
        cx_identity.get_oacx_biometric_contract(settings, method="simple_auth")


def test_simple_auth_contract_returned_when_enabled():
    settings = make_settings(fm_oacx_simple_auth_contract="simple-auth-v1")
    contract = cx_identity.get_oacx_biometric_contract(settings, method="simple_auth")
    assert contract.version == "simple-auth-v1"
    # 간편인증은 신분증 초상을 주지 않는다 — 초상 상한이 0이어야 릴레이 시도가 막힌다.
    assert contract.max_portrait_bytes == 0


def test_mid_contract_unchanged_by_new_flag():
    settings = make_settings(
        fm_oacx_contract_mode="prod-dlphoto-v1",
        fm_oacx_simple_auth_contract="simple-auth-v1",
    )
    contract = cx_identity.get_oacx_biometric_contract(settings, method="mid")
    assert contract.version == "prod-dlphoto-v1"
    assert contract.max_portrait_bytes == 5 * 1024 * 1024


def test_method_defaults_to_mid():
    settings = make_settings(fm_oacx_contract_mode="prod-dlphoto-v1")
    assert cx_identity.get_oacx_biometric_contract(settings).version == "prod-dlphoto-v1"


def test_simple_auth_evidence_parses_ci_name_birth():
    contract = cx_identity.SIMPLE_AUTH_CONTRACT
    evidence = cx_identity.parse_simple_auth_evidence(
        {"ci": "CI-VALUE", "name": "홍길동", "birth": "19900101", "txId": "tx-1"},
        contract=contract,
    )
    assert bytes(evidence.ci) == b"CI-VALUE"
    assert evidence.birth == "19900101"
    assert evidence.name_masked == "홍*동"
    assert evidence.contract_version == "simple-auth-v1"


def test_simple_auth_evidence_requires_ci():
    with pytest.raises(cx_identity.OacxBiometricError):
        cx_identity.parse_simple_auth_evidence(
            {"name": "홍길동", "birth": "19900101"},
            contract=cx_identity.SIMPLE_AUTH_CONTRACT,
        )


def test_simple_auth_evidence_requires_birth():
    with pytest.raises(cx_identity.OacxBiometricError):
        cx_identity.parse_simple_auth_evidence(
            {"ci": "CI-VALUE", "name": "홍길동"},
            contract=cx_identity.SIMPLE_AUTH_CONTRACT,
        )


def test_simple_auth_evidence_ci_is_bytearray_not_bytes():
    # wipe_bytearray 의 사용 후 0으로 지우는 계약은 bytearray 를 전제한다 — 구현이
    # plain bytes 를 반환해도 `bytes(evidence.ci) == b"..."` 비교는 통과해 버려서
    # 이 타입 자체를 명시적으로 잠근다.
    evidence = cx_identity.parse_simple_auth_evidence(
        {"ci": "CI-VALUE", "name": "홍길동", "birth": "19900101", "txId": "tx-1"},
        contract=cx_identity.SIMPLE_AUTH_CONTRACT,
    )
    assert isinstance(evidence.ci, bytearray)


def test_simple_auth_evidence_blocks_minor():
    # mid 경로(parse_oacx_biometric_evidence)와 동일한 성년 게이트가 있어야 한다 —
    # 인증 수단이 다르다고 미성년이 통과해서는 안 된다(라이선싱 계약은 공통).
    minor_birth = _birth_yyyymmdd(cx_identity.ADULT_MIN_AGE - 1)
    with pytest.raises(cx_identity.OacxBiometricError) as excinfo:
        cx_identity.parse_simple_auth_evidence(
            {"ci": "CI-VALUE", "name": "홍길동", "birth": minor_birth, "txId": "tx-1"},
            contract=cx_identity.SIMPLE_AUTH_CONTRACT,
        )
    assert excinfo.value.reason == "minor_blocked"


def test_simple_auth_evidence_allows_exact_min_age():
    adult_birth = _birth_yyyymmdd(cx_identity.ADULT_MIN_AGE)
    evidence = cx_identity.parse_simple_auth_evidence(
        {"ci": "CI-VALUE", "name": "홍길동", "birth": adult_birth, "txId": "tx-1"},
        contract=cx_identity.SIMPLE_AUTH_CONTRACT,
    )
    assert evidence.birth == adult_birth


def test_get_oacx_biometric_contract_rejects_unknown_method():
    settings = make_settings(
        fm_oacx_contract_mode="prod-dlphoto-v1",
        fm_oacx_simple_auth_contract="simple-auth-v1",
    )
    with pytest.raises(cx_identity.OacxBiometricError) as excinfo:
        cx_identity.get_oacx_biometric_contract(settings, method="simple-auth")
    assert excinfo.value.reason == "oacx_contract_unavailable"


# ── Task5: 등록 생성 시 인증 수단 분기 ───────────────────────────────────────────────────

def test_create_rejects_simple_auth_when_flag_off(enrollment_client_factory):
    """FM_IDENTITY_METHODS=mid 인데 simple_auth 를 요청하면 409."""
    client, store, settings = enrollment_client_factory(fm_identity_methods=("mid",))
    response = client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "d" * 40,
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v2"},
            "identityMethod": "simple_auth",
        },
    )
    assert response.status_code == 409
    # main.py 의 http_exception_handler 가 HTTPException.detail 딕셔너리를
    # {"error": {...}} 로 감싼다(브리프 초안의 "detail" 키는 이 레포 관례와 다르다 —
    # /id-document 의 같은 에러도 error.code 로 검증한다).
    assert response.json()["error"]["code"] == "identity_method_unavailable"


def test_create_simple_auth_starts_at_id_capture_pending(enrollment_client_factory):
    client, store, settings = enrollment_client_factory(
        fm_identity_methods=("mid", "simple_auth")
    )
    response = client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "d" * 40,
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v2"},
            "identityMethod": "simple_auth",
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "id_capture_pending"
    assert response.json()["identityMethod"] == "simple_auth"


def test_create_mid_unchanged(enrollment_client_factory):
    """기본값 경로는 지금과 똑같이 identity_pending 에서 시작한다."""
    client, store, settings = enrollment_client_factory(fm_identity_methods=("mid",))
    response = client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "d" * 40,
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v2"},
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "identity_pending"
    assert response.json()["identityMethod"] == "mid"


# ── Task6: 본인확인 라우트의 계약 분기 ────────────────────────────────────────────────

def _spy_parsers(monkeypatch):
    """어느 파서가 실제로 불렸는지 기록한다. 픽스처를 비트는 방식으로는 한 방향밖에
    못 막는다 — dig() 의 다중 키 폴백 때문에 두 파서의 수용 집합이 겹쳐서, 어떤 입력을
    줘도 '항상 simple_auth' 버그는 통과한다(리뷰 라운드1 지적)."""
    calls = []
    for name in ("parse_oacx_biometric_evidence", "parse_simple_auth_evidence"):
        original = getattr(cx_identity, name)

        def wrapper(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(cx_identity, name, wrapper)
    return calls


def _create_simple_auth_enrollment(client, store):
    """simple_auth 등록을 만들고 identity_pending 으로 밀어 넣는다.

    실제 서비스에서는 촬영 라우트(Task4)가 id_capture_pending → identity_pending
    전이를 수행하지만, 이 테스트는 /identity 라우트의 계약 분기만 검증하므로 그
    단계는 건너뛰고 상태만 직접 바꾼다 — test_facemarket_biometric_enrollment.py 의
    `_fast_forward_identity` 와 같은 결의 지름길이다.
    """
    response = client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "d" * 40,
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v2"},
            "identityMethod": "simple_auth",
        },
    )
    assert response.status_code == 201, response.text
    enrollment_id = response.json()["id"]
    row = next(item for item in store.enrollments if item["id"] == enrollment_id)
    row["status"] = "identity_pending"
    return enrollment_id


def test_identity_uses_simple_auth_contract_for_simple_auth_method(
    enrollment_client_factory, monkeypatch
):
    client, store, settings = enrollment_client_factory(
        fm_identity_methods=("mid", "simple_auth"),
        fm_oacx_simple_auth_contract="simple-auth-v1",
    )
    enrollment_id = _create_simple_auth_enrollment(client, store)
    captured = {}
    calls = _spy_parsers(monkeypatch)

    async def fake_fetch(base_url, token):
        captured["called"] = True
        return {"ci": "CI-1", "name": "홍길동", "birth": "19900101", "txId": "t1"}

    monkeypatch.setattr(cx_identity, "fetch_trans", fake_fetch)
    response = client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/identity",
        json={"token": "tok-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "photos_pending"
    assert captured.get("called")
    # 픽스처가 우연히 mid 파서로도 파싱 가능해도(dig() 다중 키 폴백 때문에 두 파서의
    # 수용 집합이 겹친다) 실제로 불린 파서가 simple_auth 전용인지를 직접 못박는다.
    assert calls == ["parse_simple_auth_evidence"]
    row = next(item for item in store.enrollments if item["id"] == enrollment_id)
    assert row["identity_contract_version"] == "simple-auth-v1"
    # 초상 없이도 CI·이름·생년월일만으로 게이트가 통과해야 한다.
    assert row["identity_ci_hash"]
    assert row["identity_birth_year"] == "1990"


def test_identity_blocked_when_simple_auth_contract_disabled(
    enrollment_client_factory, monkeypatch
):
    client, store, settings = enrollment_client_factory(
        fm_identity_methods=("mid", "simple_auth"),
        fm_oacx_simple_auth_contract="disabled",
    )
    enrollment_id = _create_simple_auth_enrollment(client, store)
    calls = _spy_parsers(monkeypatch)

    async def fail_if_called(base_url, token):
        raise AssertionError("계약이 비활성화됐으면 fetch_trans 를 호출하면 안 된다")

    monkeypatch.setattr(cx_identity, "fetch_trans", fail_if_called)
    response = client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/identity",
        json={"token": "tok-1"},
    )
    assert response.status_code == 400
    # main.py 의 http_exception_handler 가 {"error": {...}} 로 감싼다(이 레포 관례).
    assert response.json()["error"]["code"] == "oacx_contract_unavailable"
    row = next(item for item in store.enrollments if item["id"] == enrollment_id)
    assert row["status"] == "identity_pending"
    # 계약이 막히면 fetch_trans 뿐 아니라 파서도 아예 호출되지 않아야 한다.
    assert calls == []


def test_identity_uses_mid_contract_when_identity_method_is_mid(
    enrollment_client_factory, monkeypatch
):
    """기존 mid 경로 불변: identity_method='mid' 는 오늘과 같은 계약·파서로 간다."""
    client, store, settings = enrollment_client_factory(
        fm_oacx_contract_mode="dev-mock-v1",
    )
    response = client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "d" * 40,
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v2"},
        },
    )
    assert response.status_code == 201, response.text
    enrollment_id = response.json()["id"]
    assert store.enrollments[0]["status"] == "identity_pending"
    calls = _spy_parsers(monkeypatch)

    async def fake_fetch(base_url, token):
        return {"ci": "CI-2", "nm": "김철수", "birth": "19900101", "txId": "t2"}

    monkeypatch.setattr(cx_identity, "fetch_trans", fake_fetch)
    response = client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/identity",
        json={"token": "tok-2"},
    )
    assert response.status_code == 200, response.text
    row = next(item for item in store.enrollments if item["id"] == enrollment_id)
    assert (
        row["identity_contract_version"]
        == cx_identity.DEV_MOCK_OACX_BIOMETRIC_CONTRACT.version
    )
    # 이 행은 identity_method 컬럼 자체가 없는 마이그레이션-이전 모양이다(create_enrollment
    # 의 기본 INSERT 분기는 그 키를 안 채운다) — NULL 도 'mid' 로 취급됨을 같이 증명한다.
    assert "identity_method" not in row
    # 픽스처가 우연히 simple_auth 파서로도 파싱 가능해도(dig() 다중 키 폴백 때문에
    # utf8Nm/nm/name/userName 이 겹친다) 실제로 불린 파서가 mid 전용인지를 직접 못박는다.
    assert calls == ["parse_oacx_biometric_evidence"]
