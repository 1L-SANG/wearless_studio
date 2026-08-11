"""카테고리 라우팅 · 하의/아우터 하드 게이트 · 선택 정책.

여기서 잠그는 것은 "어느 방식이 이기는가"가 아니라 **정책이 정책대로 작동하는가** 다:
아우터가 열려 있으면 아무리 잘 나와도 못 이기고, 하의 선호는 동점에서만 발동하며,
skirtWrapOrSlitStructure 만 NOT_APPLICABLE 을 쓸 수 있다.
"""
import pytest

from app.agents import pattern_fidelity_qc as pfq
from app.services import pattern_fidelity_gates as pg
from scripts import hybrid_branches as hb
from scripts.hybrid_run import PREFERRED_BRANCH, blocking, pick_winner, recoverable


# ── 활성화 ───────────────────────────────────────────────────────────────────

def test_bottom_category_activates_all_eight_gates():
    assert set(pg.required_gates(None, category="bottom")) == set(pg.BOTTOM_GATES)
    assert len(pg.BOTTOM_GATES) == 8


def test_outer_category_activates_all_four_closure_gates():
    assert set(pg.required_gates(None, category="outer")) == set(pg.OUTER_GATES)


def test_top_category_owes_no_category_gate():
    assert pg.required_gates(None, category="top") == ()


def test_category_gates_do_not_need_product_truth():
    """정본이 없어도 바지는 허리와 밑단을 갖는다 — 패턴 게이트와 다른 점."""
    assert pg.required_gates(None, category="bottom")
    assert pg.required_gates({}, category="outer")


def test_category_and_pattern_gates_stack():
    truth = {"status": "approved", "patternSpec": {"type": "STRIPE"}}
    gates = pg.required_gates(truth, category="bottom")
    assert pg.GATE_STRIPE in gates
    assert pg.GATE_WAIST_PLACEMENT in gates


# ── NOT_APPLICABLE 은 wrap/slit 에만 ─────────────────────────────────────────

@pytest.mark.parametrize("gate", [g for g in pg.BOTTOM_GATES
                                  if g != pg.GATE_SKIRT_WRAP_SLIT])
def test_strict_bottom_gates_refuse_not_applicable(gate):
    assert pg.normalise_status("NOT_APPLICABLE", gate=gate) == "UNVERIFIABLE"


def test_skirt_wrap_gate_may_answer_not_applicable():
    assert pg.normalise_status("NOT_APPLICABLE", gate=pg.GATE_SKIRT_WRAP_SLIT) \
        == "NOT_APPLICABLE"
    assert pg.gate_blocks("NOT_APPLICABLE") is False


@pytest.mark.parametrize("gate", pg.OUTER_GATES)
def test_outer_gates_are_all_strict(gate):
    assert gate in pg.STRICT_GATES


def test_property_normalises_against_its_own_gate_not_a_sibling():
    """한 판정자가 8개 게이트를 가질 때, strict 형제가 NA 허용 게이트를 깎으면 안 된다."""
    spec = pfq.SPECS[pg.JUDGE_BOTTOM]
    assert spec.gate_of("skirtWrapOrSlitStructure") == pg.GATE_SKIRT_WRAP_SLIT
    assert spec.gate_of("waistPlacement") == pg.GATE_WAIST_PLACEMENT
    body = {p: {"status": "PASS", "sourceObservation": "s", "generatedObservation": "g"}
            for p in spec.properties}
    body["skirtWrapOrSlitStructure"]["status"] = "NOT_APPLICABLE"
    body.update({f"{p}Status": "PASS" for p in spec.properties})
    body.update({"evidence": "", "correctionInstruction": "", "confidence": 0.9})
    out = pfq.validate(spec, body)
    assert out["properties"]["skirtWrapOrSlitStructure"]["status"] == "NOT_APPLICABLE"
    assert out["gateStatuses"][pg.GATE_SKIRT_WRAP_SLIT]["status"] == "NOT_APPLICABLE"
    assert out["gateStatuses"][pg.GATE_WAIST_PLACEMENT]["status"] == "PASS"


def test_every_bottom_and_outer_gate_is_documented_in_its_prompt():
    for judge in (pg.JUDGE_BOTTOM, pg.JUDGE_OUTER):
        spec = pfq.SPECS[judge]
        text = pfq.load_template(spec)
        missing = [p for p in spec.properties if p not in text]
        assert not missing, f"{judge} prompt does not describe {missing}"


# ── 프롬프트 규칙 ────────────────────────────────────────────────────────────

def test_outer_prompt_demands_a_closed_front_in_both_branches():
    for prompt in (hb.baseline_prompt("P", "outer"),
                   hb.sam_augmented_prompt("P", ["Front"], ["Front"], "outer")):
        assert "present it CLOSED" in prompt
        assert "zipped all the way up" in prompt
        assert "do not invent an inner top" in prompt.lower()


def test_bottom_prompt_demands_waist_leg_and_hem_care_in_both_branches():
    for prompt in (hb.baseline_prompt("P", "bottom"),
                   hb.sam_augmented_prompt("P", ["Front"], ["Front"], "bottom")):
        assert "WAISTBAND" in prompt
        assert "LEG SHAPE" in prompt
        assert "HEM" in prompt


def test_category_rules_are_not_a_branch_variable():
    """두 브랜치가 같은 규칙을 받아야 비교가 증거 차이를 재게 된다."""
    a = hb.baseline_prompt("P", "outer")
    b = hb.sam_augmented_prompt("P", ["Front"], ["Front"], "outer")
    assert hb.category_rules("outer") in a
    assert hb.category_rules("outer") in b


def test_cutout_is_never_given_silhouette_authority():
    """컷아웃은 비율·구성 증거까지는 되지만 윤곽 권위는 절대 아니다."""
    p = hb.sam_augmented_prompt("P", ["Front"], ["Front"], "top")
    assert "NEVER A SILHOUETTE TEMPLATE" in p
    assert "own no silhouette authority" in p
    assert "Do NOT paste a cutout onto the mannequin." in p
    assert "Do NOT preserve a cutout's outline" in p
    assert "What they are NOT is a shape to trace." in p


def test_top_gets_no_category_rules():
    assert hb.baseline_prompt("P", "top") == "P"


# ── 선택 정책 ────────────────────────────────────────────────────────────────

def _cand(*, allowed=True, failed=(), cat_failed=(), integration="PASS", conf=0.9,
          cat_required=("closureState",)):
    return {"deterministic": {"passed": True},
            "vision": {"allowed": allowed, "failedChecks": list(failed),
                       "unverifiableChecks": [], "softIssues": [], "confidence": conf,
                       "checks": {"garmentBodyIntegration": {"status": integration},
                                  "garmentIdentity": {"status": "PASS"},
                                  "protectedComponents": {"status": "PASS"},
                                  "patternType": {"status": "PASS"},
                                  "materialAppearance": {"status": "PASS"}}},
            "categoryGates": {"required": list(cat_required), "failed": list(cat_failed),
                              "allowed": not cat_failed}}


def test_open_front_outer_candidate_can_never_win():
    """아우터 closed-front 위반은 QC 통과와 무관하게 탈락이다."""
    out = pick_winner({"baseline": _cand(cat_failed=["closureState"]),
                       "sam2": _cand()}, "outer")
    assert out["winner"] == "sam2"


def test_outer_blocked_when_every_candidate_is_open():
    out = pick_winner({"baseline": _cand(cat_failed=["closureState"]),
                       "sam2": _cand(cat_failed=["frontOpeningControl"])}, "outer")
    assert out["state"] == "BLOCKED"
    assert out["winner"] is None


def test_bottom_prefers_sam_when_candidates_are_indistinguishable():
    out = pick_winner({"baseline": _cand(cat_required=pg.BOTTOM_GATES),
                       "sam2": _cand(cat_required=pg.BOTTOM_GATES)}, "bottom")
    assert out["winner"] == "sam2"
    assert "prefers sam2" in out["reason"]


def test_bottom_preference_never_overrules_real_evidence():
    """선호는 동점 깨기일 뿐 — 증거로 더 나은 baseline 은 그대로 이긴다."""
    out = pick_winner({"baseline": _cand(cat_required=pg.BOTTOM_GATES),
                       "sam2": _cand(cat_required=pg.BOTTOM_GATES,
                                     integration="FAIL")}, "bottom")
    assert out["winner"] == "baseline"


def test_top_has_no_branch_preference():
    out = pick_winner({"baseline": _cand(cat_required=()),
                       "sam2": _cand(cat_required=())}, "top")
    assert out["winner"] == "baseline"          # fixed branch order, not a preference
    assert PREFERRED_BRANCH.get("top") is None


def test_category_gate_failures_count_as_blocking():
    v = {"failedChecks": ["silhouette"], "unverifiableChecks": []}
    cat = {"failed": ["waistPlacement"]}
    assert set(blocking(v, cat)) == {"silhouette", "waistPlacement"}


def test_recoverable_covers_category_gates_an_edit_can_fix():
    assert recoverable({"failedChecks": [], "unverifiableChecks": []},
                       {"failed": [pg.GATE_CLOSURE_STATE]}) is True
    assert recoverable({"failedChecks": [], "unverifiableChecks": []},
                       {"failed": [pg.GATE_BODY_INTEGRATION_LOWER]}) is True


def test_outseam_length_is_not_recoverable_by_an_edit():
    """길이 클래스가 틀린 건 다른 옷이다 — 편집으로 고칠 대상이 아니다."""
    assert recoverable({"failedChecks": [], "unverifiableChecks": []},
                       {"failed": [pg.GATE_OUTSEAM_LENGTH]}) is False
