from io import BytesIO

import pytest
from PIL import Image

from app.services import seller_evidence_grid as grid


def _image(color: tuple[int, int, int], size: tuple[int, int] = (1200, 1600)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
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


def _panels_for_sizes(sizes: list[tuple[int, int]]) -> list[dict]:
    slots = (("FRONT", "DOMINANT"), ("FRONT_DETAIL", "DOMINANT"),
             ("BACK", "CONTEXT"), ("BACK_DETAIL", "CONTEXT"))
    return [
        _panel(slot, authority, _image((40, 60, 120), size))
        for (slot, authority), size in zip(slots, sizes)
    ]


@pytest.mark.parametrize("size", [
    (1200, 1600), (1200, 1200), (1200, 1800),
    (1080, 1920), (1600, 1200), (1920, 1080),
])
@pytest.mark.parametrize("count", [1, 4])
def test_grid_displays_common_ratios_at_readable_size(size, count):
    sizes = [size] if count == 1 else [(1200, 1600), size, (1200, 1600), (1200, 1600)]
    data, records, preflight, _ = grid.compose_labelled_grid(
        _panels_for_sizes(sizes), direction="front", evidence_id="fixture_front"
    )
    assert preflight["passed"] is True
    assert preflight["observed"]["providedPanels"] == count
    for record, source_size in zip(records, sizes):
        assert record["sourceDimensions"] == dict(zip(("width", "height"), source_size))
        assert min(record["displayedDimensions"].values()) >= grid.MIN_PANEL_SHORT_EDGE
    with Image.open(BytesIO(data)) as rendered:
        assert max(rendered.size) <= grid.MAX_GRID_LONG_EDGE
        assert preflight["observed"]["gridDimensions"] == {
            "width": rendered.width, "height": rendered.height,
        }


@pytest.mark.parametrize("size, displayed", [
    ((1200, 1600), (651, 868)),
    ((1200, 1200), (852, 852)),
    ((1332, 1000), (852, 640)),
    ((737, 1000), (640, 868)),
])
@pytest.mark.parametrize("count, dimensions", [
    (1, (948, 1298)), (2, (1872, 1298)),
    (3, (1872, 2422)), (4, (1872, 2422)),
])
def test_grid_keeps_legacy_dimensions_for_passing_ratios(size, displayed, count, dimensions):
    data, records, preflight, _ = grid.compose_labelled_grid(
        _panels_for_sizes([size] * count), direction="front", evidence_id="fixture_front"
    )
    assert preflight["passed"] is True
    with Image.open(BytesIO(data)) as rendered:
        assert rendered.size == dimensions
    for record in records:
        assert record["displayedDimensions"] == dict(zip(("width", "height"), displayed))


def test_grid_sizes_panels_after_exif_orientation():
    source = BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (1800, 1200), "navy").save(source, format="JPEG", exif=exif)
    data, records, preflight, _ = grid.compose_labelled_grid(
        [_panel("FRONT", "DOMINANT", source.getvalue())],
        direction="front", evidence_id="fixture_front",
    )
    assert records[0]["sourceDimensions"] == {"width": 1200, "height": 1800}
    assert records[0]["displayedDimensions"] == {"width": 640, "height": 960}
    assert preflight["passed"] is True
    with Image.open(BytesIO(data)) as rendered:
        assert rendered.size == (948, 1390)


def test_grid_enlarges_both_axes_with_uncertain_and_missing_panels():
    panels = _panels_for_sizes([(1200, 1600), (1080, 1920), (1920, 1080)])
    panels[1]["judgeability"] = "uncertain"
    panels.append(_panel("BACK_DETAIL", "CONTEXT", None, "missing"))
    data, records, preflight, prompt_map = grid.compose_labelled_grid(
        panels, direction="front", evidence_id="fixture_front"
    )
    assert preflight["passed"] is True
    assert preflight["observed"]["providedPanels"] == 3
    assert preflight["observed"]["minDisplayedPanelShortEdgePx"] >= grid.MIN_PANEL_SHORT_EDGE
    assert records[-1]["sourceDimensions"] is None
    assert records[-1]["displayedDimensions"] is None
    assert "judgeability: UNCERTAIN" in prompt_map
    assert "NOT PROVIDED" in prompt_map
    with Image.open(BytesIO(data)) as rendered:
        assert 1872 < rendered.width <= grid.MAX_GRID_LONG_EDGE
        assert 2422 < rendered.height <= grid.MAX_GRID_LONG_EDGE


def test_grid_accepts_one_panel_at_one_to_three_ratio():
    data, records, preflight, _ = grid.compose_labelled_grid(
        _panels_for_sizes([(1000, 3000)]), direction="front", evidence_id="fixture_front"
    )
    assert preflight["passed"] is True
    assert min(records[0]["displayedDimensions"].values()) >= grid.MIN_PANEL_SHORT_EDGE
    with Image.open(BytesIO(data)) as rendered:
        assert max(rendered.size) <= grid.MAX_GRID_LONG_EDGE


@pytest.mark.parametrize("sizes", [
    [(1200, 1600), (1000, 3000), (1200, 1600), (1200, 1600)],
    [(7000, 1000)],
])
def test_grid_rejects_extreme_ratios_before_allocating_canvas(monkeypatch, sizes):
    panels = _panels_for_sizes(sizes)

    def unexpected_canvas(*args, **kwargs):
        raise AssertionError("oversized grid must be rejected before creating its canvas")

    monkeypatch.setattr(grid.Image, "new", unexpected_canvas)
    with pytest.raises(ValueError, match="^seller_evidence_display_resolution_too_small$"):
        grid.compose_labelled_grid(panels, direction="front", evidence_id="fixture_front")


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
