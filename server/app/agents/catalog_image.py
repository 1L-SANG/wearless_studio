"""AI-only catalog background; stored assets and provenance stay untouched."""
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from .gemini_image import InlineImage

CATALOG_BACKGROUND = (232, 232, 230)


def catalog_image(mime: str, data: bytes) -> InlineImage:
    """Flatten alpha without cropping/resizing; preserve non-alpha bytes exactly."""
    original = InlineImage(mime, data)
    try:
        source = Image.open(BytesIO(data))
    except UnidentifiedImageError:
        # Preserve the existing downstream validation of unsupported image bytes.
        return original
    with source:
        if "A" not in source.getbands() and "transparency" not in source.info:
            return original
        rgba = source.convert("RGBA")
        background = Image.new("RGB", source.size, CATALOG_BACKGROUND)
        background.paste(rgba, mask=rgba.getchannel("A"))
        output = BytesIO()
        background.save(output, format="PNG")
    return InlineImage("image/png", output.getvalue())
