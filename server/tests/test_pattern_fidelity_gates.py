"""속성별 하드 게이트 — 활성화·합의·2단 판정·증거 크롭.

동기(2026-08-10 실측): 라이브 스트라이프 셔츠가 일반 13체크 판정에서 **PASS 3/3** 을 받았다.
원본은 굵은 taupe + 얇은 blue 가 묶여 넓은 크림 간격을 두고 반복되는 그룹 스트라이프인데,
생성본은 균일하고 촘촘한 저대비 핀스트라이프였다. 둘 다 "striped" 라서 `patternType` 은
정직하게 PASS 였다. 샘플을 늘려도 안 고쳐진다 — 질문과 증거가 틀렸다.

여기서 잠그는 것: 무엇이 게이트를 켜는가, 어떻게 합치는가, 실패한 전용 게이트를 일반
판정이 덮을 수 있는가(없다).
"""
import io

import pytest
from PIL import Image

from app.agents import pattern_fidelity_qc as pfq
from app.services import garment_fidelity_authority as gfa
from app.services import pattern_evidence as pe
from app.services import pattern_fidelity_gates as pg


def truth(pattern=None, traits=(), subcategory=None, status="approved") -> dict:
    return {
        "status": status,
        "patternSpec": {"type": pattern} if pattern else {},
        "garmentSpec": {"materialTraits": list(traits), "subcategory": subcategory},
    }


# ── 활성화 ───────────────────────────────────────────────────────────────────

def test_stripe_truth_activates_the_stripe_gate():
    assert pg.required_gates(truth("STRIPE")) == (pg.GATE_STRIPE,)


def test_check_truth_activates_the_check_gate():
    assert pg.required_gates(truth("CHECK")) == (pg.GATE_CHECK,)


def test_solid_truth_activates_nothing():
    assert pg.required_gates(truth("SOLID")) == ()


def test_no_truth_activates_nothing():
    """게이트는 승인 정본에서만 나온다 — 정본이 없으면 새 하드 게이트를 만들지 않는다."""
    assert pg.required_gates(None) == ()


@pytest.mark.parametrize("trait", ["rib", "골지", "pointelle", "seersucker", "shirring"])
def test_relief_knits_activate_the_surface_texture_gate(trait):
    assert pg.GATE_SURFACE_TEXTURE in pg.required_gates(truth(traits=[trait]))


@pytest.mark.parametrize("trait", ["lace", "레이스", "openwork", "eyelet"])
def test_lace_activates_structure_and_opacity(trait):
    gates = pg.required_gates(truth(traits=[trait]))
    assert pg.GATE_LACE_STRUCTURE in gates and pg.GATE_OPACITY in gates


def test_sheer_activates_opacity_only():
    gates = pg.required_gates(truth(traits=["sheer"]))
    assert gates == (pg.GATE_OPACITY,)


def test_product_name_can_activate_a_material_gate():
    """소재 어휘는 patternSpec 같은 전용 필드가 없다 — 승인된 이름도 정본의 일부다."""
    gates = pg.required_gates(truth(), product={"name": "여성용 보트넥 골지 슬림 니트"})
    assert pg.GATE_SURFACE_TEXTURE in gates


def test_stripe_and_check_are_mutually_exclusive():
    assert pg.required_gates(truth("STRIPE")) == (pg.GATE_STRIPE,)
    assert pg.GATE_STRIPE not in pg.required_gates(truth("CHECK"))


def test_judges_are_deduplicated_per_gate_set():
    judges = pg.required_judges([pg.GATE_LACE_STRUCTURE, pg.GATE_OPACITY])
    assert judges == (pg.JUDGE_LACE_OPACITY,)


# ── 샘플 합의 ────────────────────────────────────────────────────────────────

def test_one_failing_sample_fails_the_gate():
    assert pg.merge_gate_samples(["PASS", "PASS", "FAIL"], gate=pg.GATE_STRIPE) == "FAIL"


def test_unverifiable_blocks_when_no_sample_failed():
    assert pg.merge_gate_samples(["PASS", "UNVERIFIABLE"], gate=pg.GATE_STRIPE) == "UNVERIFIABLE"


def test_strict_gates_may_not_answer_not_applicable():
    """정본이 STRIPE 라고 말했는데 '해당 없음' 은 면제가 아니라 증거 실패다."""
    assert pg.normalise_status("NOT_APPLICABLE", gate=pg.GATE_STRIPE) == "UNVERIFIABLE"
    assert pg.merge_gate_samples(["NOT_APPLICABLE"] * 3, gate=pg.GATE_STRIPE) == "UNVERIFIABLE"


def test_material_gates_may_answer_not_applicable_unanimously():
    assert pg.merge_gate_samples(["NOT_APPLICABLE"] * 3,
                                 gate=pg.GATE_SURFACE_TEXTURE) == "NOT_APPLICABLE"


def test_a_single_sample_cannot_exempt_a_gate_its_peers_judged():
    assert pg.merge_gate_samples(["PASS", "NOT_APPLICABLE", "PASS"],
                                 gate=pg.GATE_SURFACE_TEXTURE) == "PASS"
    assert pg.merge_gate_samples(["FAIL", "NOT_APPLICABLE", "NOT_APPLICABLE"],
                                 gate=pg.GATE_SURFACE_TEXTURE) == "FAIL"


def test_not_applicable_does_not_block():
    assert pg.gate_blocks("NOT_APPLICABLE") is False
    assert pg.gate_blocks("PASS") is False
    assert pg.gate_blocks("FAIL") is True
    assert pg.gate_blocks("UNVERIFIABLE") is True


# ── 2단 판정 ─────────────────────────────────────────────────────────────────

def _general(decision="PASS") -> gfa.FidelityDecision:
    return gfa.FidelityDecision(allowed=decision == "PASS", decision=decision,
                                model_decision=decision)


def test_general_pass_plus_stripe_fail_is_final_fail():
    """이 파일이 존재하는 이유. 일반 판정은 전용 게이트를 덮지 못한다."""
    out = gfa.combine(_general("PASS"), required=[pg.GATE_STRIPE],
                      gate_results={pg.GATE_STRIPE: {"status": "FAIL"}})
    assert out.decision == "FAIL"
    assert out.allowed is False
    assert out.failed_gates == (pg.GATE_STRIPE,)


def test_general_pass_plus_stripe_pass_stays_pass():
    out = gfa.combine(_general("PASS"), required=[pg.GATE_STRIPE],
                      gate_results={pg.GATE_STRIPE: {"status": "PASS"}})
    assert out.allowed is True
    assert out.failed_gates == ()


def test_general_fail_is_not_rescued_by_a_passing_gate():
    out = gfa.combine(_general("FAIL"), required=[pg.GATE_STRIPE],
                      gate_results={pg.GATE_STRIPE: {"status": "PASS"}})
    assert out.decision == "FAIL"


def test_unanswered_required_gate_blocks():
    """물어보려다 못 물어본 하드 게이트는 통과가 아니다 — 배선 버그가 이 모양을 한다."""
    out = gfa.combine(_general("PASS"), required=[pg.GATE_STRIPE], gate_results={})
    assert out.allowed is False
    assert out.decision == "UNVERIFIABLE"


def test_unverifiable_gate_downgrades_a_passing_general_verdict():
    out = gfa.combine(_general("PASS"), required=[pg.GATE_CHECK],
                      gate_results={pg.GATE_CHECK: {"status": "UNVERIFIABLE"}})
    assert out.decision == "UNVERIFIABLE"
    assert out.allowed is False


def test_not_applicable_gate_does_not_block():
    out = gfa.combine(_general("PASS"), required=[pg.GATE_SURFACE_TEXTURE],
                      gate_results={pg.GATE_SURFACE_TEXTURE: {"status": "NOT_APPLICABLE"}})
    assert out.allowed is True


def test_no_required_gates_leaves_the_general_decision_untouched():
    general = _general("PASS")
    assert gfa.combine(general, required=[], gate_results={}) is general


def test_gate_statuses_are_recorded_on_the_snapshot():
    out = gfa.combine(_general("PASS"), required=[pg.GATE_STRIPE],
                      gate_results={pg.GATE_STRIPE: {"status": "FAIL"}})
    scores = out.as_scores()
    assert scores["requiredGates"] == [pg.GATE_STRIPE]
    assert scores["failedGates"] == [pg.GATE_STRIPE]
    assert scores["gateStatuses"] == {pg.GATE_STRIPE: "FAIL"}


def test_correction_names_the_failed_gate_properties():
    out = gfa.combine(_general("PASS"), required=[pg.GATE_STRIPE],
                      gate_results={pg.GATE_STRIPE: {"status": "FAIL"}})
    text = gfa.correction_instruction({"checks": {}}, out,
                                      {pg.GATE_STRIPE: {"status": "FAIL"}})
    assert "stripe widths" in text and "spacing" in text
    assert "Preserve the mannequin" in text


def test_gate_correction_keeps_a_specific_model_sentence():
    results = {pg.GATE_STRIPE: {
        "status": "FAIL",
        "correctionInstruction": "Widen the stripe repeat to 5 groups across the chest and "
                                 "restore the blue line beside each taupe line."}}
    out = gfa.combine(_general("PASS"), required=[pg.GATE_STRIPE], gate_results=results)
    text = gfa.correction_instruction({"checks": {}}, out, results)
    assert "blue line beside each taupe" in text


def test_gate_correction_drops_a_vague_model_sentence():
    results = {pg.GATE_STRIPE: {"status": "FAIL",
                                "correctionInstruction": "Make it more accurate."}}
    out = gfa.combine(_general("PASS"), required=[pg.GATE_STRIPE], gate_results=results)
    text = gfa.correction_instruction({"checks": {}}, out, results)
    assert "more accurate" not in text
    assert "stripe widths" in text


# ── 증거 크롭 ────────────────────────────────────────────────────────────────

def _frame(w: int, h: int) -> bytes:
    im = Image.new("RGB", (w, h), "white")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_garment_crop_is_produced_for_a_small_frame():
    crops = pe.generated_crops(_frame(848, 1264))
    assert [c.name for c in crops] == ["generated_garment_crop"]


def test_torso_crop_appears_only_when_the_frame_can_support_it():
    """848px 컷의 몸통 크롭은 220px — 전체 프레임보다 더 보여주는 게 없다."""
    names = [c.name for c in pe.generated_crops(_frame(3392, 5056))]
    assert names == ["generated_garment_crop", "generated_torso_detail_crop"]


def test_crops_are_bounded_so_a_4k_cut_does_not_ship_a_4k_payload():
    for crop in pe.generated_crops(_frame(3392, 5056)):
        assert max(crop.width, crop.height) <= pe.MAX_EDGE * 4  # pre-encode dimensions
        with Image.open(io.BytesIO(crop.data)) as im:
            assert max(im.size) <= pe.MAX_EDGE


def test_crop_records_where_it_came_from():
    crop = pe.generated_crops(_frame(3392, 5056))[0]
    assert crop.as_dict()["rect"] == list(pe.GARMENT_RECT)
    assert crop.as_dict()["sourceSize"] == [3392, 5056]


def test_source_center_crop_declines_a_tiny_source():
    assert pe.source_center_crop(_frame(200, 200), name="x") is None


# ── 판정 응답 검증 ────────────────────────────────────────────────────────────

def _stripe_response(**overrides) -> dict:
    spec = pfq.SPECS[pg.JUDGE_STRIPE]
    body = {name: {"status": "PASS", "sourceObservation": "s", "generatedObservation": "g"}
            for name in spec.properties}
    for key, status in overrides.items():
        body[key] = {"status": status, "sourceObservation": "s", "generatedObservation": "g"}
    body.update({"overallStatus": "PASS", "evidence": "e",
                 "correctionInstruction": "", "confidence": 0.9})
    return body


def test_a_failed_property_fails_the_gate_even_if_overall_says_pass():
    """전용 판정에서도 모델의 자기 보고 필드는 권한이 아니다."""
    spec = pfq.SPECS[pg.JUDGE_STRIPE]
    out = pfq.validate(spec, _stripe_response(density="FAIL"))
    assert out["gateStatuses"][pg.GATE_STRIPE]["status"] == "FAIL"
    assert out["gateStatuses"][pg.GATE_STRIPE]["declared"] == "PASS"


def test_missing_property_degrades_to_unverifiable():
    spec = pfq.SPECS[pg.JUDGE_STRIPE]
    body = _stripe_response()
    del body["spacing"]
    out = pfq.validate(spec, body)
    assert out["properties"]["spacing"]["status"] == "UNVERIFIABLE"
    assert out["gateStatuses"][pg.GATE_STRIPE]["status"] == "UNVERIFIABLE"


def test_schema_lists_every_property_and_status_field():
    for spec in pfq.SPECS.values():
        props = pfq.schema(spec)["properties"]
        for name in spec.properties:
            assert name in props
        for field_name in spec.status_fields:
            assert field_name in props


def test_only_material_judges_offer_not_applicable():
    stripe = pfq.schema(pfq.SPECS[pg.JUDGE_STRIPE])["properties"]["density"]
    texture = pfq.schema(pfq.SPECS[pg.JUDGE_SURFACE_TEXTURE])["properties"]["structureType"]
    assert "NOT_APPLICABLE" not in stripe["properties"]["status"]["enum"]
    assert "NOT_APPLICABLE" in texture["properties"]["status"]["enum"]


def test_lace_judge_answers_two_gates_in_one_call():
    spec = pfq.SPECS[pg.JUDGE_LACE_OPACITY]
    assert set(spec.status_fields.values()) == {pg.GATE_LACE_STRUCTURE, pg.GATE_OPACITY}


def test_merge_is_fail_closed_across_samples():
    spec = pfq.SPECS[pg.JUDGE_STRIPE]
    good = pfq.validate(spec, _stripe_response())
    bad = pfq.validate(spec, _stripe_response(relativeWidth="FAIL"))
    merged = pfq.merge_samples(spec, [good, good, bad])
    assert merged["gateStatuses"][pg.GATE_STRIPE]["status"] == "FAIL"
    assert merged["properties"]["relativeWidth"]["sampleStatuses"] == ["PASS", "PASS", "FAIL"]


def test_manifest_numbers_match_attachment_order():
    from app.agents.gemini_image import InlineImage
    evidence = pfq.build_evidence(
        source_front=b"\x89PNGfront", source_detail=b"\x89PNGdetail",
        source_detail_crop=b"\x89PNGcrop",
        generated_full=InlineImage("image/png", b"gen"), generated_crops=[])
    manifest = pfq.build_manifest(evidence)
    assert "1. SOURCE FRONT" in manifest
    assert "2. SOURCE DETAIL —" in manifest
    assert "3. SOURCE DETAIL CROP" in manifest
    assert "4. GENERATED FULL" in manifest


def test_sample_policy_is_unchanged():
    """라이브 스트라이프는 PASS 3/3 이었다. 표를 더 세는 걸로는 안 고쳐진다."""
    assert pfq.DEFAULT_SAMPLES == 3
    assert pfq.JUDGE_TEMPERATURE == 0.0
