"""Storyboard content-role inference and canonicalization helpers.

``contentRole`` is internal and never selected by the seller. ``cutType`` and
product ``shot`` are user-facing recipe choices; the current section, card
order and those choices keep the hidden role aligned. A missing role may be
inferred defensively from ``cutType``; retired kind values are not interpreted.
"""

from . import horizon_background

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
SECTION_ROLES = ("hooking", "styling", "studio", "product")
_LEGACY_SECTION_ROLES = ("benefit", "fit")

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
    "hero": "hooking",
    "benefit": "hooking",
    "coordination": "styling",
    "fit": "studio",
    "realWear": "styling",
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
#: direction="side" 의 두 갈래 — cut_generator.SIDE_STYLES 와 같은 값이어야 한다.
#: 순환 import 를 피하려고 여기서 다시 쓰고, 계약 시험이 두 값을 맞춰 둔다.
_SIDE_STYLES = ("profile", "threeQuarter")
_WORN_SHOTS = ("full", "medium")
_PRODUCT_DIRECTIONS = ("front", "back")
_PRODUCT_OVERVIEW_SHOTS = ("ghost",)
_FIT_ROLE_BY_CUT_TYPE = {
    "styling": "coordination",
    "horizon": "fit",
    "mirror": "realWear",
}


def _canonicalize_example_selection(out: dict) -> dict:
    """Normalize the persisted example selection without leaving orphan metadata."""
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
    if role in ("coordination", "fit", "realWear") and cut_type in _FIT_ROLE_BY_CUT_TYPE:
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

    out = dict(block)
    # taxonomy v3가 정규화 결과의 정본이다. StoryboardBlock 저장 shape에는
    # kind가 없으며, 비저장 경로에서도 EditorBlock의 sectionRole 동치만 남긴다.
    out["taxonomyVersion"] = 3
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

    explicit_role = block.get("contentRole") or block.get("content_role")
    role = explicit_role if explicit_role in CONTENT_ROLES else resolve_content_role(block)
    role = _role_for_selected_recipe(block, role)
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
    # 옆모습 컷의 얼굴 경로(진짜 옆모습 ↔ 사선). 옆이 아니면 뜻이 없으니 지운다 —
    # 남겨 두면 방향을 바꾼 카드에 옛 값이 따라다닌다(cut_generator.SIDE_STYLES).
    side_style = block.get("sideStyle") or block.get("side_style")
    out.pop("side_style", None)
    out["sideStyle"] = side_style if (direction == "side" and side_style in _SIDE_STYLES) else None
    if cut_type == "product":
        out["faceExposure"] = None
        out["matchIds"] = []
        out["outerClosureState"] = None
    elif cut_type == "mirror":
        face = block.get("faceExposure") or block.get("face_exposure")
        out["faceExposure"] = "show" if face == "show" else "hide"
    elif (block.get("faceExposure") or block.get("face_exposure")) not in ("same", "show", "hide"):
        out["faceExposure"] = "same"
    return _canonicalize_example_selection(out)


#: 핏 확인(studio) 섹션에서 방향을 안 정한 AI 컷에 채워 넣는 순서.
#:
#: 왜 필요한가: 일곱 역할의 레시피가 전부 direction="front" 라(_CONTENT_ROLE_RECIPES),
#: 콘티가 블록마다 명시하지 않으면 상세페이지가 통째로 정면만 나온다. 2026-09-21 첫 운영
#: QA 가 그랬다 — studio 7컷 중 옆모습 0장, 뒷모습 1장. 옆·뒷모습 머리 교체를 다 배선해
#: 놓고도 쓰이질 않았다.
#:
#: 왜 한 장씩만인가: 옆·뒷모습 컷은 각도 교체가 성공해야만 나온다. 실패하면 그 컷은
#: **빈 컷**이다(남의 머리를 내보내지 않는다는 계약). 처음부터 절반을 걸면 실패했을 때
#: 셀러의 핏 섹션이 반토막 난다. 한 장씩 넣어 두고, 운영에서 붙는 것을 보고 늘린다.
#:
#: 옆이 두 칸인 이유(2026-09-21): 같은 "옆모습" 주문이 두 가지 다른 그림이 된다.
#:   profile      — 진짜 옆모습. 각도 교체가 등록자 실사진을 머리째 붙인다.
#:   threeQuarter — 사선 3/4. 몸만 옆으로 두고 얼굴은 카메라를 본다. 얼굴 패스가 그린다.
#: 각도 교체를 켜기 전에는 옆모습 주문이 전부 후자로 갔다(09-17 사선 4컷). 각도 교체가
#: 붙자 전부 전자로 넘어가면서 사선이 사라졌다 — 핏 섹션에는 둘 다 있어야 한다.
_STUDIO_DIRECTION_SPREAD = (
    ("side", "profile"),
    ("side", "threeQuarter"),
    ("back", None),
)
#: 이보다 적으면 건드리지 않는다 — 짧은 섹션을 옆·뒤로만 채우면 정면이 사라진다.
#: 기준 컷 1장 + 배분 3장.
_STUDIO_SPREAD_MIN_CUTS = 4

#: 핏 확인 섹션에서 포즈를 안 정한 AI 컷에 돌려 가며 넣는 포즈.
#:
#: 왜 필요한가: 2026-09-21 운영 QA 에서 studio 7컷이 **거의 같은 그림**으로 나왔다.
#: 콘티가 pose 를 안 주면 cut_generator 가 POSE:auto 한 줄("natural and unforced")로
#: 가는데, 같은 상품·같은 모델·같은 프롬프트면 같은 포즈가 나온다. 예시(exampleId)가
#: 붙어야 도는 EXNUANCE·EXREPEAT 변주도 안 걸리고, 포즈 변주 전용인 cut_variator(AG-07)는
#: 에디터에서만 쓴다. 즉 상세페이지에는 변주 장치가 하나도 안 걸려 있었다.
#:
#: 고르는 기준은 **옷이 계속 보이는 것**이다. 핏 확인 섹션이라 팔짱처럼 앞섶을 가리는
#: 포즈는 넣지 않는다. 문구는 _sanitize 를 거쳐 40자에서 잘리므로 그 안에 맞춘다.
_STUDIO_POSE_ROTATION = (
    "weight on one leg, hands at sides",
    "one hand in a pocket, shoulders relaxed",
    "both hands in pockets, chin level",
    "one hand adjusting a cuff",
    "arms relaxed, one small step forward",
)


#: "포즈를 안 정했다"로 보는 값. **"auto" 를 여기 넣는 게 핵심이다** — 프런트가 카드를
#: 만들 때 `pose: 'auto'` 를 명시적으로 써 넣는다(Storyboard.jsx 세 곳). 그걸 셀러의 선택으로
#: 읽으면 콘티보드를 한 번이라도 저장한 순간 변주가 통째로 사라진다. "auto" 는 선택이 아니라
#: 기본값이고, cut_generator 도 그 값을 POSE:auto(변주 없음)로 읽는다.
_POSE_UNSET = ("", "auto")


def _spread_studio_poses(raw: list) -> None:
    """핏 확인 섹션의 **포즈를 안 정한** AI 컷에 포즈를 돌려 가며 넣는다(제자리 수정).

    첫 컷은 건드리지 않는다 — 그 섹션의 기준 컷이고, 기준은 POSE:auto 가 맞다.
    콘티가 **이름 있는 포즈**를 줬으면 그대로 둔다. 셀러 카드도 안 건드린다.
    """
    open_slots = [
        block for block in raw
        if isinstance(block, dict)
        and block.get("source") != "mine"
        and (block.get("sectionRole") or block.get("section_role")
             or resolve_section_role(block)) == "studio"
        and (block.get("cutType") or block.get("cut_type")) in (None, "", "horizon")
        and str(block.get("pose") or "").strip().lower() in _POSE_UNSET
    ]
    for index, block in enumerate(open_slots[1:]):
        block["pose"] = _STUDIO_POSE_ROTATION[index % len(_STUDIO_POSE_ROTATION)]


def _spread_studio_directions(raw: list) -> None:
    """핏 확인 섹션의 **방향을 안 정한** AI 컷에 옆·뒤를 한 장씩 준다(제자리 수정).

    정규화 **전의** 블록을 받는다 — 그래야 콘티가 정한 direction 과 레시피 기본값을
    구분할 수 있다. sectionRole 도 아직 안 붙어 있을 수 있어 resolve_section_role 로 푼다.

    건드리지 않는 것:
      · 셀러 카드(source == "mine") — 셀러가 고른 구성은 정본이다.
      · 콘티가 direction 을 명시한 블록 — 명시값이 언제나 이긴다.
      · horizon 이 아닌 컷(product·mirror) — 그쪽은 direction 의 뜻이 다르다.

    방향을 바꾸면 그 블록에 붙어 있던 예시 포즈는 더 이상 맞지 않는다
    (cut_generator.pose_direction_compatible 가 정면 예시를 옆 컷에 못 쓰게 막는다).
    그래서 자동으로 고른 예시(exampleSelectionOrigin == "auto")는 같이 비운다.
    셀러가 직접 고른 예시("user")는 남기고 방향도 건드리지 않는다.
    """
    open_slots = [
        block for block in raw
        if isinstance(block, dict)
        and block.get("source") != "mine"
        and (block.get("sectionRole") or block.get("section_role")
             or resolve_section_role(block)) == "studio"
        and (block.get("cutType") or block.get("cut_type")) in (None, "", "horizon")
        and block.get("direction") not in _WORN_DIRECTIONS
        and block.get("exampleSelectionOrigin") != "user"
    ]
    if len(open_slots) < _STUDIO_SPREAD_MIN_CUTS:
        _fill_missing_side_styles(raw)
        return
    # 첫 컷은 정면으로 남긴다 — 핏 섹션의 기준 컷이고, 정면이 하나도 없으면 안 된다.
    for block, (direction, side_style) in zip(open_slots[1:], _STUDIO_DIRECTION_SPREAD):
        block["direction"] = direction
        if side_style:
            block["sideStyle"] = side_style
        if block.get("exampleId"):
            block["exampleId"] = None
            block["exampleSelectionOrigin"] = None
    _fill_missing_side_styles(raw)


def _fill_missing_side_styles(raw: list) -> None:
    """direction="side" 인데 sideStyle 이 빈 핏 확인 컷에 갈래를 채운다(제자리 수정).

    ★ 이게 없으면 **사선이 영영 안 나온다.** 위 배분은 "방향을 안 정한" 컷만 건드리는데,
      콘티 AI 는 direction 을 직접 주면서 sideStyle 은 모른다(그 개념이 콘티 스키마에 없다).
      그러면 배분이 통째로 건너뛰고 side 컷은 sideStyle=None 으로 남는데, 소비자는 미기재를
      옆모습으로 읽으므로(cut_generator._side_style_of) 전부 90도로 나간다.
      2026-09-22 운영 실측: 콘티가 front 6·back 2·side 1 을 직접 배정해 사선이 0장이었다.

    side 가 여러 장이면 옆모습·사선을 번갈아 준다. 한 장뿐이면 **사선**을 준다 —
    90도 옆모습은 얼굴이 거의 안 보여 정면 컷과 구분이 덜 가고, 사선은 등록 인물의 얼굴이
    보이는 컷이라 핏 확인 섹션에서 값이 더 크다.
    """
    side_blocks = [
        block for block in raw
        if isinstance(block, dict)
        and block.get("source") != "mine"
        and (block.get("sectionRole") or block.get("section_role")
             or resolve_section_role(block)) == "studio"
        and (block.get("cutType") or block.get("cut_type")) in (None, "", "horizon")
        and block.get("direction") == "side"
        and not block.get("sideStyle")
    ]
    if not side_blocks:
        return
    if len(side_blocks) == 1:
        side_blocks[0]["sideStyle"] = "threeQuarter"
        return
    for index, block in enumerate(side_blocks):
        block["sideStyle"] = "profile" if index % 2 == 0 else "threeQuarter"


def canonicalize_storyboard(blocks: list, *, for_storage: bool = False) -> list:
    """Canonicalize blocks and assign hidden roles from section/card order.

    Python's sort is stable, so the user's order inside each section is kept.
    Custom blocks without a section inherit their neighboring section;
    malformed non-dictionary entries remain at the end. The first AI image in
    the hooking section is the only ``hero``; later hero values are demoted to
    ``benefit``. AI custom/missing roles use the section's safe default.
    """
    defaults = {
        "hooking": "hero",
        "styling": "coordination",
        "studio": "fit",
        "product": "productOverview",
    }

    def canonicalize_for_storyboard(block):
        if not isinstance(block, dict):
            return block
        candidate = dict(block)
        declared_section = candidate.get("sectionRole") or candidate.get("section_role")
        if declared_section not in SECTION_ROLES:
            declared_section = resolve_section_role(candidate)

        # Storyboard-list 계약에서는 화면의 유효한 섹션이 정본이다. 단일 블록
        # 헬퍼의 defensive explicit-role 우선 규칙은 에디터 등 다른 소비처를
        # 위해 그대로 두고, 여기서만 섹션 안의 내부 역할을 다시 정한다.
        if candidate.get("source") != "mine" and declared_section in SECTION_ROLES:
            role = resolve_content_role(candidate)
            if role == "custom" or _CONTENT_ROLE_TO_SECTION_ROLE.get(role) != declared_section:
                role = defaults[declared_section]
            requested_cut_type = candidate.get("cutType") or candidate.get("cut_type")
            if declared_section == "styling":
                role = "realWear" if requested_cut_type == "mirror" else "coordination"
                candidate["cutType"] = "mirror" if requested_cut_type == "mirror" else "styling"
            elif declared_section == "studio":
                role = "fit"
                candidate["cutType"] = "horizon"
            candidate["sectionRole"] = declared_section
            candidate["contentRole"] = role

        previous_recipe = (
            block.get("cutType") or block.get("cut_type"),
            block.get("direction"),
            block.get("shot"),
        )
        updated = canonicalize_storyboard_block(candidate, for_storage=for_storage)
        updated = horizon_background.normalize_block(updated)
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
        return _canonicalize_example_selection(updated)

    # ★ 방향 배분은 **정규화 전에** 해야 한다. canonicalize_storyboard_block 이 레시피
    #   기본값으로 direction="front" 를 채우고 나면 "콘티가 정면으로 정했다" 와 "아무것도
    #   안 정했다" 가 구분되지 않는다. 호출자의 리스트는 안 건드리도록 복사본에 쓴다.
    raw = [dict(block) if isinstance(block, dict) else block for block in (blocks or [])]
    _spread_studio_directions(raw)
    _spread_studio_poses(raw)
    canonical = [canonicalize_for_storyboard(block) for block in raw]
    # Custom/mine cards without a semantic section inherit the preceding
    # section; leading cards inherit the next valid section (or hooking when
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
            block["sectionRole"] = next_role or "hooking"
        next_role = block["sectionRole"]

    section_order = {"hooking": 0, "styling": 1, "studio": 2, "product": 3}
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
        if section_role == "hooking":
            if not hero_assigned:
                role = "hero"
                hero_assigned = True
            elif role == "hero":
                role = "benefit"

        updated = canonicalize_for_storyboard({**block, "contentRole": role})
        out.append(updated)
    return out
