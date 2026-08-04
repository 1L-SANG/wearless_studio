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


# ── 재생성 예산 (순수) ───────────────────────────────────────────────────────

def test_budget_counts_actual_calls_not_attempts():
    """예산은 **실제 나간 호출**로 센다 — 편집이 먹은 만큼 재생성 여력이 준다.

    예약형(다음 생성+다음 편집을 미리 차감)은 세 방향으로 틀렸다: 사전 게이트가 검사를
    안 거쳐 상한을 넘었고, bust 호출은 세지도 않았고, 마지막 attempt 에서는 쓰지도 않을
    편집분을 예약해 안전한 재생성을 막았다(codex 2026-07-31 7차).
    """
    from app.workers.mannequin_job import has_budget_for_retry
    s = make_settings(mannequin_axis_qc="enforce", mannequin_max_attempts=3)
    assert has_budget_for_retry(s, calls_spent=2) is True    # 1 남음 → 생성 가능
    assert has_budget_for_retry(s, calls_spent=3) is False   # 소진
    # 편집이 예산을 먹었으면 attempt 가 1 이어도 재생성 여력이 없을 수 있다
    assert has_budget_for_retry(s, calls_spent=3) is False


def test_budget_does_not_reserve_unused_edit():
    """axis 모드와 무관하게 잔량만 본다 — 예약이 없으니 과소 사용도 없다."""
    from app.workers.mannequin_job import has_budget_for_retry
    for mode in ("off", "enforce"):
        s = make_settings(mannequin_axis_qc=mode, mannequin_max_attempts=4)
        assert has_budget_for_retry(s, calls_spent=3) is True, mode


def test_budget_exhausted_at_single_attempt():
    from app.workers.mannequin_job import has_budget_for_retry
    s = make_settings(mannequin_axis_qc="off", mannequin_max_attempts=1)
    assert has_budget_for_retry(s, calls_spent=1) is False


def test_default_thresholds_pass_observed_production_scores():
    """기본 임계가 실측 분포에서 아무것도 통과시키지 못하면 안 된다.

    2026-07-31 로컬 실컷 30건 캘리브레이션 중앙값(fidelity 58 · natural 78 · quality 80).
    초기 추측값 90/75 는 통과 0/30 이었다 — MANNEQUIN_QC_ENABLED 가 pass율 0% 로 전 생성을
    막았던 2026-07-07 사고와 같은 조건이다. 임계를 다시 올릴 때는 이 테스트를 근거와 함께
    갱신하라(무의식적 상향만 막는다).
    """
    from app.config import Settings
    s = make_settings()
    assert Settings.__dataclass_fields__["qc_score_auto_pass"].default == 80
    assert Settings.__dataclass_fields__["qc_score_review"].default == 65
    # 실측 상위권(최저축 82)은 통과해야 한다.
    good = {"product_fidelity": 82, "physical_naturalness": 85,
            "image_quality": 88, "series_consistency": 100, "critical_errors": []}
    assert score_outcome(s, good) == "auto_pass"
    # 실측 중앙값(최저축 58)은 재생성 — 실제로 로고가 깨진 컷들이다.
    median = {"product_fidelity": 58, "physical_naturalness": 78,
              "image_quality": 80, "series_consistency": 100, "critical_errors": []}
    assert score_outcome(s, median) == "regenerate"


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


def test_enforce_blocks_extreme_missing_head_crop_from_real_qa_case():
    """2026-08-04 QA: bboxTop=0.252 인 headless 상반신 컷은 검토가 아니라 차단.

    상품/시리즈 Vision 점수가 어떻게 나와도 머리와 전신 구도가 사라진 컷을
    `needs_review`로 저장하면 안 된다. 오탐 현실성이 낮은 극단 임계만 게이트한다.
    """
    s = make_settings(image_qc="enforce")
    assert gate_decision(
        s,
        "retry",
        _scored(product_fidelity=80, physical_naturalness=80, image_quality=70),
        pillow_reasons=["full_body_crop"],
        pillow_metrics={"bboxTop": 0.252, "bboxBottom": 1.0, "bboxHeight": 0.748},
    ) == (True, False)


def test_enforce_keeps_known_missing_lower_body_false_positive_in_shadow():
    """과거 전 상품 차단 사고를 낸 단독 missing_lower_body 신호는 게이트하지 않는다."""
    s = make_settings(image_qc="enforce")
    assert gate_decision(
        s,
        "retry",
        _scored(product_fidelity=90, physical_naturalness=90, image_quality=90),
        pillow_reasons=["missing_lower_body"],
        pillow_metrics={"bboxTop": 0.08, "bboxBottom": 0.93, "bboxHeight": 0.85},
    ) == (False, False)


def test_extreme_crop_signal_does_not_gate_when_image_qc_is_not_enforced():
    for mode in ("off", "shadow"):
        assert gate_decision(
            make_settings(image_qc=mode),
            "retry",
            None,
            pillow_reasons=["full_body_crop"],
            pillow_metrics={"bboxTop": 0.252, "bboxBottom": 1.0, "bboxHeight": 0.748},
        ) == (False, False)


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
