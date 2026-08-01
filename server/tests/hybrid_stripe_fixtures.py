"""Hybrid stripe 합성 fixture 생성기 — 권리 확보(전부 in-repo 코드로 생성), 결정론(고정 seed).

oracle 독립성: 생성은 '직사각형을 순서대로 채우기'라는 자명한 규칙이고, 추출기는
autocorrelation/FFT/run-length 신호처리다. 두 수학이 달라서 추출기가 생성기를 베껴 통과하는
false-positive 가 구조적으로 불가능하다. manifest 의 기대값(oracle)은 **그린 스펙 그대로**이며
추출기를 돌려 만든 값이 아니다.

이미지 바이너리는 git 에 커밋하지 않는다 — 테스트/평가가 이 모듈로 즉석 생성한다.
manifest.json(스펙·기대값)만 커밋한다.
"""

import json
import pathlib

import cv2
import numpy as np

MANIFEST_PATH = pathlib.Path(__file__).parent / "fixtures" / "hybrid_stripe" / "manifest.json"

# ── 스트라이프 신호 8종 (signal id → 스펙) ─────────────────────────────────────────
# colors 는 sRGB(R,G,B). widths_px 합 = period_px. 첫 run 이 바탕색(가장 넓게 설계).
SIGNALS = {
    "S1_blue_brown_fine": {   # 첨부 셔츠 부류 — 흰 바탕 + 파랑/갈색 잔줄
        "axis": "horizontal",
        "runs": [((246, 244, 240), 22), ((70, 105, 170), 6), ((246, 244, 240), 8),
                 ((150, 110, 75), 4)],
    },
    "S2_navy_white_wide": {
        "axis": "horizontal",
        "runs": [((250, 250, 250), 48), ((35, 48, 95), 32)],
    },
    "S3_multi_four": {
        "axis": "horizontal",
        "runs": [((240, 238, 232), 40), ((190, 60, 50), 10), ((240, 238, 232), 12),
                 ((60, 130, 90), 8), ((240, 238, 232), 12), ((230, 180, 60), 6)],
    },
    "S4_pin_fine": {          # 가는 핀스트라이프 — 주기 12px
        "axis": "vertical",
        "runs": [((252, 252, 252), 9), ((60, 60, 70), 3)],
    },
    "S5_low_sat_close": {     # 저채도 근접색 — 파랑/갈색이 한 색으로 합쳐지면 안 되는 케이스
        "axis": "horizontal",
        "runs": [((235, 233, 228), 26), ((150, 155, 175), 8), ((235, 233, 228), 10),
                 ((170, 150, 135), 8)],
    },
    "S6_vertical_blue": {
        "axis": "vertical",
        "runs": [((248, 248, 246), 30), ((80, 110, 160), 14)],
    },
    "S7_asymmetric": {
        "axis": "horizontal",
        "runs": [((244, 242, 238), 52), ((90, 90, 100), 5), ((244, 242, 238), 9),
                 ((90, 90, 100), 14)],
    },
    "S8_pastel": {
        "axis": "vertical",
        "runs": [((250, 244, 240), 36), ((235, 170, 170), 18), ((250, 244, 240), 12),
                 ((170, 200, 230), 10)],
    },
}

# 신호당 변형 3종 — 같은 crop 재사용 금지 규정에 따라 각 변형은 조명/기하가 실제로 다르다.
VARIANTS = ("illum", "illum_perspective", "scaled")

NEGATIVE_CONTROLS = {
    "N1_solid": {"kind": "solid", "color": (210, 214, 220)},
    "N2_gingham_check": {"kind": "check", "colors": ((245, 245, 245), (90, 120, 170)),
                          "period": 36},
    "N3_heavy_blur": {"kind": "blur_stripe", "base": "S2_navy_white_wide"},
    "N4_noise": {"kind": "noise"},
}

FIXTURE_SIZE = (768, 768)  # (w, h) — 최소 주기(12px)로도 반복 60회 이상


def _draw_stripes(spec: dict, size=FIXTURE_SIZE) -> np.ndarray:
    """스펙 그대로 직사각형 채우기 — oracle 의 정의 그 자체."""
    w, h = size
    img = np.zeros((h, w, 3), np.uint8)
    period = sum(px for _c, px in spec["runs"])
    if spec["axis"] == "horizontal":   # 줄이 수평 → y 방향 반복
        y = 0
        while y < h:
            off = 0
            for rgb, px in spec["runs"]:
                img[y + off:min(y + off + px, h), :] = rgb[::-1]  # BGR
                off += px
            y += period
    else:
        x = 0
        while x < w:
            off = 0
            for rgb, px in spec["runs"]:
                img[:, x + off:min(x + off + px, w)] = rgb[::-1]
                off += px
            x += period
    return img


def _apply_illumination(img: np.ndarray, *, strength=0.25, diagonal=False) -> np.ndarray:
    h, w = img.shape[:2]
    gy = np.linspace(1.0 - strength, 1.0 + strength, h).reshape(-1, 1)
    gx = np.linspace(1.0 - strength * 0.6, 1.0 + strength * 0.6, w).reshape(1, -1)
    gain = gy * gx if diagonal else np.repeat(gy, w, axis=1)
    return np.clip(img.astype(np.float64) * gain[..., None], 0, 255).astype(np.uint8)


def _apply_mild_perspective(img: np.ndarray, *, seed: int, magnitude=0.03) -> np.ndarray:
    h, w = img.shape[:2]
    rng = np.random.default_rng(seed)
    jitter = rng.uniform(-magnitude, magnitude, (4, 2)) * [w, h]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32(src + jitter)
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)


def render_signal(signal_id: str, variant: str) -> np.ndarray:
    """(signal, variant) → BGR 이미지. 결정론(seed = hash 고정)."""
    spec = SIGNALS[signal_id]
    seed = abs(hash((signal_id, variant))) % (2 ** 31)
    if variant == "scaled":
        scaled = {"axis": spec["axis"],
                  "runs": [(c, max(2, int(round(px * 1.5)))) for c, px in spec["runs"]]}
        # 1.5× 주기에서도 입력 gate(최소 8회 반복)를 만족하도록 캔버스를 함께 키운다 —
        # gate 미달 fixture 는 추출 대상이 아니라 fail-closed 검증 대상이 된다.
        img = _draw_stripes(scaled, size=(1152, 1152))
        img = _apply_illumination(img, strength=0.15)
    elif variant == "illum":
        img = _apply_illumination(_draw_stripes(spec), strength=0.25)
    elif variant == "illum_perspective":
        img = _apply_mild_perspective(
            _apply_illumination(_draw_stripes(spec), strength=0.2, diagonal=True), seed=7)
    else:
        raise ValueError(variant)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 1.5, img.shape)
    return np.clip(img.astype(np.float64) + noise, 0, 255).astype(np.uint8)


def render_negative(neg_id: str) -> np.ndarray:
    spec = NEGATIVE_CONTROLS[neg_id]
    w, h = FIXTURE_SIZE
    if spec["kind"] == "solid":
        img = np.full((h, w, 3), spec["color"][::-1], np.uint8)
        return _apply_illumination(img, strength=0.2)
    if spec["kind"] == "check":
        img = np.full((h, w, 3), spec["colors"][0][::-1], np.uint8)
        p = spec["period"]
        for y in range(0, h, p * 2):
            img[y:y + p, :] = spec["colors"][1][::-1]
        col = img.copy()
        for x in range(0, w, p * 2):
            col[:, x:x + p] = spec["colors"][1][::-1]
        return cv2.addWeighted(img, 0.5, col, 0.5, 0)
    if spec["kind"] == "blur_stripe":
        base = render_signal(spec["base"], "illum")
        period = sum(px for _c, px in SIGNALS[spec["base"]]["runs"])
        return cv2.GaussianBlur(base, (0, 0), sigmaX=period * 1.2)
    if spec["kind"] == "noise":
        rng = np.random.default_rng(11)
        return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    raise ValueError(spec["kind"])


def expected_for(signal_id: str, variant: str) -> dict:
    """oracle — 그린 스펙에서 직접 계산 (추출기 미사용)."""
    spec = SIGNALS[signal_id]
    runs = spec["runs"]
    if variant == "scaled":
        runs = [(c, max(2, int(round(px * 1.5)))) for c, px in runs]
    period = sum(px for _c, px in runs)
    widths = [px / period for _c, px in runs]
    widest = max(range(len(runs)), key=lambda i: runs[i][1])
    order = runs[widest:] + runs[:widest]
    return {
        "axis": spec["axis"],
        "period_px": period,
        "ordered_palette_rgb": [list(c) for c, _px in order],
        "line_width_ratios": [px / period for _c, px in order],
        "n_runs": len(runs),
        "_raw_widths": widths,
    }


# ── 마스크/패널 geometry 6종 — 합성 '셔츠' carrier ───────────────────────────────────

GEOMETRIES = {
    "G1_regular": {"shoulder_w": 0.42, "hem_w": 0.40, "torso_h": 0.52, "sleeve_len": 0.30,
                   "sleeve_w": 0.11, "buttons": 7},
    "G2_slim": {"shoulder_w": 0.38, "hem_w": 0.30, "torso_h": 0.50, "sleeve_len": 0.28,
                "sleeve_w": 0.09, "buttons": 7},
    "G3_boxy": {"shoulder_w": 0.48, "hem_w": 0.50, "torso_h": 0.44, "sleeve_len": 0.24,
                "sleeve_w": 0.13, "buttons": 6},
    "G4_long": {"shoulder_w": 0.40, "hem_w": 0.42, "torso_h": 0.62, "sleeve_len": 0.34,
                "sleeve_w": 0.10, "buttons": 8},
    "G5_short_sleeve": {"shoulder_w": 0.44, "hem_w": 0.42, "torso_h": 0.50, "sleeve_len": 0.14,
                        "sleeve_w": 0.12, "buttons": 7},
    "G6_wide_hem": {"shoulder_w": 0.40, "hem_w": 0.52, "torso_h": 0.54, "sleeve_len": 0.30,
                    "sleeve_w": 0.11, "buttons": 7},
}

CARRIER_SIZE = (848, 1264)  # 실제 컷 비율(2:3)


def render_carrier(geom_id: str, variant: int = 0) -> dict:
    """합성 셔츠 carrier — 회색 의류 + 조명/주름 음영 + 배경. GT mask/landmarks/panels 동봉.

    의류는 무지 회색으로 그린다: carrier 는 '기하와 조명'만 제공한다는 계약 그대로.
    variant 1 은 조명 방향과 주름 위상이 다르다(같은 crop 재사용 금지 규정).
    """
    w, h = CARRIER_SIZE
    g = GEOMETRIES[geom_id]
    img = np.full((h, w, 3), 236, np.uint8)  # 밝은 중립 배경

    cx = w / 2
    top = h * 0.16
    sh_w = g["shoulder_w"] * w
    hem_w = g["hem_w"] * w
    torso_h = g["torso_h"] * h
    hem_y = top + torso_h
    torso = np.array([
        [cx - sh_w / 2, top], [cx + sh_w / 2, top],
        [cx + hem_w / 2, hem_y], [cx - hem_w / 2, hem_y]], np.int32)

    sl_len = g["sleeve_len"] * h
    sl_w = g["sleeve_w"] * w
    left_sleeve = np.array([
        [cx - sh_w / 2, top], [cx - sh_w / 2 - sl_w, top + sl_len * 0.35],
        [cx - sh_w / 2 - sl_w * 0.75, top + sl_len], [cx - sh_w / 2 + sl_w * 0.15, top + sl_len * 0.75]],
        np.int32)
    right_sleeve = np.array([
        [cx + sh_w / 2, top], [cx + sh_w / 2 + sl_w, top + sl_len * 0.35],
        [cx + sh_w / 2 + sl_w * 0.75, top + sl_len], [cx + sh_w / 2 - sl_w * 0.15, top + sl_len * 0.75]],
        np.int32)

    garment = np.zeros((h, w), np.uint8)
    for poly in (torso, left_sleeve, right_sleeve):
        cv2.fillPoly(garment, [poly], 255)
    img[garment > 0] = (176, 176, 176)

    # 칼라(간단한 V 형) + 단추 플래킷 — construction inventory 의 근거
    collar_h = h * 0.045
    collar = np.array([[cx - sh_w * 0.16, top], [cx + sh_w * 0.16, top],
                       [cx, top + collar_h * 1.6]], np.int32)
    cv2.fillPoly(img, [collar], (120, 120, 120))
    for b in range(g["buttons"]):
        by = int(top + collar_h * 1.8 + (torso_h - collar_h * 2.2) * b / max(g["buttons"] - 1, 1))
        cv2.circle(img, (int(cx), by), max(2, int(w * 0.006)), (70, 70, 70), -1)

    # 조명·주름: 저주파 음영 + sin 주름띠 (variant 로 방향·위상 변경)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    if variant == 0:
        shade = 1.0 - 0.18 * (xx / w) - 0.06 * (yy / h)
        folds = 0.06 * np.sin(yy / 55.0 + xx / 400.0)
    else:
        shade = 1.0 - 0.18 * ((w - xx) / w) - 0.04 * (yy / h)
        folds = 0.05 * np.sin(yy / 40.0 + 1.3 + xx / 260.0)
    gain = np.clip(shade + folds, 0.55, 1.25)
    out = img.astype(np.float64)
    out[garment > 0] *= gain[garment > 0][..., None]
    img = np.clip(out, 0, 255).astype(np.uint8)

    landmarks = {
        "shoulder_l": [float((cx - sh_w / 2) / w), float(top / h)],
        "shoulder_r": [float((cx + sh_w / 2) / w), float(top / h)],
        "hem_l": [float((cx - hem_w / 2) / w), float(hem_y / h)],
        "hem_r": [float((cx + hem_w / 2) / w), float(hem_y / h)],
        "sleeve_l_end": [float((cx - sh_w / 2 - sl_w * 0.85) / w), float((top + sl_len * 0.9) / h)],
        "sleeve_r_end": [float((cx + sh_w / 2 + sl_w * 0.85) / w), float((top + sl_len * 0.9) / h)],
        "collar_center": [float(cx / w), float(top / h)],
        "placket_bottom": [float(cx / w), float(hem_y / h)],
    }
    inventory = {
        "collar": True, "placket": True, "visible_buttons": g["buttons"],
        "cuffs": g["sleeve_len"] > 0.2,
        "torso_aspect": round(float(torso_h / ((sh_w + hem_w) / 2)), 3),
        "sleeve_len_ratio": round(float(sl_len / torso_h), 3),
    }
    return {
        "image": img, "garment_mask": garment,
        "torso_poly": torso.tolist(),
        "sleeve_l_poly": left_sleeve.tolist(), "sleeve_r_poly": right_sleeve.tolist(),
        "landmarks": landmarks, "construction_inventory": inventory,
    }


def build_manifest() -> dict:
    """커밋되는 manifest.json 의 정본 — 코드와 파일이 어긋나면 테스트가 잡는다."""
    cases = []
    for sid in SIGNALS:
        for var in VARIANTS:
            exp = expected_for(sid, var)
            exp.pop("_raw_widths")
            cases.append({
                "id": f"{sid}__{var}",
                "class": ["stripe", exp["axis"],
                          "illumination" if "illum" in var else "scale",
                          *(["perspective"] if var == "illum_perspective" else []),
                          *(["multi_color"] if exp["n_runs"] >= 4 else [])],
                "generator": f"hybrid_stripe_fixtures.render_signal({sid!r}, {var!r})",
                "rights": "synthetic — in-repo 생성기 산출물, 외부 콘텐츠 없음",
                "oracle_author": "rule-based generator spec (추출기 미사용)",
                "expected": exp,
            })
    for nid in NEGATIVE_CONTROLS:
        cases.append({
            "id": nid,
            "class": ["negative_control"],
            "generator": f"hybrid_stripe_fixtures.render_negative({nid!r})",
            "rights": "synthetic — in-repo 생성기 산출물, 외부 콘텐츠 없음",
            "oracle_author": "rule-based generator spec",
            "expected": {"typed_failure": True},
        })
    geoms = []
    for gid in GEOMETRIES:
        for var in (0, 1):
            geoms.append({
                "id": f"{gid}__v{var}",
                "class": ["carrier_geometry", "torso", "sleeve", "collar", "placket",
                          *(["cuff"] if GEOMETRIES[gid]["sleeve_len"] > 0.2 else [])],
                "generator": f"hybrid_stripe_fixtures.render_carrier({gid!r}, {var})",
                "rights": "synthetic — in-repo 생성기 산출물",
                "oracle_author": "rule-based generator spec (GT mask/landmark 동봉)",
            })
    return {"version": 1, "extractor_cases": cases, "carrier_cases": geoms}


def write_manifest():
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(build_manifest(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    write_manifest()
    print("wrote", MANIFEST_PATH)
