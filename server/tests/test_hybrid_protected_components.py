from app.services.hybrid_composite.protected_components import (
    ProtectedComponentStatus,
    derive_required_protected_components,
    evaluate_protected_components,
)


def _box():
    return [[0.4, 0.1], [0.6, 0.1], [0.6, 0.2], [0.4, 0.2]]


def _inv(**overrides):
    row = {
        "collar": False,
        "placket": False,
        "cuffs": False,
        "visible_buttons": 0,
        "component_boxes": {},
    }
    row.update(overrides)
    return row


def test_approved_collar_and_button_truth_requires_source_and_carrier_geometry():
    truth = {
        "status": "approved",
        "garmentSpec": {"collarType": "spread", "buttonCount": 6},
        "protectedDetails": {"buttonCount": True},
    }
    source = _inv(collar=True, placket=True, visible_buttons=6,
                  component_boxes={"collar_box": _box(), "placket_box": _box()})
    carrier = _inv(collar=True, placket=True, visible_buttons=6,
                   component_boxes={"collar_box": _box()})

    result = evaluate_protected_components(
        truth, source_inventory=source, carrier_inventory=carrier)

    assert result.status is ProtectedComponentStatus.MISSING
    assert result.required_components == ("collar", "placket")
    assert result.available_components == ("collar",)
    assert result.missing[0].component == "placket"
    assert result.missing[0].reason == "carrier_geometry_missing"


def test_approved_collarless_truth_does_not_require_collar_geometry():
    truth = {
        "status": "approved",
        "garmentSpec": {"collarType": "none", "buttonCount": 0, "structureFlags": []},
        "protectedDetails": {},
    }
    result = evaluate_protected_components(
        truth,
        source_inventory=_inv(component_boxes={}),
        carrier_inventory=_inv(component_boxes={}),
    )

    assert result.status is ProtectedComponentStatus.PASS
    assert result.required_components == ()
    assert result.missing == ()


def test_single_side_component_box_alone_is_not_existence_truth():
    truth = {
        "status": "approved",
        "garmentSpec": {"collarType": None, "buttonCount": None, "structureFlags": []},
        "protectedDetails": {},
    }
    source = _inv(component_boxes={"collar_box": _box()})
    carrier = _inv(component_boxes={})

    result = evaluate_protected_components(
        truth, source_inventory=source, carrier_inventory=carrier)

    assert result.status is ProtectedComponentStatus.REVIEW
    assert result.required_components == ()
    assert result.review_reasons == ("insufficient_truth:collar", "insufficient_truth:placket")
    assert result.missing == ()


def test_unknown_critical_truth_reviews_instead_of_auto_pass():
    truth = {
        "status": "approved",
        "garmentSpec": {"collarType": None, "buttonCount": None},
        "protectedDetails": {},
    }

    result = evaluate_protected_components(
        truth,
        source_inventory=_inv(component_boxes={}),
        carrier_inventory=_inv(component_boxes={}),
    )

    assert result.status is ProtectedComponentStatus.REVIEW
    assert "insufficient_truth:collar" in result.review_reasons
    assert "insufficient_truth:placket" in result.review_reasons


def test_positive_validated_inventory_can_supply_secondary_requirement():
    truth = {
        "status": "approved",
        "garmentSpec": {"collarType": "none", "buttonCount": 0},
        "protectedDetails": {},
    }
    source = _inv(cuffs=True, component_boxes={"cuff_box": _box()})
    carrier = _inv(cuffs=True, component_boxes={"cuff_box": _box()})

    assert derive_required_protected_components(
        truth, source_inventory=source, carrier_inventory=carrier) == ("cuffs",)
    result = evaluate_protected_components(
        truth, source_inventory=source, carrier_inventory=carrier)

    assert result.status is ProtectedComponentStatus.PASS
    assert result.available_components == ("cuffs",)
