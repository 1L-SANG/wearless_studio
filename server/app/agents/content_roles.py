"""Storyboard content-role inference and canonicalization helpers.

``contentRole`` is an internal semantic source of truth.  The storyboard UI
does not ask the seller to choose it; the default composition and card order
assign it.  The rendering recipe (``cutType``/``direction``/``shot``) is
derived from it.  A missing role may be inferred defensively from ``cutType``;
retired kind values are not interpreted.
"""

CONTENT_ROLES = (
    "hero",
    "benefit",
    "coordination",
    "fit",
    "realWear",
    "productOverview",
    "detail",
    "custom",
)
SECTION_ROLES = ("benefit", "fit", "product")

CONTENT_ROLE_NAMES = {
    "hero": "첫 장면",
    "benefit": "핵심 장점",
    "coordination": "코디 활용",
    "fit": "핏 확인",
    "realWear": "실제 착용 느낌",
    "productOverview": "제품 전체",
    "detail": "디테일",
    "custom": "직접 구성",
}

_CONTENT_ROLE_TO_SECTION_ROLE = {
    "hero": "benefit",
    "benefit": "benefit",
    "coordination": "fit",
    "fit": "fit",
    "realWear": "fit",
    "productOverview": "product",
    "detail": "product",
}

_CONTENT_ROLE_RECIPES = {
    "hero": {"cutType": "styling", "direction": "front", "shot": "full"},
    "benefit": {"cutType": "horizon", "direction": "front", "shot": "medium"},
    "coordination": {"cutType": "styling", "direction": "front", "shot": "full"},
    "fit": {"cutType": "horizon", "direction": "front", "shot": "full"},
    "realWear": {"cutType": "mirror", "direction": None, "shot": "full"},
    "productOverview": {"cutType": "product", "direction": "front", "shot": "ghost"},
    "detail": {"cutType": "product", "direction": "front", "shot": "detail"},
}

_WORN_DIRECTIONS = ("front", "side", "back")
_WORN_SHOTS = ("full", "medium")
_PRODUCT_DIRECTIONS = ("front", "back")
_PRODUCT_OVERVIEW_SHOTS = ("ghost",)


def resolve_content_role(block: dict | None) -> str:
    """Return contentRole from the explicit field, source, or hidden recipe."""
    block = block or {}
    role = block.get("contentRole") or block.get("content_role")
    if role in CONTENT_ROLES:
        return role
    if block.get("source") == "mine":
        return "custom"
    cut_type = block.get("cutType") or block.get("cut_type")
    if cut_type == "mirror":
        return "realWear"
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
    # EditorBlock stores the same section-role value in kind.
    kind = block.get("kind")
    if kind in SECTION_ROLES:
        return kind
    inferred = _CONTENT_ROLE_TO_SECTION_ROLE.get(content_role or resolve_content_role(block))
    if inferred:
        return inferred
    return None


def canonicalize_storyboard_block(block: dict, *, for_storage: bool = False) -> dict:
    """Return a copy whose hidden cut recipe agrees with its content role.

    A valid explicit ``contentRole`` wins over contradictory recipe fields.
    When the role is absent, :func:`resolve_content_role` may infer it from
    ``cutType``. User-owned blocks clear the recipe; explicit ``custom`` blocks
    keep an existing recipe but never invent one.
    """
    if not isinstance(block, dict):
        return block

    out = dict(block)
    # taxonomy v2가 정규화 결과의 정본이다. StoryboardBlock 저장 shape에는
    # kind가 없으며, 비저장 경로에서도 EditorBlock의 sectionRole 동치만 남긴다.
    out["taxonomyVersion"] = 2
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
        return out

    explicit_role = block.get("contentRole") or block.get("content_role")
    role = explicit_role if explicit_role in CONTENT_ROLES else resolve_content_role(block)
    out["contentRole"] = role

    if role == "custom":
        section_role = resolve_section_role(block, role)
        if section_role:
            out["sectionRole"] = section_role
        cut_type = block.get("cutType") or block.get("cut_type")
        if cut_type in ("styling", "horizon", "mirror"):
            out["cutType"] = cut_type
            out.pop("cut_type", None)
            out["direction"] = None if cut_type == "mirror" else (
                block.get("direction") if block.get("direction") in _WORN_DIRECTIONS else "front"
            )
            out["shot"] = block.get("shot") if block.get("shot") in _WORN_SHOTS else "full"
        return out

    recipe = _CONTENT_ROLE_RECIPES[role]
    cut_type = recipe["cutType"]
    direction = recipe["direction"]
    shot = recipe["shot"]

    if cut_type == "mirror":
        if block.get("shot") in _WORN_SHOTS:
            shot = block["shot"]
    elif cut_type == "product":
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

    out.update({
        "sectionRole": _CONTENT_ROLE_TO_SECTION_ROLE[role],
        "cutType": cut_type,
        "direction": direction,
        "shot": shot,
    })
    if cut_type == "product":
        out["faceExposure"] = None
        out["matchIds"] = []
        out["outerClosureState"] = None
    elif cut_type == "mirror":
        face = block.get("faceExposure") or block.get("face_exposure")
        out["faceExposure"] = "show" if face == "show" else "hide"
    elif (block.get("faceExposure") or block.get("face_exposure")) not in ("same", "show", "hide"):
        out["faceExposure"] = "same"
    return out


def canonicalize_storyboard(blocks: list, *, for_storage: bool = False) -> list:
    """Canonicalize blocks and assign hidden roles from section/card order.

    Python's sort is stable, so the user's order inside each section is kept.
    Custom blocks without a section inherit their neighboring section;
    malformed non-dictionary entries remain at the end. The first AI image in
    the benefit section is the only ``hero``; later hero values are demoted to
    ``benefit``. AI custom/missing roles use the section's safe default.
    """
    defaults = {"benefit": "hero", "fit": "coordination", "product": "productOverview"}

    def canonicalize_for_storyboard(block):
        if not isinstance(block, dict):
            return block
        candidate = dict(block)
        declared_section = candidate.get("sectionRole") or candidate.get("section_role")
        if declared_section not in SECTION_ROLES and candidate.get("kind") in SECTION_ROLES:
            declared_section = candidate["kind"]

        # Storyboard-list 계약에서는 화면의 유효한 섹션이 정본이다. 단일 블록
        # 헬퍼의 defensive explicit-role 우선 규칙은 에디터 등 다른 소비처를
        # 위해 그대로 두고, 여기서만 섹션 안의 내부 역할을 다시 정한다.
        if candidate.get("source") != "mine" and declared_section in SECTION_ROLES:
            role = resolve_content_role(candidate)
            if role == "custom" or _CONTENT_ROLE_TO_SECTION_ROLE.get(role) != declared_section:
                role = defaults[declared_section]
            candidate["sectionRole"] = declared_section
            candidate["contentRole"] = role

        previous_recipe = (
            block.get("cutType") or block.get("cut_type"),
            block.get("direction"),
            block.get("shot"),
        )
        updated = canonicalize_storyboard_block(candidate, for_storage=for_storage)
        next_recipe = (updated.get("cutType"), updated.get("direction"), updated.get("shot"))
        recipe_incompatible = (
            previous_recipe[0] != next_recipe[0]
            or (previous_recipe[1] is not None and previous_recipe[1] != next_recipe[1])
            or (previous_recipe[2] is not None and previous_recipe[2] != next_recipe[2])
        )
        if recipe_incompatible:
            updated["exampleId"] = None
            if block.get("baseThumb") or block.get("thumb"):
                updated["thumb"] = block.get("baseThumb") or block.get("thumb")
            updated["baseThumb"] = None
        return updated

    canonical = [canonicalize_for_storyboard(block) for block in (blocks or [])]
    # Custom/mine cards without a semantic section inherit the preceding
    # section; leading cards inherit the next valid section (or benefit when
    # the board has no section at all). This mirrors frontend normalization.
    previous_role = None
    for block in canonical:
        if not isinstance(block, dict):
            continue
        if block.get("sectionRole") not in SECTION_ROLES and previous_role:
            block["sectionRole"] = previous_role
        if block.get("sectionRole") in SECTION_ROLES:
            previous_role = block["sectionRole"]
    next_role = None
    for block in reversed(canonical):
        if not isinstance(block, dict):
            continue
        if block.get("sectionRole") not in SECTION_ROLES:
            block["sectionRole"] = next_role or "benefit"
        next_role = block["sectionRole"]

    section_order = {"benefit": 0, "fit": 1, "product": 2}
    ordered = sorted(
        canonical,
        key=lambda block: section_order.get(block.get("sectionRole"), 3)
        if isinstance(block, dict) else 3,
    )

    hero_assigned = False
    out = []
    for block in ordered:
        if not isinstance(block, dict) or block.get("source") == "mine":
            out.append(block)
            continue

        section_role = block.get("sectionRole")
        role = block.get("contentRole")
        if role == "custom" or _CONTENT_ROLE_TO_SECTION_ROLE.get(role) != section_role:
            role = defaults.get(section_role, "hero")
        if section_role == "benefit":
            if not hero_assigned:
                role = "hero"
                hero_assigned = True
            elif role == "hero":
                role = "benefit"

        updated = canonicalize_for_storyboard({**block, "contentRole": role})
        out.append(updated)
    return out
