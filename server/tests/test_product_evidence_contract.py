from copy import deepcopy
from hashlib import sha256
import json

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
        "visibleSurfacePlan": (
            "FRONT is dominant; BACK is context only for physically revealed seam transitions."
        ),
    }


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


def test_validate_binds_only_server_metadata_and_derives_front_authority():
    contract = pec.validate_and_bind(_raw(), _binding())
    assert contract["direction"] == "front"
    assert contract["panels"][0]["surfaceAuthority"] == "DOMINANT"
    assert contract["panels"][1]["surfaceAuthority"] == "CONTEXT"
    assert contract["panels"][0]["provided"] is True
    assert contract["hardFacts"][0]["evidenceOrdinals"] == [1]
    assert pec.validate_persisted(contract) == contract


@pytest.mark.parametrize(
    "mutate,error",
    [
        (
            lambda raw: raw["panels"].reverse(),
            "panel_order_mismatch",
        ),
        (
            lambda raw: raw["panels"][0].update(
                judgeabilityReasons=["clear_enough", "mixed_light"]
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
            lambda raw: raw.update(visibleSurfacePlan="BACK is dominant."),
            "front_surface_plan_required",
        ),
    ],
)
def test_invalid_model_evidence_fails_closed(mutate, error):
    raw = _raw()
    mutate(raw)
    with pytest.raises(pec.ProductEvidenceContractError, match=error):
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


def test_schema_is_closed_and_requires_all_four_model_fields():
    schema = pec.evidence_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(schema["required"])
    panel = schema["properties"]["panels"]["items"]
    assert panel["additionalProperties"] is False
    assert set(panel["properties"]) == set(panel["required"])
