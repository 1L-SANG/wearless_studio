"""관리자 기기 게이트 플래그 — 기본값과 허용값.

코드 기본은 shadow(배포 직후 아무도 잠기지 않게), 테스트 기본은 off(관련 없는 라우트
테스트가 FakeConn 위에서 기기 조회를 돌리지 않게 — garment_qc_mode 와 같은 선례).
"""
from app.config import Settings, load_settings
from conftest import make_settings


def test_gate_defaults_to_shadow_in_code_and_off_in_tests():
    assert Settings.__dataclass_fields__["admin_device_gate"].default == "shadow"
    assert Settings.__dataclass_fields__["admin_device_max_pending_per_user"].default == 5
    assert make_settings().admin_device_gate == "off"


def test_gate_reads_env_and_falls_back_to_shadow_on_garbage(monkeypatch):
    monkeypatch.setenv("ADMIN_DEVICE_GATE", "enforce")
    assert load_settings().admin_device_gate == "enforce"
    monkeypatch.setenv("ADMIN_DEVICE_GATE", "Shadow ")
    assert load_settings().admin_device_gate == "shadow"
    monkeypatch.setenv("ADMIN_DEVICE_GATE", "yes")
    # 오타로 게이트가 꺼지거나(off) 잠기면(enforce) 안 된다 — 중간값으로 떨어진다.
    assert load_settings().admin_device_gate == "shadow"


def test_max_pending_reads_env(monkeypatch):
    monkeypatch.setenv("ADMIN_DEVICE_MAX_PENDING_PER_USER", "2")
    assert load_settings().admin_device_max_pending_per_user == 2
    monkeypatch.setenv("ADMIN_DEVICE_MAX_PENDING_PER_USER", "abc")
    assert load_settings().admin_device_max_pending_per_user == 5
