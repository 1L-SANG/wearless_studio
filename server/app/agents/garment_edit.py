"""고른 사진의 옷 색과 앞 여밈만 고친다. ADR-0020 품질 시험 문구를 유지한다."""
from . import face_identity, outer_closure, similar_cut
from .gemini_image import InlineImage, run_cpu_bound
from .model_routing import resolve_model


WORN_CUT_TYPES = ("styling", "horizon")
KEEP = (
    "Keep everything else exactly the same as the first image: the same person, face, hair, pose, hands, body shape, "
    "camera framing and angle, background, lighting, the inner top, the pants, bag and accessories, and the jacket's "
    "shape, fit, length, collar, seams, pockets and fabric texture. Do not add logos, text or new parts. Photorealistic."
)
COLOR = (
    "Edit the first image. Change ONLY the color of the jacket (the outer garment) so it matches the fabric and zipper "
    "color shown in the second image, which is a photo of the same jacket in the target color. Keep how open the "
    "zipper is exactly as in the first image. "
)
_PARTIAL = (
    "Edit the image. Change ONLY how far the jacket's front zipper is open. The jacket has ONE zipper with ONE slider. "
    "Put the single zipper slider {where}. Everything below the slider is zipped shut, the two front panels touching "
    "all the way down to the hem with no gap. Above the slider the front opens in a clean V up to the collar, and the "
    "inner top shows only inside that V. {depth} Use the jacket's existing zipper and collar. Keep the jacket color exactly the same. "
)
CLOSURE = {
    "closed": "Edit the image. Change ONLY how the jacket's front zipper is closed: zip it fully closed from the hem all the way up to the collar, so the inner top is covered. Use the jacket's existing zipper and collar, do not invent new hardware. Keep the jacket color exactly the same. ",
    "openToChest": "Edit the image. Change ONLY how far the jacket's front zipper is closed. The jacket has ONE zipper with ONE slider. The zipper must be closed continuously from the hem up to the middle of the chest (about a hand width below the collarbone), with the single slider sitting at the middle of the chest (about a hand width below the collarbone). Below the slider the two front panels are joined all the way down to the hem, with no gap and no second slider. Above the slider the front is open in a V up to the collar, showing the inner top only inside that V. Use the jacket's existing zipper and collar. Keep the jacket color exactly the same. ",
    "partial": _PARTIAL.format(where="level with the bottom of the breastbone (the solar plexus), clearly lower than the chest",
        depth="The V must be deep: it reaches about one third of the way from the collar to the hem."),
    "openToWaist": _PARTIAL.format(where="level with the navel, just above the tops of the side pockets",
        depth="The V must be very deep: it reaches about two thirds of the way from the collar to the hem, and only the lowest part near the hem stays zipped."),
    "open": "Edit the image. Change ONLY how the jacket's front is fastened: unzip it completely from the collar down to the hem so the two front panels hang open and the inner top is visible down the whole front. Use the jacket's existing zipper. Keep the jacket color exactly the same. ",
}

_BUTTON_KEEP = (
    "Use the jacket's existing {fasteners}, buttonholes and collar. Keep the number, size, color and spacing of the "
    "{fasteners} unchanged. Do not add any other type of fastening or new hardware. Keep the jacket color exactly the same. "
)
_BUTTON_PARTIAL = (
    "Edit the image. Change ONLY how far the jacket's front is fastened. Aim for the top of the fastened section {where}. "
    "Fasten only the existing {fasteners}. If there is no fastener exactly at the requested height, use the existing "
    "fastener closest to the requested height to make the V as close as possible to the requested depth. "
    "Fasten all the existing {fasteners} from the hem up to the chosen fastener, and unfasten all the {fasteners} "
    "above that height. If there are no {fasteners} at or below the requested height, unfasten all of them and open "
    "the entire front. Do not create or move {fasteners}. In any fastened section the two front panels overlap "
    "all the way down to the hem with no gap. Above the chosen fastener the front opens in a clean V up to the "
    "collar, and the inner top shows only inside that V. {depth}Let the existing fastener positions determine the "
    "natural opening rather than forcing an exact V depth. "
)
BUTTON_CLOSURE = {
    "closed": (
        "Edit the image. Change ONLY how the jacket's front is fastened. Fasten all the existing {fasteners}, from the "
        "lowest to the highest one, even if the highest one is below the collar. The two front panels overlap and "
        "close with no gap where they are fastened. Do not create or move {fasteners}. "
    ),
    "openToChest": _BUTTON_PARTIAL.format(fasteners="{fasteners}",
        where="at the middle of the chest (about a hand width below the collarbone)", depth=""),
    "partial": _BUTTON_PARTIAL.format(fasteners="{fasteners}",
        where="level with the bottom of the breastbone (the solar plexus), clearly lower than the chest",
        depth="Aim for a deep V reaching about one third of the way from the collar to the hem. "),
    "openToWaist": _BUTTON_PARTIAL.format(fasteners="{fasteners}",
        where="level with the navel, just above the tops of the side pockets",
        depth="Aim for a very deep V reaching about two thirds of the way from the collar to the hem. "),
    "open": (
        "Edit the image. Change ONLY how the jacket's front is fastened. Unfasten all the {fasteners} so the two front "
        "panels hang open from the collar down to the hem and the inner top is visible down the whole front. "
        "Keep the {fasteners} visible along the front edges. "
    ),
}
_BELT_KEEP = (
    "Use the jacket's existing belt. Do not add any other type of fastening or new hardware. "
    "Keep the jacket color exactly the same. "
)
BELT_CLOSURE = {
    "closed": (
        "Edit the image. Change ONLY how the jacket's front is fastened. Tie the existing belt at the waist so the two "
        "front panels overlap and close. "
    ) + _BELT_KEEP,
    "open": (
        "Edit the image. Change ONLY how the jacket's front is fastened. Untie the existing belt so the two front panels "
        "hang open and the belt ends hang loose. "
    ) + _BELT_KEEP,
}
_FRONT_COLOR = (
    "Edit the first image. Change ONLY the color of the jacket (the outer garment) so it matches the fabric color shown "
    "in the second image, which is a photo of the same jacket in the target color. "
    "Keep how open or fastened the front is exactly as in the first image. "
)
_INNER_DEFAULT = (
    " Keep any inner top already visible in the image unchanged. "
    "If the inner top is not visible in any image, show a plain white crew-neck T-shirt with no print or text."
)
_INNER_REFERENCE = (
    " The second image is a photograph of the same person before the edits. Use it only as a reference for the inner top. "
    "If the inner top is already visible in the first image, keep that same inner top unchanged. "
    "Otherwise, draw the newly revealed inner top as the same garment visible in the second image, with the same type, color and neckline. "
    "If the inner top is not visible in any image, show a plain white crew-neck T-shirt with no print or text. "
    "Do not follow the jacket color, front opening, pose, background or any other details from the second image."
)


def uses_inner_reference(mode: str, clothing_type: str, closure_state: str | None, front_closure: dict) -> bool:
    """두 번째 사진은 실제로 아우터를 열 때만 이너 근거로 사용한다."""
    return (mode == "closure" and clothing_type in ("outer", "아우터")
            and outer_closure.effective_state(closure_state, front_closure) != "closed")


def trusted_cut_type(asset: dict, record: dict) -> str | None:
    """실제 생성 때 쓴 종류를 우선한다. 옛 콘티의 나중 변경은 자산 종류를 바꾸지 않는다."""
    return (asset.get("metadata") or {}).get("cut_type") or record.get("cutType")


def source_cut_type(asset: dict | None, record: dict) -> str | None:
    if not asset:
        return None
    if asset.get("source") == "upload":
        return "styling"
    metadata = asset.get("metadata") or {}
    cut_type = trusted_cut_type(asset, record)
    if asset.get("source") == "ai" and cut_type in WORN_CUT_TYPES and not metadata.get("detail_recipe"):
        return cut_type
    return None


def current_values(record: dict, analysis: dict) -> dict:
    recipe = record.get("recipe") or {}
    upload = record.get("upload") is True
    state = recipe.get("outerClosureState") if not upload else None
    return {
        "colorId": record.get("colorId") if upload else recipe.get("colorId") or record.get("colorId"),
        "outerClosureState": outer_closure.effective_state(state, outer_closure.front_closure_of(analysis))
            if isinstance(state, str) and state in outer_closure.STATES else None,
        "direction": recipe.get("direction") if not upload and recipe.get("direction") in ("front", "back") else None,
        "detailTargetId": recipe.get("detailTargetId") if not upload else None,
    }


def target_image_ids(product: dict, color_id: object) -> list[str] | None:
    """None은 모르는 색, 빈 목록은 사진 없는 색. 앞면이 있으면 먼저 읽는다."""
    color = next((c for c in product.get("colors") or []
                  if isinstance(color_id, str) and c.get("id") == color_id), None)
    if color is None:
        return None
    images = [img for img in color.get("images") or [] if isinstance(img, dict) and img.get("id")]
    return [img["id"] for img in sorted(images, key=lambda img: img.get("slot") != "Front")]


def prompt_for(mode: str, *, clothing_type: str, closure_state: str | None, front_closure: dict,
               has_inner_reference: bool = False) -> str:
    noun = {"outer": "jacket", "아우터": "jacket", "top": "top", "상의": "top",
            "bottom": "pants", "하의": "pants", "dress": "dress", "원피스": "dress"}.get(clothing_type, "garment")
    possessive = "pants'" if noun == "pants" else f"{noun}'s"
    keep, color = KEEP, COLOR
    if noun != "jacket":
        keep = keep.replace("the inner top, the pants", "the other clothing")
        feature = {"top": ", neckline", "pants": ", waistband", "dress": ", neckline"}.get(noun, "")
        keep = keep.replace(", collar", feature)
        color = color.replace("(the outer garment)", "(the product being sold)")
        color = color.replace("fabric and zipper color", "fabric color").replace("Keep how open the zipper is exactly as in the first image. ", "")
    kind = front_closure.get("kind")
    if mode == "color":
        if noun == "jacket" and kind in ("button", "snap", "belt"):
            color = _FRONT_COLOR
        prompt = color + keep
    else:
        if kind in ("button", "snap"):
            fasteners = "buttons" if kind == "button" else "snap buttons"
            hardware_keep = _BUTTON_KEEP if kind == "button" else _BUTTON_KEEP.replace(", buttonholes", "")
            closure = (BUTTON_CLOSURE[closure_state] + hardware_keep).format(fasteners=fasteners)
        elif kind == "belt":
            # 벨트 중간 값이 직접 들어와도 저장/API 계약의 두 단계 규칙을 따른다.
            state = outer_closure.effective_state(closure_state, {**front_closure, "range": "binary"})
            closure = BELT_CLOSURE[state]
            closure_state = state
        else:
            closure = CLOSURE[closure_state]
        secondary = front_closure.get("secondary")
        if secondary:
            closure += (f"Operate the existing secondary {secondary} fastening to the same height and in the same way "
                        "as the main fastening. ")
        opening = uses_inner_reference(mode, clothing_type, closure_state, front_closure)
        prompt = closure + (keep if opening and has_inner_reference else keep.replace("first image", "image"))
        if opening:
            prompt += _INNER_REFERENCE if has_inner_reference else _INNER_DEFAULT
    prompt = prompt.replace("jacket's", possessive).replace("jacket", noun)
    return prompt


async def generate(settings, gemini, source_image: InlineImage, target_image: InlineImage | None,
                   cut_type: str, *, mode: str, clothing_type: str, closure_state: str | None,
                   front_closure: dict, inner_reference_image: InlineImage | None = None,
                   face_identity_spec=None, face_pass_outcome=None,
                   face_pass_url_provider=None) -> tuple[bytes, str]:
    model = resolve_model(settings, "image_high")
    if not uses_inner_reference(mode, clothing_type, closure_state, front_closure):
        inner_reference_image = None
    prompt = prompt_for(mode, clothing_type=clothing_type, closure_state=closure_state, front_closure=front_closure,
                        has_inner_reference=inner_reference_image is not None)
    images = [source_image] + ([target_image] if target_image is not None else [])
    if inner_reference_image is not None:
        images.append(inner_reference_image)
    image_size = getattr(settings, "detail_cut_image_size", None) or settings.mannequin_image_size
    size_options = {}
    if model.startswith("gpt-image"):
        size_options["openai_size_override"] = await run_cpu_bound(similar_cut.openai_source_size, source_image, image_size)
    result = await gemini.generate_content_image(model, prompt, images, image_size, aspect_ratio=None, **size_options)
    image, mime = result.image, result.mime
    if (face_identity_spec is not None and getattr(settings, "face_identity_enabled", False)
            and cut_type in WORN_CUT_TYPES):
        image, mime = await face_identity.apply_face_pass(settings, image, mime, face_identity_spec,
                                                        outcome=face_pass_outcome, url_provider=face_pass_url_provider)
    return image, mime
