"""Storyboard content-role inference and canonicalization helpers.

``contentRole`` is internal and never selected by the seller. ``cutType`` and
product ``shot`` are user-facing recipe choices; the current section, card
order and those choices keep the hidden role aligned. A missing role may be
inferred defensively from ``cutType``; retired kind values are not interpreted.
"""

from . import horizon_background, product_photography

CONTENT_ROLES = (
    "hero",
    "benefit",
    "coordination",
    "fit",
    "productOverview",
    "detail",
    "custom",
)
SECTION_ROLES = ("hooking", "styling", "studio", "product")
_LEGACY_SECTION_ROLES = ("benefit", "fit")

CONTENT_ROLE_NAMES = {
    "hero": "첫 장면",
    "benefit": "핵심 장점",
    "coordination": "코디 활용",
    "fit": "핏 확인",
    "productOverview": "제품 전체",
    "detail": "디테일",
    "custom": "직접 구성",
}

_CONTENT_ROLE_TO_SECTION_ROLE = {
    "hero": "hooking",
    "benefit": "hooking",
    "coordination": "styling",
    "fit": "studio",
    "productOverview": "product",
    "detail": "product",
}

_CONTENT_ROLE_RECIPES = {
    "hero": {"cutType": "styling", "direction": "front", "shot": "full"},
    "benefit": {"cutType": "horizon", "direction": "front", "shot": "medium"},
    "coordination": {"cutType": "styling", "direction": "front", "shot": "full"},
    "fit": {"cutType": "horizon", "direction": "front", "shot": "full"},
    "productOverview": {"cutType": "product", "direction": "front", "shot": "ghost"},
    "detail": {"cutType": "product", "direction": "front", "shot": "detail"},
}

_WORN_DIRECTIONS = ("front", "side", "back")
#: direction="side" 의 두 갈래 — cut_generator.SIDE_STYLES 와 같은 값이어야 한다.
#: 순환 import 를 피하려고 여기서 다시 쓰고, 계약 시험이 두 값을 맞춰 둔다.
_SIDE_STYLES = ("profile", "threeQuarter")
_WORN_SHOTS = ("full", "medium")
_PRODUCT_DIRECTIONS = ("front", "back")
_PRODUCT_OVERVIEW_SHOTS = ("ghost",)
_FIT_ROLE_BY_CUT_TYPE = {
    "styling": "coordination",
    "horizon": "fit",
}

# 자산의 실제 생성 설정과 원본 설정 조회가 함께 쓰는 저장 필드다.
GENERATION_RECIPE_FIELDS = (
    "sectionRole", "contentRole", "cutType", "shot", "direction", "sideStyle", "colorId",
    "faceExposure", "modelId", "matchIds", "matchIdsOrigin", "refAssetIds", "refScope",
    "exampleId", "exampleSelectionOrigin", "outerClosureState", "detailTargetId",
    "detailTargetOrigin", "spaceGroupId", "spaceVariation", "spaceSetMemberOrder",
    "horizonBackgroundMode", "outfitSelectionsSnapshot", "outfitReferenceAssets", "productPresentation",
)

_SIMILAR_RECIPE_FIELDS = (
    "cutType", "direction", "sideStyle", "shot", "colorId", "colorIds", "faceExposure",
    "modelId", "matchIds", "matchIdsOrigin", "refAssetIds", "refImages", "refScope",
    "exampleId", "exampleSelectionOrigin", "outerClosureState", "horizonBackgroundMode",
    "angle", "thumb", "previewThumb", "baseThumb", "outfitSelectionsSnapshot", "outfitReferenceAssets",
)


def validate_similar_cut_relationships(blocks: list) -> None:
    """A follow-up references exactly one ordinary worn AI card in this board."""
    by_id = {}
    for block in blocks or []:
        if isinstance(block, dict) and block.get("id") is not None:
            by_id.setdefault(str(block["id"]), []).append(block)
    for block in blocks or []:
        if not isinstance(block, dict) or block.get("similarSourceBlockId") is None:
            continue
        source_id = block["similarSourceBlockId"]
        candidates = by_id.get(source_id, []) if isinstance(source_id, str) else []
        source = candidates[0] if len(candidates) == 1 else {}
        if (
            not isinstance(source_id, str) or not source_id.strip()
            or not block.get("id") or str(block["id"]) == source_id
            or len(by_id.get(str(block.get("id")), [])) != 1
            or block.get("source") != "ai" or resolve_content_role(block) == "custom"
            or source.get("source") != "ai" or source.get("similarSourceBlockId") is not None
            or resolve_content_role(source) in ("custom", "productOverview", "detail")
            or (source.get("cutType") or source.get("cut_type")) not in ("styling", "horizon")
        ):
            raise ValueError("invalid_similar_cut_relationship")


def inherit_similar_cut_recipes(blocks: list) -> list:
    """Use canonical source settings while preserving the follow-up's section and layout."""
    by_id = {str(b.get("id")): b for b in blocks if isinstance(b, dict)}
    result = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("similarSourceBlockId") is None:
            result.append(block)
            continue
        source = by_id[block["similarSourceBlockId"]]
        out = dict(block)
        for key in _SIMILAR_RECIPE_FIELDS:
            out.pop(key, None)
            if key in source:
                out[key] = source[key]
        for key in list(out):
            if key.startswith(("space", "hook", "colorway")):
                out.pop(key, None)
        for key in ("spaceGroupId", "spaceVariation"):
            if key in source:
                out[key] = source[key]
        for key in ("sectionId", "sectionTitle", "pageSectionKey", "pageSectionKind", "pageSectionUsage", "pageDesignId"):
            if key in source:
                out[key] = source[key]
            else:
                out.pop(key, None)
        out["slotKey"] = None
        # The source owns section membership; a follow-up never becomes the hero.
        out["contentRole"] = (
            "benefit" if source.get("pageSectionKind") == "first"
            else _FIT_ROLE_BY_CUT_TYPE[source["cutType"]]
        )
        result.append(out)
    return result


def _canonicalize_example_selection(out: dict) -> dict:
    """Normalize the persisted example selection without leaving orphan metadata."""
    from .reference_mode import reference_scope
    if 'refScope' in out or 'ref_scope' in out or out.get('spaceGroupId') or out.get('space_group_id'):
        out['refScope'] = reference_scope(out)
    out.pop('ref_scope', None)
    presentation = product_photography.normalize(
        out.get("productPresentation"), cut_type=out.get("cutType"), shot=out.get("shot"))
    out.pop("productPresentation", None)
    if presentation is not None:
        out["productPresentation"] = presentation
    example_id = out.get("exampleId") or out.get("example_id")
    origin = out.get("exampleSelectionOrigin")
    if origin is not None and origin not in ("auto", "user"):
        raise ValueError("invalid_example_selection_origin")
    out.pop("example_id", None)
    if example_id:
        if origin is None:
            origin = "user"  # legacy selections are protected from automatic replacement
        out["exampleId"] = example_id
        out["exampleSelectionOrigin"] = origin
        return out

    out["exampleId"] = None
    out["exampleSelectionOrigin"] = None
    if out.get("baseThumb") is not None:
        out["thumb"] = out.get("baseThumb")
        out["baseThumb"] = None
    return out


def validate_storyboard_example_references(
    blocks: list, *, assets: dict[str, dict], clothing_type: str, gender: str,
) -> tuple[str, str, dict] | None:
    """Validate stable ID/applicability; shot/direction may differ outside same-space pose use.

    실패 시 meta에 문제의 exampleId를 실어 준다 — 카탈로그 발행 회전 뒤 클라이언트가
    (자기 카탈로그 기준으론 유효해 보여도) 정확히 그 선택만 걷어내고 재저장할 수 있게.
    """
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("exampleId"):
            continue
        example_id = str(block["exampleId"])
        meta = {"exampleId": example_id}
        entry = assets.get(example_id)
        if not entry or not entry.get("all"):
            return "unknown_example_id", "저장된 생성예시를 찾을 수 없어요. 다른 예시를 골라주세요.", meta
        if clothing_type not in (entry.get("applicableClothingTypes") or []):
            return "example_not_applicable", "상품 조건에 맞지 않는 생성예시예요. 다른 예시를 골라주세요.", meta
        if entry.get("cutType") != block.get("cutType"):
            return "example_cut_mismatch", "컷 종류에 맞지 않는 생성예시예요. 다른 예시를 골라주세요.", meta
        expected_gender = None if block.get("cutType") == "product" else gender
        if entry.get("gender") != expected_gender:
            return "example_gender_mismatch", "모델 조건에 맞지 않는 생성예시예요. 다른 예시를 골라주세요.", meta
    return None


def _role_for_selected_recipe(block: dict, role: str) -> str:
    """Align a hidden role with the seller-facing cut/shot choice."""
    cut_type = block.get("cutType") or block.get("cut_type")
    if role in ("coordination", "fit") and cut_type in _FIT_ROLE_BY_CUT_TYPE:
        return _FIT_ROLE_BY_CUT_TYPE[cut_type]
    if role in ("productOverview", "detail") and cut_type == "product":
        return "detail" if block.get("shot") == "detail" else "productOverview"
    return role


def resolve_content_role(block: dict | None) -> str:
    """Return contentRole from the explicit field, source, or hidden recipe."""
    block = block or {}
    role = block.get("contentRole") or block.get("content_role")
    if role in CONTENT_ROLES:
        return role
    if block.get("source") == "text":
        return None
    if block.get("source") == "mine":
        return "custom"
    cut_type = block.get("cutType") or block.get("cut_type")
    if cut_type == "product":
        return "detail" if block.get("shot") == "detail" else "productOverview"
    if cut_type == "horizon":
        return "fit"
    if cut_type == "styling":
        return "coordination"
    return "custom"


def resolve_section_role(block: dict | None, content_role: str | None = None) -> str | None:
    """Return a valid sectionRole or infer it from the canonical content role."""
    block = block or {}
    role = block.get("sectionRole") or block.get("section_role")
    if role in SECTION_ROLES:
        return role
    if role == "benefit":
        return "hooking"
    if role == "fit":
        return "studio" if (
            (block.get("cutType") or block.get("cut_type")) == "horizon"
            or (content_role or block.get("contentRole") or block.get("content_role")) == "fit"
        ) else "styling"
    # EditorBlock stores the same section-role value in kind.
    kind = block.get("kind")
    if kind in SECTION_ROLES:
        return kind
    if kind in _LEGACY_SECTION_ROLES:
        return resolve_section_role({**block, "sectionRole": kind}, content_role)
    inferred = _CONTENT_ROLE_TO_SECTION_ROLE.get(content_role or resolve_content_role(block))
    if inferred:
        return inferred
    return None


def canonicalize_storyboard_block(block: dict, *, for_storage: bool = False) -> dict:
    """Return a copy whose hidden cut recipe agrees with its content role.

    A valid explicit ``contentRole`` supplies section semantics. Within that
    section, a valid seller-selected ``cutType`` (or product ``shot``) wins and
    realigns the hidden role. When the role is absent,
    :func:`resolve_content_role` may infer it from ``cutType``. User-owned
    blocks clear the recipe; explicit ``custom`` blocks keep an existing recipe
    but never invent one.
    """
    if not isinstance(block, dict):
        return block

    if block.get("taxonomyVersion") == 4:
        return _canonicalize_v4_block(block)
    out = dict(block)
    for retired in ("pose", "poseLabel", "poseThumb", "pose_label", "pose_thumb"):
        out.pop(retired, None)
    # taxonomy v3가 정규화 결과의 정본이다. StoryboardBlock 저장 shape에는
    # kind가 없으며, 비저장 경로에서도 EditorBlock의 sectionRole 동치만 남긴다.
    # Standalone editor generation also uses this recipe helper.
    out.pop("taxonomyVersion", None)
    out.pop("taxonomy_version", None)
    if for_storage or out.get("kind") not in SECTION_ROLES:
        out.pop("kind", None)
    if block.get("source") == "mine":
        out["contentRole"] = "custom"
        out["cutType"] = None
        out.pop("cut_type", None)
        section_role = resolve_section_role(block)
        if section_role:
            out["sectionRole"] = section_role
        return _canonicalize_example_selection(out)

    requested_cut_type = block.get("cutType") or block.get("cut_type")
    if requested_cut_type and requested_cut_type not in ("styling", "horizon", "product"):
        raise ValueError("unknown_cut_type")

    explicit_role = block.get("contentRole") or block.get("content_role")
    role = explicit_role if explicit_role in CONTENT_ROLES else resolve_content_role(block)
    role = _role_for_selected_recipe(block, role)
    out["contentRole"] = role

    if role == "custom":
        section_role = resolve_section_role(block, role)
        if section_role:
            out["sectionRole"] = section_role
        cut_type = block.get("cutType") or block.get("cut_type")
        if cut_type in ("styling", "horizon"):
            out["cutType"] = cut_type
            out.pop("cut_type", None)
            out["direction"] = (
                block.get("direction") if block.get("direction") in _WORN_DIRECTIONS else "front"
            )
            out["shot"] = block.get("shot") if block.get("shot") in _WORN_SHOTS else "full"
        return _canonicalize_example_selection(out)

    recipe = _CONTENT_ROLE_RECIPES[role]
    requested_cut_type = block.get("cutType") or block.get("cut_type")
    cut_type = (
        requested_cut_type
        if role in ("hero", "benefit") and requested_cut_type in ("styling", "horizon")
        else recipe["cutType"]
    )
    direction = recipe["direction"]
    shot = recipe["shot"]

    if cut_type == "product":
        if block.get("direction") in _PRODUCT_DIRECTIONS:
            direction = block["direction"]
        if role == "detail":
            shot = "detail"
        elif block.get("shot") == "flatlay":
            shot = "ghost"
        elif block.get("shot") in _PRODUCT_OVERVIEW_SHOTS:
            shot = block["shot"]
    else:
        if block.get("direction") in _WORN_DIRECTIONS:
            direction = block["direction"]
        if block.get("shot") in _WORN_SHOTS:
            shot = block["shot"]

    example_direction = _example_direction(block, cut_type)
    if example_direction:
        direction = example_direction["direction"]
    out.update({
        "sectionRole": _CONTENT_ROLE_TO_SECTION_ROLE[role],
        "cutType": cut_type,
        "direction": direction,
        "shot": shot,
    })
    # 옆모습 컷의 얼굴 경로(진짜 옆모습 ↔ 사선). 옆이 아니면 뜻이 없으니 지운다 —
    # 남겨 두면 방향을 바꾼 카드에 옛 값이 따라다닌다(cut_generator.SIDE_STYLES).
    side_style = (example_direction or block).get("sideStyle") or block.get("side_style")
    out.pop("side_style", None)
    out["sideStyle"] = side_style if (direction == "side" and side_style in _SIDE_STYLES) else None
    if cut_type == "product":
        out["faceExposure"] = None
        out["matchIds"] = []
        out["outerClosureState"] = None
    elif (block.get("faceExposure") or block.get("face_exposure")) not in ("same", "show", "hide"):
        out["faceExposure"] = "same"
    return _canonicalize_example_selection(out)


def _example_direction(block: dict, cut_type: str) -> dict | None:
    """착용컷의 발행된 예시 방향만 읽는다. 제품컷은 원본 사진의 면을 따른다."""
    example_id = block.get("exampleId") or block.get("example_id")
    if cut_type not in {"styling", "horizon"} or not example_id or str(example_id).startswith("sig_"):
        return None
    from . import cut_generator

    if str(example_id).startswith("ss_"):
        from . import space_set_assets
        _base, sets = space_set_assets.load_space_set_registry()
        entry = next((member for parent in sets.values() for member in parent["members"]
                      if member["exampleId"] == example_id), {})
    else:
        _base, entries = cut_generator.load_example_asset_registry()
        entry = entries.get(str(example_id)) or {}
    if entry.get("cutType", cut_type) != cut_type or entry.get("direction") not in _WORN_DIRECTIONS:
        return None
    return {
        "direction": entry["direction"],
        "sideStyle": (entry.get("sideStyle") or "profile") if entry["direction"] == "side" else None,
    }




def _v4_section(block):
    from .page_templates import CATALOG, get_template, section_definition
    key = block.get("pageSectionKey") or ""
    template = get_template(key.split(".")[0])
    kind = block.get("pageSectionKind")
    if template is None or kind not in CATALOG["kinds"] or not key.startswith(template["id"] + ".") or not key.split(".", 1)[1]:
        raise ValueError("invalid_storyboard_section")
    section = section_definition(template, key, kind, block.get("pageSectionUsage"), block.get("pageDesignId"))
    if CATALOG["designs"].get(block.get("pageDesignId"), {}).get("kind") != kind:
        raise ValueError("invalid_storyboard_design")
    if (kind == "wear" and block.get("pageSectionUsage") not in ("styling", "fit")) or (kind != "wear" and block.get("pageSectionUsage") is not None):
        raise ValueError("invalid_storyboard_usage")
    return section


def _v4_slot(block, section):
    import re
    key = str(block.get("slotKey") or "")
    prefix = section["instanceKey"] + "."
    if key.startswith(prefix):
        key = key[len(prefix):]
    slot = next((slot for slot in section.get("photoSlots", [])
                 if (key == slot["slotKey"] or key.startswith(slot["slotKey"] + ".") or re.fullmatch(re.escape(slot["slotKey"]) + r"\d+", key))), None)
    if slot and block.get("pageSectionKind") == "wear" and block.get("pageSectionUsage") == "styling":
        return {**slot, "recipe": {**(slot.get("recipe") or {}), "allowedCutTypes": ["styling", "horizon"]}}
    return slot


def _canonicalize_v4_block(block):
    section = _v4_section(block)
    out = dict(block)
    for key in list(out):
        if key.startswith(("hook", "layoutRow", "colorway")) or key in ("pose", "poseLabel", "poseThumb", "kind"):
            out.pop(key, None)
    out["sectionLayout"] = "stack"
    source = out.get("source")
    if source == "text":
        forbidden = (set(GENERATION_RECIPE_FIELDS) - {"sectionRole", "contentRole", "cutType"}) | {
            "refImages", "ownImages", "similarSourceBlockId", "colorIds", "angle", "exampleChoice",
            "setSelectionOrigin", "thumb", "previewThumb", "baseThumb", "ref_scope", "space_set_member_order",
        }
        if any(key in block for key in forbidden) or any(block.get(key) is not None for key in ("cutType", "contentRole", "sectionRole", "slotKey")):
            raise ValueError("invalid_storyboard_text_card")
        out.update(cutType=None, contentRole=None, sectionRole=None, slotKey=None)
        return out
    if source == "mine":
        out.update(contentRole="custom", cutType=None, sectionRole=None)
        for key in ("exampleId", "exampleSelectionOrigin", "spaceGroupId", "spaceVariation", "spaceSetMemberOrder", "refImages", "refAssetIds", "productPresentation"):
            out.pop(key, None)
        return out
    if source != "ai":
        raise ValueError("invalid_storyboard_source")
    slot = _v4_slot(out, section)
    if out.get("slotKey") and not slot:
        raise ValueError("invalid_storyboard_slot")
    recipe = (slot or {}).get("recipe") or {}
    cut_type = out.get("cutType")
    if recipe and cut_type not in recipe["allowedCutTypes"]:
        cut_type = recipe["cutType"]
    if cut_type not in ("styling", "horizon", "product"):
        raise ValueError("unknown_cut_type")
    shot = out.get("shot")
    if recipe and shot not in recipe["allowedShots"]:
        shot = recipe["shot"]
    worn = cut_type in ("styling", "horizon")
    if shot not in (_WORN_SHOTS if worn else ("ghost", "detail")):
        shot = "full" if worn else "ghost"
    changed = cut_type != block.get("cutType") or shot != block.get("shot")
    out.update(cutType=cut_type, shot=shot)
    if changed:
        out.update(exampleId=None, exampleSelectionOrigin=None, thumb=block.get("baseThumb") or block.get("thumb"), baseThumb=None, previewThumb=None)
    direction = out.get("direction")
    if direction not in (_WORN_DIRECTIONS if worn else _PRODUCT_DIRECTIONS):
        direction = recipe.get("direction") or "front"
    example = _example_direction(out, cut_type)
    if example:
        direction = example["direction"]
    role = slot["contentRole"] if slot else ("detail" if shot == "detail" else "productOverview") if not worn else _FIT_ROLE_BY_CUT_TYPE[cut_type]
    if out.get("pageSectionKind") == "first" and worn and block.get("contentRole") in ("hero", "benefit"):
        role = block["contentRole"]
    out.update(contentRole=role, sectionRole=_CONTENT_ROLE_TO_SECTION_ROLE.get(role), direction=direction)
    side_style = (example or out).get("sideStyle")
    out["sideStyle"] = side_style if direction == "side" and side_style in _SIDE_STYLES else None
    if not worn:
        out.update(faceExposure=None, matchIds=[], outerClosureState=None)
    elif out.get("faceExposure") not in ("same", "show", "hide"):
        out["faceExposure"] = recipe.get("faceExposure") or "same"
    return _canonicalize_example_selection(out)


def is_worn_crop_source(block):
    if not block or block.get("pageSectionKind") != "wear":
        return False
    if block.get("source") == "mine":
        images = block.get("ownImages") or []
        return isinstance(images, list) and bool(images) and isinstance(images[0], str) and bool(images[0])
    return (block.get("source") == "ai" and block.get("cutType") in ("styling", "horizon")
            and block.get("shot") in ("full", "medium"))


def validate_storyboard_v4(blocks):
    from .page_templates import CATALOG
    if not isinstance(blocks, list) or any(not isinstance(b, dict) or b.get("taxonomyVersion") != 4 for b in blocks):
        raise ValueError("storyboard_outdated")
    seen, section_ids, by_id, runs = set(), {}, {}, []
    template_id = None
    kinds = {}
    for block in blocks:
        if not isinstance(block.get("id"), str) or not block["id"] or block["id"] in by_id:
            raise ValueError("invalid_storyboard_block_id")
        by_id[block["id"]] = block
        _v4_section(block)
        key, sid = block["pageSectionKey"], block.get("sectionId")
        if not isinstance(sid, str) or not sid:
            raise ValueError("invalid_storyboard_section")
        current_template = key.split(".")[0]
        if template_id is not None and current_template != template_id:
            raise ValueError("mixed_storyboard_templates")
        template_id = current_template
        if sid in section_ids and section_ids[sid] != key:
            raise ValueError("invalid_storyboard_section")
        section_ids[sid] = key
        if not runs or runs[-1][0]["pageSectionKey"] != key:
            if key in seen:
                raise ValueError("noncontiguous_storyboard_section")
            if block["pageSectionKind"] == "first" and runs:
                raise ValueError("invalid_storyboard_first")
            seen.add(key)
            kind = block["pageSectionKind"]
            kinds[kind] = kinds.get(kind, 0) + 1
            if not CATALOG["kinds"][kind]["repeatable"] and kinds[kind] > 1:
                raise ValueError("repeated_storyboard_section")
            runs.append([])
        first = runs[-1][0] if runs[-1] else block
        if any(block.get(field) != first.get(field) for field in ("sectionId", "pageSectionKind", "pageSectionUsage", "pageDesignId")):
            raise ValueError("invalid_storyboard_section")
        runs[-1].append(block)
    for run in runs:
        kind = run[0]["pageSectionKind"]
        text = [b for b in run if b.get("source") == "text"]
        photos = [b for b in run if b.get("source") != "text"]
        policy = CATALOG["kinds"][kind]["storyboardTextCard"]
        if policy == "only" and photos:
            section = _v4_section(run[0])
            slots = {slot["slotKey"] for slot in section.get("photoSlots", []) if slot["fill"] == "reuse"}
            prefix = section["instanceKey"] + "."
            if all(b.get("source") in ("ai", "mine") and str(b.get("slotKey") or "").removeprefix(prefix) in slots for b in photos):
                policy = "lead"
        if kind == "fabric" and not photos:
            policy = "only"
        if len(text) > 1 or (policy == "none" and text) or (policy == "only" and (len(text) != 1 or photos)) or (policy == "lead" and (not text or run[0] is not text[0])):
            raise ValueError("invalid_storyboard_text_card")
        for lead in text:
            items = lead.get("pointItems")
            if items is None:
                continue
            if kind != "points" or not isinstance(items, list):
                raise ValueError("invalid_storyboard_point_items")
            for item in items:
                if not isinstance(item, dict) or item.get("kind") not in ("detail", "wornCrop") or not isinstance(item.get("sourceBlockId"), str):
                    raise ValueError("invalid_storyboard_point_items")
                source = by_id.get(item["sourceBlockId"])
                # A removed original remains a hidden item, never a new generation request.
                if item["kind"] == "detail":
                    if source and (source.get("pageSectionKey") != lead["pageSectionKey"] or source.get("source") != "ai" or source.get("shot") != "detail"):
                        raise ValueError("invalid_storyboard_point_items")
                else:
                    region = item.get("cropRegion")
                    if (item.get("bodyPart") not in ("shoulder", "neckline", "length", "silhouette", "waistline")
                        or not isinstance(region, list) or len(region) != 4 or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in region)
                        or region[2] <= 0 or region[3] <= 0 or region[0] + region[2] > 1.00001 or region[1] + region[3] > 1.00001
                        or source and not is_worn_crop_source(source)):
                        raise ValueError("invalid_storyboard_point_items")
    validate_similar_cut_relationships(blocks)


def canonicalize_storyboard(blocks: list, *, for_storage: bool = False) -> list:
    """v4 preserves section runs and page order. It never converts an old board."""
    validate_storyboard_v4(blocks)
    canonical, hero_assigned = [], False
    for block in blocks:
        updated = _canonicalize_v4_block(dict(block))
        if updated.get("source") == "ai" and updated.get("pageSectionKind") == "first" and updated.get("cutType") in ("styling", "horizon"):
            updated["contentRole"] = "benefit" if hero_assigned or updated.get("similarSourceBlockId") else "hero"
            updated["sectionRole"] = "hooking"
            if not updated.get("similarSourceBlockId"):
                hero_assigned = True
        canonical.append(horizon_background.normalize_block(updated) if updated.get("source") == "ai" else updated)
    out = inherit_similar_cut_recipes(canonical)
    validate_storyboard_v4(out)
    return out
