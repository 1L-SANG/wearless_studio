"""공간 세트 manifest v1 검증·스테이징·업로드 도구.

출시 대상을 추론하지 않고 ``documents/space_set_release_contract.md``에 따라
manifest에 명시된 세트만 소비한다. 기존 개별 생성예시 카탈로그/레지스트리는
읽거나 쓰지 않는다. 업로드는 ``--upload``만으로는 dry-run이며
``--execute``를 함께 지정해야 R2에 쓴다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError


SERVER_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = SERVER_DIR.parent
DEFAULT_SERVER_REGISTRY_PATH = SERVER_DIR / "app" / "data" / "space_set_assets.json"
DEFAULT_FRONTEND_CATALOG_PATH = REPO_DIR / "src" / "data" / "storyboardSpaceSets.json"
FLAT_FRONTEND_CATALOG_PATH = REPO_DIR / "src" / "data" / "genExamples.json"
FLAT_SERVER_REGISTRY_PATH = SERVER_DIR / "app" / "data" / "example_assets.json"
PLACE_TYPES_PATH = REPO_DIR / "data" / "storyboard_space_place_types.json"
R2_PREFIX = "seed/genexamples/space-sets/v1/releases"
THUMB_MAX_SIDE = 480
THUMB_QUALITY = 82

_SPACE_SET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_CLOTHING_TYPES = {"top", "bottom", "outer", "dress"}
_GENDERS = {"women", "men"}
_SET_TYPES = {"styling", "horizon-rotation", "horizon-sequence"}
_CUT_TYPES = {"styling", "horizon"}
_SHOTS = {"full", "medium"}
_DIRECTIONS = {"front", "side", "back"}
_SPACE_VARIATIONS = {"subtle", "fixed"}
_PLATE_POLICIES = {"required", "not-required"}
_QC_GATES = {
    "sameSpace",
    "sourceSimilarity",
    "naturalBodyPose",
    "lightingIntegration",
    "identityGarmentIntegrity",
}
_LEGACY_PROMPT_EXCEPTION_SETS = {
    "set-style-women-dress-neighborhood-garage-modimood-3266-root04",
    "set-style-women-dress-night-riverwalk-maybins-40948-root07",
}


def _load_place_types(path: Path = PLACE_TYPES_PATH) -> frozenset[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("storyboard space place-type vocabulary is invalid")
    items = raw.get("placeTypes")
    values = [
        item.get("value")
        for item in items or []
        if isinstance(item, dict)
    ]
    if (
        raw.get("schemaVersion") != 1
        or not isinstance(items, list)
        or not items
        or any(not isinstance(value, str) or not value for value in values)
        or len(values) != len(items)
        or len(values) != len(set(values))
    ):
        raise RuntimeError("storyboard space place-type vocabulary is invalid")
    return frozenset(values)


_PLACE_TYPES = _load_place_types()

_TOP_LEVEL_FIELDS = {"schemaVersion", "releaseId", "releasedAt", "sets"}
_SET_FIELDS = {
    "setId",
    "name",
    "setType",
    "gender",
    "applicableClothingTypes",
    "placeType",
    "tone",
    "compositionLabel",
    "spaceVariation",
    "platePolicy",
    "representativePlate",
    "qc",
    "members",
}
_MEMBER_FIELDS = {
    "exampleId",
    "order",
    "cutType",
    "shot",
    "direction",
    "all",
    "pose",
    "thumb",
}
_ASSET_FIELDS = {
    "localPath",
    "key",
    "sha256",
    "width",
    "height",
    "promptLineage",
    "reviewedProvenanceException",
    "derivedFrom",
}
_FRONTEND_SET_FIELDS = {
    "setId",
    "id",
    "name",
    "setType",
    "gender",
    "applicableClothingTypes",
    "placeType",
    "place",
    "tone",
    "compositionLabel",
    "spaceVariation",
    "platePolicy",
    "representativePlate",
    "members",
}
_FRONTEND_MEMBER_FIELDS = {
    "exampleId",
    "order",
    "cutType",
    "shot",
    "direction",
    "allUrl",
    "thumbUrl",
}
_PUBLISHED_SET_FIELDS = _SET_FIELDS - {"qc"}
_PUBLISHED_MEMBER_FIELDS = {
    "exampleId",
    "order",
    "cutType",
    "shot",
    "direction",
    "all",
    "pose",
}
_PUBLISHED_ASSET_FIELDS = {"key", "sha256", "width", "height", "mime"}
_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class SpaceSetReleaseValidationError(ValueError):
    """manifest 전체 검증 결과를 한 번에 전달한다."""

    def __init__(self, violations: list[str]):
        super().__init__("space-set release validation failed")
        self.violations = violations


@dataclass(frozen=True)
class AssetFile:
    owner_id: str
    variant: str
    path: Path
    r2_key: str
    mime: str

    @property
    def size(self) -> int:
        return self.path.stat().st_size


@dataclass(frozen=True)
class SpaceSetReleaseResult:
    release_id: str
    output_dir: Path
    frontend_catalog_path: Path
    server_registry_path: Path
    audit_path: Path
    assets: tuple[AssetFile, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class UploadReceipt:
    """실제 업로드가 끝났다는 증거. 이것 없이는 적용할 수 없다."""

    release_id: str
    uploaded_keys: frozenset[str]


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SpaceSetReleaseValidationError(
            [f"manifest를 읽을 수 없습니다: {path} ({exc})"]
        ) from exc
    except json.JSONDecodeError as exc:
        raise SpaceSetReleaseValidationError(
            [f"manifest JSON이 올바르지 않습니다: {exc}"]
        ) from exc
    if not isinstance(value, dict):
        raise SpaceSetReleaseValidationError(["manifest 최상위 값은 object여야 합니다"])
    return value


def load_flat_example_ids(
    frontend_path: Path | None = None,
    server_path: Path | None = None,
) -> set[str]:
    """현재 flat 카탈로그와 런타임 레지스트리의 ID 합집합을 읽는다."""
    frontend = (frontend_path or FLAT_FRONTEND_CATALOG_PATH).resolve()
    server = (server_path or FLAT_SERVER_REGISTRY_PATH).resolve()
    violations: list[str] = []
    try:
        frontend_value = json.loads(frontend.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceSetReleaseValidationError(
            [f"기존 flat 프론트 카탈로그를 읽을 수 없습니다: {frontend} ({exc})"]
        ) from exc
    if not isinstance(frontend_value, list):
        violations.append("기존 flat 프론트 카탈로그는 배열이어야 합니다")
        frontend_value = []
    frontend_ids: set[str] = set()
    for index, item in enumerate(frontend_value):
        example_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(example_id, str) or not example_id:
            violations.append(f"기존 flat 프론트 카탈로그[{index}].id가 올바르지 않습니다")
        elif example_id in frontend_ids:
            violations.append(f"기존 flat 프론트 카탈로그 id가 중복됐습니다: {example_id}")
        else:
            frontend_ids.add(example_id)

    try:
        server_value = json.loads(server.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceSetReleaseValidationError(
            [f"기존 flat 서버 레지스트리를 읽을 수 없습니다: {server} ({exc})"]
        ) from exc
    assets = server_value.get("assets") if isinstance(server_value, dict) else None
    if not isinstance(assets, dict):
        violations.append("기존 flat 서버 레지스트리 assets는 object여야 합니다")
        assets = {}
    server_ids = set(assets)
    if any(not isinstance(example_id, str) or not example_id for example_id in server_ids):
        violations.append("기존 flat 서버 레지스트리에 올바르지 않은 id가 있습니다")

    if violations:
        raise SpaceSetReleaseValidationError(violations)
    return frontend_ids | server_ids


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_datetime(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _unknown_fields(value: dict, allowed: set[str], label: str, violations: list[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        violations.append(f"{label}에 허용되지 않은 필드가 있습니다: {unknown}")


def _valid_space_set_id(value: object, *, example: bool = False) -> bool:
    if not isinstance(value, str) or not _SPACE_SET_ID.fullmatch(value):
        return False
    if "__" in value:
        return False
    return not example or value.startswith("ss_")


def _resolved_local_path(asset_root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    relative = Path(value)
    if relative.is_absolute():
        return None
    candidate = (asset_root / relative).resolve()
    try:
        candidate.relative_to(asset_root)
    except ValueError:
        return None
    return candidate


def _normalized_extension(path: Path, variant: str) -> str:
    if variant == "thumb":
        return ".webp"
    suffix = path.suffix.lower()
    return ".jpg" if suffix == ".jpeg" else suffix


def _expected_key(
    release_id: str,
    *,
    variant: str,
    owner_id: str,
    local_path: Path,
) -> str:
    return (
        f"{R2_PREFIX}/{release_id}/{variant}/{owner_id}"
        f"{_normalized_extension(local_path, variant)}"
    )


def _validate_prompt_lineage(
    lineage: object,
    asset_root: Path,
    label: str,
    violations: list[str],
) -> None:
    if not isinstance(lineage, dict):
        violations.append(f"{label}.promptLineage는 object여야 합니다")
        return
    _unknown_fields(
        lineage,
        {"promptPath", "sha256", "model"},
        f"{label}.promptLineage",
        violations,
    )
    prompt_path = _resolved_local_path(asset_root, lineage.get("promptPath"))
    if prompt_path is None:
        violations.append(
            f"{label}.promptLineage.promptPath는 자산 루트 안의 상대 경로여야 합니다"
        )
    elif not prompt_path.is_file():
        violations.append(
            f"{label}.promptLineage.promptPath가 존재하지 않습니다: "
            f"{lineage.get('promptPath')}"
        )
    expected_hash = lineage.get("sha256")
    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
        violations.append(f"{label}.promptLineage.sha256은 64자리 16진수여야 합니다")
    elif prompt_path is not None and prompt_path.is_file():
        actual_hash = _sha256(prompt_path)
        if actual_hash != expected_hash.lower():
            violations.append(
                f"{label}.promptLineage.sha256 불일치: "
                f"expected={expected_hash.lower()} actual={actual_hash}"
            )
    if not isinstance(lineage.get("model"), str) or not lineage["model"].strip():
        violations.append(f"{label}.promptLineage.model은 비어 있지 않은 문자열이어야 합니다")


def _validate_provenance_exception(
    exception: object,
    *,
    set_id: str,
    variant: str,
    label: str,
    violations: list[str],
) -> None:
    if set_id not in _LEGACY_PROMPT_EXCEPTION_SETS or variant != "all":
        violations.append(
            f"{label}.reviewedProvenanceException은 계약에 명시된 두 legacy 세트의 "
            "all 자산에만 허용합니다"
        )
        return
    if not isinstance(exception, dict):
        violations.append(f"{label}.reviewedProvenanceException은 object여야 합니다")
        return
    _unknown_fields(
        exception,
        {"code", "reason", "reviewedBy", "reviewedAt"},
        f"{label}.reviewedProvenanceException",
        violations,
    )
    if exception.get("code") != "legacy-approved-missing-prompt":
        violations.append(
            f"{label}.reviewedProvenanceException.code가 계약값과 다릅니다"
        )
    if not isinstance(exception.get("reason"), str) or not exception["reason"].strip():
        violations.append(
            f"{label}.reviewedProvenanceException.reason은 비어 있지 않아야 합니다"
        )
    if exception.get("reviewedBy") != "owner":
        violations.append(
            f"{label}.reviewedProvenanceException.reviewedBy는 owner여야 합니다"
        )
    if not _iso_datetime(exception.get("reviewedAt")):
        violations.append(
            f"{label}.reviewedProvenanceException.reviewedAt은 ISO 8601이어야 합니다"
        )


def _validate_asset(
    spec: object,
    *,
    asset_root: Path,
    release_id: str,
    set_id: str,
    owner_id: str,
    variant: str,
    label: str,
    violations: list[str],
) -> Path | None:
    if not isinstance(spec, dict):
        violations.append(f"{label}는 object여야 합니다")
        return None
    _unknown_fields(spec, _ASSET_FIELDS, label, violations)

    path = _resolved_local_path(asset_root, spec.get("localPath"))
    if path is None:
        violations.append(f"{label}.localPath는 자산 루트 안의 상대 경로여야 합니다")
        return None
    if not path.is_file():
        violations.append(f"{label}.localPath가 존재하지 않습니다: {spec.get('localPath')}")
        return None

    expected_key = _expected_key(
        release_id,
        variant=variant,
        owner_id=owner_id,
        local_path=path,
    )
    if spec.get("key") != expected_key:
        violations.append(
            f"{label}.key가 경로 규약과 다릅니다: expected={expected_key} "
            f"actual={spec.get('key')}"
        )

    expected_hash = spec.get("sha256")
    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
        violations.append(f"{label}.sha256은 64자리 16진수여야 합니다")
    else:
        actual_hash = _sha256(path)
        if actual_hash != expected_hash.lower():
            violations.append(
                f"{label}.sha256 불일치: expected={expected_hash.lower()} "
                f"actual={actual_hash}"
            )

    try:
        with Image.open(path) as image:
            image.load()
            actual_size = image.size
            image_format = image.format
            bands = image.getbands()
            metadata = set(image.info).intersection({"exif", "icc_profile", "xmp"})
    except (OSError, UnidentifiedImageError) as exc:
        violations.append(f"{label}는 읽을 수 있는 이미지여야 합니다: {exc}")
        return path

    allowed_suffixes = {
        "PNG": {".png"},
        "JPEG": {".jpg", ".jpeg"},
        "WEBP": {".webp"},
    }
    if image_format not in allowed_suffixes:
        violations.append(f"{label} 이미지 형식은 PNG|JPEG|WebP만 허용합니다: {image_format}")
    elif path.suffix.lower() not in allowed_suffixes[image_format]:
        violations.append(
            f"{label} 확장자와 실제 이미지 형식이 다릅니다: "
            f"suffix={path.suffix.lower()} format={image_format}"
        )

    width, height = spec.get("width"), spec.get("height")
    if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
        violations.append(f"{label}.width는 양의 정수여야 합니다")
    if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
        violations.append(f"{label}.height는 양의 정수여야 합니다")
    if (
        isinstance(width, int)
        and not isinstance(width, bool)
        and isinstance(height, int)
        and not isinstance(height, bool)
        and (width, height) != actual_size
    ):
        violations.append(
            f"{label} 이미지 크기 불일치: manifest={width}x{height} "
            f"actual={actual_size[0]}x{actual_size[1]}"
        )

    lineage = spec.get("promptLineage")
    exception = spec.get("reviewedProvenanceException")
    if variant == "thumb":
        if spec.get("derivedFrom") != "all":
            violations.append(f"{label}.derivedFrom은 all이어야 합니다")
        if lineage is not None or exception is not None:
            violations.append(f"{label} thumb에는 생성 프롬프트 계보를 넣지 않습니다")
        if image_format != "WEBP":
            violations.append(f"{label}는 WebP여야 합니다")
        if max(actual_size) > THUMB_MAX_SIDE:
            violations.append(f"{label}의 최대 변은 {THUMB_MAX_SIDE}px 이하여야 합니다")
        if metadata:
            violations.append(f"{label}에 제거되지 않은 메타데이터가 있습니다: {sorted(metadata)}")
    else:
        if "derivedFrom" in spec:
            violations.append(f"{label}.derivedFrom은 thumb에만 허용합니다")
        if (lineage is None) == (exception is None):
            violations.append(
                f"{label}는 promptLineage 또는 reviewedProvenanceException 중 "
                "정확히 하나를 가져야 합니다"
            )
        elif lineage is not None:
            _validate_prompt_lineage(lineage, asset_root, label, violations)
        else:
            _validate_provenance_exception(
                exception,
                set_id=set_id,
                variant=variant,
                label=label,
                violations=violations,
            )
        if variant == "pose":
            if image_format != "PNG" or "A" not in bands:
                violations.append(f"{label}는 알파 채널을 포함한 PNG여야 합니다")
            else:
                with Image.open(path) as pose_image:
                    alpha_min, alpha_max = pose_image.getchannel("A").getextrema()
                if alpha_min != 0 or alpha_max == 0:
                    violations.append(
                        f"{label}는 실제 투명 배경과 보이는 인물을 모두 포함해야 합니다"
                    )
    return path


def _validate_qc(qc: object, label: str, violations: list[str]) -> None:
    if not isinstance(qc, dict):
        violations.append(f"{label}는 object여야 합니다")
        return
    _unknown_fields(qc, {"status", "reviewedAt", "reviewedBy", "gates"}, label, violations)
    if qc.get("status") != "pass":
        violations.append(f"{label}.status는 pass여야 합니다")
    if not _iso_datetime(qc.get("reviewedAt")):
        violations.append(f"{label}.reviewedAt은 ISO 8601이어야 합니다")
    if not isinstance(qc.get("reviewedBy"), str) or not qc["reviewedBy"].strip():
        violations.append(f"{label}.reviewedBy는 비어 있지 않은 문자열이어야 합니다")
    gates = qc.get("gates")
    if not isinstance(gates, dict):
        violations.append(f"{label}.gates는 object여야 합니다")
        return
    _unknown_fields(gates, _QC_GATES, f"{label}.gates", violations)
    missing = sorted(_QC_GATES - set(gates))
    if missing:
        violations.append(f"{label}.gates에 필수 gate가 없습니다: {missing}")
    failed = sorted(gate for gate in _QC_GATES if gates.get(gate) is not True)
    if failed:
        violations.append(f"{label}.gates는 모두 true여야 합니다: {failed}")


def validate_manifest(
    manifest: dict,
    asset_root: Path,
    *,
    flat_example_ids: set[str] | None = None,
) -> tuple[list[dict], dict[tuple[str, str, str], Path], list[str]]:
    """manifest 전체를 검증하고, 검증된 입력 자산 경로를 반환한다."""
    root = asset_root.resolve()
    violations: list[str] = []
    warnings: list[str] = []
    resolved_files: dict[tuple[str, str, str], Path] = {}
    if flat_example_ids is None:
        try:
            reserved_flat_ids = load_flat_example_ids()
        except SpaceSetReleaseValidationError as exc:
            violations.extend(exc.violations)
            reserved_flat_ids = set()
    else:
        reserved_flat_ids = set(flat_example_ids)

    _unknown_fields(manifest, _TOP_LEVEL_FIELDS, "manifest", violations)
    schema_version = manifest.get("schemaVersion")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        violations.append("schemaVersion은 정수 1이어야 합니다")
    release_id = manifest.get("releaseId")
    if not _valid_space_set_id(release_id):
        violations.append(
            "releaseId는 200자 이하 영문·숫자·_-만 사용하고 "
            "연속 밑줄(__)을 포함하지 않아야 합니다"
        )
        release_id = "#invalid-release"
    if not _iso_datetime(manifest.get("releasedAt")):
        violations.append("releasedAt은 ISO 8601 날짜시각이어야 합니다")
    if not root.is_dir():
        violations.append(f"자산 루트가 디렉터리가 아닙니다: {root}")

    sets = manifest.get("sets")
    if not isinstance(sets, list) or not sets:
        violations.append("sets는 비어 있지 않은 배열이어야 합니다")
        sets = []

    seen_set_ids: set[str] = set()
    seen_example_ids: set[str] = set()
    seen_keys: set[str] = set()
    for set_index, space_set in enumerate(sets):
        prefix = f"sets[{set_index}]"
        if not isinstance(space_set, dict):
            violations.append(f"{prefix}는 object여야 합니다")
            continue
        _unknown_fields(space_set, _SET_FIELDS, prefix, violations)
        set_id = space_set.get("setId")
        if not _valid_space_set_id(set_id):
            violations.append(
                f"{prefix}.setId는 200자 이하 영문·숫자·_-만 사용하고 "
                "연속 밑줄(__)을 포함하지 않아야 합니다"
            )
            set_id = f"#set-{set_index}"
        elif set_id in seen_set_ids:
            violations.append(f"중복 setId: {set_id}")
        else:
            seen_set_ids.add(set_id)

        for field in ("name", "placeType", "tone", "compositionLabel"):
            if not isinstance(space_set.get(field), str) or not space_set[field].strip():
                violations.append(f"{prefix}.{field}는 비어 있지 않은 문자열이어야 합니다")
        place_type = space_set.get("placeType")
        if isinstance(place_type, str) and place_type not in _PLACE_TYPES:
            violations.append(
                f"{prefix}.placeType이 통일 장소 어휘에 없습니다: {place_type}"
            )

        set_type = space_set.get("setType")
        if set_type not in _SET_TYPES:
            violations.append(f"{prefix}.setType이 허용값이 아닙니다: {set_type}")
        if space_set.get("gender") not in _GENDERS:
            violations.append(f"{prefix}.gender는 women|men이어야 합니다")
        applicable = space_set.get("applicableClothingTypes")
        if not isinstance(applicable, list) or not applicable:
            violations.append(
                f"{prefix}.applicableClothingTypes는 비어 있지 않은 배열이어야 합니다"
            )
            applicable = []
        else:
            if any(value not in _CLOTHING_TYPES for value in applicable):
                violations.append(
                    f"{prefix}.applicableClothingTypes에 허용되지 않은 값이 있습니다"
                )
            if len(applicable) != len(set(applicable)):
                violations.append(f"{prefix}.applicableClothingTypes에 중복이 있습니다")
            if space_set.get("gender") == "men" and "dress" in applicable:
                violations.append(
                    f"{prefix} 남성 원피스 적용 범위는 지원하지 않습니다"
                )
            if len(applicable) > 1 and set(applicable) != {"top", "outer"}:
                violations.append(
                    f"{prefix} 복수 적용 범위는 검토된 [top,outer]만 허용합니다"
                )

        if space_set.get("spaceVariation") not in _SPACE_VARIATIONS:
            violations.append(
                f"{prefix}.spaceVariation은 subtle|fixed여야 합니다"
            )
        plate_policy = space_set.get("platePolicy")
        if plate_policy not in _PLATE_POLICIES:
            violations.append(f"{prefix}.platePolicy는 required|not-required여야 합니다")
        if set_type == "horizon-sequence" and plate_policy != "not-required":
            violations.append(
                f"{prefix} horizon-sequence는 platePolicy=not-required여야 합니다"
            )
        representative_plate = space_set.get("representativePlate")
        if plate_policy == "not-required":
            if set_type != "horizon-sequence":
                violations.append(
                    f"{prefix} platePolicy=not-required는 horizon-sequence에만 허용합니다"
                )
            if representative_plate is not None:
                violations.append(
                    f"{prefix}.representativePlate는 platePolicy=not-required일 때 null이어야 합니다"
                )
        else:
            if representative_plate is None:
                violations.append(
                    f"{prefix}.representativePlate는 required 정책에서 필수입니다"
                )
            else:
                path = _validate_asset(
                    representative_plate,
                    asset_root=root,
                    release_id=release_id,
                    set_id=set_id,
                    owner_id=set_id,
                    variant="plate",
                    label=f"{prefix}.representativePlate",
                    violations=violations,
                )
                if path is not None:
                    resolved_files[(set_id, set_id, "plate")] = path

        _validate_qc(space_set.get("qc"), f"{prefix}.qc", violations)
        members = space_set.get("members")
        if not isinstance(members, list) or not 2 <= len(members) <= 5:
            violations.append(f"{prefix}.members는 2~5개 배열이어야 합니다")
            members = []

        member_shots: list[str] = []
        member_directions: list[str | None] = []
        for member_index, member in enumerate(members):
            member_prefix = f"{prefix}.members[{member_index}]"
            if not isinstance(member, dict):
                violations.append(f"{member_prefix}는 object여야 합니다")
                continue
            _unknown_fields(member, _MEMBER_FIELDS, member_prefix, violations)
            example_id = member.get("exampleId")
            if not _valid_space_set_id(example_id, example=True):
                violations.append(
                    f"{member_prefix}.exampleId는 ss_로 시작하는 200자 이하 "
                    "영문·숫자·_- 토큰이며 연속 밑줄(__)이 없어야 합니다"
                )
                example_id = f"#member-{set_index}-{member_index}"
            elif example_id in seen_example_ids:
                violations.append(f"중복 exampleId: {example_id}")
            elif example_id in reserved_flat_ids:
                violations.append(
                    f"{member_prefix}.exampleId가 기존 flat 생성예시 ID와 충돌합니다: "
                    f"{example_id}"
                )
            else:
                seen_example_ids.add(example_id)

            expected_order = member_index + 1
            if (
                not isinstance(member.get("order"), int)
                or isinstance(member.get("order"), bool)
                or member["order"] != expected_order
            ):
                violations.append(
                    f"{member_prefix}.order는 배열 순서와 같은 {expected_order}이어야 합니다"
                )
            cut_type = member.get("cutType")
            expected_cut = "styling" if set_type == "styling" else "horizon"
            if cut_type not in _CUT_TYPES:
                violations.append(
                    f"{member_prefix}.cutType이 허용값이 아닙니다: {cut_type}"
                )
            elif cut_type != expected_cut:
                violations.append(
                    f"{member_prefix}.cutType은 {set_type} 세트에서 {expected_cut}이어야 합니다"
                )
            shot = member.get("shot")
            member_shots.append(shot)
            if shot not in _SHOTS:
                violations.append(f"{member_prefix}.shot은 full|medium이어야 합니다")
            direction = member.get("direction")
            member_directions.append(direction)
            if direction not in _DIRECTIONS:
                violations.append(
                    f"{member_prefix}.direction은 front|side|back이어야 합니다"
                )

            for variant in ("all", "pose"):
                path = _validate_asset(
                    member.get(variant),
                    asset_root=root,
                    release_id=release_id,
                    set_id=set_id,
                    owner_id=example_id,
                    variant=variant,
                    label=f"{member_prefix}.{variant}",
                    violations=violations,
                )
                if path is not None:
                    resolved_files[(set_id, example_id, variant)] = path
            if "thumb" in member:
                thumb_path = _validate_asset(
                    member.get("thumb"),
                    asset_root=root,
                    release_id=release_id,
                    set_id=set_id,
                    owner_id=example_id,
                    variant="thumb",
                    label=f"{member_prefix}.thumb",
                    violations=violations,
                )
                if thumb_path is not None:
                    resolved_files[(set_id, example_id, "thumb")] = thumb_path
                all_path = resolved_files.get((set_id, example_id, "all"))
                if all_path is not None and thumb_path is not None:
                    try:
                        if thumb_path.read_bytes() != _thumbnail_bytes(all_path):
                            violations.append(
                                f"{member_prefix}.thumb가 all에서 고정 파라미터로 "
                                "파생한 바이트와 다릅니다"
                            )
                    except (OSError, UnidentifiedImageError) as exc:
                        violations.append(
                            f"{member_prefix}.thumb 결정성 검증에 실패했습니다: {exc}"
                        )

            for variant in ("all", "pose", "thumb"):
                spec = member.get(variant)
                if not isinstance(spec, dict):
                    continue
                key = spec.get("key")
                if isinstance(key, str):
                    if key in seen_keys:
                        violations.append(f"중복 자산 key: {key}")
                    seen_keys.add(key)

        if len(applicable) > 1 and any(shot != "full" for shot in member_shots):
            violations.append(
                f"{prefix} [top,outer] 공용 세트는 모든 멤버가 full이어야 합니다"
            )
        if set_type == "horizon-rotation" and (
            len(members) != 3
            or member_shots != ["full", "full", "full"]
            or member_directions != ["front", "side", "back"]
        ):
            violations.append(
                f"{prefix} horizon-rotation은 front→side→back full 3장이어야 합니다"
            )

        if isinstance(representative_plate, dict):
            key = representative_plate.get("key")
            if isinstance(key, str):
                if key in seen_keys:
                    violations.append(f"중복 자산 key: {key}")
                seen_keys.add(key)

    if violations:
        raise SpaceSetReleaseValidationError(violations)
    return sets, resolved_files, warnings


def _thumbnail_bytes(source: Path) -> bytes:
    """고정 파라미터·무메타 WebP 썸네일 바이트를 만든다."""
    with Image.open(source) as image:
        image.load()
        mode = "RGBA" if "A" in image.getbands() else "RGB"
        converted = image.convert(mode)
        clean = Image.new(mode, converted.size)
        clean.paste(converted)
        clean.thumbnail(
            (THUMB_MAX_SIDE, THUMB_MAX_SIDE),
            Image.Resampling.LANCZOS,
            reducing_gap=3.0,
        )
        output = BytesIO()
        clean.save(
            output,
            format="WEBP",
            quality=THUMB_QUALITY,
            method=6,
            optimize=False,
        )
    return output.getvalue()


def _public_url(public_base_url: str, key: str) -> str:
    return f"{public_base_url.rstrip('/')}/{key}"


def _published_asset(spec: dict, path: Path) -> dict:
    """런타임 레지스트리에 필요한 불변 자산 식별 정보만 남긴다."""
    return {
        "key": spec["key"],
        "sha256": _sha256(path),
        "width": spec["width"],
        "height": spec["height"],
        "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    }


def _asset_audit(spec: dict) -> dict:
    """QC/재현용 계보를 런타임 레지스트리와 분리해 보존한다."""
    audit = {
        "localPath": spec["localPath"],
        "key": spec["key"],
        "sha256": spec["sha256"].lower(),
    }
    if "promptLineage" in spec:
        audit["promptLineage"] = spec["promptLineage"]
    if "reviewedProvenanceException" in spec:
        audit["reviewedProvenanceException"] = spec["reviewedProvenanceException"]
    return audit


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalized_public_base(value: str) -> str:
    base = value.rstrip("/")
    parsed_url = urlsplit(base)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.path not in ("", "/")
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise SpaceSetReleaseValidationError(
            [
                "--public-base-url 또는 R2_PUBLIC_BASE는 경로·인증정보가 없는 "
                "http(s) origin이어야 합니다"
            ]
        )
    return base


def _write_stage_receipt(
    build_dir: Path,
    *,
    release_id: str,
    public_base_url: str,
    assets: list[AssetFile],
) -> None:
    catalog_names = (
        "storyboardSpaceSets.json",
        "space_set_assets.json",
        "space_set_release_audit.json",
    )
    payload = {
        "schemaVersion": 1,
        "releaseId": release_id,
        "publicBaseUrl": public_base_url,
        "catalogs": {
            name: {
                "path": name,
                "sha256": _sha256(build_dir / name),
                "size": (build_dir / name).stat().st_size,
            }
            for name in catalog_names
        },
        "assets": [
            {
                "ownerId": asset.owner_id,
                "variant": asset.variant,
                "path": str(asset.path.relative_to(build_dir)),
                "key": asset.r2_key,
                "mime": asset.mime,
                "sha256": _sha256(asset.path),
                "size": asset.size,
            }
            for asset in sorted(assets, key=lambda item: item.r2_key)
        ],
    }
    receipt = {
        **payload,
        "sealSha256": hashlib.sha256(_canonical_json_bytes(payload)).hexdigest(),
    }
    _write_json(build_dir / "release_stage.json", receipt)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
    try:
        shutil.copyfile(source, temp_path)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def _stage_verified_copy(
    source: Path,
    build_dir: Path,
    *,
    variant: str,
    owner_id: str,
    expected_sha256: str,
) -> Path:
    """업로드가 원본 변경에 영향받지 않도록 복제하고 복제본을 다시 해시한다."""
    destination = (
        build_dir
        / "assets"
        / variant
        / f"{owner_id}{source.suffix.lower()}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    copied_hash = _sha256(destination)
    if copied_hash != expected_sha256.lower():
        raise SpaceSetReleaseValidationError([
            f"스테이징 복제본 sha256 불일치: {variant}/{owner_id} "
            f"expected={expected_sha256.lower()} actual={copied_hash}"
        ])
    return destination


def stage_release(
    manifest_path: Path,
    asset_root: Path,
    *,
    public_base_url: str,
    output_dir: Path | None = None,
) -> SpaceSetReleaseResult:
    manifest_path = manifest_path.resolve()
    root = asset_root.resolve()
    manifest = _read_json(manifest_path)
    sets, source_files, warnings = validate_manifest(manifest, root)

    base = _normalized_public_base(public_base_url)

    release_id = manifest["releaseId"]
    destination = (
        output_dir or (SERVER_DIR / ".space-set-release" / release_id)
    ).resolve()
    if destination.exists():
        raise FileExistsError(f"스테이징 출력 경로가 이미 존재합니다: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    build_dir = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    assets: list[AssetFile] = []
    try:
        frontend_sets: list[dict] = []
        registry_sets: list[dict] = []
        audit_sets: list[dict] = []
        thumb_dir = build_dir / "assets" / "thumb"
        thumb_dir.mkdir(parents=True, exist_ok=True)

        for space_set in sets:
            set_id = space_set["setId"]
            plate_path = source_files.get((set_id, set_id, "plate"))
            plate_url = None
            published_plate = None
            plate_audit = None
            if plate_path is not None:
                plate_spec = space_set["representativePlate"]
                plate_key = plate_spec["key"]
                plate_url = _public_url(base, plate_key)
                staged_plate = _stage_verified_copy(
                    plate_path,
                    build_dir,
                    variant="plate",
                    owner_id=set_id,
                    expected_sha256=plate_spec["sha256"],
                )
                published_plate = _published_asset(plate_spec, staged_plate)
                plate_audit = _asset_audit(plate_spec)
                assets.append(
                    AssetFile(
                        set_id,
                        "plate",
                        staged_plate,
                        plate_key,
                        mimetypes.guess_type(plate_key)[0] or "application/octet-stream",
                    )
                )

            frontend_members: list[dict] = []
            registry_members: list[dict] = []
            audit_members: list[dict] = []
            for member in space_set["members"]:
                example_id = member["exampleId"]
                staged_variants = {
                    variant: _stage_verified_copy(
                        source_files[(set_id, example_id, variant)],
                        build_dir,
                        variant=variant,
                        owner_id=example_id,
                        expected_sha256=member[variant]["sha256"],
                    )
                    for variant in ("all", "pose")
                }
                staged_thumb = thumb_dir / f"{example_id}.webp"
                input_thumb = source_files.get((set_id, example_id, "thumb"))
                if input_thumb is None:
                    staged_thumb.write_bytes(
                        _thumbnail_bytes(staged_variants["all"])
                    )
                else:
                    staged_thumb = _stage_verified_copy(
                        input_thumb,
                        build_dir,
                        variant="thumb",
                        owner_id=example_id,
                        expected_sha256=member["thumb"]["sha256"],
                    )

                urls: dict[str, str] = {}
                for variant in ("all", "pose"):
                    spec = member[variant]
                    path = staged_variants[variant]
                    key = spec["key"]
                    urls[variant] = _public_url(base, key)
                    assets.append(
                        AssetFile(
                            example_id,
                            variant,
                            path,
                            key,
                            mimetypes.guess_type(key)[0] or "application/octet-stream",
                        )
                    )
                thumb_key = _expected_key(
                    release_id,
                    variant="thumb",
                    owner_id=example_id,
                    local_path=staged_thumb,
                )
                urls["thumb"] = _public_url(base, thumb_key)
                assets.append(
                    AssetFile(
                        example_id,
                        "thumb",
                        staged_thumb,
                        thumb_key,
                        "image/webp",
                    )
                )

                common_member = {
                    "exampleId": example_id,
                    "order": member["order"],
                    "cutType": member["cutType"],
                    "shot": member["shot"],
                    "direction": member["direction"],
                }
                frontend_members.append({
                    **common_member,
                    "thumbUrl": urls["thumb"],
                    "allUrl": urls["all"],
                })
                registry_members.append({
                    **common_member,
                    "all": _published_asset(
                        member["all"],
                        staged_variants["all"],
                    ),
                    "pose": _published_asset(
                        member["pose"],
                        staged_variants["pose"],
                    ),
                })
                audit_members.append({
                    "exampleId": example_id,
                    "all": _asset_audit(member["all"]),
                    "pose": _asset_audit(member["pose"]),
                })

            common_set = {
                "setId": set_id,
                "name": space_set["name"],
                "setType": space_set["setType"],
                "gender": space_set["gender"],
                "applicableClothingTypes": space_set["applicableClothingTypes"],
                "placeType": space_set["placeType"],
                "tone": space_set["tone"],
                "compositionLabel": space_set["compositionLabel"],
                "spaceVariation": space_set["spaceVariation"],
                "platePolicy": space_set["platePolicy"],
            }
            frontend_sets.append({
                **common_set,
                "id": set_id,
                "place": space_set["placeType"],
                "representativePlate": (
                    {"url": plate_url} if plate_url is not None else None
                ),
                "members": frontend_members,
            })
            registry_sets.append({
                **common_set,
                "representativePlate": published_plate,
                "members": registry_members,
            })
            audit_sets.append({
                "setId": set_id,
                "qc": space_set["qc"],
                "representativePlate": plate_audit,
                "members": audit_members,
            })

        frontend_meta = {
            "schemaVersion": 1,
            "releaseId": release_id,
            "releasedAt": manifest["releasedAt"],
            "defaultBaseUrl": base,
        }
        frontend_catalog = {"_meta": frontend_meta, "sets": frontend_sets}
        server_registry = {
            "schemaVersion": 1,
            "releaseId": release_id,
            "releasedAt": manifest["releasedAt"],
            "baseUrl": base,
            "placeTypes": sorted(_PLACE_TYPES),
            "sets": registry_sets,
        }
        release_audit = {
            "schemaVersion": 1,
            "releaseId": release_id,
            "releasedAt": manifest["releasedAt"],
            "sets": audit_sets,
        }
        frontend_path = build_dir / "storyboardSpaceSets.json"
        registry_path = build_dir / "space_set_assets.json"
        audit_path = build_dir / "space_set_release_audit.json"
        _write_json(frontend_path, frontend_catalog)
        _write_json(registry_path, server_registry)
        _write_json(audit_path, release_audit)
        _write_stage_receipt(
            build_dir,
            release_id=release_id,
            public_base_url=base,
            assets=assets,
        )
        os.replace(build_dir, destination)
    except Exception:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise

    remapped_assets = tuple(
        AssetFile(
            asset.owner_id,
            asset.variant,
            destination / asset.path.relative_to(build_dir)
            if asset.path.is_relative_to(build_dir)
            else asset.path,
            asset.r2_key,
            asset.mime,
        )
        for asset in assets
    )
    return SpaceSetReleaseResult(
        release_id=release_id,
        output_dir=destination,
        frontend_catalog_path=destination / "storyboardSpaceSets.json",
        server_registry_path=destination / "space_set_assets.json",
        audit_path=destination / "space_set_release_audit.json",
        assets=remapped_assets,
        warnings=tuple(warnings),
    )


def _verified_stage_path(
    stage_root: Path,
    relative_value: object,
    *,
    label: str,
    violations: list[str],
) -> Path | None:
    if not isinstance(relative_value, str) or not relative_value:
        violations.append(f"{label}.path는 비어 있지 않은 상대 경로여야 합니다")
        return None
    relative = Path(relative_value)
    if relative.is_absolute():
        violations.append(f"{label}.path는 상대 경로여야 합니다")
        return None
    candidate = stage_root / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(stage_root)
    except (OSError, ValueError):
        violations.append(f"{label}.path가 스테이징 경로 밖이거나 존재하지 않습니다")
        return None
    if not resolved.is_file() or candidate.is_symlink():
        violations.append(f"{label}.path는 일반 파일이어야 합니다")
        return None
    return resolved


def _validate_sealed_file(
    path: Path | None,
    spec: object,
    *,
    label: str,
    violations: list[str],
    allowed_fields: set[str] | None = None,
) -> None:
    if path is None or not isinstance(spec, dict):
        if not isinstance(spec, dict):
            violations.append(f"{label}는 object여야 합니다")
        return
    allowed = allowed_fields or {"path", "sha256", "size"}
    if set(spec) - allowed:
        violations.append(f"{label}에 허용되지 않은 필드가 있습니다")
    expected_hash = spec.get("sha256")
    expected_size = spec.get("size")
    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
        violations.append(f"{label}.sha256이 올바르지 않습니다")
    elif _sha256(path) != expected_hash.lower():
        violations.append(f"{label}.sha256이 승인된 스테이징 바이트와 다릅니다")
    if (
        not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size < 0
        or path.stat().st_size != expected_size
    ):
        violations.append(f"{label}.size가 승인된 스테이징 바이트와 다릅니다")


def load_staged_release(output_dir: Path) -> SpaceSetReleaseResult:
    """검토된 동일 staging을 다시 검증해 업로드·적용 입력으로 사용한다."""
    root = output_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"스테이징 경로가 존재하지 않습니다: {root}")
    receipt_path = root / "release_stage.json"
    receipt = _read_json(receipt_path)
    violations: list[str] = []
    expected_receipt_fields = {
        "schemaVersion",
        "releaseId",
        "publicBaseUrl",
        "catalogs",
        "assets",
        "sealSha256",
    }
    if set(receipt) != expected_receipt_fields:
        violations.append("release_stage.json 필드가 릴리스 도구 계약과 다릅니다")
    payload = {key: value for key, value in receipt.items() if key != "sealSha256"}
    seal = receipt.get("sealSha256")
    actual_seal = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    if not isinstance(seal, str) or not _SHA256.fullmatch(seal) or seal != actual_seal:
        violations.append("release_stage.json sealSha256이 일치하지 않습니다")

    release_id = receipt.get("releaseId")
    if not _valid_space_set_id(release_id):
        violations.append("release_stage.json releaseId가 올바르지 않습니다")
        release_id = "#invalid-release"
    try:
        public_base = _normalized_public_base(receipt.get("publicBaseUrl", ""))
    except SpaceSetReleaseValidationError as exc:
        violations.extend(exc.violations)
        public_base = ""

    expected_catalog_names = {
        "storyboardSpaceSets.json",
        "space_set_assets.json",
        "space_set_release_audit.json",
    }
    catalog_specs = receipt.get("catalogs")
    if not isinstance(catalog_specs, dict) or set(catalog_specs) != expected_catalog_names:
        violations.append("release_stage.json catalogs 목록이 올바르지 않습니다")
        catalog_specs = {}
    catalog_paths: dict[str, Path] = {}
    for name in sorted(expected_catalog_names):
        spec = catalog_specs.get(name)
        if not isinstance(spec, dict) or spec.get("path") != name:
            violations.append(f"release_stage.json catalogs.{name}.path가 다릅니다")
            continue
        path = _verified_stage_path(
            root,
            spec.get("path"),
            label=f"catalogs.{name}",
            violations=violations,
        )
        _validate_sealed_file(
            path,
            spec,
            label=f"catalogs.{name}",
            violations=violations,
        )
        if path is not None:
            catalog_paths[name] = path

    raw_assets = receipt.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        violations.append("release_stage.json assets는 비어 있지 않은 배열이어야 합니다")
        raw_assets = []
    assets: list[AssetFile] = []
    seen_paths: set[str] = set()
    seen_keys: set[str] = set()
    asset_by_key: dict[str, tuple[AssetFile, dict]] = {}
    for index, spec in enumerate(raw_assets):
        label = f"assets[{index}]"
        if not isinstance(spec, dict):
            violations.append(f"{label}는 object여야 합니다")
            continue
        if set(spec) != {
            "ownerId", "variant", "path", "key", "mime", "sha256", "size"
        }:
            violations.append(f"{label} 필드가 릴리스 도구 계약과 다릅니다")
        owner_id = spec.get("ownerId")
        variant = spec.get("variant")
        relative = spec.get("path")
        key = spec.get("key")
        mime = spec.get("mime")
        if variant not in {"plate", "all", "pose", "thumb"}:
            violations.append(f"{label}.variant가 올바르지 않습니다")
            continue
        if not _valid_space_set_id(owner_id, example=variant != "plate"):
            violations.append(f"{label}.ownerId가 올바르지 않습니다")
            continue
        path = _verified_stage_path(
            root,
            relative,
            label=label,
            violations=violations,
        )
        if path is None:
            continue
        expected_relative = f"assets/{variant}/{owner_id}{path.suffix.lower()}"
        if relative != expected_relative:
            violations.append(
                f"{label}.path가 variant/ownerId 경로와 일치하지 않습니다"
            )
        expected_key = _expected_key(
            release_id,
            variant=variant,
            owner_id=owner_id,
            local_path=path,
        )
        if key != expected_key:
            violations.append(f"{label}.key가 releaseId 경로와 일치하지 않습니다")
        expected_mime = (
            "image/webp"
            if variant == "thumb"
            else mimetypes.guess_type(str(key))[0] if isinstance(key, str) else None
        )
        if mime != expected_mime:
            violations.append(f"{label}.mime이 파일 형식과 일치하지 않습니다")
        _validate_sealed_file(
            path,
            spec,
            label=label,
            violations=violations,
            allowed_fields={
                "ownerId", "variant", "path", "key", "mime", "sha256", "size"
            },
        )
        if relative in seen_paths:
            violations.append(f"중복 스테이징 파일 경로: {relative}")
        if isinstance(key, str) and key in seen_keys:
            violations.append(f"중복 R2 key: {key}")
        seen_paths.add(relative)
        if isinstance(key, str):
            seen_keys.add(key)
        try:
            with Image.open(path) as image:
                image.load()
                if variant == "pose":
                    if image.format != "PNG" or "A" not in image.getbands():
                        violations.append(f"{label}는 투명 PNG여야 합니다")
                    else:
                        alpha_min, alpha_max = image.getchannel("A").getextrema()
                        if alpha_min != 0 or alpha_max == 0:
                            violations.append(
                                f"{label}는 실제 투명 배경과 보이는 인물을 포함해야 합니다"
                            )
                if variant == "thumb" and image.format != "WEBP":
                    violations.append(f"{label}는 WebP여야 합니다")
        except (OSError, UnidentifiedImageError) as exc:
            violations.append(f"{label} 이미지를 읽을 수 없습니다: {exc}")
        asset = AssetFile(owner_id, variant, path, key, mime)
        assets.append(asset)
        if isinstance(key, str):
            asset_by_key[key] = (asset, spec)

    assets_root = root / "assets"
    actual_asset_paths = {
        str(path.relative_to(root))
        for path in assets_root.rglob("*")
        if path.is_file()
    } if assets_root.is_dir() else set()
    if actual_asset_paths != seen_paths:
        missing = sorted(seen_paths - actual_asset_paths)
        extra = sorted(actual_asset_paths - seen_paths)
        violations.append(
            f"스테이징 assets 파일 목록이 seal과 다릅니다: missing={missing} extra={extra}"
        )

    if violations:
        raise SpaceSetReleaseValidationError(violations)

    frontend = _read_json(catalog_paths["storyboardSpaceSets.json"])
    registry = _read_json(catalog_paths["space_set_assets.json"])
    audit = _read_json(catalog_paths["space_set_release_audit.json"])
    frontend_meta = frontend.get("_meta")
    release_values = [
        frontend_meta.get("releaseId") if isinstance(frontend_meta, dict) else None,
        registry.get("releaseId"),
        audit.get("releaseId"),
    ]
    released_values = [
        frontend_meta.get("releasedAt") if isinstance(frontend_meta, dict) else None,
        registry.get("releasedAt"),
        audit.get("releasedAt"),
    ]
    cross_violations: list[str] = []
    if any(value != release_id for value in release_values):
        cross_violations.append("세 스테이징 JSON의 releaseId가 seal과 다릅니다")
    if len(set(released_values)) != 1 or not _iso_datetime(released_values[0]):
        cross_violations.append("세 스테이징 JSON의 releasedAt이 서로 다릅니다")
    if registry.get("baseUrl") != public_base or (
        not isinstance(frontend_meta, dict)
        or frontend_meta.get("defaultBaseUrl") != public_base
    ):
        cross_violations.append("프론트·서버 base URL이 seal과 다릅니다")
    frontend_sets = frontend.get("sets")
    registry_sets = registry.get("sets")
    audit_sets = audit.get("sets")
    if not all(isinstance(value, list) for value in (
        frontend_sets, registry_sets, audit_sets
    )) or not (
        len(frontend_sets) == len(registry_sets) == len(audit_sets)
    ):
        cross_violations.append("세 스테이징 JSON의 세트 목록 크기가 다릅니다")
        frontend_sets, registry_sets, audit_sets = [], [], []

    for set_index, (front_set, server_set, audit_set) in enumerate(zip(
        frontend_sets, registry_sets, audit_sets
    )):
        label = f"sets[{set_index}]"
        if not all(isinstance(item, dict) for item in (
            front_set, server_set, audit_set
        )):
            cross_violations.append(f"{label} 형식이 올바르지 않습니다")
            continue
        set_id = server_set.get("setId")
        if front_set.get("setId") != set_id or front_set.get("id") != set_id or (
            audit_set.get("setId") != set_id
        ):
            cross_violations.append(f"{label} setId가 서로 다릅니다")
        server_place_type = server_set.get("placeType")
        if (
            server_place_type not in _PLACE_TYPES
            or front_set.get("placeType") != server_place_type
            or front_set.get("place") != server_place_type
        ):
            cross_violations.append(f"{label} placeType이 서로 다르거나 허용값이 아닙니다")
        server_plate = server_set.get("representativePlate")
        front_plate = front_set.get("representativePlate")
        audit_plate = audit_set.get("representativePlate")
        if server_plate is None:
            if front_plate is not None or audit_plate is not None:
                cross_violations.append(f"{label} plate null 상태가 서로 다릅니다")
        elif not isinstance(server_plate, dict):
            cross_violations.append(f"{label} 서버 plate 형식이 올바르지 않습니다")
        else:
            key = server_plate.get("key")
            sealed = asset_by_key.get(key)
            if sealed is None or sealed[0].variant != "plate":
                cross_violations.append(f"{label} plate가 seal 자산에 없습니다")
            else:
                if (
                    server_plate.get("sha256") != sealed[1].get("sha256")
                    or not isinstance(audit_plate, dict)
                    or audit_plate.get("key") != key
                    or audit_plate.get("sha256") != server_plate.get("sha256")
                    or not isinstance(front_plate, dict)
                    or front_plate.get("url") != _public_url(public_base, key)
                ):
                    cross_violations.append(f"{label} plate 정의가 서로 다릅니다")

        front_members = front_set.get("members")
        server_members = server_set.get("members")
        audit_members = audit_set.get("members")
        if not all(isinstance(value, list) for value in (
            front_members, server_members, audit_members
        )) or not (
            len(front_members) == len(server_members) == len(audit_members)
        ):
            cross_violations.append(f"{label} 멤버 목록 크기가 다릅니다")
            continue
        for member_index, (front_member, server_member, audit_member) in enumerate(zip(
            front_members, server_members, audit_members
        )):
            member_label = f"{label}.members[{member_index}]"
            if not all(isinstance(item, dict) for item in (
                front_member, server_member, audit_member
            )):
                cross_violations.append(f"{member_label} 형식이 올바르지 않습니다")
                continue
            example_id = server_member.get("exampleId")
            recipe_fields = ("exampleId", "order", "cutType", "shot", "direction")
            if any(front_member.get(field) != server_member.get(field) for field in recipe_fields):
                cross_violations.append(f"{member_label} 프론트·서버 레시피가 다릅니다")
            if audit_member.get("exampleId") != example_id:
                cross_violations.append(f"{member_label} 감사 exampleId가 다릅니다")
            for variant in ("all", "pose"):
                published = server_member.get(variant)
                if not isinstance(published, dict):
                    cross_violations.append(
                        f"{member_label}.{variant} 형식이 올바르지 않습니다"
                    )
                    continue
                key = published.get("key")
                sealed = asset_by_key.get(key)
                audited = audit_member.get(variant)
                if (
                    sealed is None
                    or sealed[0].variant != variant
                    or published.get("sha256") != sealed[1].get("sha256")
                    or not isinstance(audited, dict)
                    or audited.get("key") != key
                    or audited.get("sha256") != published.get("sha256")
                ):
                    cross_violations.append(
                        f"{member_label}.{variant} 정의가 seal과 다릅니다"
                    )
                if variant == "all" and front_member.get("allUrl") != _public_url(
                    public_base, key
                ):
                    cross_violations.append(
                        f"{member_label}.allUrl이 서버 key와 다릅니다"
                    )
            thumb_key = (
                f"{R2_PREFIX}/{release_id}/thumb/{example_id}.webp"
            )
            sealed_thumb = asset_by_key.get(thumb_key)
            sealed_all = asset_by_key.get(
                server_member.get("all", {}).get("key")
                if isinstance(server_member.get("all"), dict)
                else ""
            )
            if sealed_thumb is None or sealed_thumb[0].variant != "thumb":
                cross_violations.append(f"{member_label}.thumb가 seal에 없습니다")
            else:
                if front_member.get("thumbUrl") != _public_url(public_base, thumb_key):
                    cross_violations.append(
                        f"{member_label}.thumbUrl이 seal key와 다릅니다"
                    )
                if sealed_all is not None and (
                    sealed_thumb[0].path.read_bytes()
                    != _thumbnail_bytes(sealed_all[0].path)
                ):
                    cross_violations.append(
                        f"{member_label}.thumb가 all의 결정적 파생본이 아닙니다"
                    )

    if cross_violations:
        raise SpaceSetReleaseValidationError(cross_violations)
    return SpaceSetReleaseResult(
        release_id=release_id,
        output_dir=root,
        frontend_catalog_path=catalog_paths["storyboardSpaceSets.json"],
        server_registry_path=catalog_paths["space_set_assets.json"],
        audit_path=catalog_paths["space_set_release_audit.json"],
        assets=tuple(assets),
        warnings=(),
    )


def upload_release(
    result: SpaceSetReleaseResult,
    *,
    execute: bool,
    r2_client=None,
) -> UploadReceipt | None:
    """업로드 목록을 출력하고 실제 쓰기가 끝나면 적용용 영수증을 돌려준다."""
    ordered = sorted(result.assets, key=lambda asset: asset.r2_key)
    print(f"UPLOAD {'EXECUTE' if execute else 'DRY-RUN'}: {len(ordered)} objects")
    for asset in ordered:
        print(f"{asset.r2_key}\t{asset.size} bytes")
    if not execute:
        return
    if r2_client is None:
        if str(SERVER_DIR) not in sys.path:
            sys.path.insert(0, str(SERVER_DIR))
        from app.config import load_settings
        from app.r2 import R2Client

        r2_client = R2Client(load_settings())
    prefix = f"{R2_PREFIX}/{result.release_id}/"
    existing = r2_client.list_prefix(prefix)
    if existing:
        raise RuntimeError(
            f"불변 릴리스 경로에 이미 {len(existing)}개 객체가 있어 업로드를 거부합니다: "
            f"{prefix}"
        )
    for asset in ordered:
        r2_client.put_bytes(
            asset.r2_key,
            asset.path.read_bytes(),
            asset.mime,
            cache="public, max-age=31536000, immutable",
        )
    return UploadReceipt(
        release_id=result.release_id,
        uploaded_keys=frozenset(asset.r2_key for asset in ordered),
    )


def _applied_release_id(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("releaseId"), str):
        return value["releaseId"]
    meta = value.get("_meta")
    return meta.get("releaseId") if isinstance(meta, dict) else None


def _read_server_registry(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"기존 공간 세트 서버 레지스트리를 읽을 수 없습니다: {path}") from exc
    _validate_runtime_registry(value, label=f"기존 공간 세트 서버 레지스트리: {path}")
    return value


def _frontend_url_matches(
    value: object,
    *,
    base_url: str,
    variant: str,
    owner_id: str,
) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    expected_base = urlsplit(base_url)
    if (
        parsed.scheme != expected_base.scheme
        or parsed.netloc != expected_base.netloc
        or parsed.query
        or parsed.fragment
    ):
        return False
    relative = parsed.path.lstrip("/")
    prefix = f"{R2_PREFIX}/"
    parts = relative[len(prefix) :].split("/") if relative.startswith(prefix) else []
    return (
        len(parts) == 3
        and _valid_space_set_id(parts[0])
        and parts[1] == variant
        and Path(parts[2]).name == parts[2]
        and Path(parts[2]).stem == owner_id
        and Path(parts[2]).suffix.lower()
        in ({".webp"} if variant == "thumb" else {".png", ".jpg", ".jpeg", ".webp"})
    )


def _validate_frontend_catalog(value: object, *, label: str) -> None:
    violations: list[str] = []
    if not isinstance(value, dict) or set(value) != {"_meta", "sets"}:
        raise RuntimeError(f"{label} 형식이 프론트 계약과 다릅니다: document_invalid")
    meta = value.get("_meta")
    sets = value.get("sets")
    if (
        not isinstance(meta, dict)
        or set(meta) != {
            "schemaVersion",
            "releaseId",
            "releasedAt",
            "defaultBaseUrl",
        }
        or not isinstance(meta.get("schemaVersion"), int)
        or isinstance(meta.get("schemaVersion"), bool)
        or meta["schemaVersion"] != 1
        or not isinstance(sets, list)
    ):
        raise RuntimeError(f"{label} 형식이 프론트 계약과 다릅니다: metadata_invalid")
    release_id = meta.get("releaseId")
    if sets and not _valid_space_set_id(release_id):
        violations.append("release_id_invalid")
    if not _iso_datetime(meta.get("releasedAt")):
        violations.append("released_at_invalid")
    try:
        base_url = _normalized_public_base(meta.get("defaultBaseUrl"))
    except (AttributeError, TypeError, SpaceSetReleaseValidationError):
        violations.append("base_url_invalid")
        base_url = ""

    set_ids: set[str] = set()
    example_ids: set[str] = set()
    for set_index, space_set in enumerate(sets):
        if (
            not isinstance(space_set, dict)
            or set(space_set) != _FRONTEND_SET_FIELDS
        ):
            violations.append(f"sets[{set_index}]_fields_invalid")
            continue
        set_id = space_set.get("setId")
        if (
            not _valid_space_set_id(set_id)
            or space_set.get("id") != set_id
            or set_id in set_ids
        ):
            violations.append(f"sets[{set_index}]_set_id_invalid")
            continue
        set_ids.add(set_id)
        for field in ("name", "tone", "compositionLabel"):
            if (
                not isinstance(space_set.get(field), str)
                or not space_set[field].strip()
            ):
                violations.append(f"sets[{set_index}]_{field}_invalid")
        set_type = space_set.get("setType")
        gender = space_set.get("gender")
        applicable = space_set.get("applicableClothingTypes")
        if set_type not in _SET_TYPES:
            violations.append(f"sets[{set_index}]_set_type_invalid")
        if gender not in _GENDERS:
            violations.append(f"sets[{set_index}]_gender_invalid")
        if (
            not isinstance(applicable, list)
            or not applicable
            or any(not isinstance(item, str) for item in applicable)
            or len(applicable) != len(set(applicable))
            or any(item not in _CLOTHING_TYPES for item in applicable)
            or (gender == "men" and "dress" in applicable)
            or (
                len(applicable) > 1
                and set(applicable) != {"top", "outer"}
            )
        ):
            violations.append(f"sets[{set_index}]_applicability_invalid")
            applicable = []
        if space_set.get("spaceVariation") not in _SPACE_VARIATIONS:
            violations.append(f"sets[{set_index}]_space_variation_invalid")
        if (
            space_set.get("placeType") not in _PLACE_TYPES
            or space_set.get("place") != space_set.get("placeType")
        ):
            violations.append(f"sets[{set_index}]_place_type_invalid")
        plate_policy = space_set.get("platePolicy")
        plate = space_set.get("representativePlate")
        if plate_policy == "required" and set_type != "horizon-sequence":
            if (
                not isinstance(plate, dict)
                or set(plate) != {"url"}
                or not _frontend_url_matches(
                    plate.get("url"),
                    base_url=base_url,
                    variant="plate",
                    owner_id=set_id,
                )
            ):
                violations.append(f"sets[{set_index}]_plate_invalid")
        elif (
            plate_policy != "not-required"
            or set_type != "horizon-sequence"
            or plate is not None
        ):
            violations.append(f"sets[{set_index}]_plate_policy_invalid")
        members = space_set.get("members")
        if not isinstance(members, list) or not 2 <= len(members) <= 5:
            violations.append(f"sets[{set_index}]_members_invalid")
            continue
        for expected_order, member in enumerate(members, start=1):
            member_label = f"sets[{set_index}].members[{expected_order - 1}]"
            if (
                not isinstance(member, dict)
                or set(member) != _FRONTEND_MEMBER_FIELDS
            ):
                violations.append(f"{member_label}_fields_invalid")
                continue
            example_id = member.get("exampleId")
            if (
                not _valid_space_set_id(example_id, example=True)
                or example_id in example_ids
            ):
                violations.append(f"{member_label}_example_id_invalid")
                continue
            example_ids.add(example_id)
            if (
                not isinstance(member.get("order"), int)
                or isinstance(member.get("order"), bool)
                or member["order"] != expected_order
            ):
                violations.append(f"{member_label}_order_invalid")
            expected_cut_type = "styling" if set_type == "styling" else "horizon"
            if (
                member.get("cutType") not in _CUT_TYPES
                or member.get("cutType") != expected_cut_type
                or member.get("shot") not in _SHOTS
                or member.get("direction") not in _DIRECTIONS
            ):
                violations.append(f"{member_label}_recipe_invalid")
            if not _frontend_url_matches(
                member.get("allUrl"),
                base_url=base_url,
                variant="all",
                owner_id=example_id,
            ) or not _frontend_url_matches(
                member.get("thumbUrl"),
                base_url=base_url,
                variant="thumb",
                owner_id=example_id,
            ):
                violations.append(f"{member_label}_asset_url_invalid")
        if set_type == "horizon-rotation" and [
            (
                member.get("shot"),
                member.get("direction"),
            )
            for member in members
            if isinstance(member, dict)
        ] != [
            ("full", "front"),
            ("full", "side"),
            ("full", "back"),
        ]:
            violations.append(f"sets[{set_index}]_rotation_recipe_invalid")
        if len(applicable) > 1 and any(
            not isinstance(member, dict) or member.get("shot") != "full"
            for member in members
        ):
            violations.append(f"sets[{set_index}]_shared_recipe_invalid")
    if violations:
        raise RuntimeError(
            f"{label} 형식이 프론트 계약과 다릅니다: " + ", ".join(violations)
        )


def _read_frontend_catalog(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"기존 공간 세트 프론트 카탈로그를 읽을 수 없습니다: {path}"
        ) from exc
    _validate_frontend_catalog(
        value,
        label=f"기존 공간 세트 프론트 카탈로그: {path}",
    )
    return value


def _published_asset_release_id(
    asset: object,
    *,
    variant: str,
    owner_id: str,
    label: str,
    violations: list[str],
    seen_keys: set[str],
) -> str | None:
    if not isinstance(asset, dict) or set(asset) != _PUBLISHED_ASSET_FIELDS:
        violations.append(f"{label}_fields_invalid")
        return None
    key = asset.get("key")
    if not isinstance(key, str):
        violations.append(f"{label}_key_invalid")
        return None
    relative = key[len(f"{R2_PREFIX}/") :] if key.startswith(
        f"{R2_PREFIX}/"
    ) else ""
    parts = relative.split("/")
    filename = parts[2] if len(parts) == 3 else ""
    suffix = Path(filename).suffix.lower()
    release_id = parts[0] if len(parts) == 3 else None
    if (
        len(parts) != 3
        or not _valid_space_set_id(release_id)
        or parts[1] != variant
        or Path(filename).name != filename
        or Path(filename).stem != owner_id
        or suffix not in _MIME_BY_SUFFIX
    ):
        violations.append(f"{label}_key_invalid")
    elif asset.get("mime") != _MIME_BY_SUFFIX[suffix]:
        violations.append(f"{label}_mime_invalid")
    if variant == "pose" and (
        suffix != ".png" or asset.get("mime") != "image/png"
    ):
        violations.append(f"{label}_pose_format_invalid")
    if key in seen_keys:
        violations.append(f"{label}_key_duplicate")
    seen_keys.add(key)
    return release_id


def _validate_published_registry(value: object, *, label: str) -> None:
    violations: list[str] = []
    # placeTypes 는 런타임 계약(app/agents/space_set_assets.py)이 요구하는 필드다.
    # 이 가드가 delta 경로에 들어오기 전에 main 에서 추가돼, 정확 일치 검사에 빠져 있으면
    # 정상 레지스트리를 metadata_invalid 로 오거부한다.
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "releaseId",
        "releasedAt",
        "baseUrl",
        "placeTypes",
        "sets",
    }:
        raise RuntimeError(f"{label} 형식이 릴리스 계약과 다릅니다: metadata_invalid")
    if (
        not isinstance(value.get("schemaVersion"), int)
        or isinstance(value.get("schemaVersion"), bool)
        or value["schemaVersion"] != 1
        or not _valid_space_set_id(value.get("releaseId"))
        or not _iso_datetime(value.get("releasedAt"))
        or not isinstance(value.get("baseUrl"), str)
        or not isinstance(value.get("sets"), list)
    ):
        violations.append("metadata_invalid")

    seen_keys: set[str] = set()
    for set_index, space_set in enumerate(value.get("sets") or []):
        set_label = f"sets[{set_index}]"
        if (
            not isinstance(space_set, dict)
            or set(space_set) != _PUBLISHED_SET_FIELDS
        ):
            violations.append(f"{set_label}_fields_invalid")
            continue
        set_id = space_set.get("setId")
        release_ids: set[str] = set()
        plate = space_set.get("representativePlate")
        if plate is not None:
            release_id = _published_asset_release_id(
                plate,
                variant="plate",
                owner_id=set_id,
                label=f"{set_label}.representativePlate",
                violations=violations,
                seen_keys=seen_keys,
            )
            if release_id is not None:
                release_ids.add(release_id)
        for member_index, member in enumerate(space_set.get("members") or []):
            member_label = f"{set_label}.members[{member_index}]"
            if (
                not isinstance(member, dict)
                or set(member) != _PUBLISHED_MEMBER_FIELDS
            ):
                violations.append(f"{member_label}_fields_invalid")
                continue
            if (
                not isinstance(member.get("order"), int)
                or isinstance(member.get("order"), bool)
                or member["order"] != member_index + 1
            ):
                violations.append(f"{member_label}_order_invalid")
            example_id = member.get("exampleId")
            for variant in ("all", "pose"):
                release_id = _published_asset_release_id(
                    member.get(variant),
                    variant=variant,
                    owner_id=example_id,
                    label=f"{member_label}.{variant}",
                    violations=violations,
                    seen_keys=seen_keys,
                )
                if release_id is not None:
                    release_ids.add(release_id)
        if len(release_ids) != 1:
            violations.append(f"{set_label}_release_root_invalid")
    if violations:
        raise RuntimeError(
            f"{label} 형식이 릴리스 계약과 다릅니다: " + ", ".join(violations)
        )


def _validate_runtime_registry(value: object, *, label: str) -> None:
    if str(SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(SERVER_DIR))
    from app.agents.space_set_assets import validate_space_set_registry_document

    try:
        validate_space_set_registry_document(value)
    except ValueError as exc:
        raise RuntimeError(f"{label} 형식이 런타임 계약과 다릅니다: {exc}") from exc
    _validate_published_registry(value, label=label)


def _validate_catalog_pair(frontend: dict, registry: dict, *, label: str) -> None:
    _validate_frontend_catalog(frontend, label=f"{label} 프론트 카탈로그")
    _validate_runtime_registry(registry, label=f"{label} 서버 레지스트리")
    frontend_meta = frontend["_meta"]
    if (
        frontend_meta["schemaVersion"] != registry["schemaVersion"]
        or frontend_meta["releaseId"] != registry["releaseId"]
        or frontend_meta["releasedAt"] != registry["releasedAt"]
    ):
        raise RuntimeError(f"{label} 프론트·서버 릴리스 메타데이터가 다릅니다")
    frontend_base = frontend["_meta"]["defaultBaseUrl"]
    server_base = registry.get("baseUrl")
    if (
        not isinstance(server_base, str)
        or frontend_base != server_base
    ):
        raise RuntimeError(f"{label} 프론트·서버 baseUrl이 다릅니다")
    frontend_sets = frontend["sets"]
    server_sets = registry["sets"]
    frontend_ids = [item["setId"] for item in frontend_sets]
    server_ids = [item["setId"] for item in server_sets]
    if frontend_ids != server_ids:
        raise RuntimeError(f"{label} 프론트·서버 setId 순서가 다릅니다")

    common_fields = (
        "setId",
        "name",
        "setType",
        "gender",
        "applicableClothingTypes",
        "placeType",
        "tone",
        "compositionLabel",
        "spaceVariation",
        "platePolicy",
    )
    recipe_fields = ("exampleId", "order", "cutType", "shot", "direction")
    for frontend_set, server_set in zip(frontend_sets, server_sets):
        set_id = server_set["setId"]
        if any(
            frontend_set.get(field) != server_set.get(field)
            for field in common_fields
        ):
            raise RuntimeError(f"{label} 세트 정의가 프론트·서버에서 다릅니다: {set_id}")
        server_plate = server_set["representativePlate"]
        frontend_plate = frontend_set["representativePlate"]
        expected_plate_url = (
            _public_url(server_base, server_plate["key"])
            if server_plate is not None
            else None
        )
        if (
            frontend_plate.get("url")
            if isinstance(frontend_plate, dict)
            else None
        ) != expected_plate_url:
            raise RuntimeError(f"{label} plate 정의가 프론트·서버에서 다릅니다: {set_id}")
        frontend_members = frontend_set["members"]
        server_members = server_set["members"]
        if len(frontend_members) != len(server_members):
            raise RuntimeError(f"{label} 멤버 수가 프론트·서버에서 다릅니다: {set_id}")
        for frontend_member, server_member in zip(frontend_members, server_members):
            example_id = server_member["exampleId"]
            if any(
                frontend_member.get(field) != server_member.get(field)
                for field in recipe_fields
            ):
                raise RuntimeError(
                    f"{label} 멤버 정의가 프론트·서버에서 다릅니다: {example_id}"
                )
            expected_all_url = _public_url(server_base, server_member["all"]["key"])
            release_root = server_member["all"]["key"].rsplit("/all/", 1)[0]
            expected_thumb_url = _public_url(
                server_base,
                f"{release_root}/thumb/{example_id}.webp",
            )
            if (
                frontend_member.get("allUrl") != expected_all_url
                or frontend_member.get("thumbUrl") != expected_thumb_url
            ):
                raise RuntimeError(
                    f"{label} 멤버 자산 URL이 프론트·서버에서 다릅니다: {example_id}"
                )


def _merged_catalogs(result: SpaceSetReleaseResult) -> tuple[dict, dict]:
    next_frontend = _read_json(result.frontend_catalog_path)
    next_registry = _read_json(result.server_registry_path)
    _validate_catalog_pair(next_frontend, next_registry, label="새 공간 세트 릴리스")
    current_frontend = _read_frontend_catalog(DEFAULT_FRONTEND_CATALOG_PATH)
    current_registry = _read_server_registry(DEFAULT_SERVER_REGISTRY_PATH)
    if current_frontend is None and current_registry is None:
        return next_frontend, next_registry
    if current_frontend is None or current_registry is None:
        raise RuntimeError(
            "기존 공간 세트 프론트·서버 파일 중 하나만 존재해 병합을 거부합니다"
        )
    _validate_catalog_pair(
        current_frontend,
        current_registry,
        label="기존 공간 세트",
    )

    current_base = current_registry.get("baseUrl")
    next_base = next_registry.get("baseUrl")
    if current_base and next_base and current_base != next_base:
        raise RuntimeError(
            "기존 공간 세트 서버 레지스트리와 새 릴리스의 baseUrl이 다릅니다"
        )

    next_sets = next_registry["sets"]
    next_by_id = {item["setId"]: item for item in next_sets}
    next_frontend_sets = next_frontend["sets"]
    next_frontend_by_id = {item["setId"]: item for item in next_frontend_sets}
    preserved_frontend: list[dict] = []
    preserved_registry: list[dict] = []
    for existing_frontend, existing_registry in zip(
        current_frontend["sets"],
        current_registry["sets"],
    ):
        set_id = existing_registry["setId"]
        frontend_replacement = next_frontend_by_id.get(set_id)
        registry_replacement = next_by_id.get(set_id)
        if frontend_replacement is None and registry_replacement is None:
            preserved_frontend.append(existing_frontend)
            preserved_registry.append(existing_registry)
        elif (
            frontend_replacement != existing_frontend
            or registry_replacement != existing_registry
        ):
            raise RuntimeError(
                f"동일 setId의 정의 변경을 거부합니다: {set_id}. "
                "새 세트는 새 setId로 발행하세요"
            )
    merged_frontend = {
        **next_frontend,
        "sets": [*next_frontend_sets, *preserved_frontend],
    }
    merged_registry = {
        **next_registry,
        "baseUrl": next_base or current_base,
        "sets": [*next_sets, *preserved_registry],
    }
    _validate_catalog_pair(
        merged_frontend,
        merged_registry,
        label="병합된 공간 세트",
    )
    return merged_frontend, merged_registry


def _restore_bytes(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
        tmp.write(previous)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _assert_receipt_covers_release(
    result: SpaceSetReleaseResult,
    receipt: UploadReceipt,
    registry: dict,
) -> None:
    """영수증이 이번 릴리스와 그 명단이 참조하는 모든 자산을 덮는지 검사한다."""
    if receipt.release_id != result.release_id:
        raise RuntimeError(
            "업로드 영수증이 다른 릴리스의 것입니다: "
            f"receipt={receipt.release_id} staged={result.release_id}"
        )
    staged_keys = {asset.r2_key for asset in result.assets}
    missing = staged_keys - receipt.uploaded_keys
    if missing:
        raise RuntimeError(
            f"업로드되지 않은 자산이 {len(missing)}개 있습니다: {sorted(missing)[:5]}"
        )

    prefix = f"{R2_PREFIX}/{result.release_id}/"
    referenced: set[str] = set()
    for space_set in registry["sets"]:
        plate = space_set["representativePlate"]
        if plate is not None:
            referenced.add(plate["key"])
        for member in space_set["members"]:
            referenced.add(member["all"]["key"])
            referenced.add(member["pose"]["key"])
            release_root = member["all"]["key"].rsplit("/all/", 1)[0]
            referenced.add(f'{release_root}/thumb/{member["exampleId"]}.webp')
    unbacked = {
        key
        for key in referenced
        if key.startswith(prefix) and key not in receipt.uploaded_keys
    }
    if unbacked:
        raise RuntimeError(
            f"명단이 올리지 않은 키를 참조합니다: {sorted(unbacked)[:5]}"
        )


def apply_release(
    result: SpaceSetReleaseResult,
    receipt: UploadReceipt,
) -> None:
    """업로드 증거를 확인한 뒤 JSON 두 개를 적용하고 실패 시 되돌린다."""
    for target in (DEFAULT_FRONTEND_CATALOG_PATH, DEFAULT_SERVER_REGISTRY_PATH):
        if _applied_release_id(target) == result.release_id:
            raise FileExistsError(
                f"같은 releaseId가 이미 적용되어 덮어쓰기를 거부합니다: {target}"
            )
    merged_frontend, merged_registry = _merged_catalogs(result)
    _assert_receipt_covers_release(result, receipt, merged_registry)
    merge_dir = Path(tempfile.mkdtemp(prefix=".space-set-registry-merge."))
    merged_frontend_path = merge_dir / "storyboardSpaceSets.json"
    merged_path = merge_dir / "space_set_assets.json"
    frontend_previous = (
        DEFAULT_FRONTEND_CATALOG_PATH.read_bytes()
        if DEFAULT_FRONTEND_CATALOG_PATH.is_file()
        else None
    )
    server_previous = (
        DEFAULT_SERVER_REGISTRY_PATH.read_bytes()
        if DEFAULT_SERVER_REGISTRY_PATH.is_file()
        else None
    )
    try:
        _write_json(merged_frontend_path, merged_frontend)
        _write_json(merged_path, merged_registry)
        _atomic_copy(merged_frontend_path, DEFAULT_FRONTEND_CATALOG_PATH)
        _atomic_copy(merged_path, DEFAULT_SERVER_REGISTRY_PATH)
    except Exception:
        rollback_errors: list[str] = []
        for path, previous in (
            (DEFAULT_FRONTEND_CATALOG_PATH, frontend_previous),
            (DEFAULT_SERVER_REGISTRY_PATH, server_previous),
        ):
            try:
                _restore_bytes(path, previous)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                "공간 세트 적용 실패 후 롤백도 실패했습니다: "
                + "; ".join(rollback_errors)
            )
        raise
    finally:
        shutil.rmtree(merge_dir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="공간 세트 release manifest v1 릴리스")
    parser.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        help="space-set release manifest 경로",
    )
    parser.add_argument(
        "asset_root",
        type=Path,
        nargs="?",
        help="localPath 기준 자산 루트",
    )
    parser.add_argument("--out", type=Path, help="스테이징 출력 디렉터리")
    parser.add_argument(
        "--from-stage",
        type=Path,
        help="이미 dry-run 검토한 sealed staging을 그대로 재검증·재사용",
    )
    parser.add_argument(
        "--public-base-url",
        default=os.getenv("R2_PUBLIC_BASE"),
        help="R2 공개 서빙 URL (기본: R2_PUBLIC_BASE)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="--upload --execute 성공 후 공간 세트 JSON 두 개를 정식 위치에 적용",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="R2 업로드 목록 출력(기본 dry-run)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="--upload를 실제 R2 쓰기로 전환",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.from_stage:
        if args.manifest is not None or args.asset_root is not None or args.out is not None:
            print(
                "ERROR: --from-stage는 manifest, asset_root, --out과 함께 쓸 수 없습니다",
                file=sys.stderr,
            )
            return 2
    elif args.manifest is None or args.asset_root is None:
        print(
            "ERROR: 새 staging을 만들 때는 manifest와 asset_root가 필요합니다",
            file=sys.stderr,
        )
        return 2
    if args.execute and not args.from_stage:
        print(
            "ERROR: 실제 업로드는 먼저 검토한 sealed staging을 "
            "--from-stage로 지정해야 합니다",
            file=sys.stderr,
        )
        return 2
    if args.execute and not args.upload:
        print("ERROR: --execute는 --upload와 함께만 사용할 수 있습니다", file=sys.stderr)
        return 2
    if args.apply and not (args.upload and args.execute):
        print(
            "ERROR: --apply는 같은 실행의 --upload --execute가 성공한 뒤에만 사용할 수 있습니다",
            file=sys.stderr,
        )
        return 2
    try:
        if args.from_stage:
            result = load_staged_release(args.from_stage)
            print(f"REUSED SEALED STAGE: {result.output_dir}")
        else:
            result = stage_release(
                args.manifest,
                args.asset_root,
                public_base_url=args.public_base_url or "",
                output_dir=args.out,
            )
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        print(f"STAGED: {result.output_dir}")
        print(f"FRONTEND CATALOG: {result.frontend_catalog_path}")
        print(f"SERVER REGISTRY: {result.server_registry_path}")
        print(f"AUDIT: {result.audit_path}")
        receipt = None
        if args.upload:
            receipt = upload_release(result, execute=args.execute)
        if args.apply:
            if receipt is None:
                raise RuntimeError("업로드 영수증이 없어 적용할 수 없습니다")
            apply_release(result, receipt)
            print(f"APPLIED: {DEFAULT_FRONTEND_CATALOG_PATH}")
            print(f"APPLIED: {DEFAULT_SERVER_REGISTRY_PATH}")
        return 0
    except SpaceSetReleaseValidationError as exc:
        print(f"ERROR: {len(exc.violations)} validation violation(s)", file=sys.stderr)
        for violation in exc.violations:
            print(f"- {violation}", file=sys.stderr)
        return 2
    except (FileExistsError, RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
