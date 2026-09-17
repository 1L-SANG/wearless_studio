from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image


def _texture(size=160):
    y, x = np.indices((size, size))
    arr = np.stack(
        [
            (x * 7 + y * 3) % 251,
            (x * 5 + y * 11) % 253,
            (x * 13 + y * 2) % 247,
        ],
        axis=2,
    ).astype(np.uint8)
    return Image.fromarray(arr)


def _crop(size=160, *, skin=False):
    skin_mask = np.zeros((size, size), bool)
    if skin:
        skin_mask[28:95, 56:104] = True
    return SimpleNamespace(current=_texture(size), face_box=(55.0, 20.0, 50.0, 70.0), skin=skin_mask)


def test_polygon_region_scales_1024_coordinates_and_rejects_bad_geometry():
    from app.agents import face_seam_geometry as geom
    from app.agents.face_seam_repair import SeamRepairUnavailable

    crop = _crop(160)
    region = geom.polygon_region(crop, [[(448, 640), (576, 640), (576, 768), (448, 768)]])

    assert region.shape == (160, 160)
    assert region.dtype == bool
    assert region[110, 80]
    assert not region[20, 80]

    with pytest.raises(SeamRepairUnavailable, match="invalid_polygon"):
        geom.polygon_region(crop, [[(10, 10), (20, 20)]])
    with pytest.raises(SeamRepairUnavailable, match="outside_polygon"):
        geom.polygon_region(crop, [[(-1, 600), (400, 600), (400, 700)]])
    with pytest.raises(SeamRepairUnavailable, match="self_intersecting_polygon"):
        geom.polygon_region(crop, [[(400, 600), (600, 800), (400, 800), (600, 600)]])


def test_polygon_region_rejects_face_and_crop_edge_contact():
    from app.agents import face_seam_geometry as geom
    from app.agents.face_seam_repair import SeamRepairUnavailable

    crop = _crop(160, skin=True)
    with pytest.raises(SeamRepairUnavailable, match="protected_face"):
        geom.polygon_region(crop, [[(420, 250), (600, 250), (600, 430), (420, 430)]])
    with pytest.raises(SeamRepairUnavailable, match="protected_face"):
        geom.polygon_region(crop, [[(390, 530), (640, 530), (640, 600), (390, 600)]])
    with pytest.raises(SeamRepairUnavailable, match="crop_edge"):
        geom.polygon_region(crop, [[(0, 700), (160, 700), (160, 850), (0, 850)]])


def test_api_mask_is_1024_rgba_with_transparent_polygon_interior():
    from app.agents import face_seam_geometry as geom

    crop = _crop(160)
    mask = geom.api_mask(crop, [[(448, 640), (576, 640), (576, 768), (448, 768)]])
    arr = np.asarray(mask)

    assert mask.mode == "RGBA"
    assert mask.size == (1024, 1024)
    assert arr[704, 512, 3] == 0
    assert arr[100, 512, 3] == 255


def test_composition_alpha_feathers_inside_support_and_keeps_outside_zero():
    from app.agents import face_seam_geometry as geom

    crop = _crop(160)
    damage = [[(460, 660), (560, 660), (560, 760), (460, 760)]]
    composition = [[(420, 620), (610, 620), (610, 810), (420, 810)]]

    alpha, support = geom.composition_alpha(crop, damage, composition)

    assert alpha.shape == support.shape == (160, 160)
    assert alpha.dtype == np.float32
    assert support.dtype == bool
    assert support[112, 80]
    assert alpha[112, 80] > 0.6
    assert alpha[20, 80] == 0
    assert not support[20, 80]


def test_align_generated_recovers_shifted_and_rotated_texture_outside_damage():
    from app.agents import face_seam_geometry as geom

    crop = _crop(180)
    current = np.asarray(crop.current)
    M = np.array([[1.0, 0.025, 4.0], [-0.025, 1.0, -3.0]], np.float32)
    cv2 = __import__("cv2")
    generated = Image.fromarray(
        cv2.warpAffine(
            current,
            M,
            (180, 180),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
    )
    region = np.zeros((180, 180), bool)
    region[82:130, 70:112] = True

    aligned, meta = geom.align_generated(crop, generated, region)
    before = np.abs(np.asarray(generated, np.int16)[~region] - current.astype(np.int16)[~region]).mean()
    after = np.abs(np.asarray(aligned, np.int16)[~region] - current.astype(np.int16)[~region]).mean()

    assert meta["applied"] is True
    assert after < before * 0.65


def test_align_generated_bypasses_unmoved_and_fails_uncertain_discernible_motion():
    from app.agents import face_seam_geometry as geom
    from app.agents.face_seam_repair import SeamRepairUnavailable

    crop = _crop(150)
    region = np.zeros((150, 150), bool)
    region[70:100, 65:95] = True
    same, meta = geom.align_generated(crop, crop.current.copy(), region)
    assert np.array_equal(np.asarray(same), np.asarray(crop.current))
    assert meta["applied"] is False

    scrambled = np.asarray(crop.current).copy()
    rng = np.random.default_rng(4)
    outside = np.argwhere(~region)
    shuffled = outside.copy()
    rng.shuffle(shuffled)
    values = scrambled[outside[:, 0], outside[:, 1]].copy()
    scrambled[shuffled[:, 0], shuffled[:, 1]] = values
    with pytest.raises(SeamRepairUnavailable, match="alignment_uncertain"):
        geom.align_generated(crop, Image.fromarray(scrambled), region)


def test_align_generated_rejects_large_rotation_as_unsafe_transform():
    from app.agents import face_seam_geometry as geom
    from app.agents.face_seam_repair import SeamRepairUnavailable

    crop = _crop(180)
    current = np.asarray(crop.current)
    cv2 = __import__("cv2")
    M = cv2.getRotationMatrix2D((89.5, 89.5), 18.0, 1.0)
    generated = Image.fromarray(
        cv2.warpAffine(
            current,
            M,
            (180, 180),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
    )
    region = np.zeros((180, 180), bool)
    region[82:130, 70:112] = True

    with pytest.raises(SeamRepairUnavailable, match="alignment_uncertain"):
        geom.align_generated(crop, generated, region)


def test_align_generated_rejects_localized_anchor_evidence():
    from app.agents import face_seam_geometry as geom
    from app.agents.face_seam_repair import SeamRepairUnavailable

    size = 180
    rng = np.random.default_rng(12)
    current = np.full((size, size, 3), 128, np.uint8)
    patch = rng.integers(0, 255, (48, 48, 3), dtype=np.uint8)
    current[118:166, 12:60] = patch
    generated = np.full_like(current, 128)
    generated[122:170, 17:65] = patch
    crop = SimpleNamespace(
        current=Image.fromarray(current),
        face_box=(55.0, 20.0, 50.0, 70.0),
        skin=np.zeros((size, size), bool),
    )
    region = np.zeros((size, size), bool)
    region[82:130, 70:112] = True

    with pytest.raises(SeamRepairUnavailable, match="alignment_uncertain"):
        geom.align_generated(crop, Image.fromarray(generated), region)
