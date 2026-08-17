"""Hash-bound service-example directing metadata for the confirmed GPT profile.

The confirmed profile must not guess pose semantics from an example filename or
silently reuse metadata after an example image is replaced.  Curated metadata
lives beside the server-owned example asset and is released only after the
downloaded ``all`` image bytes match its pinned SHA-256.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from .confirmed_gpt_prompt import CutLock, OutfitLock, PoseSemantics


_SERVER_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_EXAMPLE_ASSETS = _SERVER_DIR / "app" / "data" / "example_assets.json"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DIRECTING_FIELDS = frozenset(
    {
        "allSha256",
        "directionDescription",
        "faceExposure",
        "requestedFraming",
        "fixedFootwear",
        "fixedInner",
        "poseSemantics",
    }
)
_POSE_FIELDS = frozenset(
    {
        "action",
        "bodyDirection",
        "weightAndSupport",
        "keyContacts",
        "gaze",
        "roughFraming",
    }
)
_EXACT_ELIGIBILITY_FIELD = "confirmedGptEligibleV1"


class ConfirmedGptDirectingError(ValueError):
    """The selected example cannot prove the confirmed directing contract."""


@dataclass(frozen=True)
class ConfirmedGptDirecting:
    example_id: str
    all_url: str
    all_sha256: str
    applicable_clothing_types: tuple[str, ...]
    shot: str
    direction_description: str
    face_exposure: str
    requested_framing: str
    fixed_footwear: str
    fixed_inner: str | None
    pose_semantics: PoseSemantics

    def cut_lock(self) -> CutLock:
        return CutLock(
            shot=self.shot,
            user_direction="front",
            direction_description=self.direction_description,
            face_exposure=self.face_exposure,
            requested_framing=self.requested_framing,
        )

    def outfit_lock(self, *, matching_attached: bool) -> OutfitLock:
        return OutfitLock(
            fixed_inner=self.fixed_inner,
            fixed_footwear=self.fixed_footwear,
            matching_attached=matching_attached,
        )


def _line(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _CONTROL_RE.search(value)
    ):
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_invalid_line:{field}"
        )
    return value


def _parse_entry(example_id: str, raw: object) -> ConfirmedGptDirecting | None:
    if not isinstance(raw, Mapping):
        return None
    directing = raw.get("confirmedGptDirectingV1")
    eligibility = raw.get(_EXACT_ELIGIBILITY_FIELD)
    if eligibility is not None and eligibility is not False:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_invalid_eligibility:{example_id}"
        )
    if eligibility is False and directing is not None:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_conflicting_eligibility:{example_id}"
        )
    if directing is None:
        return None
    if not isinstance(directing, Mapping) or set(directing) != _DIRECTING_FIELDS:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_invalid_fields:{example_id}"
        )
    if raw.get("cutType") != "styling" or raw.get("direction") != "front":
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_invalid_example_scope:{example_id}"
        )
    shot = raw.get("shot")
    if shot not in {"full", "medium"}:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_invalid_example_shot:{example_id}"
        )
    all_url = _line(raw.get("all"), f"{example_id}.all")
    applicable = raw.get("applicableClothingTypes")
    if (
        not isinstance(applicable, list)
        or not applicable
        or not all(isinstance(value, str) and value for value in applicable)
        or len(applicable) != len(set(applicable))
    ):
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_invalid_clothing_types:{example_id}"
        )
    all_sha256 = directing.get("allSha256")
    if not isinstance(all_sha256, str) or not _SHA256_RE.fullmatch(all_sha256):
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_invalid_sha256:{example_id}"
        )
    pose = directing.get("poseSemantics")
    if not isinstance(pose, Mapping) or set(pose) != _POSE_FIELDS:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_invalid_pose_fields:{example_id}"
        )
    fixed_inner = directing.get("fixedInner")
    if fixed_inner is not None:
        fixed_inner = _line(fixed_inner, f"{example_id}.fixedInner")
    return ConfirmedGptDirecting(
        example_id=example_id,
        all_url=all_url,
        all_sha256=all_sha256,
        applicable_clothing_types=tuple(applicable),
        shot=shot,
        direction_description=_line(
            directing.get("directionDescription"),
            f"{example_id}.directionDescription",
        ),
        face_exposure=_line(
            directing.get("faceExposure"), f"{example_id}.faceExposure"
        ),
        requested_framing=_line(
            directing.get("requestedFraming"), f"{example_id}.requestedFraming"
        ),
        fixed_footwear=_line(
            directing.get("fixedFootwear"), f"{example_id}.fixedFootwear"
        ),
        fixed_inner=fixed_inner,
        pose_semantics=PoseSemantics(
            action=_line(pose.get("action"), f"{example_id}.pose.action"),
            body_direction=_line(
                pose.get("bodyDirection"), f"{example_id}.pose.bodyDirection"
            ),
            weight_and_support=_line(
                pose.get("weightAndSupport"),
                f"{example_id}.pose.weightAndSupport",
            ),
            key_contacts=_line(
                pose.get("keyContacts"), f"{example_id}.pose.keyContacts"
            ),
            gaze=_line(pose.get("gaze"), f"{example_id}.pose.gaze"),
            rough_framing=_line(
                pose.get("roughFraming"), f"{example_id}.pose.roughFraming"
            ),
        ),
    )


@lru_cache(maxsize=8)
def _load_registry(path: str) -> dict[str, object]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfirmedGptDirectingError(
            "confirmed_gpt_directing_catalog_unavailable"
        ) from exc
    assets = raw.get("assets") if isinstance(raw, Mapping) else None
    if not isinstance(assets, Mapping):
        raise ConfirmedGptDirectingError(
            "confirmed_gpt_directing_catalog_invalid"
        )
    return dict(assets)


@lru_cache(maxsize=8)
def _load_catalog(path: str) -> dict[str, ConfirmedGptDirecting]:
    assets = _load_registry(path)
    result: dict[str, ConfirmedGptDirecting] = {}
    for raw_id, entry in assets.items():
        example_id = _line(raw_id, "exampleId")
        parsed = _parse_entry(example_id, entry)
        if parsed is not None:
            result[example_id] = parsed
    return result


def load_confirmed_gpt_directing_catalog(
    registry_path: str | Path | None = None,
) -> dict[str, ConfirmedGptDirecting]:
    """Load every curated entry, rejecting any malformed curated metadata."""

    path = Path(registry_path) if registry_path is not None else _DEFAULT_EXAMPLE_ASSETS
    return dict(_load_catalog(str(path.resolve())))


def confirmed_gpt_explicitly_excluded(
    example_id: str,
    registry_path: str | Path | None = None,
) -> bool:
    """Return a reviewed, server-owned opt-out from the exact profile.

    Absence is deliberately *not* an opt-out: a newly published structurally eligible
    example without directing metadata must still enter the exact path and fail closed.
    Only an explicit ``false`` marker makes the generic route the intended route.
    """

    path = Path(registry_path) if registry_path is not None else _DEFAULT_EXAMPLE_ASSETS
    raw = _load_registry(str(path.resolve())).get(str(example_id))
    if not isinstance(raw, Mapping):
        return False
    # Reuse the same validation that rejects true/metadata conflicts.
    _parse_entry(str(example_id), raw)
    return raw.get(_EXACT_ELIGIBILITY_FIELD) is False


def bind_confirmed_gpt_directing(
    example_id: str,
    all_image_bytes: bytes,
    *,
    shot: str,
    direction: str,
    clothing_type: str,
    registry_path: str | Path | None = None,
) -> ConfirmedGptDirecting:
    """Bind curated prose only to its exact released image and requested cut.

    Missing metadata, an unsupported cut, or byte drift raises; this function
    deliberately has no generic fallback.
    """

    catalog = load_confirmed_gpt_directing_catalog(registry_path)
    metadata = catalog.get(str(example_id))
    if metadata is None:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_not_curated:{example_id}"
        )
    if direction != "front" or shot != metadata.shot:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_cut_mismatch:{example_id}"
        )
    if clothing_type not in metadata.applicable_clothing_types:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_clothing_mismatch:{example_id}"
        )
    if not isinstance(all_image_bytes, bytes) or not all_image_bytes:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_image_bytes_required:{example_id}"
        )
    observed = sha256(all_image_bytes).hexdigest()
    if observed != metadata.all_sha256:
        raise ConfirmedGptDirectingError(
            f"confirmed_gpt_directing_hash_mismatch:{example_id}:"
            f"{observed}!={metadata.all_sha256}"
        )
    return metadata
