"""Deterministic labelled seller-evidence grid for the confirmed GPT cut profile.

This module deliberately does not infer judgeability or product facts.  Its caller must
provide the already-validated panel contract; silently inventing those values would make
the production request differ from the owner-reviewed Phase-1 input contract.
"""

from __future__ import annotations

import math
import re
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


PANEL_STATUSES = frozenset({"usable", "uncertain", "missing"})
SURFACE_AUTHORITIES = frozenset({"DOMINANT", "CONTEXT"})
JUDGEABILITY_REASONS = frozenset({
    "clear_enough",
    "blur",
    "occlusion",
    "hanger_distortion",
    "fold_distortion",
    "mixed_light",
    "background_interference",
    "partial_crop",
})
ALLOWED_SLOTS = frozenset({"FRONT", "FRONT_DETAIL", "BACK", "BACK_DETAIL", "SHARED_DETAIL"})

MIN_PANEL_SHORT_EDGE = 640
MIN_LABEL_FONT_PX = 28
MAX_GRID_LONG_EDGE = 4096
_EVIDENCE_ID_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def expected_surface_authority(slot: str, direction: str) -> str:
    if direction not in {"front", "back"}:
        raise ValueError("unsupported_evidence_direction")
    if slot == "SHARED_DETAIL":
        return "DOMINANT"
    target = "FRONT" if direction == "front" else "BACK"
    return "DOMINANT" if slot.startswith(target) else "CONTEXT"


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _ellipsize(
    draw: ImageDraw.ImageDraw,
    value: str,
    width: int,
    selected_font: ImageFont.FreeTypeFont,
) -> str:
    if draw.textbbox((0, 0), value, font=selected_font)[2] <= width:
        return value
    candidate = value
    while candidate and draw.textbbox(
        (0, 0), candidate + "...", font=selected_font
    )[2] > width:
        candidate = candidate[:-1]
    return candidate.rstrip() + "..."


def validate_panels(panels: list[dict[str, Any]], *, direction: str) -> None:
    if not 1 <= len(panels) <= 4:
        raise ValueError("seller_evidence_grid_requires_1_to_4_panels")
    for panel in panels:
        if set(panel) != {
            "slot",
            "detail",
            "surfaceAuthority",
            "judgeability",
            "judgeabilityReasons",
            "data",
        }:
            raise ValueError("seller_evidence_panel_field_set_mismatch")
        slot = panel["slot"]
        if slot not in ALLOWED_SLOTS:
            raise ValueError("invalid_seller_evidence_slot")
        expected = expected_surface_authority(slot, direction)
        if panel["surfaceAuthority"] not in SURFACE_AUTHORITIES or panel["surfaceAuthority"] != expected:
            raise ValueError("seller_evidence_surface_authority_drift")
        status = panel["judgeability"]
        if status not in PANEL_STATUSES:
            raise ValueError("invalid_seller_evidence_judgeability")
        reasons = panel["judgeabilityReasons"]
        if (
            not isinstance(reasons, list)
            or not reasons
            or any(reason not in JUDGEABILITY_REASONS for reason in reasons)
        ):
            raise ValueError("invalid_seller_evidence_judgeability_reasons")
        data = panel["data"]
        if status == "missing":
            if data is not None:
                raise ValueError("missing_seller_panel_has_bytes")
        elif not isinstance(data, bytes) or not data:
            raise ValueError("provided_seller_panel_has_no_bytes")
        if not isinstance(panel["detail"], str) or not panel["detail"].strip():
            raise ValueError("seller_evidence_detail_required")


def panel_map(records: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for record in records:
        supplied = "seller pixels supplied" if record["provided"] else "NOT PROVIDED — do not infer"
        lines.append(
            f"- Panel {record['panel']}: {record['slot'].replace('_', ' ')} "
            f"({record['detail']}) — surface authority: {record['surfaceAuthority']} — "
            f"judgeability: {record['judgeability'].upper()} — limits: "
            f"{', '.join(record['judgeabilityReasons'])} — {supplied}"
        )
    return "\n".join(lines)


def _minimum_box_long_edge(long_edge: int, short_edge: int) -> int:
    # Pillow contain rounds the fitted short side. A value within half a
    # pixel of the minimum can already satisfy it, including at the grid cap.
    scale = short_edge / long_edge
    needed = math.ceil((MIN_PANEL_SHORT_EDGE - 0.5) * long_edge / short_edge)
    return needed if round(scale * needed) >= MIN_PANEL_SHORT_EDGE else needed + 1


def compose_labelled_grid(
    panels: list[dict[str, Any]],
    *,
    direction: str,
    evidence_id: str,
) -> tuple[bytes, list[dict[str, Any]], dict[str, Any], str]:
    """Render one deterministic PNG and return records, preflight, and prompt map."""

    validate_panels(panels, direction=direction)
    if not isinstance(evidence_id, str) or not _EVIDENCE_ID_RE.fullmatch(evidence_id):
        raise ValueError("seller_evidence_grid_id_required")
    gap = 24
    base_cell_w, base_cell_h = 900, 1100
    header_h, label_h = 150, 190
    horizontal_padding, vertical_padding = 48, label_h + 42
    box_w, box_h = base_cell_w - horizontal_padding, base_cell_h - vertical_padding
    columns = 1 if len(panels) == 1 else 2
    rows = math.ceil(len(panels) / columns)
    column_widths = [base_cell_w] * columns
    row_heights = [base_cell_h] * rows
    images: dict[int, Image.Image] = {}
    for index, panel in enumerate(panels):
        if panel["judgeability"] == "missing":
            continue
        try:
            with Image.open(BytesIO(panel["data"])) as opened:
                oriented = ImageOps.exif_transpose(opened).convert("RGB")
                oriented.load()
        except Exception as exc:
            raise ValueError("seller_evidence_image_decode_failed") from exc
        images[index] = oriented
        row, column = divmod(index, columns)
        # Match Pillow's contain rounding so layouts that already pass keep their pixels.
        # A narrow photo only needs more height in its row, and a wide one more
        # width in its column. Keep their labels and reading order intact.
        if round(oriented.width / oriented.height * box_h) < MIN_PANEL_SHORT_EDGE:
            height_needed = _minimum_box_long_edge(oriented.height, oriented.width)
            row_heights[row] = max(row_heights[row], height_needed + vertical_padding)
        elif round(oriented.height / oriented.width * box_w) < MIN_PANEL_SHORT_EDGE:
            width_needed = _minimum_box_long_edge(oriented.width, oriented.height)
            column_widths[column] = max(column_widths[column], width_needed + horizontal_padding)

    width = sum(column_widths) + (columns + 1) * gap
    height = header_h + sum(row_heights) + (rows + 1) * gap
    if max(width, height) > MAX_GRID_LONG_EDGE:
        raise ValueError("seller_evidence_display_resolution_too_small")
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    title_font = _font(34)
    label_font = _font(30)
    meta_font = _font(MIN_LABEL_FONT_PX)
    # Keep the exact Phase-1 pixel header. A generic replacement changes provider
    # input bytes and therefore is not a faithful replay of the confirmed profile.
    draw.text(
        (gap, 18),
        f"SELLER EVIDENCE GRID | {evidence_id}",
        fill="black",
        font=title_font,
    )
    draw.text(
        (gap, 68),
        "Pixel evidence is final truth | judgeability limits certainty | no missing panel is invented",
        fill="#333333",
        font=meta_font,
    )

    records: list[dict[str, Any]] = []
    for index, panel in enumerate(panels):
        row, column = divmod(index, columns)
        cell_w, cell_h = column_widths[column], row_heights[row]
        row_count = min(columns, len(panels) - row * columns)
        row_width = sum(column_widths[:row_count]) + (row_count - 1) * gap
        row_offset = (width - row_width) // 2
        x = row_offset + sum(column_widths[:column]) + column * gap
        y = header_h + sum(row_heights[:row]) + row * gap
        authority = panel["surfaceAuthority"]
        status = panel["judgeability"]
        authority_color = "#a52323" if authority == "DOMINANT" else "#555555"
        status_color = {"usable": "#176337", "uncertain": "#9a6513", "missing": "#7a2525"}[status]
        draw.rectangle((x, y, x + cell_w, y + cell_h), fill="white", outline=authority_color, width=9)
        draw.rectangle((x + 9, y + 9, x + cell_w - 9, y + label_h), fill="#f2f2f2")
        pixel_label = f"{panel['slot'].replace('_', ' ')} | {authority} | {status.upper()}"
        draw.text((x + 24, y + 18), pixel_label, fill=authority_color, font=label_font)
        detail = _ellipsize(draw, f"detail: {panel['detail']}", cell_w - 48, meta_font)
        reasons = _ellipsize(
            draw,
            "limits: " + ", ".join(panel["judgeabilityReasons"]),
            cell_w - 48,
            meta_font,
        )
        draw.text(
            (x + 24, y + 67),
            f"surface: {authority} | judgeability: {status.upper()}",
            fill=status_color,
            font=meta_font,
        )
        draw.text((x + 24, y + 105), detail, fill="#222222", font=meta_font)
        draw.text((x + 24, y + 143), reasons, fill="#444444", font=meta_font)

        record: dict[str, Any] = {
            "panel": index + 1,
            "slot": panel["slot"],
            "detail": panel["detail"],
            "surfaceAuthority": authority,
            "judgeability": status,
            "judgeabilityReasons": list(panel["judgeabilityReasons"]),
            "provided": panel["data"] is not None,
            "renderedPixelLabel": pixel_label,
            "sourceDimensions": None,
            "displayedDimensions": None,
        }
        image_box = (x + 24, y + label_h + 18, x + cell_w - 24, y + cell_h - 24)
        if status == "missing":
            draw.text((x + 210, y + 560), "NOT PROVIDED — DO NOT INFER", fill=status_color, font=label_font)
        else:
            oriented = images[index]
            fitted = ImageOps.contain(
                oriented,
                (image_box[2] - image_box[0], image_box[3] - image_box[1]),
                Image.Resampling.LANCZOS,
            )
            if min(fitted.size) < MIN_PANEL_SHORT_EDGE:
                raise ValueError("seller_evidence_display_resolution_too_small")
            px = image_box[0] + (image_box[2] - image_box[0] - fitted.width) // 2
            py = image_box[1] + (image_box[3] - image_box[1] - fitted.height) // 2
            canvas.paste(fitted, (px, py))
            record["sourceDimensions"] = {"width": oriented.width, "height": oriented.height}
            record["displayedDimensions"] = {"width": fitted.width, "height": fitted.height}
        records.append(record)

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=False, compress_level=9)
    provided = [
        min(record["displayedDimensions"].values())
        for record in records
        if record["displayedDimensions"] is not None
    ]
    preflight = {
        "thresholds": {
            "minDisplayedPanelShortEdgePx": MIN_PANEL_SHORT_EDGE,
            "minLabelFontPx": MIN_LABEL_FONT_PX,
            "maxGridLongEdgePx": MAX_GRID_LONG_EDGE,
            "singleGridOnly": True,
        },
        "observed": {
            "gridDimensions": {"width": width, "height": height},
            "providedPanels": len(provided),
            "minDisplayedPanelShortEdgePx": min(provided),
            "labelFontPx": MIN_LABEL_FONT_PX,
            "renderedPanelLabels": [record["renderedPixelLabel"] for record in records],
        },
        "passed": bool(provided) and max(width, height) <= MAX_GRID_LONG_EDGE,
    }
    if not preflight["passed"]:
        raise ValueError("seller_evidence_grid_preflight_failed")
    return output.getvalue(), records, preflight, panel_map(records)
