"""마네킹 없는 컷의 칩 판독과 거리 판정. 모델에게 상품 사진과 목표 값은 주지 않는다."""

import asyncio
from collections.abc import Mapping
from io import BytesIO
import time

from PIL import Image

from . import analysis_fit
from .analysis_fit import FIT_CHECK_SCALES, FIT_VALUE_SENTENCES, select_applied_fit_axes
from .gemini_image import InlineImage, run_cpu_bound
from .prompts import clean_text
from .vision_llm import VisionError, analyze_with_fallback


SCALES = {'top_hem': {'what': 'Where the bottom edge of the OUTERMOST upper-body garment (the top, shirt, knit or '
                     'jacket worn on the torso; for a one-piece dress use bottom_hem instead and set this to '
                     'not_applicable) sits at the center front when it hangs freely. Use the waistband of '
                     'the bottom and the crotch (where the legs of the bottom split) as the ruler. If a hand '
                     'in a pocket or a raised arm lifts part of the hem, judge from the part that hangs '
                     'freely.',
             'values': {'crop': 'a band of bare stomach shows between the hem and the waistband of the '
                                'bottom (hem at the high waist or above)',
                        'semi_crop': 'the hem reaches the waistband or just covers it, ending on the lower '
                                     'abdomen; no bare skin, but it stops clearly above the hip line',
                        'basic': 'the hem covers the waistband and ends around the hip line, clearly above '
                                 'the crotch',
                        'semi_long': 'the hem ends at about crotch level or just below it, covering the '
                                     'front crotch area, but not far down the thighs',
                        'long': 'the hem clearly passes the crotch and ends on the upper or mid thigh',
                        'longer': 'the hem ends near the knee or lower (tunic or coat length)',
                        'tucked': 'the garment is tucked into the bottom, so its natural hem cannot be '
                                  'judged',
                        'not_visible': 'the hem is outside the frame or hidden',
                        'not_applicable': 'no separate upper-body garment (for example a one-piece dress)'}},
 'sleeve_end': {'what': 'Where the lower edge of the sleeve of the OUTERMOST upper-body garment (or dress) '
                        'sits on the arm. Use the most relaxed, most visible arm; sleeves ride up on bent or '
                        'raised arms. If the hand is in a pocket, judge where the sleeve stops on the wrist '
                        'or hand going into the pocket.',
                'values': {'sleeveless': 'no sleeve (tank, camisole, sleeveless)',
                           'cap': 'the sleeve just caps the shoulder and ends high on the upper arm near the '
                                  'armpit',
                           'cap_short': 'extra-short sleeve covering only the uppermost part of the upper '
                                        'arm, between cap and regular short',
                           'short': 'regular short sleeve ending around the middle of the upper arm, clearly '
                                    'above the elbow',
                           'elbow': 'the sleeve ends at the elbow joint (just above or just below it)',
                           'forearm': 'the sleeve ends between the elbow and the wrist (three-quarter)',
                           'wrist': 'the cuff sits at the wrist; the back of the hand is bare',
                           'back_of_hand': 'the cuff covers part of the back of the hand; the knuckles are '
                                           'still visible',
                           'fingers': 'the sleeve reaches the knuckles or covers the fingers',
                           'not_visible': 'the sleeve end is outside the frame or hidden',
                           'not_applicable': 'no upper-body garment or dress'}},
 'bottom_hem': {'what': 'Where the hem of a SKIRT or DRESS sits on the legs at the center front. If the '
                        'person wears trousers or shorts and no skirt or dress, use not_applicable.',
                'values': {'mini': 'the hem ends above mid-thigh',
                           'above_knee': 'the hem ends between mid-thigh and the top of the knee',
                           'knee': 'the hem ends at the knee (covering the kneecap or just above or below '
                                   'it)',
                           'midi': 'the hem ends below the knee, between the knee and mid-calf',
                           'long': 'the hem ends on the lower calf, below mid-calf and above or at the ankle',
                           'floor': 'the hem reaches the feet or the floor',
                           'not_visible': 'the hem is outside the frame or hidden',
                           'not_applicable': 'no skirt or dress'}},
 'pants_hem': {'what': 'Where the hem of the TROUSERS sits at the ankle. If there are no trousers (skirt, '
                       'dress or shorts), use not_applicable.',
               'values': {'above_ankle': 'the hem ends above the ankle bone, so the ankle is visible',
                          'ankle': 'the hem ends at the ankle bone with little or no break',
                          'below_ankle': 'the hem falls past the ankle and rests on or covers the top of the '
                                         'foot or the shoe',
                          'not_visible': 'the hem is outside the frame or hidden',
                          'not_applicable': 'no trousers'}},
 'top_fit': {'what': 'How much room the OUTERMOST upper-body garment has around the torso, judged from the '
                     'gap between the garment and the body at the chest, waist and hem, and from where the '
                     'shoulder seam sits.',
             'values': {'slim': 'close to the body with a narrow clean torso line, shoulder seam at the '
                                'shoulder, little air',
                        'regular': 'natural fit with light ease around chest and waist, shoulder seam at or '
                                   'very near the shoulder',
                        'semi_over': 'relaxed volume; clear air around the torso, shoulder seam slightly '
                                     'dropped',
                        'over': 'oversized; roomy torso well wider than the body, clearly dropped shoulders, '
                                'wide sleeves',
                        'not_visible': 'the torso is mostly hidden or outside the frame',
                        'not_applicable': 'no upper-body garment'}}}

ORDERS = {name: tuple(value for value in scale["values"] if value not in
                     {"not_visible", "not_applicable", "tucked"}) for name, scale in SCALES.items()}

_SYSTEM = """Read the garment in these two views of the SAME generated photograph.
Image 1 is the full photograph. Image 2 enlarges its lower three quarters.
Use body landmarks inside this photograph as the ruler: waistband, crotch, elbow,
wrist and knee. Judge the most relaxed visible arm and freely hanging hem; do not
mistake a locally lifted edge for the natural hem. Do not compare with any product
photo or guess a desired choice. You are given no target choice.
Give brief visual reasoning first, then select exactly one listed value for each
requested axis. Use not_visible or not_applicable when its definition applies.
Return only the JSON object required by the response schema.
"""


def build_prompt(scales):
    lines = [_SYSTEM]
    for name in scales:
        scale = SCALES[name]
        lines.append(f"{name}: {scale['what']}")
        lines.extend(f"  {value} = {definition}" for value, definition in scale["values"].items())
    return "\n".join(lines)


def response_schema(scales):
    return {"type": "object", "additionalProperties": False, "properties": {
        "reasoning": {"type": "string"},
        "axes": {"type": "object", "additionalProperties": False,
                 "properties": {name: {"type": "string", "enum": list(SCALES[name]["values"])} for name in scales},
                 "required": list(scales)},
    }, "required": ["reasoning", "axes"]}


def judge_distance(scale, target, observed):
    order = ORDERS[scale]
    if target not in order or observed not in order:
        return {"distance": None, "delta": None, "status": "UNJUDGEABLE"}
    delta = order.index(observed) - order.index(target)
    failed = abs(delta) >= 2 or ((target == "crop") != (observed == "crop"))
    return {"distance": abs(delta), "delta": delta, "status": "FAIL" if failed else "PASS"}


def _inspection_images(candidate):
    with Image.open(BytesIO(candidate.data)) as picture:
        picture = picture.convert("RGB")
        width, height = picture.size
        crop = picture.crop((0, height // 4, width, height))
        crop = crop.resize((round(width * height / crop.height), height), Image.Resampling.LANCZOS)
        out = BytesIO()
        crop.save(out, format="JPEG", quality=90)
    return [candidate, InlineImage("image/jpeg", out.getvalue())]


async def _ask(settings, model, scales, images, timeout):
    started = time.perf_counter()
    check = {"scales": list(scales), "model": model}
    try:
        raw, provider = await asyncio.wait_for(analyze_with_fallback(
            settings, build_prompt(scales), images, response_schema(scales),
            thinking_level="low", models={"gemini": model}, order=["gemini"],
            require_complete_envelope=True, timeout_seconds=timeout,
        ), timeout=timeout)
        if (not isinstance(raw, dict) or set(raw) != {"reasoning", "axes"}
                or not isinstance(raw["reasoning"], str) or not raw["reasoning"].strip()
                or not isinstance(raw["axes"], dict) or set(raw["axes"]) != set(scales)
                or any(not isinstance(value, str) or value not in SCALES[name]["values"] for name, value in raw["axes"].items())):
            raise VisionError("cut_fit_chip_qc: invalid response")
        check.update(provider=provider, observed=raw["axes"], evidence=clean_text(raw["reasoning"], 240))
    except Exception:
        check.update(provider=None, observed={}, error="check_unavailable")
    check["elapsedMs"] = max(0, round((time.perf_counter() - started) * 1000))
    return check


def enabled(settings, profile, *, has_mannequin=False, cut_type="horizon"):
    return (getattr(settings, "cut_fit_chip_qc_mode", "off") in {"shadow", "repair"}
            and not has_mannequin and cut_type in {"styling", "horizon"}
            and bool(select_applied_fit_axes(profile)))


async def verdict(settings, profile, candidate, *, has_mannequin=False, cut_type="horizon", observe_only=False):
    mode = getattr(settings, "cut_fit_chip_qc_mode", "off")
    if not enabled(settings, profile, has_mannequin=has_mannequin, cut_type=cut_type):
        return None
    if observe_only:
        mode = "shadow"
    selected = select_applied_fit_axes(profile)
    category = profile["category"]
    groups, rows = {}, []
    for axis, value, source in selected:
        scale, target = FIT_CHECK_SCALES[category, axis, value]
        model = (settings.model_cut_fit_chip_qc_top_length if scale == "top_hem"
                 else settings.model_cut_fit_chip_qc_other)
        level = mode if analysis_fit.is_enforced_fit_axis(category, axis) else "shadow"
        rows.append({"axis": axis, "value": value, "scale": scale, "target": target,
                     "level": level, "model": model})
        groups.setdefault(model, []).append(scale)
    timeout = settings.cut_fit_chip_qc_timeout_seconds
    started = time.perf_counter()
    try:
        images = await asyncio.wait_for(run_cpu_bound(_inspection_images, candidate), timeout=timeout)
        remaining = max(0.001, timeout - (time.perf_counter() - started))
        checks = await asyncio.gather(*(_ask(settings, model, tuple(dict.fromkeys(scales)), images, remaining)
                                       for model, scales in groups.items()))
    except Exception:
        checks = [{"scales": scales, "model": model, "observed": {}, "provider": None,
                   "elapsedMs": max(0, round((time.perf_counter() - started) * 1000)), "error": "image_unavailable"}
                  for model, scales in groups.items()]
    by_model = {check["model"]: check for check in checks}
    for row in rows:
        check = by_model[row["model"]]
        observed = check["observed"].get(row["scale"])
        row.update(observed=observed, elapsedMs=check["elapsedMs"], evidence=check.get("evidence", ""),
                   **judge_distance(row["scale"], row["target"], observed))
    return {"version": 1, "mode": mode, "category": category, "axes": rows, "checks": checks}


def axis_repair_route(axis: str, target: str | None, delta: int) -> str:
    """Choose how to repair one enforced mismatch using its signed chip distance."""
    if axis == "sleeve" or target == "crop" or delta >= 0:
        return "EDIT_STAGE1"
    return "REGENERATE_FROM_SCRATCH"


def repair_route(report):
    routes = []
    for row in (report or {}).get("axes", ()):
        if row.get("status") != "FAIL" or row.get("level") != "repair":
            continue
        routes.append(axis_repair_route(row["axis"], row.get("target"), row.get("delta", 0)))
    return "REGENERATE_FROM_SCRATCH" if "REGENERATE_FROM_SCRATCH" in routes else "EDIT_STAGE1" if routes else "KEEP_STAGE1"


def combine_routes(operating, chip):
    if "REGENERATE_FROM_SCRATCH" in (operating, chip):
        return "REGENERATE_FROM_SCRATCH"
    if "EDIT_STAGE1" in (operating, chip):
        return "EDIT_STAGE1"
    return "HOLD_STAGE1" if "HOLD_STAGE1" in (operating, chip) else "KEEP_STAGE1"


def repair_instructions(report, operating=()):
    instructions = []
    for row in (report or {}).get("axes", ()):
        if row.get("status") == "FAIL" and row.get("level") == "repair":
            sentence = FIT_VALUE_SENTENCES.get(((report or {}).get("category"), row.get("axis"), row.get("value")))
            if sentence and sentence not in instructions:
                instructions.append(sentence)
    for sentence in operating:
        if sentence not in instructions:
            instructions.append(sentence)
    if any(not isinstance(sentence, str) or not 0 < len(sentence) <= 240 for sentence in instructions):
        raise VisionError("cut_fit_chip_qc: invalid fixed correction")
    return tuple(instructions[:5])


_EDIT_GARMENTS = {"top": "top", "outer": "outerwear garment", "dress": "dress"}
_EDIT_CATEGORIES = {"상의": "top", "아우터": "outer", "원피스": "dress"}
_EDIT_TARGETS = {
    "crop": "its untucked hem ends well above the waistband of the bottom and a wide band of bare stomach shows",
    "slight_crop": "its untucked hem ends just above the waistband of the bottom and only a narrow strip of bare stomach shows",
    "semi_crop": "its untucked hem just covers the waistband, stays above the hip bones and shows no bare stomach",
    "basic": "its untucked hem covers the waistband and ends at the hip bones, over the upper half of the fly and above the crotch",
    "semi_long": "its untucked hem falls to crotch level and covers the waistband and the whole fly",
    "cap": "each sleeve caps the shoulder and ends high on the upper arm near the armpit",
    "cap_short": "each sleeve ends between the upper third and the middle of the upper arm, clearly above the elbow",
    "short": "each sleeve ends between the upper third and the middle of the upper arm, clearly above the elbow",
    "above_elbow": "each sleeve covers most of the upper arm and ends just above the elbow, leaving the elbow bare",
    "elbow": "each sleeve covers the whole upper arm and ends at the elbow joint",
    "above_wrist": "each relaxed cuff ends at the wrist and the whole back of the hand stays bare",
    "wrist": "each relaxed cuff ends at the wrist and the whole back of the hand stays bare",
    "back_of_hand": "each sleeve covers the back of the hand to just before the knuckles, leaving the fingers visible",
    "fingers": "each sleeve passes the wrist and covers the hand down to the fingers, leaving only the fingertips visible",
}


def _make_edit_command(category, axis, value, direction):
    subject = f"sold {_EDIT_GARMENTS[category]}"
    if axis == "sleeve":
        subject = f"sleeves of the {subject}"
    head = f"Make the {subject} {direction}" if direction else f"Change the {subject}"
    return f"{head} so that {_EDIT_TARGETS[value]}."


_EDIT_KEYS = tuple(key for key in FIT_CHECK_SCALES if analysis_fit.is_enforced_fit_axis(key[0], key[1]))
_EDIT_KEY_BY_SENTENCE = {(key[0], FIT_VALUE_SENTENCES[key]): key for key in _EDIT_KEYS}
_EDIT_COMMAND_BY_DIRECTION = {
    (key, sign): _make_edit_command(*key, direction)
    for key in _EDIT_KEYS for sign, direction in ((-1, "longer"), (0, None), (1, "shorter"))
}
FIT_EDIT_COMMANDS = frozenset(_EDIT_COMMAND_BY_DIRECTION.values())


def fit_edit_instructions(corrections, clothing_type, qc_result):
    """Render fixed edit commands using only known targets and observed scale positions."""
    category = str(clothing_type).strip().lower()
    category = _EDIT_CATEGORIES.get(category, category)
    result = qc_result if isinstance(qc_result, Mapping) else {}
    chip = result.get("fitChipQc")
    observations = []
    if isinstance(chip, Mapping) and chip.get("category") == category:
        rows = chip.get("axes")
        if isinstance(rows, (list, tuple)):
            observations.extend(row for row in rows
                                if isinstance(row, Mapping) and row.get("level") == "repair" and row.get("status") == "FAIL")
    operating = result.get("enforcedFitFailures")
    if isinstance(operating, (list, tuple)):
        observations.extend(row for row in operating if isinstance(row, Mapping))

    instructions = []
    for sentence in corrections:
        key = _EDIT_KEY_BY_SENTENCE.get((category, sentence)) if isinstance(sentence, str) else None
        if key is None:
            instructions.append(sentence)
            continue
        scale, target = FIT_CHECK_SCALES[key]
        delta = 0
        for row in observations:
            observed = row.get("observed")
            if row.get("axis") == key[1] and row.get("target") == target and observed in ORDERS[scale]:
                delta = judge_distance(scale, target, observed)["delta"]
                break
        sign = (delta > 0) - (delta < 0)
        instructions.append(_EDIT_COMMAND_BY_DIRECTION[key, sign])
    return tuple(instructions)


def compare_distances(before, after):
    following = {row["axis"]: row for row in (after or {}).get("axes", ())}
    regressions = []
    before = before or {}
    for row in before.get("axes", ()):
        if not analysis_fit.is_enforced_fit_axis(before.get("category"), row["axis"]):
            continue
        distance = row.get("distance")
        next_distance = following.get(row["axis"], {}).get("distance")
        if next_distance is None or (distance is not None and next_distance > distance):
            regressions.append(row["axis"])
    return {"accepted": not regressions, "regressions": regressions}


def finalize(before, after=None, *, attempted=False, accepted=False):
    result = dict(before)
    if after is not None:
        result["stage2"] = after
    if attempted:
        final = after if accepted and after is not None else before
        axes = [f"{final['category']}.{row['axis']}" for row in final["axes"]
                if row.get("status") == "FAIL" and row.get("level") == "repair"]
        result.update(finalSource="stage2" if accepted else "stage1", sellerCheck={"required": bool(axes), "axes": axes})
    return result


async def check_and_repair(settings, profile, initial, *, judge_operating, make_stage2,
                           operating_mode, can_repair=True, operating_qc_outcome=None):
    """운영 검사와 강제 칩 거리 비교를 함께 통과한 보정본만 채택한다."""
    from . import cut_output_qc

    warnings, operating, stage2_chips = [], None, None

    async def operating_verdict(candidate):
        try:
            return await judge_operating(candidate)
        except Exception:
            warnings.append({"code": "cut_output_qc_unavailable"})
            return None

    checked_operating = operating_mode in {"shadow", "repair"}
    if checked_operating:
        operating = await operating_verdict(initial)
    first = await verdict(settings, profile, initial)
    if first is None:
        return initial, operating, warnings
    chip_route = repair_route(first)
    action_routes = {"EDIT_STAGE1", "REGENERATE_FROM_SCRATCH"}
    # 칩만 repair인 환경도 기존 compare_repair의 1차 근거를 한 번 확보한다.
    if chip_route in action_routes and not checked_operating and can_repair:
        operating = await operating_verdict(initial)
    operating_route = (cut_output_qc.repair_route(operating) if operating is not None
                       and (operating_mode == "repair" or chip_route in action_routes) else "KEEP_STAGE1")
    if operating_qc_outcome is not None:
        operating_qc_outcome.clear()
        operating_qc_outcome.update(operating or {})
    route = combine_routes(operating_route, chip_route)
    repair = {"attempted": False, "route": route, "accepted": False, "finalSource": "stage1"}
    chosen = initial
    has_operating_baseline = set((operating or {}).get("gates") or {}) == set(cut_output_qc.GATES)
    if route in action_routes and can_repair and not has_operating_baseline:
        repair["reason"] = "operating_qc_unavailable"
    if route in action_routes and can_repair and has_operating_baseline:
        repair["attempted"] = True
        try:
            operating_instructions = (cut_output_qc.repair_instructions(operating)
                                      if operating_route in action_routes else ())
            instructions = repair_instructions(first, operating_instructions)
            if route == "EDIT_STAGE1" and operating_qc_outcome is not None:
                operating_qc_outcome["fitChipQc"] = first
            repaired = await make_stage2(route, instructions)
            stage2_operating = await operating_verdict(repaired)
            stage2_chips = await verdict(settings, profile, repaired)
            comparison = (cut_output_qc.compare_repair(operating, stage2_operating)
                          if operating is not None and stage2_operating is not None
                          else {"accepted": False, "reason": "operating_qc_unavailable"})
            chip_comparison = (compare_distances(first, stage2_chips) if settings.cut_fit_chip_qc_mode == "repair"
                               else {"accepted": True, "regressions": []})
            accepted = comparison["accepted"] and chip_comparison["accepted"]
            repair.update(comparison, accepted=accepted, stage2Qc=stage2_operating,
                          operatingAccepted=comparison["accepted"], chipNonRegression=chip_comparison["accepted"],
                          chipRegressions=chip_comparison["regressions"])
            if accepted:
                chosen = repaired
                repair["finalSource"] = "stage2"
        except Exception:
            repair["error"] = "repair_unavailable"
            warnings.append({"code": "cut_output_qc_repair_unavailable"})
    result = {**(operating or {}), "fitChipQc": finalize(first, stage2_chips,
              attempted=repair["attempted"] and settings.cut_fit_chip_qc_mode == "repair", accepted=repair["accepted"])}
    if operating_mode == "repair" or settings.cut_fit_chip_qc_mode == "repair":
        result["repair"] = repair
    return chosen, result, warnings
