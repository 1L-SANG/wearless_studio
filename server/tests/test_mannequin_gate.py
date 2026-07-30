from app.workers.mannequin_job import gate_decision, score_outcome
from conftest import make_settings


def _scored(**axes):
    """4축 점수 dict — 미지정 축은 None(신호 없음)."""
    base = {"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": None, "physical_naturalness": None,
            "image_quality": None, "series_consistency": None, "critical_errors": []}
    base.update(axes)
    return base


# ── score_outcome (순수 3치 분기) ─────────────────────────────────────────────

def test_score_outcome_uses_worst_axis_not_average():
    """한 축의 붕괴가 다른 축 고득점에 가려지면 안 된다 — 평균이 아니라 최저값으로 판정."""
    s = make_settings(qc_score_auto_pass=90, qc_score_review=75)
    # 평균은 76(=통과권)이지만 최저 40 이라 재생성이어야 한다.
    assert score_outcome(s, _scored(product_fidelity=40, physical_naturalness=99,
                                    image_quality=90)) == "regenerate"


def test_score_outcome_three_way_thresholds():
    s = make_settings(qc_score_auto_pass=90, qc_score_review=75)
    assert score_outcome(s, _scored(product_fidelity=95, image_quality=90)) == "auto_pass"
    assert score_outcome(s, _scored(product_fidelity=95, image_quality=80)) == "needs_review"
    assert score_outcome(s, _scored(product_fidelity=95, image_quality=74)) == "regenerate"
    # 경계값은 포함 쪽
    assert score_outcome(s, _scored(image_quality=90)) == "auto_pass"
    assert score_outcome(s, _scored(image_quality=75)) == "needs_review"


def test_score_outcome_critical_error_overrides_high_scores():
    """치명 오류는 점수와 무관하게 재생성 — 평균으로 희석되면 로고 변형이 출고된다."""
    s = make_settings()
    assert score_outcome(s, _scored(product_fidelity=100, image_quality=100,
                                    critical_errors=["logo altered"])) == "regenerate"


def test_score_outcome_no_signal_is_auto_pass():
    """신호 부재(off·shadow·판정실패·미채점)를 나쁨으로 읽으면 QC 를 켜는 순간 멀쩡한 컷이 죽는다."""
    s = make_settings()
    assert score_outcome(s, None) == "auto_pass"
    assert score_outcome(s, _scored()) == "auto_pass"           # 전 축 None
    assert score_outcome(s, {"verdict": "retry"}) == "auto_pass"  # 구 shape(점수 없음)


def test_score_outcome_ignores_bool_as_score():
    """bool 은 int 의 서브클래스라 True 가 1점으로 새면 전부 regenerate 가 된다."""
    s = make_settings()
    assert score_outcome(s, _scored(product_fidelity=True, image_quality=95)) == "auto_pass"


# ── gate_decision × 점수 (게이팅 연동) ────────────────────────────────────────

def test_scores_gate_only_under_enforce():
    low = _scored(product_fidelity=10)
    assert gate_decision(make_settings(image_qc="off"), "pass", low) == (False, False)
    assert gate_decision(make_settings(image_qc="shadow"), "pass", low) == (False, False)
    assert gate_decision(make_settings(image_qc="enforce"), "pass", low) == (False, True)


def test_enforce_falls_back_to_binary_verdict_when_unscored():
    """미채점 응답에서 게이트가 통째로 풀리면 안 된다 — 구 이진 판정으로 폴백."""
    s = make_settings(image_qc="enforce")
    assert gate_decision(s, "pass", {"verdict": "retry"}) == (False, True)
    assert gate_decision(s, "pass", {"verdict": "pass"}) == (False, False)


def test_enforce_scores_win_over_stale_verdict():
    """점수 신호가 있으면 그쪽이 정본 — verdict=retry 여도 전 축 고득점이면 통과시킨다."""
    s = make_settings(image_qc="enforce")
    scored_ok = _scored(verdict="retry", mismatches=["사소함"],
                        product_fidelity=95, image_quality=95)
    assert gate_decision(s, "pass", scored_ok) == (False, False)


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


def test_pillow_hard_shadow_even_when_enabled():
    # 재캘리브 전 강제 shadow 계약(2026-07-12 prod 사고): env 가 true 여도 Pillow 는 게이트 금지.
    # 오탐(pass율 0%)인 휴리스틱이 env 하나로 전 생성을 차단하는 사고의 회귀 방지.
    s = make_settings(image_qc="off", mannequin_qc_enabled=True)
    assert gate_decision(s, "fail", None)[0] is False
    assert gate_decision(s, "pass", None)[0] is False


def test_p2_gate_unaffected_by_pillow_shadow():
    s = make_settings(image_qc="enforce", mannequin_qc_enabled=True)
    assert gate_decision(s, "fail", {"verdict": "retry"}) == (False, True)
    assert gate_decision(s, "pass", {"verdict": "pass"}) == (False, False)
