from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def _err(reason: str):
    from .face_seam_repair import SeamRepairUnavailable

    return SeamRepairUnavailable(reason)


def _current(crop) -> Image.Image:
    image = getattr(crop, "current", None)
    if not isinstance(image, Image.Image):
        raise _err("invalid_crop")
    if image.width != image.height:
        raise _err("invalid_crop")
    return image.convert("RGB")


def _face_box(crop) -> tuple[float, float, float, float] | None:
    box = getattr(crop, "face_box", None)
    if not isinstance(box, (tuple, list)) or len(box) < 4:
        return None
    vals = tuple(float(v) for v in box[:4])
    if not all(np.isfinite(vals)):
        return None
    return vals


def _scale_point(point, side: int) -> tuple[float, float]:
    if not isinstance(point, (tuple, list)) or len(point) != 2:
        raise _err("invalid_polygon")
    x, y = point
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
    ):
        raise _err("invalid_polygon")
    x = float(x)
    y = float(y)
    if not np.isfinite(x) or not np.isfinite(y):
        raise _err("invalid_polygon")
    if x < 0 or y < 0 or x > 1024 or y > 1024:
        raise _err("outside_polygon")
    return x * side / 1024.0, y * side / 1024.0


def _orientation(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a, b, c, d) -> bool:
    def between(p, q, r):
        return (
            min(p[0], r[0]) <= q[0] <= max(p[0], r[0])
            and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])
        )

    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    eps = 1e-9
    if abs(o1) < eps and between(a, c, b):
        return True
    if abs(o2) < eps and between(a, d, b):
        return True
    if abs(o3) < eps and between(c, a, d):
        return True
    if abs(o4) < eps and between(c, b, d):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _validate_polygon(poly, side: int) -> list[tuple[float, float]]:
    if not isinstance(poly, (tuple, list)) or not (3 <= len(poly) <= 32):
        raise _err("invalid_polygon")
    points = [_scale_point(p, side) for p in poly]
    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        for j in range(i + 1, n):
            if abs(i - j) <= 1 or {i, j} == {0, n - 1}:
                continue
            c, d = points[j], points[(j + 1) % n]
            if _segments_intersect(a, b, c, d):
                raise _err("self_intersecting_polygon")
    area = 0.0
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    if abs(area) < 1.0:
        raise _err("degenerate_polygon")
    return points


def _validate_polygons(crop, polygons) -> tuple[Image.Image, list[list[tuple[float, float]]]]:
    image = _current(crop)
    if not isinstance(polygons, (tuple, list)) or not (1 <= len(polygons) <= 8):
        raise _err("invalid_polygon")
    return image, [_validate_polygon(poly, image.width) for poly in polygons]


def _raster(side: int, polygons: list[list[tuple[float, float]]]) -> np.ndarray:
    mask = np.zeros((side, side), np.uint8)
    for poly in polygons:
        pts = np.asarray([[(int(round(x)), int(round(y))) for x, y in poly]], np.int32)
        cv2.fillPoly(mask, pts, 255)
    return mask > 0


def _crop_edge_touched(mask: np.ndarray) -> bool:
    return bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())


def _protected_face_mask(crop, side: int) -> np.ndarray:
    protected = np.zeros((side, side), bool)
    box = _face_box(crop)
    if box is None:
        return protected
    fx, fy, fw, fh = box
    yy, xx = np.indices((side, side))
    protected |= yy < fy + 0.55 * fh
    skin = getattr(crop, "skin", None)
    if isinstance(skin, np.ndarray) and skin.shape == protected.shape:
        in_face_skin = (
            (xx >= fx - 0.05 * fw)
            & (xx <= fx + 1.05 * fw)
            & (yy <= fy + 0.95 * fh)
            & skin.astype(bool)
        )
        protected |= in_face_skin
    return protected


def polygon_region(crop, polygons) -> np.ndarray:
    image, scaled = _validate_polygons(crop, polygons)
    region = _raster(image.width, scaled)
    if not region.any():
        raise _err("degenerate_polygon")
    if _crop_edge_touched(region):
        raise _err("crop_edge")
    if (region & _protected_face_mask(crop, image.width)).any():
        raise _err("protected_face")
    return region


def api_mask(crop, polygons) -> Image.Image:
    image, scaled = _validate_polygons(crop, polygons)
    scale = 1024.0 / image.width
    api_polys = [[(x * scale, y * scale) for x, y in poly] for poly in scaled]
    polygon_region(crop, polygons)
    # Validate native safety first, then draw directly at API resolution to match edit input.
    api = _raster(1024, api_polys)
    alpha = np.where(api, 0, 255).astype(np.uint8)
    rgba = np.full((1024, 1024, 4), 255, np.uint8)
    rgba[..., 3] = alpha
    return Image.fromarray(rgba, "RGBA")


def composition_alpha(crop, damage_polygons, composition_polygons) -> tuple[np.ndarray, np.ndarray]:
    image = _current(crop)
    damage = polygon_region(crop, damage_polygons)
    composition = polygon_region(crop, composition_polygons)
    if (damage & ~composition).any():
        raise _err("damage_outside_composition")
    soft = cv2.GaussianBlur(
        composition.astype(np.uint8) * 255,
        (0, 0),
        7.0,
    )
    alpha = np.where(
        composition,
        np.clip((soft.astype(np.float32) - 128.0) / 110.0, 0.0, 1.0),
        0.0,
    ).astype(np.float32)
    if not (alpha > 0).any():
        raise _err("degenerate_alpha")
    return alpha, composition.astype(bool)


def _dilated_region(region: np.ndarray) -> np.ndarray:
    k = max(9, int(round(min(region.shape) * 0.08)) | 1)
    return cv2.dilate(region.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)


def _gray(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(image.convert("RGB"), np.uint8), cv2.COLOR_RGB2GRAY)


def _residual(cur: np.ndarray, gen: np.ndarray, anchors: np.ndarray) -> float:
    if not anchors.any():
        return float("inf")
    return float(np.abs(cur.astype(np.float32) - gen.astype(np.float32))[anchors].mean())


def _points_in_mask(mask: np.ndarray, points: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    rounded = np.rint(points).astype(np.int32)
    inside = (
        (rounded[:, 0] >= 0)
        & (rounded[:, 0] < w)
        & (rounded[:, 1] >= 0)
        & (rounded[:, 1] < h)
    )
    ok = np.zeros((len(points),), bool)
    if inside.any():
        xy = rounded[inside]
        ok[inside] = mask[xy[:, 1], xy[:, 0]]
    return ok


def _spatially_covered(points: np.ndarray, side: int) -> bool:
    if len(points) < 16:
        return False
    span = np.ptp(points, axis=0)
    if span[0] < side * 0.30 or span[1] < side * 0.30:
        return False
    quadrants = set()
    for x, y in points:
        quadrants.add((int(x >= side / 2), int(y >= side / 2)))
    return len(quadrants) >= 3


def _transform_metrics(M: np.ndarray, side: int) -> tuple[float, float, float]:
    linear = M[:, :2].astype(np.float64)
    sx = float(np.linalg.norm(linear[:, 0]))
    sy = float(np.linalg.norm(linear[:, 1]))
    scale = (sx + sy) / 2.0
    angle = float(np.degrees(np.arctan2(linear[1, 0], linear[0, 0])))
    corners = np.array(
        [[0, 0], [side - 1, 0], [side - 1, side - 1], [0, side - 1]],
        np.float32,
    )
    moved = cv2.transform(corners[None, :, :], M)[0]
    corner_displacement = float(np.linalg.norm(moved - corners, axis=1).max())
    return scale, angle, corner_displacement


def _estimate_alignment(p0: np.ndarray, p1: np.ndarray, side: int):
    heldout = ((p0[:, 0].astype(np.int32) + p0[:, 1].astype(np.int32)) % 5) == 0
    if int(heldout.sum()) < 8 or int((~heldout).sum()) < 16:
        heldout = np.zeros((len(p0),), bool)
        heldout[::5] = True
    train = ~heldout
    if int(heldout.sum()) < 8 or int(train.sum()) < 16:
        raise _err("alignment_uncertain")
    M, inliers = cv2.estimateAffinePartial2D(
        p1[train],
        p0[train],
        method=cv2.RANSAC,
        ransacReprojThreshold=2.5,
        maxIters=3000,
        confidence=0.995,
    )
    if M is None or inliers is None:
        raise _err("alignment_uncertain")
    train_inliers = inliers.reshape(-1).astype(bool)
    inlier_count = int(train_inliers.sum())
    inlier_ratio = inlier_count / max(1, int(train.sum()))
    if inlier_count < 16 or inlier_ratio < 0.45:
        raise _err("alignment_uncertain")
    if not _spatially_covered(p0[train][train_inliers], side):
        raise _err("alignment_uncertain")

    projected = cv2.transform(p1[heldout][None, :, :], M)[0]
    heldout_error = np.linalg.norm(projected - p0[heldout], axis=1)
    heldout_good = heldout_error <= 3.0
    heldout_ratio = float(heldout_good.mean()) if len(heldout_good) else 0.0
    if int(heldout_good.sum()) < 8 or heldout_ratio < 0.55 or float(np.median(heldout_error)) > 2.5:
        raise _err("alignment_uncertain")

    scale, angle, corner_displacement = _transform_metrics(M, side)
    if not (0.94 <= scale <= 1.06) or abs(angle) > 6.0 or corner_displacement > side * 0.18:
        raise _err("alignment_uncertain")
    return M, inlier_count, inlier_ratio, heldout_ratio, scale, angle, corner_displacement


def align_generated(crop, generated: Image.Image, region: np.ndarray) -> tuple[Image.Image, dict]:
    current_img = _current(crop)
    generated = generated.convert("RGB")
    if generated.size != current_img.size:
        generated = generated.resize(current_img.size, Image.LANCZOS)
    cur_rgb = np.asarray(current_img, np.uint8)
    gen_rgb = np.asarray(generated, np.uint8)
    if region.shape != cur_rgb.shape[:2]:
        raise _err("invalid_region")

    anchors = ~_dilated_region(region.astype(bool))
    before = _residual(cur_rgb, gen_rgb, anchors)
    if before < 1.0:
        return generated, {
            "applied": False,
            "reason": "unmoved",
            "residual_before": round(before, 3),
        }

    cur_g = _gray(current_img)
    gen_g = _gray(generated)
    feature_mask = anchors.astype(np.uint8) * 255
    pts0 = cv2.goodFeaturesToTrack(
        cur_g,
        maxCorners=800,
        qualityLevel=0.01,
        minDistance=5,
        mask=feature_mask,
    )
    if pts0 is None or len(pts0) < 24:
        if before < 4.0:
            return generated, {
                "applied": False,
                "reason": "no_evidence",
                "residual_before": round(before, 3),
            }
        raise _err("alignment_uncertain")

    pts1, status, _ = cv2.calcOpticalFlowPyrLK(
        cur_g,
        gen_g,
        pts0,
        None,
        winSize=(21, 21),
        maxLevel=3,
    )
    if pts1 is None or status is None:
        raise _err("alignment_uncertain")
    back, back_status, _ = cv2.calcOpticalFlowPyrLK(
        gen_g,
        cur_g,
        pts1,
        None,
        winSize=(21, 21),
        maxLevel=3,
    )
    if back is None or back_status is None:
        raise _err("alignment_uncertain")

    p0_all = pts0.reshape(-1, 2)
    p1_all = pts1.reshape(-1, 2)
    back_all = back.reshape(-1, 2)
    good = (status.reshape(-1) == 1) & (back_status.reshape(-1) == 1)
    good &= np.linalg.norm(back_all - p0_all, axis=1) <= 1.5
    good &= _points_in_mask(anchors, p0_all) & _points_in_mask(anchors, p1_all)
    if int(good.sum()) < 24:
        raise _err("alignment_uncertain")

    p0 = p0_all[good].astype(np.float32)
    p1 = p1_all[good].astype(np.float32)
    M, inlier_count, inlier_ratio, heldout_ratio, scale, angle, corner_displacement = _estimate_alignment(
        p0,
        p1,
        current_img.width,
    )

    aligned = cv2.warpAffine(
        gen_rgb,
        M,
        current_img.size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )
    after = _residual(cur_rgb, aligned, anchors)
    if corner_displacement < 0.35:
        if before < 4.0:
            return generated, {
                "applied": False,
                "reason": "no_evidence",
                "residual_before": round(before, 3),
            }
        raise _err("alignment_uncertain")
    if after > before * 0.8 or after > 35.0:
        raise _err("alignment_uncertain")

    return Image.fromarray(aligned), {
        "applied": True,
        "inliers": inlier_count,
        "inlier_ratio": round(inlier_ratio, 3),
        "heldout_ratio": round(heldout_ratio, 3),
        "residual_before": round(before, 3),
        "residual_after": round(after, 3),
        "dx": round(float(M[0, 2]), 3),
        "dy": round(float(M[1, 2]), 3),
        "scale": round(scale, 6),
        "angle": round(angle, 3),
        "corner_displacement": round(corner_displacement, 3),
    }
