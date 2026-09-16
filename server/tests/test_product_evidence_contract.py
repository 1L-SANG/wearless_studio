from copy import deepcopy
from hashlib import sha256
import json
import logging

import pytest

from app.agents import product_evidence_contract as pec


def _binding():
    return pec.build_input_binding(
        [
            (b"seller-front-original", "image/png"),
            (b"seller-back-original", "image/jpeg"),
        ],
        [
            (b"analysis-front", "image/jpeg"),
            (b"analysis-back", "image/jpeg"),
        ],
        ["Front", "Back"],
    )


def _raw():
    return {
        "panels": [
            {
                "evidenceOrdinal": 1,
                "detail": "complete front, neckline and closure",
                "judgeability": "usable",
                "judgeabilityReasons": ["fold_distortion", "mixed_light"],
            },
            {
                "evidenceOrdinal": 2,
                "detail": "complete back and shoulder seams",
                "judgeability": "uncertain",
                "judgeabilityReasons": ["partial_crop"],
            },
        ],
        "hardFacts": [
            {
                "code": "front_closure",
                "value": "single front button placket",
                "evidenceOrdinals": [1],
            }
        ],
        "uncertainties": [
            {
                "code": "exact_worn_fit",
                "value": "exact body-worn ease and hem position",
                "reason": "flat presentation does not prove body-worn fit",
                "evidenceOrdinals": [1, 2],
            }
        ],
        "hem_shape": {"value": "straight", "evidenceOrdinals": [1]},
        "cuff": {
            "value": "cuff height is about one sleeve-band width; opening is slightly wider than the sleeve",
            "evidenceOrdinals": [1],
        },
        "button_count_visible": {"value": 5, "evidenceOrdinals": [1]},
        "pattern_structure": {"value": "none", "evidenceOrdinals": [1]},
        "surface_texture": {
            "value": "fine vertical rib structure is visible on the front",
            "evidenceOrdinals": [1],
        },
        "seam_lines": {
            "value": "two front seam lines start below the placket and continue to the hem",
            "evidenceOrdinals": [1],
        },
    }


def _legacy_contract():
    return json.loads("""{"contractSha256":"762584f1a95d106708e6affe9325656b38ba27acca239e10835d8b8f6f579241","direction":"front","hardFacts":[{"code":"front_closure","evidenceOrdinals":[1],"value":"single front button placket"}],"inputBinding":{"hashAlgorithm":"sha256","images":[{"analysis":{"byteLength":14,"mime":"image/jpeg","sha256":"2c643c785a3d5dccb8c73a0a11580e4d9a130062cbfe812a8f23caf41d8ed03a"},"ordinal":1,"slot":"FRONT","source":{"byteLength":21,"mime":"image/png","sha256":"074ef3e696455a524941b60431b96a2c46ca1ebef74063eaf4a8425a6e0aeac7"}},{"analysis":{"byteLength":13,"mime":"image/jpeg","sha256":"967d70635d87c73ab615c12dec9bd0d326f26b94ccd57542f42b407c32feab77"},"ordinal":2,"slot":"BACK","source":{"byteLength":20,"mime":"image/jpeg","sha256":"ec9ddcbc968f9b56b14d978d167ef50ad191a7a470604ea5487e262c8f9b30a2"}}],"orderedAnalysisInputSha256":"c7647d3fc741438d6f557746cd2f7737782af33e077959cad32277d4e81fe62a","orderedSourceInputSha256":"03cd02c5d8dac7cd1f448e312730c91a92ca151c0e4324b6ed077f9fc2ca3308","schemaVersion":1},"panels":[{"detail":"complete front, neckline and closure","evidenceOrdinal":1,"judgeability":"usable","judgeabilityReasons":["fold_distortion","mixed_light"],"provided":true,"slot":"FRONT","surfaceAuthority":"DOMINANT"},{"detail":"complete back and shoulder seams","evidenceOrdinal":2,"judgeability":"uncertain","judgeabilityReasons":["partial_crop"],"provided":true,"slot":"BACK","surfaceAuthority":"CONTEXT"}],"schemaVersion":1,"uncertainties":[{"code":"exact_worn_fit","evidenceOrdinals":[1,2],"reason":"flat presentation does not prove body-worn fit","value":"exact body-worn ease and hem position"}],"visibleSurfacePlan":"FRONT/FRONT_DETAIL surfaces are DOMINANT; BACK/BACK_DETAIL surfaces are CONTEXT only for physically revealed slivers and transitions. Observed visible-surface details: FRONT is dominant; BACK is context only for physically revealed seam transitions."}""")


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def test_binding_seals_ordered_original_and_attached_analysis_bytes():
    binding = _binding()
    assert binding["images"] == [
        {
            "ordinal": 1,
            "slot": "FRONT",
            "source": {
                "mime": "image/png",
                "sha256": sha256(b"seller-front-original").hexdigest(),
                "byteLength": len(b"seller-front-original"),
            },
            "analysis": {
                "mime": "image/jpeg",
                "sha256": sha256(b"analysis-front").hexdigest(),
                "byteLength": len(b"analysis-front"),
            },
        },
        {
            "ordinal": 2,
            "slot": "BACK",
            "source": {
                "mime": "image/jpeg",
                "sha256": sha256(b"seller-back-original").hexdigest(),
                "byteLength": len(b"seller-back-original"),
            },
            "analysis": {
                "mime": "image/jpeg",
                "sha256": sha256(b"analysis-back").hexdigest(),
                "byteLength": len(b"analysis-back"),
            },
        },
    ]
    source_rows = [
        {"ordinal": row["ordinal"], "slot": row["slot"], **row["source"]}
        for row in binding["images"]
    ]
    assert binding["orderedSourceInputSha256"] == sha256(
        _canonical(source_rows)
    ).hexdigest()
    assert (
        binding["orderedSourceInputSha256"]
        != binding["orderedAnalysisInputSha256"]
    )


def test_binding_requires_front_and_exact_parallel_lengths():
    with pytest.raises(pec.ProductEvidenceContractError, match="front_image_required"):
        pec.build_input_binding(
            [(b"back", "image/png")], [(b"back-small", "image/png")], ["Back"]
        )
    with pytest.raises(pec.ProductEvidenceContractError, match="count_mismatch"):
        pec.build_input_binding(
            [(b"front", "image/png")], [], ["Front"]
        )


def test_prompt_block_names_exact_order_hashes_and_same_call_contract():
    binding = _binding()
    prompt = pec.render_prompt_block(binding)
    assert "evidenceOrdinal 1: slot FRONT" in prompt
    assert "evidenceOrdinal 2: slot BACK" in prompt
    assert binding["images"][0]["source"]["sha256"] in prompt
    assert binding["images"][0]["analysis"]["sha256"] in prompt
    assert "additional output from this same AG-01 call" in prompt


def test_prompt_block_prioritizes_visible_construction_without_inventing_answers():
    prompt = pec.render_prompt_block(_binding())
    normalized = " ".join(prompt.split())

    assert "record closure count separately from closure function" in normalized
    assert "Keep boundary count distinct from parallel stitch rows" in normalized
    assert "do not bridge an occlusion by symmetry or expectation" in normalized
    assert "Do not infer that a feature is absent merely because it is hidden" in normalized
    assert "back-only detail must remain back-only" in normalized
    assert "This does not mean every broad shoulder is sleeveless" in normalized
    assert "Unsupported worn fit remains an uncertainty" in normalized
    assert "Use JSON null for unknown" in normalized
    for field in (
        "hem_shape", "cuff", "button_count_visible", "pattern_structure",
        "surface_texture", "seam_lines",
    ):
        assert field in prompt
    assert "- visibleSurfacePlan:" not in prompt
    assert "EXPERIMENT-ONLY PROPOSAL" not in prompt


def test_validate_binds_only_server_metadata_and_derives_front_authority():
    contract = pec.validate_and_bind(_raw(), _binding())
    assert contract["direction"] == "front"
    assert contract["observationsVersion"] == pec.OBSERVATIONS_VERSION
    assert contract["panels"][0]["surfaceAuthority"] == "DOMINANT"
    assert contract["panels"][1]["surfaceAuthority"] == "CONTEXT"
    assert contract["panels"][0]["provided"] is True
    assert contract["hardFacts"][0]["evidenceOrdinals"] == [1]
    assert pec.validate_persisted(contract) == contract


def test_new_surface_plan_is_server_owned_routing_policy_not_model_design_prose():
    contract = pec.validate_and_bind(_raw(), _binding())
    assert contract["visibleSurfacePlan"] == pec.FRONT_SURFACE_POLICY

    raw = _raw()
    raw["visibleSurfacePlan"] = "Preserve a clean curved lower hem."
    with pytest.raises(pec.ProductEvidenceContractError, match="field_set_mismatch"):
        pec.validate_and_bind(raw, _binding())


@pytest.mark.parametrize(
    "mutate,error",
    [
        (
            lambda raw: raw["panels"].reverse(),
            "panel_order_mismatch",
        ),
        (
            lambda raw: raw["panels"][0].update(
                judgeabilityReasons=["mixed_light", "sharp_enough"]
            ),
            "judgeability_reasons",
        ),
        (
            lambda raw: raw["hardFacts"][0].update(evidenceOrdinals=[2]),
            "requires_usable_panel",
        ),
        (
            lambda raw: raw["uncertainties"][0].update(
                evidenceOrdinals=[2, 1]
            ),
            "invalid_ordinals",
        ),
        (
            lambda raw: raw["button_count_visible"].update(value=True),
            "invalid_button_count_visible",
        ),
        (
            lambda raw: raw["hem_shape"].update(value="none"),
            "invalid_hem_shape",
        ),
        (
            lambda raw: raw["surface_texture"].update(evidenceOrdinals=[2]),
            "requires_usable_panel",
        ),
        (
            lambda raw: raw["seam_lines"].update(value="x" * 241),
            "invalid_single_line",
        ),
        (
            lambda raw: raw["hardFacts"][0].update(value="x" * 241),
            "invalid_single_line",
        ),
        (
            lambda raw: raw["uncertainties"][0].update(value="x" * 241),
            "invalid_single_line",
        ),
    ],
)
def test_invalid_model_evidence_fails_closed(mutate, error):
    raw = _raw()
    mutate(raw)
    with pytest.raises(pec.ProductEvidenceContractError, match=error):
        pec.validate_and_bind(raw, _binding())


_REASON_LOG = "wearless.product_evidence_contract"


@pytest.mark.parametrize(
    "reasons,expected",
    [
        # 한 패널에 clear_enough 와 진짜 한계가 같이 오면 모순은 "문제 있음" 쪽으로 읽는다.
        (["clear_enough", "fold_distortion"], ["fold_distortion"]),
        (
            ["fold_distortion", "clear_enough", "mixed_light"],
            ["fold_distortion", "mixed_light"],
        ),
        # 중복은 순서를 지키며 하나로 합친다.
        (["mixed_light", "blur", "mixed_light"], ["mixed_light", "blur"]),
        (["clear_enough", "clear_enough"], ["clear_enough"]),
    ],
)
def test_contradictory_or_repeated_reasons_normalize_instead_of_failing_analysis(
    reasons, expected, caplog
):
    raw = _raw()
    raw["panels"][0]["judgeabilityReasons"] = reasons
    with caplog.at_level(logging.INFO, logger=_REASON_LOG):
        contract = pec.validate_and_bind(raw, _binding())
    assert contract["panels"][0]["judgeabilityReasons"] == expected
    # 모델 품질을 추적할 수 있도록 정규화 사실을 남긴다.
    assert "judgeability_reasons_normalized" in caplog.text
    # 정규화 결과는 저장본 재검증을 그대로 통과해야 한다(2차 검증이 같은 규칙을 본다).
    assert pec.validate_persisted(contract) == contract


def test_reason_normalization_never_moves_the_panel_verdict():
    raw = _raw()
    raw["panels"][0]["judgeabilityReasons"] = ["clear_enough", "fold_distortion"]
    raw["panels"][1]["judgeabilityReasons"] = [
        "partial_crop", "clear_enough", "partial_crop",
    ]
    baseline = pec.validate_and_bind(_raw(), _binding())
    contract = pec.validate_and_bind(raw, _binding())
    verdicts = [panel["judgeability"] for panel in contract["panels"]]
    assert verdicts == [panel["judgeability"] for panel in baseline["panels"]]
    assert verdicts == ["usable", "uncertain"]


def test_clean_reasons_pass_through_untouched_and_unlogged(caplog):
    with caplog.at_level(logging.INFO, logger=_REASON_LOG):
        contract = pec.validate_and_bind(_raw(), _binding())
    assert contract["panels"][0]["judgeabilityReasons"] == [
        "fold_distortion", "mixed_light",
    ]
    assert contract["panels"][1]["judgeabilityReasons"] == ["partial_crop"]
    assert "judgeability_reasons_normalized" not in caplog.text


def test_clear_enough_alone_stays_the_whole_reason_list(caplog):
    raw = _raw()
    raw["panels"][0]["judgeabilityReasons"] = ["clear_enough"]
    with caplog.at_level(logging.INFO, logger=_REASON_LOG):
        contract = pec.validate_and_bind(raw, _binding())
    assert contract["panels"][0]["judgeabilityReasons"] == ["clear_enough"]
    assert "judgeability_reasons_normalized" not in caplog.text


@pytest.mark.parametrize(
    "reasons",
    [
        [],
        # 목록에 없는 사유는 서버가 정의한 적 없는 어휘라 계약 위반이 맞다.
        ["clear_enough", "sharp_enough"],
        ["fold_distortion", None],
        [["clear_enough"]],
        "clear_enough",
        None,
    ],
)
def test_empty_or_unlisted_reasons_still_fail_closed(reasons):
    raw = _raw()
    raw["panels"][0]["judgeabilityReasons"] = reasons
    with pytest.raises(
        pec.ProductEvidenceContractError,
        match="product_evidence_invalid_judgeability_reasons",
    ):
        pec.validate_and_bind(raw, _binding())


def test_persisted_contract_hash_and_server_input_binding_detect_tamper():
    contract = pec.validate_and_bind(_raw(), _binding())
    tampered = deepcopy(contract)
    tampered["hardFacts"][0]["value"] = "invented zipper"
    with pytest.raises(pec.ProductEvidenceContractError, match="contract_hash_mismatch"):
        pec.validate_persisted(tampered)

    tampered_binding = deepcopy(contract)
    tampered_binding["inputBinding"]["images"][0]["source"]["byteLength"] += 1
    with pytest.raises(pec.ProductEvidenceContractError, match="source_sequence_hash_mismatch"):
        pec.validate_persisted(tampered_binding)

    tampered_observation = deepcopy(contract)
    tampered_observation["hem_shape"]["value"] = "curved"
    with pytest.raises(pec.ProductEvidenceContractError, match="contract_hash_mismatch"):
        pec.validate_persisted(tampered_observation)


def test_current_source_must_match_sha_bytes_slots_and_order():
    contract = pec.validate_and_bind(_raw(), _binding())
    assert pec.source_binding_matches(
        contract,
        [
            (b"seller-front-original", "image/png"),
            (b"seller-back-original", "image/jpeg"),
        ],
        ["Front", "Back"],
    )
    assert not pec.source_binding_matches(
        contract,
        [
            (b"seller-front-CHANGED", "image/png"),
            (b"seller-back-original", "image/jpeg"),
        ],
        ["Front", "Back"],
    )


def test_schema_is_closed_and_requires_the_six_fixed_observations():
    schema = pec.evidence_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(schema["required"])
    assert set(schema["required"]) == {
        "panels", "hardFacts", "uncertainties", "hem_shape", "cuff",
        "button_count_visible", "pattern_structure", "surface_texture", "seam_lines",
    }
    panel = schema["properties"]["panels"]["items"]
    assert panel["additionalProperties"] is False
    assert set(panel["properties"]) == set(panel["required"])


def test_unknown_fixed_observations_do_not_require_invented_evidence():
    raw = _raw()
    for field in (
        "hem_shape", "cuff", "button_count_visible", "pattern_structure",
        "surface_texture", "seam_lines",
    ):
        raw[field] = {
            "value": None if field == "button_count_visible" else "unknown",
            "evidenceOrdinals": [],
        }
    contract = pec.validate_and_bind(raw, _binding())
    assert all(contract[field]["value"] == "unknown" for field in (
        "hem_shape", "cuff", "button_count_visible", "pattern_structure",
        "surface_texture", "seam_lines",
    ))


@pytest.mark.parametrize("field", [
    "hem_shape", "cuff", "button_count_visible", "pattern_structure",
    "surface_texture", "seam_lines",
])
def test_missing_fixed_observation_fails_new_output_closed(field):
    raw = _raw()
    del raw[field]
    with pytest.raises(pec.ProductEvidenceContractError, match="field_set_mismatch"):
        pec.validate_and_bind(raw, _binding())


def test_fresh_ag01_rejects_the_complete_legacy_four_field_raw_shape():
    legacy = _legacy_contract()
    raw = {
        "panels": [{
            key: panel[key]
            for key in ("evidenceOrdinal", "detail", "judgeability", "judgeabilityReasons")
        } for panel in legacy["panels"]],
        "hardFacts": legacy["hardFacts"],
        "uncertainties": legacy["uncertainties"],
        "visibleSurfacePlan": legacy["visibleSurfacePlan"],
    }
    with pytest.raises(pec.ProductEvidenceContractError, match="field_set_mismatch"):
        pec.validate_and_bind(raw, legacy["inputBinding"])


@pytest.mark.parametrize("value", [-1, 1.5, True, "5", "none"])
def test_visible_button_count_is_nonnegative_integer_or_unknown(value):
    raw = _raw()
    raw["button_count_visible"]["value"] = value
    with pytest.raises(pec.ProductEvidenceContractError, match="invalid_button_count_visible"):
        pec.validate_and_bind(raw, _binding())


def test_legacy_hash_and_signature_fixture_remain_byte_compatible():
    legacy = _legacy_contract()
    assert pec.validate_persisted(legacy) == legacy
    assert legacy["contractSha256"] == "762584f1a95d106708e6affe9325656b38ba27acca239e10835d8b8f6f579241"
    handoff = pec.issue_handoff(legacy, "test-only-handoff-secret-32-bytes", now=1000)
    assert handoff["signature"] == "878168d79e21ea4e8483e007e2a5662aef267213fed19d48a184c5864116a9aa"
    assert pec.verify_handoff(handoff, "test-only-handoff-secret-32-bytes", now=1001) == legacy


def test_public_analysis_handoff_is_signed_short_lived_and_tamper_evident():
    contract = pec.validate_and_bind(_raw(), _binding())
    secret = "test-only-handoff-secret-32-bytes"
    handoff = pec.issue_handoff(contract, secret, now=1000)

    assert pec.verify_handoff(handoff, secret, now=1001) == contract

    tampered = deepcopy(handoff)
    tampered["contract"]["hardFacts"][0]["value"] = "invented zipper"
    with pytest.raises(pec.ProductEvidenceContractError, match="signature_invalid"):
        pec.verify_handoff(tampered, secret, now=1001)

    with pytest.raises(pec.ProductEvidenceContractError, match="expired_or_invalid"):
        pec.verify_handoff(handoff, secret, now=handoff["expiresAt"] + 1)
