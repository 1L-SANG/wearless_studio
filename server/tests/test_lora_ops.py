"""운영 스위치 — 매니페스트 값과 코드 기본값이 갈라지면 "켠 줄 알았는데 안 돈다" 가 된다.

그리고 켜는 것 자체가 돈이 나가는 스위치라, **기본이 off** 인 것과 되돌리는 법이 문서에 있는
것을 여기서 잠근다.
"""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "copilot/api/manifest.yml"
RUNBOOK = ROOT / "docs/runbooks/facemarket-lora-automation.md"


def _variables() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["variables"]


def test_the_queue_ships_off():
    """★ 켜는 순간 GPU 파드가 돈다. 배포로 저절로 켜지면 안 된다."""
    assert _variables()["FM_LORA_TRAINING"] == "off"


def test_the_manifest_and_the_code_agree_on_the_defaults():
    from app.config import load_settings

    variables = _variables()
    settings = load_settings()          # env 없는 상태 = 코드 기본값
    assert settings.fm_lora_training == "off"
    assert float(variables["FM_LORA_MIN_BALANCE_USD"]) == settings.fm_lora_min_balance_usd
    assert int(variables["FM_LORA_MAX_SECONDS"]) == settings.fm_lora_max_seconds


def test_the_run_cap_is_longer_than_a_real_run():
    """실측 1800 스텝 ≈ 3시간 40분. 상한이 그보다 짧으면 정상 학습이 매번 타임아웃이다."""
    assert int(_variables()["FM_LORA_MAX_SECONDS"]) >= 4 * 3600


def test_the_balance_floor_covers_one_run():
    """한 번이 약 $11 이다 — 임계가 그보다 낮으면 잔액이 바닥난 채로 시작한다."""
    assert float(_variables()["FM_LORA_MIN_BALANCE_USD"]) >= 15


def test_the_runbook_says_how_to_turn_each_thing_back_off():
    text = RUNBOOK.read_text(encoding="utf-8")
    for switch in ("FM_LORA_TRAINING", "FM_LORA_MIN_BALANCE_USD", "FACE_MASK_LOCK",
                   "FACE_AUTOSCALE", "skin_finish"):
        assert switch in text, switch
    # 선행 조건(키 교체)과 그 뒤 실측 확인이 적혀 있어야 한다.
    assert "RUNPOD_API_KEY" in text and "clientBalance" in text
    assert "되돌리기" in text


def test_the_runbook_has_the_end_to_end_checklist():
    text = RUNBOOK.read_text(encoding="utf-8")
    for step in ("사진 확인", "동시 1건", "enabled=false", "파드가 사라졌다",
                 "보정 100/50/0", "같은 트랜잭션"):
        assert step in text, step
