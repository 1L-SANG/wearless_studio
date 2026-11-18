"""마네킹 없는 컷의 주상품 핏 정책, 고정 문단과 검사 눈금."""

from .fit_axes import FIT_AXES


ANALYSIS_FIT_AXES = {
    "top": {"women": ("fit", "length", "sleeve"), "men": ("fit", "sleeve")},
    "outer": {"women": ("fit", "sleeve"), "men": ("fit", "sleeve")},
    "dress": {"women": ("length", "sleeve"), "men": ()},
    "skirt": {"women": ("length",), "men": ()},
    "pants": {"women": ("length",), "men": ("length",)},
}

# 출처별 허용 값(None은 모든 카탈로그 값). 핏을 셀러 값만 쓰려면 fit의 ai 항목을 뺀다.
FIT_SOURCE_POLICY = {
    "length": {"seller": None, "measure": None, "ai": None},
    "sleeve": {"seller": None, "measure": None, "ai": None},
    "fit": {"seller": None, "ai": ("slim", "semi_over", "over")},
}

LONG_SLEEVES = ("above_wrist", "wrist", "back_of_hand", "fingers")


def is_enforced_fit_axis(category: str, axis: str) -> bool:
    return axis == "sleeve" or (category == "top" and axis == "length")


def select_applied_fit_axes(profile: dict | None) -> list[tuple[str, str, str]]:
    """이 컷의 그림 지시와 칩 검사에 넣을 (축, 값, 출처)를 카탈로그 순서로 돌려준다."""
    if not isinstance(profile, dict):
        return []
    category, gender = profile.get("category"), profile.get("gender")
    if not isinstance(category, str) or not isinstance(gender, str):
        return []
    live_axes = ANALYSIS_FIT_AXES.get(category, {}).get(gender, ())
    axes, sources = profile.get("axes"), profile.get("axisSources")
    if not isinstance(axes, dict) or not isinstance(sources, dict):
        return []
    selected = []
    for axis, by_gender in FIT_AXES.get(category, {}).items():
        value, source = axes.get(axis), sources.get(axis)
        if axis not in live_axes or not isinstance(value, str) or not isinstance(source, str):
            continue
        if category == "top" and axis == "length" and value == "long":
            continue
        if not any(entry["value"] == value for entry in by_gender.get(gender, ())):
            continue
        policy = FIT_SOURCE_POLICY.get(axis, {})
        if source in policy and (policy[source] is None or value in policy[source]):
            selected.append((axis, value, source))
    return selected


LENGTH_HEAD = "LENGTH (seller-confirmed; for the hem position this overrides the product photo): "
SLEEVE_HEAD = "SLEEVE LENGTH (seller-confirmed; for the sleeve end this overrides the product photo): "
FIT_HEADS = {
    "top": "TOP FIT (seller-selected; for the fit this overrides the product photo): ",
    "outer": "OUTER FIT (seller-selected; for the fit this overrides the product photo): ",
}
LENGTH_TAILS = {
    "top": " Take the fabric, color, neckline, print, details and width from the product photo; change only where the hem ends.",
    "skirt": " Take the fabric, color, print, details, silhouette and width from the product photo; change only where the hem ends.",
    "dress": " Take the fabric, color, neckline, print, details, silhouette and width from the product photo; change only where the hem ends.",
    "pants": " Take the leg shape and width from the product photo; change only where the hems end.",
}
SLEEVE_TAIL = " Take the sleeve shape, cuff and width from the product photo; change only where the sleeve ends."
FIT_TAIL = " Take the fabric, color, neckline, collar, print, buttons, details, sleeve length and hem length from the product photo; change only how loosely the garment sits on the body."

TOP_SENTENCES = {
    'crop': 'Cropped. The hem ends well above the waistband of the bottom, so a wide band of bare stomach shows between the hem and the waistband. Do not lengthen it to meet the waistband.',
    'slight_crop': 'Slightly cropped. The hem ends just above the waistband of the bottom, so only a narrow strip of bare stomach shows. Do not lengthen it to cover the waistband, and do not shorten it into a wide bare midriff.',
    'semi_crop': 'Waist length. The hem meets the waistband of the bottom and just covers it; no bare stomach shows, and the hem stays above the hip bones.',
    'basic': 'Hip length. The hem covers the waistband and ends at the hip bones, over the upper half of the fly and clearly above the crotch.',
    'semi_long': 'Covers the seat. The hem falls to crotch level, covering the waistband and the whole fly, with the thighs visible below. Do not shorten it to the hip bones.',
}

SLEEVE_SENTENCES = {
    'cap': 'Cap sleeves. Each sleeve only caps the shoulder and ends high on the upper arm near the armpit.',
    'cap_short': 'Short sleeves. Each sleeve ends on the upper arm, between its upper third and its middle, clearly above the elbow.',
    'short': 'Short sleeves. Each sleeve ends on the upper arm, between its upper third and its middle, clearly above the elbow.',
    'above_elbow': 'Long short sleeves. Each sleeve covers most of the upper arm and ends just above the elbow; the elbow itself stays bare. Do not shorten it to the middle of the upper arm.',
    'elbow': 'Elbow-length sleeves. Each sleeve covers the whole upper arm and ends right at the elbow joint. Do not shorten them to a regular short sleeve.',
    'above_wrist': 'Wrist-length sleeves. With the arms relaxed, each cuff ends at the wrist and the whole back of the hand stays bare. Do not let the cuff cover the hand.',
    'wrist': 'Wrist-length sleeves. With the arms relaxed, each cuff ends at the wrist and the whole back of the hand stays bare. Do not let the cuff cover the hand.',
    'back_of_hand': 'Long sleeves that cover the back of the hand. With the arms relaxed, each cuff passes the wrist and covers the back of the hand to just before the knuckles; the fingers stay visible. Do not shorten the sleeves to end at the wrist.',
    'fingers': 'Extra-long sleeves over the fingers. With the arms relaxed, each sleeve passes the wrist and covers the hand down to the fingers; only the fingertips peek out. Do not shorten the sleeves to end at the wrist.',
}

HEM_SENTENCES = {
    'mini': 'The hem ends on the upper thigh, above mid-thigh.',
    'above_knee': 'The hem ends between mid-thigh and the top of the knee; the knees stay bare.',
    'knee': 'The hem ends at the knee, covering the kneecap or stopping right at it. Do not shorten it above the knee.',
    'midi': 'The hem passes the knee and ends between the knee and mid-calf. Do not shorten it to the knee.',
    'long': 'The hem ends on the lower calf, below mid-calf, with the ankles visible.',
    'floor': 'The hem covers the ankles and reaches the tops of the feet.',
}

PANTS_SENTENCES = {
    'above_ankle': 'Each hem ends just above the ankle bone, so the ankle shows.',
    'ankle': 'Each hem ends at the ankle bone with no break.',
    'ankle_cover': 'Each hem covers the ankle bone and rests lightly on the top of the shoe with a small break.',
    'below_ankle': 'Each hem falls past the ankle and covers the top of the foot.',
}

FIT_SENTENCES = {
    'slim': 'Slim fit: shoulder seams on the shoulder points; the body follows the bust and the waist curve so the side seams touch the waist; the sleeves sit close to the arms.',
    'regular': 'Regular fit: the shoulder seams sit on the shoulder points, with light, even ease at the chest and waist; the body falls straight without clinging and without oversized volume; the sleeves have a little room around the arms.',
    'semi_over': 'Semi-oversized fit: the shoulder seams sit a few centimeters below the shoulder points, and the body falls straight with an even gap of air at the sides; relaxed but tidy, not baggy or puffed; the sleeves are a little wider than the arms.',
    'over': 'Oversized fit: the shoulder seams drop well down onto the upper arms, about a third of the way to the elbows; the body hangs from the chest as a wide boxy rectangle that does not touch the waist, with deep armholes and wide sleeves, clearly a size or more bigger than the body.',
}

MALE_SLIM_SENTENCE = 'Slim fit: shoulder seams on the shoulder points; the stretchy fabric hugs the chest, shoulders and upper arms so their shape shows through; the torso runs straight down with only a slight taper, no waist curve; the short sleeves grip the biceps.'

FIT_VALUE_SENTENCES = {
    **{("top", "length", value): sentence for value, sentence in TOP_SENTENCES.items()},
    **{(category, "sleeve", value): sentence for category in ("top", "outer", "dress") for value, sentence in SLEEVE_SENTENCES.items()},
    **{(category, "length", value): sentence for category in ("skirt", "dress") for value, sentence in HEM_SENTENCES.items()},
    **{("pants", "length", value): sentence for value, sentence in PANTS_SENTENCES.items()},
    **{(category, "fit", value): sentence for category in ("top", "outer") for value, sentence in FIT_SENTENCES.items()},
}

FRONT_HEM_SENTENCES = {
    "curved": "FRONT HEM SHAPE (from the product photo): the front hem is curved like a shirt tail, higher at the side seams and lower at the center.",
    "back_longer": "FRONT HEM SHAPE (from the product photo): the front hem is straight and the back hem hangs longer than the front.",
}


def build_analysis_fit_block(
    category: str, gender: str, applied_axes: list | tuple, *,
    sleeve_type: str | None = None, front_hem: dict | None = None,
) -> str:
    """선택 목록을 다시 검증한 뒤 고정 문장만 쓴다. 생성 경로 연결은 별도 작업이다."""
    axes, sources = {}, {}
    for entry in applied_axes:
        if isinstance(entry, (tuple, list)) and len(entry) == 3 and isinstance(entry[0], str):
            axis, value, source = entry
            axes[axis], sources[axis] = value, source
    selected = select_applied_fit_axes({"category": category, "gender": gender, "axes": axes, "axisSources": sources})
    if not selected:
        return ""
    long_sleeves = sleeve_type == "long" or axes.get("sleeve") in LONG_SLEEVES
    paragraphs = []
    for axis, value, source in selected:
        sentence = FIT_VALUE_SENTENCES[category, axis, value]
        if axis == "fit":
            if gender == "men" and value == "slim":
                sentence = MALE_SLIM_SENTENCE
                if long_sleeves:
                    sentence = sentence.replace("; the short sleeves grip the biceps.", ".")
            head, tail = FIT_HEADS[category], FIT_TAIL
        elif axis == "sleeve":
            head, tail = SLEEVE_HEAD, SLEEVE_TAIL
        else:
            head, tail = LENGTH_HEAD, LENGTH_TAILS[category]
        paragraphs.append(head + sentence + tail)
    if category == "top" and isinstance(front_hem, dict) and front_hem.get("confident") is True:
        value = front_hem.get("value")
        sentence = FRONT_HEM_SENTENCES.get(value) if isinstance(value, str) else None
        if sentence:
            paragraphs.append(sentence)
    return "\n\n".join(paragraphs)


FIT_CHECK_SCALES = {
    **{("top", "length", value): ("top_hem", target) for value, target in {
        "crop": "crop", "slight_crop": "crop", "semi_crop": "semi_crop", "basic": "basic", "semi_long": "semi_long",
    }.items()},
    **{(category, "sleeve", value): ("sleeve_end", target)
       for category in ("top", "outer", "dress") for value, target in {
           "cap": "cap", "cap_short": "short", "short": "short", "above_elbow": "short", "elbow": "elbow",
           "above_wrist": "wrist", "wrist": "wrist", "back_of_hand": "back_of_hand", "fingers": "fingers",
       }.items()},
    **{(category, "length", value): ("bottom_hem", value)
       for category in ("skirt", "dress") for value in ("mini", "above_knee", "knee", "midi", "long", "floor")},
    **{("pants", "length", value): ("pants_hem", target) for value, target in {
        "above_ankle": "above_ankle", "ankle": "ankle", "ankle_cover": "below_ankle", "below_ankle": "below_ankle",
    }.items()},
    **{(category, "fit", value): ("top_fit", value)
       for category in ("top", "outer") for value in ("slim", "regular", "semi_over", "over")},
}
