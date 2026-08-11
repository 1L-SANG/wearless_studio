import hashlib
from io import BytesIO

import pytest
from PIL import Image

from app.services import garment_grid


def _image(size, color, *, bottom_color=None):
    image = Image.new("RGB", size, color)
    if bottom_color is not None:
        for y in range(size[1] // 2, size[1]):
            for x in range(size[0]):
                image.putpixel((x, y), bottom_color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_garment_grid_is_1600_jpeg_and_keeps_empty_cells(count):
    colors = [(220, 20, 20), (20, 180, 20), (20, 20, 220), (220, 180, 20)]
    raw = garment_grid.compose_garment_grid([
        _image((500, 500), colors[index]) for index in range(count)
    ])

    with Image.open(BytesIO(raw)) as result:
        assert result.format == "JPEG"
        assert result.mode == "RGB"
        assert result.size == (1600, 1600)
        centers = [(414, 414), (1186, 414), (414, 1186), (1186, 1186)]
        for index, center in enumerate(centers):
            actual = result.getpixel(center)
            expected = colors[index] if index < count else garment_grid.BACKGROUND
            assert all(abs(actual[channel] - expected[channel]) < 12 for channel in range(3))


def test_garment_grid_contains_full_portrait_without_crop():
    raw = garment_grid.compose_garment_grid([
        _image((400, 800), (230, 20, 20), bottom_color=(20, 20, 230))
    ])

    with Image.open(BytesIO(raw)) as result:
        assert result.getpixel((414, 45))[0] > 180
        assert result.getpixel((414, 782))[2] > 180
        assert result.getpixel((45, 414))[0] > 230
        assert result.getpixel((45, 414))[1] > 230


def test_garment_grid_output_and_checksum_are_order_deterministic():
    red = _image((420, 520), (230, 20, 20))
    blue = _image((420, 520), (20, 20, 230))
    first = garment_grid.compose_garment_grid([red, blue])
    repeat = garment_grid.compose_garment_grid([red, blue])
    reversed_order = garment_grid.compose_garment_grid([blue, red])

    assert first == repeat
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(repeat).hexdigest()
    assert hashlib.sha256(first).hexdigest() != hashlib.sha256(reversed_order).hexdigest()


@pytest.mark.parametrize("count", [0, 5])
def test_garment_grid_rejects_out_of_contract_counts(count):
    with pytest.raises(ValueError, match="garment_grid_requires_1_to_4_images"):
        garment_grid.compose_garment_grid([_image((400, 400), (0, 0, 0))] * count)

