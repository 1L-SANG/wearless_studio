"""핏 프로필 축 카탈로그 — 프론트 src/lib/fitAxes.js 와 수동 미러.

셀러 입력값은 절대 프롬프트에 직접 넣지 않는다. profile 값은 카탈로그 키 검증에만 쓰고,
실제 출력은 고정 promptEn 문구만 사용한다.
"""

FIT_AXES = {
    "top": {
        "fit": {
            "women": [
                {"value": "tight", "label": "타이트", "promptEn": "tight/bodycon — clings to the torso like a second skin"},
                {"value": "slim", "label": "슬림", "promptEn": "close to the body with a narrow clean torso line, not skin-tight"},
                {"value": "regular", "label": "레귤러", "promptEn": "natural regular fit with light ease around chest and waist"},
                {"value": "semi_over", "label": "세미오버", "promptEn": "semi-oversized, relaxed volume"},
                {"value": "over", "label": "오버", "promptEn": "oversized volume, dropped shoulders, roomy chest and wide sleeves"},
            ],
            "men": [
                {"value": "slim", "label": "슬림", "promptEn": "close to the body with a narrow clean torso line, not skin-tight"},
                {"value": "regular", "label": "레귤러", "promptEn": "natural regular fit with light ease around chest and waist"},
                {"value": "semi_over", "label": "세미오버", "promptEn": "semi-oversized, relaxed volume"},
                {"value": "over", "label": "오버", "promptEn": "oversized volume, dropped shoulders, roomy chest and wide sleeves"},
            ],
        },
        "length": {
            "women": [
                {"value": "ultra_crop", "label": "울트라크롭", "promptEn": "hem ends well above the navel, midriff exposed"},
                {"value": "crop", "label": "크롭", "promptEn": "short cropped hem ending around the high waist"},
                {"value": "basic", "label": "기본", "promptEn": "standard hem ending around the hip line"},
                {"value": "long", "label": "롱", "promptEn": "long hem extending below the hips"},
            ],
            "men": [
                {"value": "crop", "label": "크롭", "promptEn": "short cropped hem ending around the high waist"},
                {"value": "basic", "label": "기본", "promptEn": "standard hem ending around the hip line"},
                {"value": "long", "label": "롱", "promptEn": "long hem extending below the hips"},
            ],
        },
    },
    "pants": {
        "cut": {
            "women": [
                {"value": "skinny", "label": "스키니", "promptEn": "clings tightly from hip to ankle, second-skin leg line"},
                {"value": "slim", "label": "슬림", "promptEn": "narrow fitted leg from hip to ankle without clinging"},
                {"value": "straight", "label": "일자", "promptEn": "straight leg line with consistent width from thigh to hem"},
                {"value": "bootcut", "label": "부츠컷", "promptEn": "fitted through hip and thigh, then flares out gently from the knee over the foot"},
                {"value": "wide", "label": "와이드", "promptEn": "a full, voluminous wide-leg silhouette; the legs drape as broad swinging columns from hip to hem, each hem opening visibly wider than the foot beneath it"},
            ],
            "men": [
                {"value": "slim", "label": "슬림", "promptEn": "narrow fitted leg from hip to ankle without clinging"},
                {"value": "straight", "label": "일자", "promptEn": "straight leg line with consistent width from thigh to hem"},
                {"value": "tapered", "label": "테이퍼드", "promptEn": "roomy thigh tapering gradually to a narrower hem"},
                {"value": "relaxed", "label": "릴렉스", "promptEn": "roomy and easy through hip and thigh, falling in a gently straight, slightly tapered leg with loose, non-fitted hem openings"},
                {"value": "semi_wide", "label": "세미와이드", "promptEn": "a moderately wide, clean straight column from the knee down; noticeably wider than a regular straight leg with moderately broad, non-flared hem openings"},
                {"value": "wide", "label": "와이드", "promptEn": "a full, voluminous wide-leg silhouette; the legs drape as broad swinging columns from hip to hem, each hem opening visibly wider than the foot beneath it"},
            ],
        },
        "length": {
            "women": [
                {"value": "above_ankle", "label": "발목 위", "promptEn": "hem ends just above the ankle bone, ankle visible"},
                {"value": "ankle", "label": "발목", "promptEn": "hem ends at the ankle bone, no break"},
                {"value": "below_ankle", "label": "발목 덮음", "promptEn": "a lengthened version of the same trousers whose hem falls just past the ankle and rests lightly on the top of the foot with one soft break; if the photographed garment stops at or above the ankle bone, visibly re-tailor only its length proportions by extending the leg hems down over the instep until a single soft break forms; if it already satisfies this target, preserve those proportions"},
            ],
            "men": [
                {"value": "above_ankle", "label": "발목 위", "promptEn": "hem ends just above the ankle bone, ankle visible"},
                {"value": "ankle", "label": "발목", "promptEn": "hem ends at the ankle bone, no break"},
                {"value": "below_ankle", "label": "발목 덮음", "promptEn": "a lengthened version of the same trousers whose hem falls just past the ankle and rests lightly on the top of the foot with one soft break; if the photographed garment stops at or above the ankle bone, visibly re-tailor only its length proportions by extending the leg hems down over the instep until a single soft break forms; if it already satisfies this target, preserve those proportions"},
            ],
        },
    },
    "skirt": {
        "length": {
            "women": [
                {"value": "mini", "label": "미니", "promptEn": "short skirt hem ending above mid-thigh"},
                {"value": "midi", "label": "미디", "promptEn": "skirt hem falls between the knee and mid-calf"},
                {"value": "long", "label": "롱", "promptEn": "long skirt hem reaching the lower calf to ankle"},
            ],
            "men": [],
        },
        "silhouette": {
            "women": [
                {"value": "h_line", "label": "H라인", "promptEn": "an H-line / pencil version of the same skirt — a straight narrow column with parallel side seams and no flare; if the photographed garment flares outward toward the hem, visibly re-tailor only its silhouette by straightening the side seams into parallel lines from hip to hem; if it already satisfies this target, preserve those proportions"},
                {"value": "a_line", "label": "A라인", "promptEn": "fitted at the waist then flares out steadily to a wide hem"},
                {"value": "mermaid", "label": "머메이드", "promptEn": "tight through hip and thigh, then flares out sharply near the hem"},
            ],
            "men": [],
        },
    },
    "dress": {
        "length": {
            "women": [
                {"value": "mini", "label": "미니", "promptEn": "short dress hem ending above mid-thigh"},
                {"value": "midi", "label": "미디", "promptEn": "dress hem falls between the knee and mid-calf"},
                {"value": "long", "label": "롱", "promptEn": "long dress hem reaching the lower calf to ankle"},
            ],
            "men": [],
        },
        "silhouette": {
            "women": [
                {"value": "h_line", "label": "H라인", "promptEn": "straight narrow dress column from shoulder to hem, no flare"},
                {"value": "a_line", "label": "A라인", "promptEn": "fitted upper body then widens steadily toward the hem"},
                {"value": "fit_and_flare", "label": "핏앤플레어", "promptEn": "fitted bodice through the waist, skirt flares out from the waist"},
                {"value": "mermaid", "label": "머메이드", "promptEn": "tight through hip and thigh, then flares out sharply near the hem"},
            ],
            "men": [],
        },
    },
    "outer": {
        "fit": {
            "women": [
                {"value": "slim", "label": "슬림", "promptEn": "a slim version of the same outerwear; if the photographed garment is looser or drop-shouldered than this target, visibly re-tailor only its fit proportions by placing the shoulder seams at the natural shoulder points and reducing excess ease through the body and sleeves; if it already satisfies this target, preserve those proportions"},
                {"value": "regular", "label": "레귤러", "promptEn": "regular outerwear fit with natural room for layering"},
                {"value": "semi_over", "label": "세미오버", "promptEn": "semi-oversized outerwear with relaxed shoulders and extra body room"},
                {"value": "over", "label": "오버", "promptEn": "an oversized version of the same outerwear; if the photographed garment sits at the natural shoulder point or is close-fitting, visibly re-tailor only its fit proportions by dropping the shoulder seams clearly below the shoulder points and adding broad air volume through the body and sleeves; if it already satisfies this target, preserve those proportions"},
            ],
            "men": [
                {"value": "slim", "label": "슬림", "promptEn": "a slim version of the same outerwear; if the photographed garment is looser or drop-shouldered than this target, visibly re-tailor only its fit proportions by placing the shoulder seams at the natural shoulder points and reducing excess ease through the body and sleeves; if it already satisfies this target, preserve those proportions"},
                {"value": "regular", "label": "레귤러", "promptEn": "regular outerwear fit with natural room for layering"},
                {"value": "semi_over", "label": "세미오버", "promptEn": "semi-oversized outerwear with relaxed shoulders and extra body room"},
                {"value": "over", "label": "오버", "promptEn": "an oversized version of the same outerwear; if the photographed garment sits at the natural shoulder point or is close-fitting, visibly re-tailor only its fit proportions by dropping the shoulder seams clearly below the shoulder points and adding broad air volume through the body and sleeves; if it already satisfies this target, preserve those proportions"},
            ],
        },
        "length": {
            "women": [
                {"value": "crop_short", "label": "크롭·숏", "promptEn": "a cropped version of the same outerwear ending at the waist or high hip; if the photographed garment is longer than this target, visibly re-tailor only its length proportions by proportionally shortening the front and back body panels; if it already satisfies this target, preserve those proportions"},
                {"value": "basic", "label": "기본", "promptEn": "standard outerwear hem ending around the hip"},
                {"value": "long", "label": "롱", "promptEn": "a lengthened version of the same outerwear whose hem extends to mid-thigh or lower; if the photographed garment ends above mid-thigh, visibly re-tailor only its length proportions by proportionally extending the front and back body panels downward past mid-thigh; if it already satisfies this target, preserve those proportions"},
            ],
            "men": [
                {"value": "crop_short", "label": "크롭·숏", "promptEn": "a cropped version of the same outerwear ending at the waist or high hip; if the photographed garment is longer than this target, visibly re-tailor only its length proportions by proportionally shortening the front and back body panels; if it already satisfies this target, preserve those proportions"},
                {"value": "basic", "label": "기본", "promptEn": "standard outerwear hem ending around the hip"},
                {"value": "long", "label": "롱", "promptEn": "a lengthened version of the same outerwear whose hem extends to mid-thigh or lower; if the photographed garment ends above mid-thigh, visibly re-tailor only its length proportions by proportionally extending the front and back body panels downward past mid-thigh; if it already satisfies this target, preserve those proportions"},
            ],
        },
    },
}


# 백엔드 전용 관측 목표(Observable target) — 판정 가능한 신체 랜드마크·가시성 고정 문구.
# (category, axis, value) 키의 상수 맵: 셀러 문자열은 절대 여기 보간되지 않는다(인젝션 방어).
# 특히 top/outer length 는 tuck 가림 사고(2026-07-13, fidelity 기획 §C-3) 대응으로
# 'untucked·hem visible·하의로 가리지 말 것'을 명문화한다. dress 에는 fit 축이 없다(카탈로그 실사).
AXIS_OBSERVABLES = {
    ("top", "fit", "tight"): "continuous contact at chest, waist, and upper arms, with no visible ease",
    ("top", "fit", "slim"): "follows chest and waist closely with only slight visible ease and does not read as bodycon",
    ("top", "fit", "regular"): "light, even ease at chest and waist, without clinging or oversized volume",
    ("top", "fit", "semi_over"): "extra room at shoulder, chest, and sleeves, with a mildly dropped shoulder point",
    ("top", "fit", "over"): "shoulder seam below the shoulder point and clear air around chest, waist, and sleeves",
    ("top", "length", "ultra_crop"): "entire untucked hem well above the navel and visible above any matching bottom",
    ("top", "length", "crop"): "entire untucked hem at the high waist and visible above any matching bottom",
    ("top", "length", "basic"): "entire untucked hem at the hip line and not covered by any matching bottom",
    ("top", "length", "long"): "entire untucked hem below the hips and neither tucked into nor covered by any matching bottom",
    ("outer", "fit", "slim"): "visible shoulder-seam placement at or near the natural shoulder point, no dropped shoulder or boxy body, and minimal layering ease through body and sleeves",
    ("outer", "fit", "regular"): "natural shoulder line with moderate layering room",
    ("outer", "fit", "semi_over"): "mildly dropped shoulder with extra room through body and sleeves",
    ("outer", "fit", "over"): "shoulder seam visibly below the shoulder point with broad air volume around body and sleeves",
    ("outer", "length", "crop_short"): "the garment body itself is cropped so the entire untucked hem ends at the waist or high hip and is unobscured rather than tucked or hidden",
    ("outer", "length", "basic"): "entire untucked hem at the hip and unobscured",
    ("outer", "length", "long"): "entire untucked hem at mid-thigh or lower and fully visible",
    ("pants", "cut", "skinny"): "outline hugs hip, thigh, knee, calf, and ankle continuously",
    ("pants", "cut", "slim"): "narrow at thigh, knee, and ankle with slight ease rather than continuous skin contact",
    ("pants", "cut", "straight"): "inner and outer leg lines nearly parallel from thigh to hem",
    ("pants", "cut", "bootcut"): "close through hip and knee, then visibly wider from knee to hem",
    ("pants", "cut", "wide"): "leg outlines clear of thighs and calves from hip to hem, with each hem opening visibly wider than the foot beneath it",
    ("pants", "cut", "tapered"): "ample thigh width narrowing visibly from knee to hem",
    ("pants", "cut", "relaxed"): "clear room at seat and thigh, then a soft near-straight fall with slight taper and loose, non-fitted hem openings",
    ("pants", "cut", "semi_wide"): "moderate straight column below the knee, wider than straight but narrower than wide, with moderately broad, non-flared hem openings",
    ("pants", "length", "above_ankle"): "both hems just above the ankle bones with a visible ankle gap and unobscured",
    ("pants", "length", "ankle"): "both hems at the ankle bones with no break and unobscured",
    ("pants", "length", "below_ankle"): "both hems extend past and fully cover the ankle bones, forming one visible soft fold over each instep",
    ("skirt", "length", "mini"): "entire hem above mid-thigh and fully visible",
    ("skirt", "length", "midi"): "entire hem between knee and mid-calf and fully visible",
    ("skirt", "length", "long"): "entire hem from lower calf to ankle and fully visible",
    ("skirt", "silhouette", "h_line"): "side seams nearly parallel from hip to hem with no flare and full outline visible",
    ("skirt", "silhouette", "a_line"): "fitted waist with both side seams widening continuously to the hem and full outline visible",
    ("skirt", "silhouette", "mermaid"): "outline hugs hip and thigh, then flares sharply near the lower leg with full flare visible",
    ("dress", "length", "mini"): "entire hem above mid-thigh and fully visible",
    ("dress", "length", "midi"): "entire hem between knee and mid-calf and fully visible",
    ("dress", "length", "long"): "entire hem from lower calf to ankle and fully visible",
    ("dress", "silhouette", "h_line"): "outer lines nearly parallel from shoulder to hem with no flare and full outline visible",
    ("dress", "silhouette", "a_line"): "fitted upper body with outer lines widening steadily to the hem and full outline visible",
    ("dress", "silhouette", "fit_and_flare"): "bodice fitted through the natural waist, skirt volume beginning clearly at the waist, full flare visible",
    ("dress", "silhouette", "mermaid"): "outline hugs hip and thigh, then flares sharply near the lower leg with full flare visible",
}


def _axis_entry(category: str, axis: str, gender: str, value):
    by_axis = FIT_AXES.get(category) or {}
    entries = (by_axis.get(axis) or {}).get(gender) or []
    return next((e for e in entries if e["value"] == value), None)


_MATCHING_FIT_AXIS = {"pants": "cut", "skirt": "silhouette"}


def _profile_version(profile: dict) -> int:
    version = profile.get("version")
    return version if type(version) is int and version in (1, 2) else 1


def _normalize_matching_fit(raw, gender: str) -> dict | None:
    """v2 매칭 의류 축을 카탈로그 값으로만 정규화한다.

    clothingId 는 DB 정본 대조용일 뿐 프롬프트에는 절대 쓰지 않는다. axes 는 계약의
    단일 키만 허용해 pants.cut 과 skirt.silhouette 어휘가 서로 섞이지 않게 한다.
    """
    if not isinstance(raw, dict):
        return None
    clothing_id = raw.get("clothingId")
    fit_category = raw.get("fitCategory")
    axis = _MATCHING_FIT_AXIS.get(fit_category) if isinstance(fit_category, str) else None
    raw_axes = raw.get("axes")
    if not isinstance(clothing_id, str) or not clothing_id or axis is None \
            or not isinstance(raw_axes, dict) or set(raw_axes) != {axis}:
        return None
    value = raw_axes.get(axis)
    if not _axis_entry(fit_category, axis, gender, value):
        return None
    return {
        "clothingId": clothing_id,
        "fitCategory": fit_category,
        "axes": {axis: value},
    }


def normalize_fit_profile(profile: dict | None) -> dict | None:
    """카탈로그 allowlist 로 프로필을 정규화 — 스냅샷·diff·렌더러가 공유하는 단일 검증 경로.

    category/gender 가 카탈로그에 없으면 None. axes 는 해당 category×gender 에서 유효한
    (axis, value) 만 남긴다(null·미지값 제거). v1 matchCut 은 pants.cut 어휘로,
    v2 matchingFit 은 clothingId·fitCategory·단일 axes 키와 해당 카탈로그 어휘로 검증한다.
    matchingFit 하나라도 유효하지 않으면 그 객체 전체를 버린다. source 는 seller/auto 만
    허용(그 외 auto), 셀러 자유 문자열은 프롬프트에 쓰이는 어떤 필드에도 남지 않는다.
    """
    if not isinstance(profile, dict):
        return None
    category = profile.get("category")
    gender = profile.get("gender")
    by_axis = FIT_AXES.get(category)
    if not by_axis or gender not in ("women", "men"):
        return None
    raw_axes = profile.get("axes") if isinstance(profile.get("axes"), dict) else {}
    axes = {}
    for axis in by_axis:  # 카탈로그 순서 유지 (diff·렌더 순서의 단일 소스)
        value = raw_axes.get(axis)
        if value is not None and _axis_entry(category, axis, gender, value):
            axes[axis] = value
    version = _profile_version(profile)
    out = {
        "category": category,
        "gender": gender,
        "axes": axes,
        "source": profile.get("source") if profile.get("source") in ("seller", "auto") else "auto",
        "version": version,
    }
    if version == 2:
        matching_fit = _normalize_matching_fit(profile.get("matchingFit"), gender)
        if matching_fit:
            out["matchingFit"] = matching_fit
    elif version == 1:
        match_cut = profile.get("matchCut")
        if match_cut is not None and _axis_entry("pants", "cut", gender, match_cut):
            out["matchCut"] = match_cut
    return out


def adjusted_axes_between(prev: dict | None, new: dict | None) -> list[str]:
    """셀러 조정 축 계산(서버 산출 전용 — 클라이언트 값 신뢰 금지, fidelity 설계 §E-2).

    새 프로필의 선언 축 중 직전 정규화 프로필과 값이 다른 것만, 카탈로그 순서로.
    category/gender 가 바뀌면 새 선언 축 전체가 조정으로 간주된다. matchCut·matchingFit 은
    둘 다 제외한다(주상품 axes 전용 계약).
    """
    if not isinstance(new, dict):
        return []
    new_axes = new.get("axes") or {}
    if not isinstance(prev, dict) or prev.get("category") != new.get("category") \
            or prev.get("gender") != new.get("gender"):
        return list(new_axes.keys())
    prev_axes = prev.get("axes") or {}
    return [axis for axis, value in new_axes.items() if prev_axes.get(axis) != value]


def _render_axis_line(category: str, axis: str, gender: str, value) -> str | None:
    entry = _axis_entry(category, axis, gender, value)
    if not entry:
        return None
    observable = AXIS_OBSERVABLES.get((category, axis, value))
    if observable:
        return f"- {axis}: {entry['promptEn']}. Observable target: {observable}."
    return f"- {axis}: {entry['promptEn']}."


def _render_matching_axis_line(category: str, axis: str, gender: str, value) -> str | None:
    entry = _axis_entry(category, axis, gender, value)
    observable = AXIS_OBSERVABLES.get((category, axis, value))
    if not entry or not observable:
        return None
    label = "matching bottom" if category == "pants" else "matching skirt silhouette"
    return (
        f"- {label} (the separate bottom garment styled with the product, "
        f"NOT the product itself): {entry['promptEn']}. Observable target: {observable}."
    )


def build_fit_profile_block(profile: dict | None, adjusted_axes: tuple | list = ()) -> str:
    """선언 축을 '관측 가능한 목표'로 렌더 + 셀러 조정 축은 CHANGES 섹션으로 분리 강조.

    문구 계약(fidelity 설계 D2): 충돌 시에만 선언 축이 이긴다 — 무조건 differ 금지.
    알 수 없는 값·축은 조용히 스킵(기존 계약 유지). 셀러 문자열은 절대 보간되지 않는다.
    """
    if not isinstance(profile, dict):
        return ""
    category = profile.get("category")
    gender = profile.get("gender")
    axes = profile.get("axes") if isinstance(profile.get("axes"), dict) else {}
    by_axis = FIT_AXES.get(category) or {}
    lines = []
    for axis in by_axis:
        value = axes.get(axis)
        if value is None:
            continue
        line = _render_axis_line(category, axis, gender, value)
        if line:
            lines.append(line)
    # 별도 매칭 의류는 버전별 계약으로만 렌더한다. clothingId·원문 카테고리·상품명은 보간하지
    # 않고, 고정 카탈로그 promptEn·observable 문구만 사용한다.
    version = _profile_version(profile)
    if version == 2:
        matching_fit = _normalize_matching_fit(profile.get("matchingFit"), gender)
        if matching_fit:
            match_category = matching_fit["fitCategory"]
            match_axis = _MATCHING_FIT_AXIS[match_category]
            line = _render_matching_axis_line(
                match_category, match_axis, gender, matching_fit["axes"][match_axis])
            if line:
                lines.append(line)
    elif version == 1:
        match_cut = profile.get("matchCut")
        line = _render_matching_axis_line("pants", "cut", gender, match_cut)
        if line:
            lines.append(line)
    if not lines:
        return ""
    block = (
        "FIT PROFILE (declared target axes; preserve garment identity and every undeclared axis):\n"
        + "\n".join(lines)
        + "\nWhere the photos conflict with a declared axis, the declared axis wins; "
          "otherwise preserve the photographed shape for that axis."
    )
    # CHANGES — 셀러가 이번에 조정한 주상품 축만 재강조(matchCut·matchingFit 제외).
    if profile.get("source") == "seller" and adjusted_axes:
        change_lines = []
        for axis in by_axis:  # 카탈로그 순서
            if axis not in adjusted_axes:
                continue
            value = axes.get(axis)
            if value is None:
                continue
            line = _render_axis_line(category, axis, gender, value)
            if line:
                change_lines.append(line)
        if change_lines:
            block += (
                "\n\nCHANGES FOR THIS GENERATION (seller-adjusted declared axes):\n"
                + "\n".join(change_lines)
                + "\nApply these targets where the photos conflict; "
                  "do not force a difference when the photos already satisfy them."
            )
    return block
