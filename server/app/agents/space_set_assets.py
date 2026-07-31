"""Published storyboard space-set registry and secure runtime binding.

Space sets are intentionally separate from the flat generation-example registry:
one immutable representative plate belongs to the set, while each current block
chooses a compatible published pose. A production group id embeds the stable set
id as ``ssg1__<setId>__<instance>``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from io import BytesIO
from urllib.parse import urlsplit

import httpx
from PIL import Image

from ..config import Settings
from .gemini_image import InlineImage

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_DEFAULT_SPACE_SET_ASSETS = os.path.join(
    _SERVER_DIR, "app", "data", "space_set_assets.json"
)
_FETCH_TIMEOUT = 15.0
_PRODUCTION_GROUP_PREFIX = "ssg1__"
_RELEASE_ROOT = "seed/genexamples/space-sets/v1/releases"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CLOTHING_TYPES = ("top", "bottom", "outer", "dress")
_CUT_TYPES = ("styling", "horizon", "mirror")
_SET_TYPES = ("styling", "horizon-rotation", "horizon-sequence")
_SHOTS = ("full", "medium")
_DIRECTIONS = ("front", "side", "back")

log = logging.getLogger("wearless.space_set_assets")


class SpaceSetBindingError(ValueError):
    """Fail-closed mismatch between a saved storyboard group and its release."""

    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


def parse_space_set_group_id(group_id: object) -> tuple[str, str] | None:
    """Return ``(set_id, instance)`` for a production set group.

    A missing value means an ordinary standalone block. Every present group id must
    use the published v1 namespace; arbitrary pre-release group ids are rejected.
    """

    if group_id is None:
        return None
    if not isinstance(group_id, str) or not group_id.startswith(
        _PRODUCTION_GROUP_PREFIX
    ):
        raise SpaceSetBindingError(
            "invalid_space_set_group_id",
            "촬영 세트 식별자가 올바르지 않아요. 세트를 다시 선택해 주세요.",
        )
    segments = group_id[len(_PRODUCTION_GROUP_PREFIX) :].split("__")
    if len(segments) != 2 or any(not _is_safe_id(segment) for segment in segments):
        raise SpaceSetBindingError(
            "invalid_space_set_group_id",
            "촬영 세트 식별자가 올바르지 않아요. 세트를 다시 선택해 주세요.",
        )
    return segments[0], segments[1]


def _is_safe_id(value: object, *, prefix: str | None = None) -> bool:
    return (
        isinstance(value, str)
        and "__" not in value
        and _SAFE_ID_RE.fullmatch(value) is not None
        and (prefix is None or value.startswith(prefix))
    )


def _clean_asset(raw: object, *, field: str, variant: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{field}_missing")
    key = raw.get("key")
    if "url" in raw or not isinstance(key, str) or not key.strip():
        raise ValueError(f"{field}_location_invalid")
    key = key.strip()
    key_url = urlsplit(key)
    release_root = f"{_RELEASE_ROOT}/"
    release_parts = (
        key[len(release_root) :].split("/")
        if key.startswith(release_root)
        else []
    )
    if (
        key_url.scheme
        or key_url.netloc
        or key_url.query
        or key_url.fragment
        or key.startswith("/")
        or "\\" in key
        or re.fullmatch(r"[A-Za-z0-9_./-]+", key) is None
        or any(part in ("", ".", "..") for part in key.split("/"))
        or len(release_parts) < 3
        or not _is_safe_id(release_parts[0])
        or release_parts[1] != variant
    ):
        raise ValueError(f"{field}_location_invalid")
    sha256 = raw.get("sha256")
    width = raw.get("width")
    height = raw.get("height")
    mime = raw.get("mime")
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise ValueError(f"{field}_sha256_invalid")
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or width <= 0
        or not isinstance(height, int)
        or isinstance(height, bool)
        or height <= 0
    ):
        raise ValueError(f"{field}_dimensions_invalid")
    if mime not in ("image/png", "image/jpeg", "image/webp"):
        raise ValueError(f"{field}_mime_invalid")
    return {
        "key": key,
        "sha256": sha256,
        "width": width,
        "height": height,
        "mime": mime,
    }


def validate_space_set_registry_document(
    raw: object,
) -> tuple[str | None, dict[str, dict]]:
    """Validate one registry document with the exact runtime contract."""

    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise ValueError("space_set_registry_schema_invalid")
    raw_sets = raw.get("sets")
    if not isinstance(raw_sets, list):
        raise ValueError("space_set_registry_sets_invalid")
    raw_place_types = raw.get("placeTypes")
    if (
        not isinstance(raw_place_types, list)
        or not raw_place_types
        or any(
            not isinstance(value, str) or not value
            for value in raw_place_types
        )
        or len(raw_place_types) != len(set(raw_place_types))
    ):
        raise ValueError("space_set_registry_place_types_invalid")
    place_types = set(raw_place_types)
    release_id = raw.get("releaseId")
    if raw_sets and not _is_safe_id(release_id):
        raise ValueError("space_set_registry_release_id_invalid")
    base_url = raw.get("baseUrl")
    if base_url is not None:
        parts = urlsplit(base_url.strip()) if isinstance(base_url, str) else None
        if (
            parts is None
            or parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError("space_set_registry_base_url_invalid")
        base_url = base_url.rstrip("/")
    if raw_sets and base_url is None:
        raise ValueError("space_set_registry_base_url_invalid")

    sets: dict[str, dict] = {}
    example_ids: set[str] = set()
    for raw_set in raw_sets:
        if not isinstance(raw_set, dict):
            raise ValueError("space_set_registry_set_invalid")
        set_id = raw_set.get("setId") or raw_set.get("id")
        if not _is_safe_id(set_id):
            raise ValueError("space_set_registry_set_id_invalid")
        if set_id in sets:
            raise ValueError("space_set_registry_set_id_duplicate")
        gender = raw_set.get("gender")
        if gender not in ("women", "men"):
            raise ValueError("space_set_registry_gender_invalid")
        set_type = raw_set.get("setType")
        if set_type not in _SET_TYPES:
            raise ValueError("space_set_registry_set_type_invalid")
        place_type = raw_set.get("placeType")
        if place_type not in place_types:
            raise ValueError("space_set_registry_place_type_invalid")
        plate_policy = raw_set.get("platePolicy")
        if plate_policy not in ("required", "not-required"):
            raise ValueError("space_set_registry_plate_policy_invalid")
        if plate_policy == "not-required":
            if (
                set_type != "horizon-sequence"
                or raw_set.get("representativePlate") is not None
            ):
                raise ValueError("space_set_registry_plate_policy_invalid")
            representative_plate = None
        else:
            representative_plate = _clean_asset(
                raw_set.get("representativePlate"),
                field="space_set_representative_plate",
                variant="plate",
            )
        applicable = raw_set.get("applicableClothingTypes")
        if (
            not isinstance(applicable, list)
            or not applicable
            or len(applicable) != len(set(applicable))
            or any(item not in _CLOTHING_TYPES for item in applicable)
            or (gender == "men" and "dress" in applicable)
        ):
            raise ValueError("space_set_registry_applicability_invalid")
        space_variation = raw_set.get("spaceVariation")
        if space_variation not in ("fixed", "subtle"):
            raise ValueError("space_set_registry_space_variation_invalid")
        raw_members = raw_set.get("members")
        if (
            not isinstance(raw_members, list)
            or not 2 <= len(raw_members) <= 5
        ):
            raise ValueError("space_set_registry_members_invalid")

        members: list[dict] = []
        for expected_order, raw_member in enumerate(raw_members, start=1):
            if not isinstance(raw_member, dict) or raw_member.get("order") != expected_order:
                raise ValueError("space_set_registry_member_order_invalid")
            example_id = raw_member.get("exampleId")
            if (
                not _is_safe_id(example_id, prefix="ss_")
                or example_id in example_ids
            ):
                raise ValueError("space_set_registry_example_id_invalid")
            cut_type = raw_member.get("cutType")
            shot = raw_member.get("shot")
            direction = raw_member.get("direction")
            if cut_type not in _CUT_TYPES or shot not in _SHOTS:
                raise ValueError("space_set_registry_member_recipe_invalid")
            expected_cut_type = "styling" if set_type == "styling" else "horizon"
            if cut_type != expected_cut_type:
                raise ValueError("space_set_registry_member_recipe_invalid")
            if cut_type == "mirror":
                if direction is not None:
                    raise ValueError("space_set_registry_member_direction_invalid")
            elif direction not in _DIRECTIONS:
                raise ValueError("space_set_registry_member_direction_invalid")
            member_gender = raw_member.get("gender", gender)
            member_applicable = raw_member.get(
                "applicableClothingTypes", applicable
            )
            if member_gender != gender or member_applicable != applicable:
                raise ValueError("space_set_registry_member_applicability_invalid")
            members.append(
                {
                    "exampleId": example_id,
                    "order": expected_order,
                    "cutType": cut_type,
                    "shot": shot,
                    "direction": direction,
                    "gender": gender,
                    "applicableClothingTypes": list(applicable),
                    "all": _clean_asset(
                        raw_member.get("all"),
                        field="space_set_member_all",
                        variant="all",
                    ),
                    "pose": _clean_asset(
                        raw_member.get("pose"),
                        field="space_set_member_pose",
                        variant="pose",
                    ),
                }
            )
            example_ids.add(example_id)
        if len(applicable) > 1 and (
            set(applicable) != {"top", "outer"}
            or any(member["shot"] != "full" for member in members)
        ):
            raise ValueError("space_set_registry_applicability_invalid")
        sets[set_id] = {
            "setId": set_id,
            "setType": set_type,
            "gender": gender,
            "applicableClothingTypes": list(applicable),
            "placeType": place_type,
            "spaceVariation": space_variation,
            "platePolicy": plate_policy,
            "representativePlate": representative_plate,
            "members": members,
        }
    return base_url, sets


@lru_cache(maxsize=1)
def load_space_set_registry() -> tuple[str | None, dict[str, dict]]:
    """Load and structurally validate the independent published set registry."""

    with open(_DEFAULT_SPACE_SET_ASSETS, encoding="utf-8") as f:
        raw = json.load(f)
    return validate_space_set_registry_document(raw)


def _resolve_pose_reference(
    block: dict,
    *,
    clothing_type: str,
    gender: str,
    registry: dict[str, dict],
    flat_assets: dict[str, dict] | None,
) -> tuple[dict, dict[str, dict] | None]:
    example_id = block.get("exampleId") or block.get("example_id")
    if not _is_safe_id(example_id):
        raise SpaceSetBindingError(
            "space_set_pose_id_invalid",
            "공간 세트의 포즈 예시가 올바르지 않아요. 다른 예시를 골라주세요.",
        )
    if example_id.startswith("ss_"):
        pose_entry = next(
            (
                member
                for set_entry in registry.values()
                for member in set_entry["members"]
                if member["exampleId"] == example_id
            ),
            None,
        )
        source = "space-set"
    else:
        if flat_assets is None:
            try:
                from . import cut_generator

                _flat_base, flat_assets = (
                    cut_generator.load_example_asset_registry()
                )
            except (
                OSError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
            ) as exc:
                raise SpaceSetBindingError(
                    "space_set_pose_registry_unavailable",
                    "포즈 예시 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
                ) from exc
        pose_entry = flat_assets.get(example_id)
        source = "flat"
    if not pose_entry or not pose_entry.get("pose"):
        raise SpaceSetBindingError(
            "space_set_pose_unavailable",
            "공간 세트에 사용할 포즈 예시를 찾을 수 없어요. 다른 예시를 골라주세요.",
        )
    compatible = (
        pose_entry.get("cutType") == block.get("cutType")
        and pose_entry.get("shot") == block.get("shot")
        and pose_entry.get("direction") == block.get("direction")
        and pose_entry.get("gender") == gender
        and clothing_type
        in (pose_entry.get("applicableClothingTypes") or [])
    )
    if not compatible:
        raise SpaceSetBindingError(
            "space_set_pose_incompatible",
            "현재 컷과 맞지 않는 포즈 예시예요. 방향과 샷에 맞는 예시를 골라주세요.",
        )
    return (
        {
            "source": source,
            "exampleId": example_id,
            "asset": pose_entry["pose"] if source == "space-set" else None,
        },
        flat_assets,
    )


def resolve_published_pose_reference(
    block: dict, *, clothing_type: str, gender: str
) -> dict:
    """Resolve one pose without granting a location plate.

    This is used when a seller drags a member out of a production set: the
    published pose may remain selected, while the block no longer inherits the
    set's representative background.
    """

    try:
        _base_url, registry = load_space_set_registry()
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise SpaceSetBindingError(
            "space_set_registry_unavailable",
            "공간 세트 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
        ) from exc
    reference, _flat_assets = _resolve_pose_reference(
        block,
        clothing_type=clothing_type,
        gender=gender,
        registry=registry,
        flat_assets=None,
    )
    return reference


def resolve_published_example_reference(
    block: dict,
    *,
    clothing_type: str,
    gender: str,
    scope: str,
) -> dict:
    """Resolve one released set member as an ordinary generation example.

    A member selected outside a production ``spaceGroupId`` may contribute its
    complete image (``all``) or transparent pose (``pose``).  It never grants
    the set's representative plate, and ``bg`` is deliberately unavailable.
    """

    if scope not in ("all", "pose"):
        raise SpaceSetBindingError(
            "space_set_example_scope_invalid",
            "공간 세트의 개별 사진은 전부 참고 또는 포즈만 참고할 수 있어요.",
        )
    example_id = block.get("exampleId") or block.get("example_id")
    if not _is_safe_id(example_id, prefix="ss_"):
        raise SpaceSetBindingError(
            "space_set_example_id_invalid",
            "공간 세트의 생성예시 식별자가 올바르지 않아요.",
        )
    try:
        _base_url, registry = load_space_set_registry()
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise SpaceSetBindingError(
            "space_set_registry_unavailable",
            "공간 세트 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
        ) from exc
    member = next(
        (
            candidate
            for set_entry in registry.values()
            for candidate in set_entry["members"]
            if candidate["exampleId"] == example_id
        ),
        None,
    )
    if member is None or not member.get(scope):
        raise SpaceSetBindingError(
            "space_set_example_unavailable",
            "선택한 공간 세트 생성예시를 찾을 수 없어요. 다른 예시를 골라주세요.",
        )
    compatible = (
        member.get("cutType") == block.get("cutType")
        and (
            scope == "all"
            or member.get("direction") == block.get("direction")
        )
        and member.get("gender") == gender
        and clothing_type in (member.get("applicableClothingTypes") or [])
    )
    if not compatible:
        raise SpaceSetBindingError(
            "space_set_example_incompatible",
            "현재 컷과 맞지 않는 생성예시예요. 컷 종류와 방향을 확인해 주세요.",
        )
    return {
        "source": "space-set",
        "exampleId": example_id,
        "scope": scope,
        "asset": member[scope],
    }


def bind_storyboard_space_sets(
    blocks: list,
    *,
    clothing_type: str,
    gender: str,
) -> dict[int, dict]:
    """Bind each production group to one released location and compatible poses.

    The set id authorizes the representative location plate, not an immutable
    copy of the set's original member tuple.  Sellers may add/remove members and
    choose another published pose.  The current run must stay contiguous, and
    every selected pose must exactly match the block's current worn-cut recipe,
    gender and product applicability.
    """

    grouped: dict[str, tuple[str, list[tuple[int, dict]]]] = {}
    for index, block in enumerate(blocks or []):
        if not isinstance(block, dict):
            continue
        group_id = block.get("spaceGroupId") or block.get("space_group_id")
        parsed = parse_space_set_group_id(group_id)
        if parsed is None:
            continue
        set_id, _instance = parsed
        if group_id not in grouped:
            grouped[group_id] = (set_id, [])
        elif grouped[group_id][0] != set_id:
            raise SpaceSetBindingError(
                "space_set_id_mismatch", "공간 세트 구성이 올바르지 않아요."
            )
        grouped[group_id][1].append((index, block))

    if not grouped:
        return {}
    try:
        _base_url, registry = load_space_set_registry()
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise SpaceSetBindingError(
            "space_set_registry_unavailable",
            "공간 세트 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
        ) from exc

    flat_assets: dict[str, dict] | None = None

    bindings: dict[int, dict] = {}
    for group_id, (set_id, indexed_group_blocks) in grouped.items():
        set_entry = registry.get(set_id)
        if not set_entry:
            raise SpaceSetBindingError(
                "unknown_space_set",
                "저장된 공간 세트를 찾을 수 없어요. 세트를 다시 선택해 주세요.",
            )
        if set_entry["gender"] != gender:
            raise SpaceSetBindingError(
                "space_set_gender_mismatch",
                "모델 조건에 맞지 않는 공간 세트예요. 다른 세트를 골라주세요.",
            )
        if clothing_type not in set_entry["applicableClothingTypes"]:
            raise SpaceSetBindingError(
                "space_set_not_applicable",
                "상품 종류에 맞지 않는 공간 세트예요. 다른 세트를 골라주세요.",
            )
        if any(
            block.get("spaceVariation") != set_entry["spaceVariation"]
            for _position, block in indexed_group_blocks
        ):
            raise SpaceSetBindingError(
                "space_set_variation_mismatch",
                "촬영 세트의 공간 변화 설정이 발행 정보와 맞지 않아요. 세트를 다시 선택해 주세요.",
            )
        positions = [position for position, _block in indexed_group_blocks]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise SpaceSetBindingError(
                "space_set_members_not_contiguous",
                "공간 세트 사진이 떨어져 있어요. 세트를 다시 선택해 주세요.",
            )
        group_blocks = [block for _position, block in indexed_group_blocks]
        for block in group_blocks:
            pose_reference, flat_assets = _resolve_pose_reference(
                block,
                clothing_type=clothing_type,
                gender=gender,
                registry=registry,
                flat_assets=flat_assets,
            )
            bindings[id(block)] = {
                "groupId": group_id,
                "set": set_entry,
                "poseReference": pose_reference,
            }
    return bindings


def validate_storyboard_space_sets(
    blocks: list, *, clothing_type: str, gender: str
) -> tuple[str, str] | None:
    try:
        bind_storyboard_space_sets(
            blocks, clothing_type=clothing_type, gender=gender
        )
    except SpaceSetBindingError as exc:
        return exc.code, exc.message
    return None


def resolve_asset_url(asset: dict, base_url: str | None = None) -> str | None:
    key = asset.get("key")
    base = (base_url or "").rstrip("/")
    key_parts = urlsplit(key) if isinstance(key, str) else None
    if (
        "url" in asset
        or not isinstance(key, str)
        or not key
        or key_parts is None
        or key_parts.scheme
        or key_parts.netloc
        or key_parts.query
        or key_parts.fragment
        or key.startswith("/")
        or "\\" in key
        or re.fullmatch(r"[A-Za-z0-9_./-]+", key) is None
        or any(part in ("", ".", "..") for part in key.split("/"))
        or not base
    ):
        return None
    resolved = f"{base}/{key.lstrip('/')}"
    base_parts, resolved_parts = urlsplit(base), urlsplit(resolved)
    if (
        base_parts.scheme not in ("http", "https")
        or not base_parts.hostname
        or resolved_parts.scheme != base_parts.scheme
        or resolved_parts.hostname != base_parts.hostname
        or resolved_parts.port != base_parts.port
    ):
        return None
    return resolved


async def load_space_set_image(
    settings: Settings, asset: dict, *, role: str
) -> InlineImage:
    """Fetch a required published set asset; unlike flat examples, never degrade."""

    del settings  # kept in the signature for parity with the flat asset loader
    base_url, _sets = load_space_set_registry()
    url = resolve_asset_url(asset, base_url)
    if not url:
        raise SpaceSetBindingError(
            "space_set_asset_unavailable",
            f"공간 세트 {role} 자산을 불러오지 못했어요.",
        )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT, follow_redirects=False
            ) as client:
                response = await client.get(url)
            if 300 <= response.status_code < 400:
                raise ValueError("space set asset redirect rejected")
            response.raise_for_status()
            response_mime = (
                response.headers.get("content-type") or ""
            ).split(";", 1)[0].strip().lower()
            asset_mime = str(asset.get("mime") or "").strip().lower()
            if response_mime != asset_mime or not response.content:
                raise ValueError("space set asset response is not an image")
            expected_sha = asset.get("sha256")
            if (
                not isinstance(expected_sha, str)
                or hashlib.sha256(response.content).hexdigest() != expected_sha
            ):
                raise ValueError("space set asset hash mismatch")
            try:
                with Image.open(BytesIO(response.content)) as image:
                    actual_size = image.size
            except Exception as exc:
                raise ValueError("space set asset image decode failed") from exc
            if actual_size != (asset.get("width"), asset.get("height")):
                raise ValueError("space set asset dimensions mismatch")
            return InlineImage(asset_mime, response.content)
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
    log.warning("space-set %s asset unavailable after retries: %r", role, last_error)
    raise SpaceSetBindingError(
        "space_set_asset_unavailable",
        f"공간 세트 {role} 자산을 불러오지 못했어요.",
    )
