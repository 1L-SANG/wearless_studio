"""저장된 칸의 위치와 상품 사실로 AI로 채울 후보를 만든다."""

from collections import Counter
from copy import deepcopy
import math
from urllib.parse import urlsplit
import uuid

from . import content_roles, cut_generator, mannequin, outer_closure


_PHOTO_ROWS = {"twocol", "threecol", "grid2x2", "colorcmp"}
_UNSUPPORTED = {"info", "size", "care", "ai-notice"}
_WORN = {"styling", "horizon"}
_DIRECTIONS = ("front", "side", "back")
_DEFAULT_PURPOSE = {"hooking": "hero", "styling": "coordination", "studio": "fit", "product": "productOverview"}
_DETAIL_NAMES = {"neckline": "목선", "closure": "여밈", "pocket": "주머니", "waist": "허리", "cuff": "소매 끝",
                 "hem": "밑단", "construction": "봉제", "surface": "표면", "fabric": "원단", "label": "라벨"}


def _unsupported_block(block):
    return not isinstance(block, dict) or block.get("kind") in _UNSUPPORTED or bool(block.get("auto")) or bool(block.get("hidden"))


def slot_kind(block, element, storyboard_by_id):
    """화면과 같은 순서로 대상 밖, 채워짐, 생성 중, 계획된 칸을 가린다."""
    if _unsupported_block(block) or not isinstance(element, dict) or element.get("type") != "image" or element.get("hidden"):
        return "unsupported"
    if element.get("src"):
        return "filled"
    if element.get("genPending"):
        return "pending"
    if element.get("genFailed"):
        return "planned"
    source = (storyboard_by_id or {}).get(element.get("sourceBlockId")) or {}
    generated = not block.get("templateId") and (
        block.get("kind") in _PHOTO_ROWS or bool(block.get("contentRole") and block["contentRole"] != "custom")
    )
    return "planned" if source.get("source") == "ai" and generated else "unplanned"


def _asset_id(src):
    if not isinstance(src, str):
        return None
    try:
        parts = urlsplit(src).path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["v1", "assets"] and parts[3] == "file":
            return str(uuid.UUID(parts[2]))
    except (ValueError, TypeError):
        pass
    return None


def _number(element, key):
    value = element.get(key, 0)
    return float(value) if type(value) in (int, float) and math.isfinite(value) else 0


def _overlaps(left, right, axis, length):
    left_start, right_start = _number(left, axis), _number(right, axis)
    left_length, right_length = _number(left, length), _number(right, length)
    shorter = min(left_length, right_length)
    return shorter > 0 and min(left_start + left_length, right_start + right_length) - max(left_start, right_start) >= shorter / 2


def _recipe(record):
    value = dict(record.get("recipe") or {})
    for key in ("cutType", "colorId"):
        if not value.get(key) and record.get(key):
            value[key] = record[key]
    if value.get("cutType") not in cut_generator.CUT_TYPES:
        return {}
    return value


def _neighbor_photos(page, block_index, above):
    indexes = sorted({photo["blockIndex"] for photo in page if photo["blockIndex"] < block_index} if above else
                     {photo["blockIndex"] for photo in page if photo["blockIndex"] > block_index}, reverse=above)
    return [photo for index in indexes for photo in page if photo["blockIndex"] == index]


def build_context(*, project_id, slot, blocks, storyboard, product, analysis, asset_recipes,
                  examples, previous_recipes, attempt, ai_asset_ids=None):
    """칸, 같은 줄과 위 칸, 위아래 블록과 페이지 설정을 읽기만 한다."""
    blocks = deepcopy(blocks or [])
    storyboard = deepcopy(storyboard or [])
    product, analysis = deepcopy(product or {}), deepcopy(analysis or {})
    slot = dict(slot or {})
    block_index = next((i for i, b in enumerate(blocks) if isinstance(b, dict) and b.get("id") == slot.get("blockId")), None)
    target_block = blocks[block_index] if block_index is not None else {}
    target = next((e for e in target_block.get("elements", []) if isinstance(e, dict) and e.get("id") == slot.get("elementId")), {})
    by_id = {b.get("id"): b for b in storyboard if isinstance(b, dict)}
    page = []
    for index, current in enumerate(blocks):
        if _unsupported_block(current):
            continue
        elements = sorted((e for e in current.get("elements", []) if isinstance(e, dict) and e.get("type") == "image"
                           and not e.get("hidden") and not e.get("genPending") and e.get("src")),
                          key=lambda e: (_number(e, "y"), _number(e, "x")))
        for element in elements:
            asset_id = _asset_id(element.get("src"))
            record = deepcopy((asset_recipes or {}).get(asset_id) or {})
            page.append({"assetId": asset_id, "element": element, "blockIndex": index,
                         "record": record, "recipe": _recipe(record)})
    same_block = [photo for photo in page if photo["blockIndex"] == block_index]
    row = [photo for photo in same_block if _overlaps(photo["element"], target, "y", "h")]
    usable = [photo for photo in same_block if photo["recipe"].get("cutType") and not photo["record"].get("upload")
              and not photo["record"].get("realDerived") and not photo["record"].get("detail_recipe")]
    row_usable = [photo for photo in usable if photo in row]
    left = sorted((p for p in row_usable if _number(p["element"], "x") < _number(target, "x")), key=lambda p: _number(p["element"], "x"), reverse=True)
    right = sorted((p for p in row_usable if _number(p["element"], "x") > _number(target, "x")), key=lambda p: _number(p["element"], "x"))
    above_pair = sorted((p for p in usable if _number(p["element"], "y") + _number(p["element"], "h") <= _number(target, "y")
                         and _overlaps(p["element"], target, "x", "w")), key=lambda p: _number(p["element"], "y") + _number(p["element"], "h"), reverse=True)
    pair, pair_position = (left[0], "left") if left else (right[0], "right") if right else (above_pair[0], "above") if not row and above_pair else (None, None)
    upper = _neighbor_photos(page, block_index, True) if block_index is not None else []
    lower = _neighbor_photos(page, block_index, False) if block_index is not None else []
    known = [p for p in same_block + upper + lower if content_roles.resolve_section_role(p["recipe"])]
    hint = target.get("roleHint")
    purpose = hint if hint in content_roles.CONTENT_ROLES and hint != "custom" else target_block.get("contentRole")
    if purpose not in content_roles.CONTENT_ROLES or purpose == "custom":
        purpose = _DEFAULT_PURPOSE.get(content_roles.resolve_section_role(known[0]["recipe"]) if known else None, "coordination")
    first_photo = block_index is not None and not any(
        index < block_index or (index == block_index and (_number(element, "y"), _number(element, "x")) < (_number(target, "y"), _number(target, "x")))
        for index, current in enumerate(blocks) if not _unsupported_block(current)
        for element in current.get("elements", []) if isinstance(element, dict) and element.get("type") == "image" and not element.get("hidden")
    )
    if purpose == "hero" and not first_photo:
        purpose = "benefit"
    nearest_worn = next((p for p in upper + lower if p["recipe"].get("cutType") in _WORN), None)
    colors = [c for c in product.get("colors", []) if isinstance(c, dict)]
    base = next((c for c in colors if c.get("isBase")), colors[0] if colors else {})
    ordered_colors = ([base] if base else []) + [c for c in colors if c is not base]
    colors = [c for c in ordered_colors if c.get("id") and any(isinstance(im, dict) and im.get("slot") == "Front" and im.get("id") for im in c.get("images", []))]
    registry = examples.get("assets", examples) if isinstance(examples, dict) else {
        e.get("id"): e for e in (examples or []) if isinstance(e, dict) and e.get("id")}
    previous = [deepcopy(p.get("recipe") or p) for p in (previous_recipes or []) if isinstance(p, dict)]
    return {"projectId": project_id, "slot": slot, "block": target_block, "element": target, "blockIndex": block_index,
            "kind": slot_kind(target_block, target, by_id), "purpose": purpose, "page": page, "row": row,
            "pair": pair, "pairPosition": pair_position, "upper": upper, "lower": lower, "nearestWorn": nearest_worn,
            "storyboard": storyboard, "product": product, "analysis": analysis, "colors": colors, "baseColorId": base.get("id"),
            "examples": deepcopy(registry), "previousRecipes": previous, "attempt": attempt,
            "aiAssetIds": set(ai_asset_ids or []), "colorCompare": target_block.get("kind") == "colorcmp" or
            target_block.get("templateId") == "kiwi-5" or target_block.get("name") in ("컬러 비교", "컬러 비교 카드")}


def _used_recipes(context):
    return [photo["recipe"] for photo in context["page"]] + context["previousRecipes"]


def _repeated(candidate, context):
    for old in _used_recipes(context):
        if candidate.get("exampleId") and candidate.get("exampleId") == old.get("exampleId") and candidate.get("colorId") == old.get("colorId"):
            return True
        if candidate.get("cutType") == "product" and old.get("cutType") == "product":
            if candidate.get("shot") == old.get("shot") == "ghost" and (candidate.get("colorId"), candidate.get("direction")) == (old.get("colorId"), old.get("direction")):
                return True
            if candidate.get("shot") == old.get("shot") == "detail" and candidate.get("detailTargetId") and candidate.get("detailTargetId") == old.get("detailTargetId"):
                return True
    return False


def _worn_settings(recipe, context):
    if recipe.get("cutType") not in _WORN:
        return recipe
    out = dict(recipe)
    out["modelId"] = context["analysis"].get("selectedModelId")
    pair_recipe = context["pair"]["recipe"] if context["pair"] else {}
    matches = pair_recipe.get("matchIds")
    if matches is None:
        matches = next((b.get("matchIds") for b in context["storyboard"] if isinstance(b, dict) and b.get("source") == "ai"
                        and b.get("cutType") in _WORN and b.get("colorId") == out.get("colorId") and b.get("matchIds") is not None), [])
    out["matchIds"] = list(matches or [])
    out.update({"faceExposure": "same", "refAssetIds": [], "refScope": "all"})
    if (context["product"].get("clothingType") or context["product"].get("clothing_type")) == "outer":
        closure = outer_closure.front_closure_of(context["analysis"])
        if closure["kind"] != "none":
            state = pair_recipe.get("outerClosureState")
            if state not in outer_closure.STATES:
                states = [p["recipe"].get("outerClosureState") for p in context["page"] if p["recipe"].get("cutType") in _WORN
                          and p["recipe"].get("outerClosureState") in outer_closure.STATES]
                state = Counter(states).most_common(1)[0][0] if states else "open"
            out["outerClosureState"] = outer_closure.effective_state(state, closure)
        else:
            out.pop("outerClosureState", None)
    else:
        out.pop("outerClosureState", None)
    return out


def _candidate(context, recipe, rule, rank, *, path="new", anchor=None, detail=None):
    return {"recipe": _worn_settings(recipe, context), "rule": rule, "rank": rank, "path": path,
            "anchorAssetId": anchor, "detail": detail}


def _examples_for(context, cut, shot, direction):
    clothing = context["product"].get("clothingType") or context["product"].get("clothing_type")
    gender = mannequin.select_base_gender(context["analysis"], clothing)
    return [(key, entry) for key, entry in sorted(context["examples"].items()) if isinstance(entry, dict) and entry.get("all")
            and not str(key).startswith(("sig_", "ss_")) and entry.get("cutType") == cut and entry.get("shot") == shot
            and entry.get("direction") == direction and entry.get("gender") == gender and clothing in (entry.get("applicableClothingTypes") or [])]


def _worn_candidates(context, cut, shot, color_id, directions, rule, rank=0):
    candidates = []
    for order, direction in enumerate(directions):
        for example_id, entry in _examples_for(context, cut, shot, direction):
            recipe = {"contentRole": context["purpose"], "cutType": cut, "shot": shot, "direction": direction,
                      "colorId": color_id, "exampleId": example_id, "exampleSelectionOrigin": "auto",
                      "sideStyle": (entry.get("sideStyle") or "profile") if direction == "side" else None}
            candidate = _candidate(context, recipe, rule, rank + order)
            if not _repeated(recipe, context):
                candidates.append(candidate)
    return candidates


def _ghost_candidates(context, rule="ghost_order", *, allow_repeat=False, rank=0):
    colors, pair = context["colors"], context["pair"]
    combos = [(c, "front") for c in colors[:1]] + [(c, "back") for c in colors[:1]] + [(c, "front") for c in colors[1:]] + [(c, "back") for c in colors[1:]]
    other = None
    if pair and pair["recipe"].get("cutType") == "product" and pair["recipe"].get("shot") == "ghost":
        other = (pair["recipe"].get("colorId"), "back" if pair["recipe"].get("direction") == "front" else "front")
        combos.sort(key=lambda item: (item[0].get("id"), item[1]) != other)
    result = []
    for order, (color, direction) in enumerate(combos):
        if not any(isinstance(im, dict) and im.get("id") and im.get("slot") == {"front": "Front", "back": "Back"}[direction] for im in color.get("images", [])):
            continue
        recipe = {"contentRole": "productOverview", "cutType": "product", "shot": "ghost", "direction": direction, "colorId": color["id"]}
        if not allow_repeat and _repeated(recipe, context):
            continue
        chosen_rule = "ghost_other_face" if rule == "ghost_order" and (color["id"], direction) == other else rule
        result.append(_candidate(context, recipe, chosen_rule, rank + order))
    return result


def _detail_candidates(context):
    if context["baseColorId"] not in {c["id"] for c in context["colors"]}:
        return []
    contract = context["analysis"].get("detailRecommendations") or {}
    if type(contract.get("version")) is not int or contract.get("version") != 1 or contract.get("status") != "ready":
        return []
    targets = [t for t in contract.get("candidates", []) if isinstance(t, dict) and t.get("id") and t.get("photoUse") == "standalone"
               and t.get("visibility") == "clear" and t.get("direction") in ("front", "back")]
    groups = {t.get("id"): t.get("informationGroup") for t in contract.get("candidates", []) if isinstance(t, dict)}
    used = {groups.get(old.get("detailTargetId")) or old.get("informationGroup") for old in _used_recipes(context)} - {None, ""}
    result = []
    for target in sorted(targets, key=lambda t: t.get("rank", 99)):
        recipe = {"contentRole": "detail", "cutType": "product", "shot": "detail", "direction": target["direction"],
                  "colorId": context["baseColorId"], "detailTargetId": target["id"], "detailTargetOrigin": "auto"}
        if target.get("informationGroup") not in used and not _repeated(recipe, context):
            result.append(_candidate(context, recipe, "detail_unused", target.get("rank", 99), detail=target))
    return result


def _color_candidates(context):
    present = {p["recipe"].get("colorId") for p in context["row"]}
    pair = context["pair"]
    source = pair["recipe"] if pair else {}
    result = []
    for color in context["colors"]:
        if color["id"] in present:
            continue
        recipe = {key: source[key] for key in ("cutType", "shot", "direction", "sideStyle", "contentRole", "exampleId", "exampleSelectionOrigin")
                  if key in source} if source.get("cutType") and source.get("shot") else {"cutType": "product", "shot": "ghost", "direction": "front"}
        recipe["colorId"] = color["id"]
        if recipe.get("cutType") in _WORN:
            public = _examples_for(context, recipe["cutType"], recipe.get("shot"), recipe.get("direction"))
            if not public:
                continue
            inherited = recipe.get("exampleId")
            eligible = [(key, entry) for key, entry in public if key == inherited] or public
            for key, entry in eligible:
                option = {**recipe, "exampleId": key, "exampleSelectionOrigin": "auto",
                          "sideStyle": (entry.get("sideStyle") or "profile") if recipe.get("direction") == "side" else None}
                if not _repeated(option, context):
                    result.append(_candidate(context, option, "color_compare", 0))
            continue
        if recipe.get("cutType") == "product":
            if recipe.get("shot") == "detail":
                continue
            if not any(im.get("slot") == {"front": "Front", "back": "Back"}.get(recipe.get("direction")) and im.get("id") for im in color.get("images", []) if isinstance(im, dict)):
                continue
            recipe["contentRole"] = "productOverview"
        if not _repeated(recipe, context):
            result.append(_candidate(context, recipe, "color_compare", 0))
    return result


def build_candidates(context):
    """이유가 있는 후보를 순위대로 만들고 결정적 선택은 따로 맡긴다."""
    if context["kind"] != "unplanned" or not context["colors"]:
        return []
    if context["colorCompare"]:
        colors = _color_candidates(context)
        return colors or _ghost_candidates(context) or _ghost_candidates(context, "section_default", allow_repeat=True, rank=300)
    purpose, pair = context["purpose"], context["pair"]
    if purpose in ("productOverview", "detail"):
        if purpose == "detail":
            details = _detail_candidates(context)
            if details:
                return details
        return _ghost_candidates(context, "detail_to_ghost" if purpose == "detail" else "ghost_order") or _ghost_candidates(context, "section_default", allow_repeat=True, rank=300)
    previous_anchors = {p.get("anchorAssetId") or p.get("sourceAssetId") for p in context["previousRecipes"]}
    if purpose != "fit" and pair and pair["assetId"] in context["aiAssetIds"] and pair["recipe"].get("cutType") in _WORN and pair["recipe"].get("shot") != "detail" and pair["assetId"] not in previous_anchors:
        return [_candidate(context, pair["recipe"], "pair_similar", 0, path="similar", anchor=pair["assetId"])]
    cut = "horizon" if purpose in ("fit", "benefit") else "styling"
    color_id = pair["recipe"].get("colorId") if purpose == "fit" and pair and pair["recipe"].get("cutType") == "horizon" else context["baseColorId"]
    if color_id not in {c["id"] for c in context["colors"]}:
        color_id = context["colors"][0]["id"]
    nearest = context["nearestWorn"]
    shot = "medium" if nearest and nearest["recipe"].get("shot") == "full" else "full"
    if purpose == "benefit":
        shot = "medium"
    rule, directions = "section_new", list(_DIRECTIONS)
    if purpose == "fit":
        if pair and pair["recipe"].get("cutType") == "horizon":
            shot = pair["recipe"].get("shot") if pair["recipe"].get("shot") in ("full", "medium") else "full"
            seen = {p["recipe"].get("direction") for p in context["row"] if p["recipe"].get("cutType") == "horizon"}
            directions, rule = [d for d in _DIRECTIONS if d not in seen], "row_direction"
        else:
            page = [p["recipe"] for p in context["page"] if p["recipe"].get("cutType") == "horizon" and p["recipe"].get("colorId") == color_id]
            shots = Counter(p.get("shot") for p in page if p.get("shot") in ("full", "medium"))
            shot = shots.most_common(1)[0][0] if shots else "full"
            directions = [d for d in _DIRECTIONS if d not in {p.get("direction") for p in page}]
            if not directions:
                shot = "medium" if shot == "full" else "full"
                directions = [d for d in _DIRECTIONS if d not in {p.get("direction") for p in page if p.get("shot") == shot}]
            rule = "page_direction"
    else:
        neighboring_directions = {next((p["recipe"].get("direction") for p in photos if p["recipe"].get("cutType") in _WORN
                                        and p["recipe"].get("direction") in _DIRECTIONS), None)
                                  for photos in (context["upper"], context["lower"])}
        directions.sort(key=lambda d: (d in neighboring_directions, _DIRECTIONS.index(d)))
    primary = _worn_candidates(context, cut, shot, color_id, directions, rule)
    if primary:
        return primary
    relaxed = []
    for color in context["colors"]:
        if color["id"] != color_id:
            relaxed.extend(_worn_candidates(context, cut, shot, color["id"], list(_DIRECTIONS), "relax_other_color", 100))
    if relaxed:
        return relaxed
    other_shot = "medium" if shot == "full" else "full"
    relaxed = _worn_candidates(context, cut, other_shot, color_id, list(_DIRECTIONS), "relax_shot", 200)
    if relaxed:
        return relaxed
    default_shot = "medium" if purpose == "benefit" else "full"
    default = {"contentRole": purpose, "cutType": cut, "shot": default_shot, "direction": "front", "colorId": color_id}
    public = _examples_for(context, cut, default_shot, "front")
    if public:
        default.update({"exampleId": public[0][0], "exampleSelectionOrigin": "auto"})
    return [_candidate(context, default, "section_default", 300)]


def pick_candidate(candidates, *, project_id, slot, attempt):
    """가장 높은 순위의 후보만 기존 결정적 씨앗으로 하나 고른다."""
    if not candidates:
        return None
    rank = min(candidate["rank"] for candidate in candidates)
    pool = [candidate for candidate in candidates if candidate["rank"] == rank]
    seed = f"{project_id}:fill:{slot['blockId']}:{slot['elementId']}:{attempt}"
    index = int(cut_generator._mulberry32_once(cut_generator._hash_seed(seed)) * len(pool))
    return pool[index]


def _label(context, candidate):
    recipe, purpose = candidate["recipe"], context["purpose"]
    if recipe.get("cutType") == "product":
        if recipe.get("shot") == "detail":
            target = candidate.get("detail") or {}
            name = " ".join(str(target.get("label") or _DETAIL_NAMES.get(target.get("kind"), "부위")).split())
            name = name.replace("—", " ").replace("–", " ").replace("-", " ")
            label = f"디테일 · {name}"
            return label if len(label) <= 20 else f"디테일 · {_DETAIL_NAMES.get(target.get('kind'), '부위')}"
        color = next((c for c in context["colors"] if c.get("id") == recipe.get("colorId")), {})
        face = "뒷면" if recipe.get("direction") == "back" else "앞면"
        name = color.get("name") or color.get("colorName") or ""
        label = f"고스트샷 · {name}" if candidate["rule"] == "color_compare" and name else f"고스트샷 · {face}"
        return label if len(label) <= 20 and not any(dash in label for dash in ("—", "–", "-")) else f"고스트샷 · {face}"
    prefix = "첫 장면" if purpose in ("hero", "benefit") else "스튜디오" if purpose == "fit" else "스타일링"
    if candidate["path"] == "similar":
        position = {"left": "왼쪽", "right": "오른쪽", "above": "위"}.get(context["pairPosition"], "짝")
        return f"{prefix} · {position} 사진과 같은 장소"
    reason = {"front": "정면", "side": "옆모습", "back": "뒷모습"}.get(recipe.get("direction"), "정면") if purpose == "fit" else "중간샷" if recipe.get("shot") == "medium" else "풀샷"
    return f"{prefix} · {reason}"


def decide_context_fill(*, project_id, slot, blocks, storyboard, product, analysis, asset_recipes,
                        examples, previous_recipes, attempt, ai_asset_ids=None):
    """접수 단계가 저장할 생성 요청과 판단 기록을 돌려준다."""
    context = build_context(project_id=project_id, slot=slot, blocks=blocks, storyboard=storyboard, product=product,
                            analysis=analysis, asset_recipes=asset_recipes, examples=examples, previous_recipes=previous_recipes,
                            attempt=attempt, ai_asset_ids=ai_asset_ids)
    candidate = pick_candidate(build_candidates(context), project_id=project_id, slot=context["slot"], attempt=attempt)
    if candidate is None:
        return None
    recipe = candidate["recipe"]
    if candidate["path"] == "similar":
        payload = {"mode": "vary", "variationMode": "similar", "changes": [],
                   "source": {"src": f"/v1/assets/{candidate['anchorAssetId']}/file", "cutType": recipe["cutType"]}}
    else:
        payload = content_roles.canonicalize_storyboard_block({"source": "ai", **recipe})
        payload["mode"] = "new"
    return {"payload": payload, "contextFill": {"version": 1, "slot": context["slot"],
            "slotKey": f"{context['slot']['blockId']}:{context['slot']['elementId']}", "purpose": context["purpose"],
            "rule": candidate["rule"], "label": _label(context, candidate), "path": candidate["path"],
            "colorId": recipe.get("colorId"), "anchorAssetId": candidate["anchorAssetId"], "attempt": attempt}}
