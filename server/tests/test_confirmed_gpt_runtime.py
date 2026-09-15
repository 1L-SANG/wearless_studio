from io import BytesIO
from hashlib import sha256
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from app.agents import confirmed_gpt_runtime, product_evidence_contract
from app.agents.confirmed_gpt_prompt import (
    CutLock,
    OutfitLock,
    PoseSemantics,
    compile_confirmed_gpt_prompt,
)
from app.agents.gemini_image import InlineImage


def _png(color="navy") -> bytes:
    out = BytesIO()
    Image.new("RGB", (900, 900), color).save(out, format="PNG")
    return out.getvalue()


def _contract(source: bytes) -> dict:
    binding = product_evidence_contract.build_input_binding(
        [(source, "image/png")], [(source, "image/png")], ["Front"]
    )
    return product_evidence_contract.validate_and_bind(
        {
            "panels": [
                {
                    "evidenceOrdinal": 1,
                    "detail": "front neckline, sleeves and hem",
                    "judgeability": "usable",
                    "judgeabilityReasons": ["clear_enough"],
                }
            ],
            "hardFacts": [
                {
                    "code": "front_shape",
                    "value": "The front neckline, sleeves and hem are visible.",
                    "evidenceOrdinals": [1],
                }
            ],
            "uncertainties": [
                {
                    "code": "back_shape",
                    "value": "The hidden back construction is not proven.",
                    "reason": "Only the front seller view is supplied.",
                    "evidenceOrdinals": [1],
                }
            ],
            "hem_shape": {"value": "straight", "evidenceOrdinals": [1]},
            "cuff": {"value": "unknown", "evidenceOrdinals": []},
            "button_count_visible": {"value": None, "evidenceOrdinals": []},
            "pattern_structure": {"value": "none", "evidenceOrdinals": [1]},
            "surface_texture": {"value": "unknown", "evidenceOrdinals": []},
            "seam_lines": {"value": "unknown", "evidenceOrdinals": []},
        },
        binding,
    )


def _legacy_front_back_contract(front: bytes, back: bytes) -> dict:
    binding = product_evidence_contract.build_input_binding(
        [(front, "image/png"), (back, "image/png")],
        [(front, "image/png"), (back, "image/png")],
        ["Front", "Back"],
    )
    contract = {
        "schemaVersion": 1,
        "direction": "front",
        "inputBinding": binding,
        "panels": [
            {
                "evidenceOrdinal": 1,
                "slot": "FRONT",
                "detail": "front neckline and placket",
                "surfaceAuthority": "DOMINANT",
                "judgeability": "usable",
                "judgeabilityReasons": ["clear_enough"],
                "provided": True,
            },
            {
                "evidenceOrdinal": 2,
                "slot": "BACK",
                "detail": "back yoke",
                "surfaceAuthority": "CONTEXT",
                "judgeability": "usable",
                "judgeabilityReasons": ["clear_enough"],
                "provided": True,
            },
        ],
        "hardFacts": [
            {
                "code": "front_neckline",
                "value": "round front neckline with narrow binding",
                "evidenceOrdinals": [1],
            },
            {
                "code": "back_yoke",
                "value": "deep curved back-only yoke",
                "evidenceOrdinals": [2],
            },
        ],
        "uncertainties": [
            {
                "code": "side_connection",
                "value": "side seam connection",
                "reason": "side is not visible",
                "evidenceOrdinals": [1, 2],
            }
        ],
        "visibleSurfacePlan": (
            product_evidence_contract.FRONT_SURFACE_POLICY
            + " Observed visible-surface details: preserve a clean curved lower hem."
        ),
    }
    canonical = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    contract["contractSha256"] = sha256(canonical).hexdigest()
    assert product_evidence_contract.validate_persisted(contract) == contract
    return contract


def _directing():
    pose = PoseSemantics(
        action="quiet standing portrait",
        body_direction="front-family with a slight turn",
        weight_and_support="weight biased to one side",
        key_contacts="arms relaxed without a prop",
        gaze="slightly off camera",
        rough_framing="vertical medium styling cut",
    )
    return SimpleNamespace(
        cut_lock=lambda: CutLock(
            shot="medium",
            user_direction="front",
            direction_description="front-family medium view",
            face_exposure="visible with an off-camera gaze",
            requested_framing="complete head through the sold top hem",
        ),
        outfit_lock=lambda *, matching_attached: OutfitLock(
            fixed_inner=None,
            fixed_footwear="not visible in the medium composition",
            matching_attached=matching_attached,
        ),
        pose_semantics=pose,
    )


def _spec():
    return {
        "cutType": "styling",
        "direction": "front",
        "shot": "medium",
        "refScope": "all",
        "pose": "auto",
        "spaceGroupId": None,
        "exampleId": "ex",
        "_referenceDirectionCompatible": True,
    }


def _build(monkeypatch, *, source=None, selected="mE", effective="mE", matches=(),
           contract=None, seller_images=None):
    source = source or _png()
    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "bind_confirmed_gpt_directing",
        lambda *_args, **_kwargs: _directing(),
    )
    image = InlineImage("image/png", source)
    return confirmed_gpt_runtime.build_packet(
        _spec(),
        clothing_type="top",
        identity_source="VIRTUAL",
        selected_model_id=selected,
        effective_model_id=effective,
        uses_base_color=True,
        mannequin_image=InlineImage("image/png", b"mannequin"),
        face_direction_sheet=InlineImage("image/png", b"face sheet"),
        full_body_direction_sheet=InlineImage("image/png", b"body sheet"),
        seller_images=seller_images if seller_images is not None else (("Front", image),),
        matching_images=matches,
        example_image=InlineImage("image/png", b"example"),
        evidence_contract=contract if contract is not None else _contract(source),
    )


def _observation_contract(front, *, extra_slot=None, changes=None):
    """Use the real AG-01 validator and byte binding, not a forged persisted fixture."""
    base = _contract(front)
    fields = ('hardFacts', 'uncertainties', *product_evidence_contract.FIXED_OBSERVATION_FIELDS)
    raw = {field: base[field] for field in fields}
    raw['panels'] = [{
        'evidenceOrdinal': 1, 'detail': 'front garment construction',
        'judgeability': 'usable', 'judgeabilityReasons': ['clear_enough'],
    }]
    images = [('Front', InlineImage('image/png', front))]
    if extra_slot:
        images.append((extra_slot, InlineImage('image/png', _png('gray'))))
        raw['panels'].append({
            'evidenceOrdinal': 2, 'detail': 'additional garment view',
            'judgeability': 'usable', 'judgeabilityReasons': ['clear_enough'],
        })
    raw.update(changes or {})
    sources = [(im.data, im.mime) for _, im in images]
    binding = product_evidence_contract.build_input_binding(sources, sources, [s for s, _ in images])
    return product_evidence_contract.validate_and_bind(raw, binding), tuple(images)


@pytest.mark.parametrize('count', [0, 7])
def test_fixed_front_observations_reach_the_actual_final_prompt(monkeypatch, count):
    source = _png()
    contract, images = _observation_contract(source, changes={
        'cuff': {'value': 'narrow cuff with an open edge', 'evidenceOrdinals': [1]},
        'button_count_visible': {'value': count, 'evidenceOrdinals': [1]},
        'pattern_structure': {'value': 'paired blue lines with a fine dark center', 'evidenceOrdinals': [1]},
        'surface_texture': {'value': 'fine outer knit loops', 'evidenceOrdinals': [1]},
        'seam_lines': {'value': 'Two curved front seams join two vertical seams extending to the hem.', 'evidenceOrdinals': [1]},
    })
    original = json.dumps(contract, sort_keys=True)
    packet = _build(monkeypatch, source=source, contract=contract, seller_images=images)
    prompt = compile_confirmed_gpt_prompt(packet.prompt_input)
    for field in product_evidence_contract.FIXED_OBSERVATION_FIELDS:
        assert f'[observed_{field}]' in prompt
        assert str(contract[field]['value']) in prompt
    assert f'not total or hidden button count): {count}' in prompt
    assert 'Observed front hem shape (not worn length): straight' in prompt
    assert contract['hardFacts'][0]['value'] in prompt
    assert 'selected resolved mannequin owns' in prompt
    assert json.dumps(contract, sort_keys=True) == original


def test_unknown_observations_are_not_promoted_to_final_facts(monkeypatch):
    source = _png()
    contract, images = _observation_contract(source, changes={
        field: {'value': None if field == 'button_count_visible' else 'unknown', 'evidenceOrdinals': []}
        for field in product_evidence_contract.FIXED_OBSERVATION_FIELDS
    })
    packet = _build(monkeypatch, source=source, contract=contract, seller_images=images)
    assert [fact.code for fact in packet.prompt_input.hard_facts] == ['front_shape']


@pytest.mark.parametrize('field,value', [
    ('hem_shape', 'curved'), ('cuff', 'wide cuff'), ('button_count_visible', 7),
    ('pattern_structure', 'back-only paired lines'), ('surface_texture', 'back-only rib texture'),
    ('seam_lines', 'deep curved back-only yoke'),
])
def test_back_only_fixed_observation_is_not_front_design(monkeypatch, field, value):
    source = _png()
    contract, images = _observation_contract(source, extra_slot='Back', changes={
        field: {'value': value, 'evidenceOrdinals': [2]},
    })
    packet = _build(monkeypatch, source=source, contract=contract, seller_images=images)
    assert f'observed_{field}' not in [fact.code for fact in packet.prompt_input.hard_facts]


def test_uncertain_front_cannot_borrow_usable_back_to_certify_seams(monkeypatch):
    source = _png()
    contract, images = _observation_contract(source, extra_slot='Back', changes={
        'panels': [
            {'evidenceOrdinal': 1, 'detail': 'blurred front', 'judgeability': 'uncertain', 'judgeabilityReasons': ['blur']},
            {'evidenceOrdinal': 2, 'detail': 'clear back', 'judgeability': 'usable', 'judgeabilityReasons': ['clear_enough']},
        ],
        'hardFacts': [{'code': 'back_yoke', 'value': 'back yoke', 'evidenceOrdinals': [2]}],
        'hem_shape': {'value': 'unknown', 'evidenceOrdinals': []},
        'pattern_structure': {'value': 'unknown', 'evidenceOrdinals': []},
        'seam_lines': {'value': 'curved yoke joins vertical seams', 'evidenceOrdinals': [1, 2]},
    })
    with pytest.raises(confirmed_gpt_runtime.ConfirmedGptRuntimeError, match='front_hard_facts_required'):
        _build(monkeypatch, source=source, contract=contract, seller_images=images)


def test_front_detail_observation_suffices_when_old_facts_are_back_only(monkeypatch):
    source = _png()
    contract, images = _observation_contract(source, extra_slot='Back', changes={
        'hardFacts': [{'code': 'back_yoke', 'value': 'back yoke', 'evidenceOrdinals': [2]}],
        'button_count_visible': {'value': 7, 'evidenceOrdinals': [1]},
    })
    # Keep the required full front and bind a separate genuine detail photograph.
    raw = {field: contract[field] for field in ('hardFacts', 'uncertainties', *product_evidence_contract.FIXED_OBSERVATION_FIELDS)}
    raw['panels'] = [{k: p[k] for k in ('evidenceOrdinal', 'detail', 'judgeability', 'judgeabilityReasons')} for p in contract['panels']]
    raw['panels'].append({'evidenceOrdinal': 3, 'detail': 'front placket closeup',
                          'judgeability': 'usable', 'judgeabilityReasons': ['clear_enough']})
    for field in product_evidence_contract.FIXED_OBSERVATION_FIELDS:
        raw[field] = {'value': 'unknown', 'evidenceOrdinals': []}
    raw['button_count_visible'] = {'value': 7, 'evidenceOrdinals': [3]}
    images = (*images, ('Detail', InlineImage('image/png', _png('pink'))))
    sources = [(im.data, im.mime) for _, im in images]
    binding = product_evidence_contract.build_input_binding(sources, sources, [s for s, _ in images])
    contract = product_evidence_contract.validate_and_bind(raw, binding)
    packet = _build(monkeypatch, source=source, contract=contract, seller_images=images)
    prompt = compile_confirmed_gpt_prompt(packet.prompt_input)
    assert 'not total or hidden button count): 7' in prompt
    assert '[back_yoke]' not in prompt


def test_observation_codes_do_not_collide_with_existing_fact_or_uncertainty(monkeypatch):
    source = _png()
    contract, images = _observation_contract(source, changes={
        'hardFacts': [{'code': 'observed_hem_shape', 'value': 'source identity', 'evidenceOrdinals': [1]}],
        'uncertainties': [{'code': 'observed_hem_shape_2', 'value': 'hidden hem transition', 'reason': 'occluded', 'evidenceOrdinals': [1]}],
    })
    prompt = compile_confirmed_gpt_prompt(_build(monkeypatch, source=source, contract=contract, seller_images=images).prompt_input)
    assert '[observed_hem_shape_3] Observed front hem shape' in prompt
    assert '[observed_hem_shape] source identity' in prompt
    assert 'hidden hem transition' in prompt


def test_packet_replays_exact_role_order_and_compiles(monkeypatch):
    packet = _build(monkeypatch)

    assert [image.data for image in packet.images[:3]] == [
        b"mannequin",
        b"face sheet",
        b"body sheet",
    ]
    assert packet.images[-1].data == b"example"
    assert len(packet.images) == 5
    assert packet.manifest.splitlines() == [
        "1. MANNEQUIN — selected garment-local color and fit authority",
        "2. MODEL FACE — direction sheet",
        "3. MODEL FULL BODY — direction sheet",
        "4. PRODUCT — sold-product labelled evidence grid",
        "5. EXAMPLE REFERENCE (scope: all) — service reference",
    ]
    prompt = compile_confirmed_gpt_prompt(packet.prompt_input)
    assert "existing_exact" not in prompt
    assert "RECENT IPHONE DEFAULT PHOTO CONTRACT" in prompt
    assert "naturally plausible nearby-feeling alternate" in prompt


def test_legacy_contract_cannot_send_model_surface_prose_or_back_only_fact_to_provider(monkeypatch):
    front, back = _png("navy"), _png("gray")
    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "bind_confirmed_gpt_directing",
        lambda *_args, **_kwargs: _directing(),
    )
    packet = confirmed_gpt_runtime.build_packet(
        _spec(),
        clothing_type="top",
        identity_source="VIRTUAL",
        selected_model_id="mE",
        effective_model_id="mE",
        uses_base_color=True,
        mannequin_image=InlineImage("image/png", b"mannequin"),
        face_direction_sheet=InlineImage("image/png", b"face sheet"),
        full_body_direction_sheet=InlineImage("image/png", b"body sheet"),
        seller_images=(
            ("Front", InlineImage("image/png", front)),
            ("Back", InlineImage("image/png", back)),
        ),
        matching_images=(),
        example_image=InlineImage("image/png", b"example"),
        evidence_contract=_legacy_front_back_contract(front, back),
    )
    prompt = compile_confirmed_gpt_prompt(packet.prompt_input)
    assert "clean curved lower hem" not in prompt
    assert "deep curved back-only yoke" not in prompt
    assert "round front neckline with narrow binding" in prompt
    assert product_evidence_contract.FRONT_SURFACE_POLICY in prompt


def test_packet_inserts_only_one_matching_image_before_example(monkeypatch):
    matching = InlineImage("image/png", b"matching")
    packet = _build(monkeypatch, matches=(matching,))
    assert [image.data for image in packet.images[-2:]] == [b"matching", b"example"]
    assert packet.manifest.splitlines()[-2].startswith("5. MATCHING")


def test_packet_fails_on_current_seller_byte_drift(monkeypatch):
    source = _png()
    contract = _contract(source)
    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "bind_confirmed_gpt_directing",
        lambda *_args, **_kwargs: _directing(),
    )
    with pytest.raises(
        confirmed_gpt_runtime.ConfirmedGptRuntimeError,
        match="seller_source_binding_drift",
    ):
        confirmed_gpt_runtime.build_packet(
            _spec(),
            clothing_type="top",
            identity_source="VIRTUAL",
            selected_model_id="mE",
            effective_model_id="mE",
            uses_base_color=True,
            mannequin_image=InlineImage("image/png", b"mannequin"),
            face_direction_sheet=InlineImage("image/png", b"face"),
            full_body_direction_sheet=InlineImage("image/png", b"body"),
            seller_images=(("Front", InlineImage("image/png", _png("red"))),),
            matching_images=(),
            example_image=InlineImage("image/png", b"example"),
            evidence_contract=contract,
        )


def test_packet_forbids_silent_virtual_model_substitution(monkeypatch):
    with pytest.raises(
        confirmed_gpt_runtime.ConfirmedGptRuntimeError,
        match="forbids_model_substitution",
    ):
        _build(monkeypatch, selected="unknown", effective="mB")


def test_uncurated_eligible_example_requests_fail_closed_profile():
    assert confirmed_gpt_runtime.profile_requested(_spec()) is True


@pytest.mark.parametrize(
    "overrides,error",
    [
        # 실제 모델에는 이 컷이 오면 안 된다 — 콘티보드 scope 가 막고, 여기서도 fail-closed.
        ({"identity_source": "REAL"}, "requires_virtual_model"),
        (
            {"selected_model_id": None, "effective_model_id": None},
            "forbids_model_substitution",
        ),
        (
            {"selected_model_id": "unknown", "effective_model_id": "mB"},
            "forbids_model_substitution",
        ),
        ({"uses_base_color": False}, "requires_base_color"),
    ],
)
def test_structurally_exact_cut_fails_closed_when_prerequisite_is_missing(
    overrides, error,
):
    kwargs = {
        "identity_source": "VIRTUAL",
        "selected_model_id": "mE",
        "effective_model_id": "mE",
        "uses_base_color": True,
        **overrides,
    }

    with pytest.raises(confirmed_gpt_runtime.ConfirmedGptRuntimeError, match=error):
        confirmed_gpt_runtime.resolve_profile_request(_spec(), **kwargs)


def test_explicitly_excluded_example_uses_the_generic_route(monkeypatch):
    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "confirmed_gpt_explicitly_excluded",
        lambda example_id: example_id == "ex",
    )

    assert confirmed_gpt_runtime.profile_requested(_spec()) is False


def test_explicitly_excluded_profile_stays_generic_without_exact_prerequisites(
    monkeypatch,
):
    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "confirmed_gpt_explicitly_excluded",
        lambda _example_id: True,
    )

    assert confirmed_gpt_runtime.resolve_profile_request(
        _spec(),
        identity_source="NONE",
        selected_model_id=None,
        effective_model_id=None,
        uses_base_color=False,
    ) is False


def test_signature_example_does_not_enter_the_confirmed_detail_profile():
    spec = _spec()
    spec["exampleId"] = "sig_men_01"

    assert confirmed_gpt_runtime.profile_requested(spec) is False


@pytest.mark.parametrize(
    "example_id",
    (
        "ex_styling_men_outer_full_06",
        "ex_styling_women_dress_full_home_01",
        "ex_styling_women_outer_full_alley_01",
        "ex_styling_women_top_full_mia_cafe_snapshot_01",
        "ex_styling_women_top_full_snapshot_04",
    ),
)
def test_curated_head_cropped_full_examples_enter_exact_profile(example_id):
    spec = _spec()
    spec.update(exampleId=example_id, shot="full")

    assert confirmed_gpt_runtime.profile_requested(spec) is True


def test_invalid_eligibility_catalog_fails_the_exact_route_closed(monkeypatch):
    def broken(_example_id):
        raise confirmed_gpt_runtime.ConfirmedGptDirectingError("catalog drift")

    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "confirmed_gpt_explicitly_excluded",
        broken,
    )

    with pytest.raises(
        confirmed_gpt_runtime.ConfirmedGptRuntimeError,
        match="catalog drift",
    ):
        confirmed_gpt_runtime.profile_requested(_spec())
