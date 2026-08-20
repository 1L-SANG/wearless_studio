"""qc_observe 편집 효과 리포트 × 게이트 이벤트 (리뷰 지적 2026-08-19).

bust_pass 이벤트는 이제 편집이 실제로 나가지 않은 결과(outcome=skipped_gate·
budget_exhausted·failed_open)도 싣는다. 리포트가 status 만 보고 'bust 편집됨'으로
집계하면, 게이트 도입 근거였던 "보정 47% 폐기" 계열 수치가 스킵된 컷들로 오염돼
GATE_SKIP_CONFIDENCE 튜닝 근거를 잃는다 — **applied 만** 편집으로 센다.
"""

from scripts.qc_observe import _report_edit_impact
from tests.conftest import make_settings

_PRE = {"product_fidelity": 85, "physical_naturalness": 82, "image_quality": 85,
        "critical_errors": []}
_POST = {"product_fidelity": 40, "physical_naturalness": 70, "image_quality": 80,
         "critical_errors": ["garment fit changed"]}


def _rows(bust_outcome):
    key = {"candidate": "A", "attempt": 1}
    return [
        {"job_id": "j1", "payload": {**key, "status": "image_qc", "imageQc": _PRE}},
        {"job_id": "j1", "payload": {**key, "status": "bust_pass", "outcome": bust_outcome}},
        {"job_id": "j1", "payload": {**key, "status": "image_qc_rescored", "imageQc": _POST}},
    ]


def _attribution(capsys, bust_outcome):
    _report_edit_impact(_rows(bust_outcome), make_settings())
    return capsys.readouterr().out


def test_applied_bust_is_attributed(capsys):
    out = _attribution(capsys, "applied")
    assert "bust" in out, "실제 적용된 편집은 회귀 귀속에 잡혀야 한다"


def test_skipped_gate_bust_is_not_attributed(capsys):
    out = _attribution(capsys, "skipped_gate")
    assert "bust" not in out, "게이트가 스킵한 잡의 회귀를 bust 탓으로 돌리면 안 된다"


def test_budget_exhausted_and_failed_open_are_not_attributed(capsys):
    """편집 콜이 이미지에 닿지 않은 결과들 — 종전부터 오귀속이던 케이스도 함께 고정."""
    for outcome in ("budget_exhausted", "failed_open"):
        out = _attribution(capsys, outcome)
        assert "bust" not in out, outcome
