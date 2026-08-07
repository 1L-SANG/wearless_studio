"""Deterministic 2x2 contact sheet for one garment's 1-4 source views."""

from io import BytesIO

from PIL import Image, ImageOps

CANVAS_SIZE = 1600
MARGIN = 40
GUTTER = 24
CELL_SIZE = 748
BACKGROUND = (245, 245, 245)
JPEG_QUALITY = 92


def compose_garment_grid(images: list[bytes]) -> bytes:
    """Place 1-4 fully decoded images in input order without cropping or distortion."""
    if not 1 <= len(images) <= 4:
        raise ValueError("garment_grid_requires_1_to_4_images")

    canvas = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), BACKGROUND)
    positions = (
        (MARGIN, MARGIN),
        (MARGIN + CELL_SIZE + GUTTER, MARGIN),
        (MARGIN, MARGIN + CELL_SIZE + GUTTER),
        (MARGIN + CELL_SIZE + GUTTER, MARGIN + CELL_SIZE + GUTTER),
    )
    for raw, (cell_x, cell_y) in zip(images, positions, strict=False):
        with Image.open(BytesIO(raw)) as opened:
            source = ImageOps.exif_transpose(opened)
            source.load()
            rgb = source.convert("RGB")
        contained = ImageOps.contain(
            rgb,
            (CELL_SIZE, CELL_SIZE),
            method=Image.Resampling.LANCZOS,
        )
        x = cell_x + (CELL_SIZE - contained.width) // 2
        y = cell_y + (CELL_SIZE - contained.height) // 2
        canvas.paste(contained, (x, y))

    output = BytesIO()
    canvas.save(output, format="JPEG", quality=JPEG_QUALITY)
    return output.getvalue()
