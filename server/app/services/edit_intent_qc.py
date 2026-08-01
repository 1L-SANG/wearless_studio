"""Edit Intent QC — "요청한 변화는 일어났고, 요청하지 않은 것은 그대로인가".

기존 상품 QC(image_qc·mannequin_fit_qc)와 **다른 질문**을 한다. 그쪽은 "이 이미지가 이
상품인가"를 보고, 여기는 baseline 대비 **차분**을 본다. 같은 이미지가 상품 QC 는 통과하면서
편집 의도는 완전히 배신할 수 있다 — 총장만 줄여 달랬는데 소매까지 줄어든 컷이 그렇다.

설계 규칙:
  · decision 은 **서버 정책**이 만든다. Vision 은 관찰 JSON 만 주고 판정하지 않는다.
  · 측정 불가는 성공이 아니다. mask·측정 신뢰도가 낮으면 자동 PASS 하지 않고 review 로 간다.
  · deterministic 측정(OpenCV/Pillow)이 1차 근거다. LLM 은 구조 관찰의 보조다.
"""

import numpy as np

try:  # cv2 는 워커 환경엔 항상 있지만, 순수 정책 테스트가 그것 때문에 죽지 않게 한다
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

# 요청한 축의 변화가 목표 대비 이 범위 안이면 "반영됐다"로 본다. 편집 모델은 단계를
# 정확히 재현하지 못한다 — 방향이 맞고 크기가 절반~두 배면 의도는 충족된 것으로 읽는다.
SATISFY_MIN_FRACTION = 0.4
SATISFY_MAX_FRACTION = 2.5

# 요청하지 않은 축의 허용 드리프트(baseline 대비 비율). 측정 노이즈보다 크고, 사람이
# "달라졌다"고 말할 정도보다는 작게 잡는다.
DRIFT_TOL = {
    "hemY": 0.03,
    "cuffY": 0.03,
    "bodyWidth": 0.04,
    "shoulderWidth": 0.04,
    "subjectHeight": 0.03,
    "centerX": 0.02,
    "centerY": 0.02,
}
BACKGROUND_DELTA_TOL = 6.0      # 배경 평균색 L*a*b* 유클리드 거리
SILHOUETTE_IOU_MIN = 0.80       # 실루엣이 이보다 덜 겹치면 "같은 장면"이 아니다
MIN_MEASURE_CONFIDENCE = 0.5

# 축 이름 → 측정 키. 요청 축은 이 키의 변화로 검증하고, 나머지는 드리프트로 본다.
# 요청 축이 **물리적으로 함께 움직이는** 지표. 밑단을 올리면 피사체 높이와 bbox 중심은
# 반드시 따라 온다 — 그걸 드리프트로 세면 정상 편집이 전부 reject 된다(실측).
_AXIS_ENTAILED = {
    "garmentLength": ("hemY", "subjectHeight", "centerY"),
    "tuckState": ("hemY", "subjectHeight", "centerY"),
    "sleeveLength": ("cuffY",),
    "bodyWidth": ("bodyWidth",),
    "shoulderWidth": ("shoulderWidth",),
    "mannequinVolume": ("bodyWidth", "shoulderWidth"),
}

_AXIS_METRIC = {
    "garmentLength": "hemY",
    "sleeveLength": "cuffY",
    "bodyWidth": "bodyWidth",
    "shoulderWidth": "shoulderWidth",
    "mannequinVolume": "bodyWidth",
    "tuckState": "hemY",
}


# ── 측정 ─────────────────────────────────────────────────────────────────────

def _foreground_mask(bgr):
    """밝은 스튜디오 배경 기준 전경 마스크. 실패하면 (None, 0.0)."""
    if cv2 is None or bgr is None or getattr(bgr, "size", 0) == 0:
        return None, 0.0
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    bg = float(np.median(border))
    mask = (np.abs(gray.astype(np.int16) - bg) > 12).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    frac = float((mask > 0).mean())
    # 전경이 거의 없거나 화면을 다 덮으면 분리에 실패한 것이다 — 신뢰도로 정직하게 낮춘다.
    conf = 0.0 if not (0.02 < frac < 0.9) else min(1.0, 0.5 + frac)
    return mask, conf


def _geometry(mask, shape) -> dict | None:
    if mask is None:
        return None
    ys, xs = np.nonzero(mask)
    if len(xs) < 50:
        return None
    h, w = shape[:2]
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    rows = np.nonzero((mask > 0).sum(axis=1) > 0)[0]
    mid = rows[len(rows) // 2] if len(rows) else y0
    row_w = int((mask[mid] > 0).sum())
    upper = rows[len(rows) // 6] if len(rows) else y0
    # 소매 끝은 **몸통 폭 바깥 컬럼**의 최하단이다. 실루엣 백분위로 재면 밑단이 올라갈 때
    # 같이 올라가서(실측 -5.6%), 총장만 바꾼 편집이 소매 드리프트로 오탐된다.
    mid_cols = np.nonzero(mask[mid] > 0)[0]
    cuff_y = None
    if len(mid_cols):
        outer = mask.copy()
        outer[:, mid_cols.min():mid_cols.max() + 1] = 0
        oys = np.nonzero(outer)[0]
        if len(oys) > 20:
            cuff_y = float(oys.max() / h)
    if cuff_y is None:                    # 소매가 몸통 폭 안에 있는 실루엣 — 근사로 폴백
        cuff_y = float(np.percentile(ys, 92) / h)
    return {
        "centerX": float((x0 + x1) / 2 / w),
        "centerY": float((y0 + y1) / 2 / h),
        "subjectHeight": float((y1 - y0) / h),
        "hemY": float(y1 / h),                      # 밑단 = 전경 최하단
        "cuffY": cuff_y,
        "bodyWidth": float(row_w / w),
        "shoulderWidth": float((mask[upper] > 0).sum() / w),
    }


def _background_lab(bgr, mask):
    if cv2 is None or bgr is None:
        return None
    sel = np.ones(bgr.shape[:2], bool) if mask is None else (mask == 0)
    if sel.sum() < 100:
        return None
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    return [float(np.median(lab[..., i][sel])) for i in range(3)]


def measure(baseline_bgr, edited_bgr) -> dict:
    """baseline 대비 차분 측정. 측정 불가는 confidence 0 으로 **정직하게** 남긴다."""
    m_b, c_b = _foreground_mask(baseline_bgr)
    m_e, c_e = _foreground_mask(edited_bgr)
    g_b = _geometry(m_b, baseline_bgr.shape) if baseline_bgr is not None else None
    g_e = _geometry(m_e, edited_bgr.shape) if edited_bgr is not None else None
    out = {"confidence": 0.0, "baseline": g_b, "edited": g_e, "delta": {},
           "backgroundDeltaE": None, "silhouetteIou": None}
    if not g_b or not g_e:
        return out
    delta = {}
    for k, v in g_b.items():
        if v:
            delta[k] = round((g_e[k] - v) / v, 4)
        else:
            delta[k] = round(g_e[k] - v, 4)
    out["delta"] = delta
    lab_b, lab_e = _background_lab(baseline_bgr, m_b), _background_lab(edited_bgr, m_e)
    if lab_b and lab_e:
        out["backgroundDeltaE"] = round(
            float(np.linalg.norm(np.array(lab_e) - np.array(lab_b))), 3)
    if m_b is not None and m_e is not None and m_b.shape == m_e.shape:
        inter = int(((m_b > 0) & (m_e > 0)).sum())
        union = int(((m_b > 0) | (m_e > 0)).sum())
        out["silhouetteIou"] = round(inter / union, 4) if union else None
    out["confidence"] = round(min(c_b, c_e), 3)
    return out


# ── 판정 (순수 정책) ─────────────────────────────────────────────────────────

def decide(*, edit_type: str, allowed_scope: dict, target_ratio: float | None,
           metrics: dict, vision: dict | None = None) -> dict:
    """측정 + (선택) Vision 관찰 → decision. **LLM 이 아니라 여기가 정한다.**

    반환: {decision, requestedChangeSatisfied, requestedChangeMeasurements,
           unexpectedChanges, lockedInvariantViolations, regenerationInstructions, checks}
    """
    allowed = list(allowed_scope.get("allowed") or ())
    checks: dict = {}
    unexpected: list[str] = []
    violations: list[str] = []
    instructions: list[str] = []

    conf = float(metrics.get("confidence") or 0.0)
    checks["measurementConfidence"] = conf
    measurable = conf >= MIN_MEASURE_CONFIDENCE and bool(metrics.get("delta"))
    checks["measurable"] = measurable

    delta = metrics.get("delta") or {}

    # ① 요청한 변화가 실제로 일어났는가
    satisfied = None
    measured: dict = {}
    axis = allowed[0] if allowed else None
    metric_key = _AXIS_METRIC.get(axis) if axis else None
    if target_ratio is not None and metric_key and measurable:
        observed = delta.get(metric_key)
        measured[metric_key] = observed
        measured["targetRatio"] = target_ratio
        if observed is None:
            satisfied = None
        elif target_ratio == 0:
            satisfied = abs(observed) <= DRIFT_TOL.get(metric_key, 0.03)
        else:
            frac = observed / target_ratio
            satisfied = SATISFY_MIN_FRACTION <= frac <= SATISFY_MAX_FRACTION
            measured["observedFraction"] = round(frac, 3)
        if satisfied is False:
            instructions.append(
                f"Apply the requested {axis} change again — measured "
                f"{observed:+.3f} vs target {target_ratio:+.3f}.")
    checks["requestedChange"] = satisfied

    # ② 요청하지 않은 축의 드리프트
    entailed = set(_AXIS_ENTAILED.get(axis, ())) if axis else set()
    checks["entailedMetrics"] = sorted(entailed)
    if measurable:
        for key, tol in DRIFT_TOL.items():
            if key == metric_key or key in entailed:
                continue           # 요청 축과 그것이 끌고 가는 지표는 바뀌는 게 정상
            v = delta.get(key)
            if v is not None and abs(v) > tol:
                unexpected.append(key)
        # 프레이밍·구도는 모든 edit type 에서 잠긴다
        for key in ("centerX", "centerY", "subjectHeight"):
            if key in unexpected and "framing" not in allowed:
                violations.append("framing")
                break
        # 배경/조명
        bg = metrics.get("backgroundDeltaE")
        checks["backgroundDeltaE"] = bg
        if bg is not None and bg > BACKGROUND_DELTA_TOL and "background" not in allowed:
            unexpected.append("background")
            violations.append("background")
            instructions.append("Preserve the baseline background exactly.")
        iou = metrics.get("silhouetteIou")
        checks["silhouetteIou"] = iou
        # 실루엣이 크게 달라지는 건 길이·폭 편집에서는 정상이다. 배경/조명 전용 편집에서만
        # 위반으로 본다 — 그쪽은 사람과 옷이 그대로여야 한다.
        if (iou is not None and iou < SILHOUETTE_IOU_MIN
                and edit_type in ("BACKGROUND_ONLY", "LIGHTING_ONLY")):
            unexpected.append("silhouette")
            violations.append("garmentOrMannequin")
            instructions.append("Keep the garment and mannequin pixel-identical.")

    # ③ Vision 관찰(있으면). **판정이 아니라 관찰이다** — 여기서 정책으로 환산한다.
    if isinstance(vision, dict):
        checks["vision"] = vision
        for key in ("collar", "sleeves", "buttons", "pockets", "pattern", "logo"):
            if vision.get(f"{key}Changed") is True:
                unexpected.append(key)
                violations.append(key)
        for key in ("pose", "camera", "framing", "mannequinIdentity"):
            if vision.get(f"{key}Changed") is True and key not in allowed:
                violations.append(key)
        if vision.get("requestedChangeApplied") is False and satisfied is not False:
            # 측정이 못 잡은 미충족을 Vision 이 봤다 — 통과시키지 않는다
            satisfied = False
            checks["requestedChange"] = False
            instructions.append("Requested change not visible; retry the edit.")
        if violations:
            instructions.append("Restore every locked element from the baseline.")

    unexpected = sorted(set(unexpected))
    violations = sorted(set(violations))

    # ── 결정 ────────────────────────────────────────────────────────────────
    # 정책은 고정 순서다. 자유 판단이 끼어들 자리를 만들지 않는다.
    if edit_type == "CUSTOM_REVIEW_REQUIRED":
        decision = "review_required"          # 자동 PASS 경로 자체가 없다
    elif violations:
        decision = "reject"                   # 잠근 것이 바뀌었다
    elif not measurable:
        decision = "review_required"          # 측정 불가는 성공이 아니다
    elif satisfied is False:
        decision = "reject"                   # 요청한 변화가 없다
    elif satisfied is None:
        decision = "review_required"          # 잴 수 없는 축(배경·조명 등)
    elif unexpected:
        decision = "review_required"          # 잠금 위반은 아니나 사람이 봐야 한다
    else:
        decision = "pass"

    return {
        "decision": decision,
        "requestedChangeSatisfied": bool(satisfied) if satisfied is not None else None,
        "requestedChangeMeasurements": measured,
        "unexpectedChanges": unexpected,
        "lockedInvariantViolations": violations,
        "regenerationInstructions": instructions,
        "checks": checks,
    }


def evaluate(*, baseline_bgr, edited_bgr, edit_type: str, allowed_scope: dict,
             target_ratio: float | None, vision: dict | None = None) -> dict:
    """측정 + 판정 한 번에. 워커가 쓰는 진입점."""
    metrics = measure(baseline_bgr, edited_bgr)
    result = decide(edit_type=edit_type, allowed_scope=allowed_scope,
                    target_ratio=target_ratio, metrics=metrics, vision=vision)
    result["metrics"] = metrics
    return result


def should_retry(result: dict, *, retry_count: int) -> bool:
    """재시도는 **1회**, 그리고 무엇을 고칠지 아는 경우에만.

    이유 없는 재시도는 같은 결과를 한 번 더 사는 것이다.
    """
    if retry_count >= 1:
        return False
    return result.get("decision") == "reject" and bool(
        result.get("regenerationInstructions"))
