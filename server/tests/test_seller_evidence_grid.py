from io import BytesIO

import pytest
from PIL import Image

from app.services import seller_evidence_grid as grid


def _image(color: tuple[int, int, int]) -> bytes:
    out = BytesIO()
    Image.new("RGB", (1200, 1600), color).save(out, format="PNG")
    return out.getvalue()


def _panel(slot: str, authority: str, data: bytes | None, status: str = "usable") -> dict:
    return {
        "slot": slot,
        "detail": f"verified {slot.lower()} evidence",
        "surfaceAuthority": authority,
        "judgeability": status,
        "judgeabilityReasons": ["fold_distortion", "mixed_light"],
        "data": data,
    }


def test_labelled_grid_is_deterministic_and_matches_prompt_map():
    panels = [
        _panel("FRONT", "DOMINANT", _image((200, 20, 20))),
        _panel("FRONT_DETAIL", "DOMINANT", _image((20, 200, 20))),
        _panel("BACK", "CONTEXT", _image((20, 20, 200))),
    ]
    first = grid.compose_labelled_grid(
        panels, direction="front", evidence_id="women_dress_white_full_front"
    )
    second = grid.compose_labelled_grid(
        panels, direction="front", evidence_id="women_dress_white_full_front"
    )
    assert first[0] == second[0]
    assert first[1] == second[1]
    assert first[2]["passed"] is True
    assert first[2]["observed"]["providedPanels"] == 3
    assert "Panel 1: FRONT" in first[3]
    assert "surface authority: DOMINANT" in first[3]
    with Image.open(BytesIO(first[0])) as rendered:
        assert rendered.format == "PNG"
        assert max(rendered.size) <= grid.MAX_GRID_LONG_EDGE


def test_grid_rejects_inferred_or_drifted_panel_contracts():
    good = _panel("FRONT", "DOMINANT", _image((0, 0, 0)))
    with pytest.raises(ValueError, match="field_set"):
        grid.compose_labelled_grid(
            [{**good, "invented": True}], direction="front", evidence_id="fixture_front"
        )
    with pytest.raises(ValueError, match="surface_authority_drift"):
        grid.compose_labelled_grid(
            [{**good, "surfaceAuthority": "CONTEXT"}],
            direction="front",
            evidence_id="fixture_front",
        )
    with pytest.raises(ValueError, match="missing_seller_panel_has_bytes"):
        grid.compose_labelled_grid(
            [{**good, "judgeability": "missing"}],
            direction="front",
            evidence_id="fixture_front",
        )


def test_grid_requires_real_judgeability_reasons_and_decodable_pixels():
    panel = _panel("FRONT", "DOMINANT", _image((0, 0, 0)))
    with pytest.raises(ValueError, match="judgeability_reasons"):
        grid.compose_labelled_grid(
            [{**panel, "judgeabilityReasons": []}],
            direction="front",
            evidence_id="fixture_front",
        )
    with pytest.raises(ValueError, match="decode_failed"):
        grid.compose_labelled_grid(
            [{**panel, "data": b"not-an-image"}],
            direction="front",
            evidence_id="fixture_front",
        )


def test_grid_accepts_clear_enough_without_inventing_a_defect_reason():
    panel = _panel("FRONT", "DOMINANT", _image((0, 0, 0)))
    rendered = grid.compose_labelled_grid(
        [{**panel, "judgeabilityReasons": ["clear_enough"]}],
        direction="front",
        evidence_id="fixture_front",
    )
    assert rendered[1][0]["judgeabilityReasons"] == ["clear_enough"]


def test_grid_requires_explicit_phase1_style_evidence_id():
    panel = _panel("FRONT", "DOMINANT", _image((0, 0, 0)))
    for value in ("", "Scenario 1", "fixture/front", "UPPER_CASE"):
        with pytest.raises(ValueError, match="grid_id_required"):
            grid.compose_labelled_grid(
                [panel], direction="front", evidence_id=value
            )
