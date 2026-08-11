"""이미지 실비 원장 적재 스위치 — 운영 배포에서만 기본 on.

왜 테스트가 필요한가: 리포트가 운영 수치를 보려고 `--database-url` 로 운영 접속 문자열을
받는다. 그 문자열이 개발자 손에 돌아다니다 로컬 .env 의 DATABASE_URL 에 붙으면, 로컬 실험
비용이 운영 원장에 그대로 쌓인다. 잡 단위 집계는 job_id is not null 로 걸러지지만 총액·
모델별·일자별은 안 걸러져서 원가가 조용히 부푼다. 기본값 자체를 환경에 묶어 막는다.
"""

from app.config import Settings, load_settings


def test_persist_defaults_off_outside_production(monkeypatch):
    monkeypatch.delenv("IMAGE_USAGE_PERSIST", raising=False)
    for env in ("dev", "staging", "test", ""):
        monkeypatch.setenv("APP_ENV", env)
        assert load_settings().image_usage_persist is False, env


def test_persist_defaults_on_in_production(monkeypatch):
    monkeypatch.delenv("IMAGE_USAGE_PERSIST", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert load_settings().image_usage_persist is True


def test_explicit_env_var_always_wins(monkeypatch):
    # 로컬에서 일부러 쌓아 보는 경우 — 명시적으로 켤 수 있어야 한다.
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("IMAGE_USAGE_PERSIST", "true")
    assert load_settings().image_usage_persist is True

    # 운영에서 급히 끄는 경우(마이그레이션 롤백 등) — 끄는 쪽도 명시적으로 가능해야 한다.
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("IMAGE_USAGE_PERSIST", "false")
    assert load_settings().image_usage_persist is False


def test_dataclass_default_is_off(monkeypatch):
    # Settings() 를 직접 만드는 경로(테스트·스크립트)도 원장에 쓰지 않는다.
    assert Settings.__dataclass_fields__["image_usage_persist"].default is False


def test_unknown_value_is_not_true(monkeypatch):
    # 오타("True인가?" "1" "yes")로 운영 원장이 켜지지 않게 — true 문자열만 켠다.
    monkeypatch.setenv("APP_ENV", "dev")
    for raw in ("1", "yes", "on", "TRUE ", "  true"):
        monkeypatch.setenv("IMAGE_USAGE_PERSIST", raw)
        expected = raw.strip().lower() == "true"
        assert load_settings().image_usage_persist is expected, raw
