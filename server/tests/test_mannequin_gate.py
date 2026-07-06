from app.workers.mannequin_job import gate_decision
from conftest import make_settings


def test_off_never_gates():
    s = make_settings(image_qc="off", mannequin_qc_enabled=False)
    assert gate_decision(s, "fail", {"verdict": "retry"}) == (False, False)
    assert gate_decision(s, "pass", None) == (False, False)


def test_shadow_never_gates():
    # shadow 는 AG-P2 판정을 계산·로그만, 게이트는 안 함
    s = make_settings(image_qc="shadow", mannequin_qc_enabled=False)
    assert gate_decision(s, "pass", {"verdict": "retry"}) == (False, False)


def test_enforce_rejects_on_p2_retry():
    s = make_settings(image_qc="enforce", mannequin_qc_enabled=False)
    assert gate_decision(s, "pass", {"verdict": "retry"}) == (False, True)
    assert gate_decision(s, "pass", {"verdict": "pass"}) == (False, False)


def test_enforce_graceful_when_no_p2():
    # 키 미설정/판정 실패 → p2=None → 게이트 미적용
    s = make_settings(image_qc="enforce", mannequin_qc_enabled=False)
    assert gate_decision(s, "pass", None) == (False, False)


def test_pillow_gate_independent():
    s = make_settings(image_qc="off", mannequin_qc_enabled=True)
    assert gate_decision(s, "fail", None)[0] is True     # pillow reject
    assert gate_decision(s, "pass", None)[0] is False


def test_both_gates_combine():
    s = make_settings(image_qc="enforce", mannequin_qc_enabled=True)
    assert gate_decision(s, "fail", {"verdict": "retry"}) == (True, True)
    assert gate_decision(s, "pass", {"verdict": "pass"}) == (False, False)
