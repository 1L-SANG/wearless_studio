"""생성예시 release manifest v1 검증·스테이징·업로드 도구.

출시 대상을 추론하지 않고 ``documents/genexamples_release_contract.md``에 따라
manifest에 명시된 자산만 소비한다. 업로드는 ``--upload``만으로는 목록을
출력하는 dry-run이며, ``--execute``를 함께 지정해야 R2에 쓴다.
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
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError


SERVER_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = SERVER_DIR.parent
DEFAULT_REGISTRY_PATH = SERVER_DIR / "app" / "data" / "example_assets.json"
DEFAULT_CATALOG_PATH = REPO_DIR / "src" / "data" / "genExamples.json"
R2_PREFIX = "seed/genexamples/v1/releases"
THUMB_MAX_SIDE = 480
THUMB_QUALITY = 82

_SAFE_PATH_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_CLOTHING_TYPES = {"top", "bottom", "outer", "dress"}
_WORN_CUTS = {"styling", "horizon", "mirror"}
_GENDERS = {"women", "men"}
_DIRECTIONS = {"front", "side", "back"}
_DETAIL_SUBJECTS = {"원단·봉제", "단추·지퍼", "포켓"}
_PRESENTATION_METHODS = {"ghost", "flatlay"}
_VARIANT_ORDER = ("all", "pose", "bg", "thumb")


class ReleaseValidationError(ValueError):
    """manifest 전체 검증 결과를 한 번에 전달한다."""

    def __init__(self, violations: list[str]):
        super().__init__("generation-example release validation failed")
        self.violations = violations


@dataclass(frozen=True)
class AssetFile:
    example_id: str
    variant: str
    path: Path
    r2_key: str
    mime: str

    @property
    def size(self) -> int:
        return self.path.stat().st_size


@dataclass(frozen=True)
class ReleaseResult:
    release_id: str
    output_dir: Path
    registry_path: Path
    catalog_path: Path
    assets: tuple[AssetFile, ...]
    warnings: tuple[str, ...]


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReleaseValidationError([f"manifest를 읽을 수 없습니다: {path} ({exc})"]) from exc
    except json.JSONDecodeError as exc:
        raise ReleaseValidationError([f"manifest JSON이 올바르지 않습니다: {exc}"]) from exc
    if not isinstance(value, dict):
        raise ReleaseValidationError(["manifest 최상위 값은 object여야 합니다"])
    return value


def _resolved_asset_path(asset_root: Path, file_value: object) -> Path | None:
    if not isinstance(file_value, str) or not file_value.strip():
        return None
    relative = Path(file_value)
    if relative.is_absolute():
        return None
    candidate = (asset_root / relative).resolve()
    try:
        candidate.relative_to(asset_root)
    except ValueError:
        return None
    return candidate


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


def _validate_variant_file(
    example_id: str,
    variant: str,
    spec: object,
    asset_root: Path,
    violations: list[str],
) -> Path | None:
    label = f"examples[{example_id}].variants.{variant}"
    if not isinstance(spec, dict):
        violations.append(f"{label}는 object여야 합니다")
        return None
    path = _resolved_asset_path(asset_root, spec.get("file"))
    if path is None:
        violations.append(f"{label}.file은 자산 루트 안의 상대 경로여야 합니다")
        return None
    if not path.is_file():
        violations.append(f"{label}.file이 존재하지 않습니다: {spec.get('file')}")
        return None

    expected_hash = spec.get("sha256")
    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
        violations.append(f"{label}.sha256은 64자리 16진수여야 합니다")
    else:
        actual_hash = _sha256(path)
        if actual_hash != expected_hash.lower():
            violations.append(
                f"{label}.sha256 불일치: expected={expected_hash.lower()} actual={actual_hash}"
            )

    try:
        with Image.open(path) as image:
            image.load()
            actual_width, actual_height = image.size
            image_format = image.format
            bands = image.getbands()
            info_keys = set(image.info)
    except (OSError, UnidentifiedImageError) as exc:
        violations.append(f"{label}.file은 읽을 수 있는 이미지여야 합니다: {exc}")
        return path

    format_suffixes = {
        "PNG": {".png"},
        "JPEG": {".jpg", ".jpeg"},
        "WEBP": {".webp"},
    }
    allowed_suffixes = format_suffixes.get(image_format)
    if allowed_suffixes is None:
        violations.append(f"{label}.file 이미지 형식은 PNG|JPEG|WebP만 허용합니다: {image_format}")
    elif path.suffix.lower() not in allowed_suffixes:
        violations.append(
            f"{label}.file 확장자와 실제 이미지 형식이 다릅니다: "
            f"suffix={path.suffix.lower()} format={image_format}"
        )

    width, height = spec.get("width"), spec.get("height")
    if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
        violations.append(f"{label}.width는 양의 정수여야 합니다")
    if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
        violations.append(f"{label}.height는 양의 정수여야 합니다")
    if isinstance(width, int) and isinstance(height, int) \
            and (width, height) != (actual_width, actual_height):
        violations.append(
            f"{label} 이미지 크기 불일치: manifest={width}x{height} "
            f"actual={actual_width}x{actual_height}"
        )
    if variant == "pose" and (image_format != "PNG" or "A" not in bands):
        violations.append(f"{label}는 알파 채널을 포함한 PNG여야 합니다")
    if variant == "thumb":
        if image_format != "WEBP":
            violations.append(f"{label}는 WebP여야 합니다")
        if max(actual_width, actual_height) > THUMB_MAX_SIDE:
            violations.append(f"{label}의 최대 변은 {THUMB_MAX_SIDE}px 이하여야 합니다")
        metadata = info_keys.intersection({"exif", "icc_profile", "xmp"})
        if metadata:
            violations.append(f"{label}에 제거되지 않은 메타데이터가 있습니다: {sorted(metadata)}")
    return path


def validate_manifest(
    manifest: dict,
    asset_root: Path,
    *,
    manifest_path: Path | None = None,
) -> tuple[list[dict], dict[tuple[str, str], Path], list[str]]:
    """manifest 전체를 검증한다. 실패 시 위반 목록 전체를 담아 예외를 낸다."""
    root = asset_root.resolve()
    violations: list[str] = []
    warnings: list[str] = []
    resolved_files: dict[tuple[str, str], Path] = {}

    if manifest.get("schemaVersion") != 1:
        violations.append("schemaVersion은 1이어야 합니다")
    release_id = manifest.get("releaseId")
    if not isinstance(release_id, str) or not _SAFE_PATH_TOKEN.fullmatch(release_id):
        violations.append("releaseId는 경로에 안전한 영문·숫자·._- 토큰이어야 합니다")
    if not _iso_datetime(manifest.get("releasedAt")):
        violations.append("releasedAt은 ISO 8601 날짜시각이어야 합니다")
    source = manifest.get("source")
    if not isinstance(source, dict):
        violations.append("source는 object여야 합니다")
    else:
        if not isinstance(source.get("anchors"), str) or not source.get("anchors"):
            violations.append("source.anchors는 비어 있지 않은 문자열이어야 합니다")
        qc = source.get("qcCompletion")
        if not isinstance(qc, list) or not qc or not all(isinstance(v, str) and v for v in qc):
            violations.append("source.qcCompletion은 비어 있지 않은 문자열 배열이어야 합니다")

    examples = manifest.get("examples")
    if not isinstance(examples, list) or not examples:
        violations.append("examples는 비어 있지 않은 배열이어야 합니다")
        examples = []

    seen_ids: set[str] = set()
    groups: dict[str, list[int]] = defaultdict(list)
    for index, example in enumerate(examples):
        prefix = f"examples[{index}]"
        if not isinstance(example, dict):
            violations.append(f"{prefix}는 object여야 합니다")
            continue
        example_id = example.get("id")
        if not isinstance(example_id, str) or not _SAFE_PATH_TOKEN.fullmatch(example_id):
            violations.append(f"{prefix}.id는 경로에 안전한 안정 ID여야 합니다")
            example_id = f"#{index}"
        elif example_id in seen_ids:
            violations.append(f"중복 id: {example_id}")
        else:
            seen_ids.add(example_id)

        group = example.get("serviceGroupKey")
        rank = example.get("rank")
        valid_group = isinstance(group, str) and bool(group.strip())
        valid_rank = isinstance(rank, int) and not isinstance(rank, bool) and rank >= 1
        if not valid_group:
            violations.append(f"{prefix}.serviceGroupKey는 비어 있지 않은 문자열이어야 합니다")
        if not valid_rank:
            violations.append(f"{prefix}.rank는 1 이상의 정수여야 합니다")
        if valid_group and valid_rank:
            groups[group].append(rank)

        cut_type = example.get("cutType")
        gender = example.get("gender")
        shot = example.get("shot")
        direction = example.get("direction")
        source_type = example.get("sourceClothingType")
        applicable = example.get("applicableClothingTypes")
        if cut_type not in _WORN_CUTS | {"product"}:
            violations.append(f"{prefix}.cutType이 허용값이 아닙니다: {cut_type}")
        if cut_type in _WORN_CUTS:
            if gender not in _GENDERS:
                violations.append(f"{prefix}.gender는 착용컷에서 women|men이어야 합니다")
        elif cut_type == "product" and gender is not None:
            violations.append(f"{prefix}.gender는 성별 공용 제품컷에서 null이어야 합니다")
        if direction is not None and direction not in _DIRECTIONS:
            violations.append(f"{prefix}.direction이 허용값이 아닙니다: {direction}")
        if source_type not in _CLOTHING_TYPES:
            violations.append(f"{prefix}.sourceClothingType이 허용값이 아닙니다: {source_type}")
        if cut_type == "product":
            if shot not in {"ghost", "detail"}:
                violations.append(f"{prefix}.shot은 제품컷에서 ghost|detail이어야 합니다")
        elif shot not in {"full", "medium"}:
            violations.append(f"{prefix}.shot은 착용컷에서 full|medium이어야 합니다")

        if not isinstance(applicable, list) or not applicable:
            violations.append(f"{prefix}.applicableClothingTypes는 비어 있지 않은 배열이어야 합니다")
        else:
            if any(value not in _CLOTHING_TYPES for value in applicable):
                violations.append(f"{prefix}.applicableClothingTypes에 허용되지 않은 값이 있습니다")
            if len(applicable) != len(set(applicable)):
                violations.append(f"{prefix}.applicableClothingTypes에 중복이 있습니다")
            if source_type not in applicable:
                violations.append(f"{prefix}.applicableClothingTypes가 sourceClothingType을 포함해야 합니다")
            if len(applicable) > 1 and not (
                set(applicable) == {"top", "outer"}
                and cut_type in {"styling", "horizon"}
                and shot == "full"
            ):
                violations.append(
                    f"{prefix} 공용 적용 범위는 검토된 styling|horizon full의 [top,outer]만 허용합니다"
                )

        mood = example.get("mood")
        detail_subject = example.get("detailSubject")
        presentation = example.get("presentationMethod")
        if cut_type == "styling":
            if not isinstance(mood, str) or not mood:
                violations.append(f"{prefix}.mood는 styling에서 비어 있지 않아야 합니다")
        elif mood is not None:
            violations.append(f"{prefix}.mood는 styling 외 컷에서 null이어야 합니다")
        if cut_type == "product" and shot == "detail":
            if detail_subject not in _DETAIL_SUBJECTS:
                violations.append(f"{prefix}.detailSubject가 제품 detail 허용값이 아닙니다")
        elif detail_subject is not None:
            violations.append(f"{prefix}.detailSubject는 제품 detail 외에는 null이어야 합니다")
        if cut_type == "product" and shot == "ghost":
            if presentation not in _PRESENTATION_METHODS:
                violations.append(f"{prefix}.presentationMethod가 제품 ghost 허용값이 아닙니다")
        elif presentation is not None:
            violations.append(f"{prefix}.presentationMethod는 제품 ghost 외에는 null이어야 합니다")

        variants = example.get("variants")
        if not isinstance(variants, dict):
            violations.append(f"{prefix}.variants는 object여야 합니다")
            continue
        unknown_variants = sorted(set(variants) - set(_VARIANT_ORDER))
        if unknown_variants:
            violations.append(f"{prefix}.variants에 허용되지 않은 값이 있습니다: {unknown_variants}")
        if "all" not in variants:
            violations.append(f"{prefix}.variants.all은 필수입니다")
        if cut_type == "product" and any(v in variants for v in ("pose", "bg")):
            violations.append(f"{prefix} 제품컷에는 pose·bg variant를 발행할 수 없습니다")
        for variant in _VARIANT_ORDER:
            if variant not in variants:
                continue
            path = _validate_variant_file(example_id, variant, variants[variant], root, violations)
            if path is not None:
                resolved_files[(example_id, variant)] = path
        all_path = resolved_files.get((example_id, "all"))
        thumb_path = resolved_files.get((example_id, "thumb"))
        if all_path is not None and thumb_path is not None:
            try:
                expected_thumb = _thumbnail_bytes(all_path)
                if thumb_path.read_bytes() != expected_thumb:
                    violations.append(
                        f"{prefix}.variants.thumb가 all에서 고정 파라미터로 파생한 바이트와 다릅니다"
                    )
            except (OSError, UnidentifiedImageError) as exc:
                violations.append(f"{prefix}.variants.thumb 결정성 검증에 실패했습니다: {exc}")

    for group, ranks in sorted(groups.items()):
        if len(ranks) > 6:
            violations.append(f"serviceGroupKey '{group}'는 6개를 초과합니다: {len(ranks)}")
        expected = list(range(1, len(ranks) + 1))
        if sorted(ranks) != expected:
            violations.append(
                f"serviceGroupKey '{group}' rank는 1부터 연속·유일해야 합니다: {sorted(ranks)}"
            )

    if root.is_dir():
        declared = {path for path in resolved_files.values()}
        manifest_resolved = manifest_path.resolve() if manifest_path else None
        extras = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file() and path.resolve() not in declared and path.resolve() != manifest_resolved
        )
        if extras:
            preview = ", ".join(extras[:10])
            suffix = f" 외 {len(extras) - 10}개" if len(extras) > 10 else ""
            warnings.append(f"manifest 밖 파일 {len(extras)}개는 릴리스에서 제외합니다: {preview}{suffix}")
    else:
        violations.append(f"자산 루트가 디렉터리가 아닙니다: {root}")

    if violations:
        raise ReleaseValidationError(violations)
    return examples, resolved_files, warnings


def _thumbnail_bytes(source: Path) -> bytes:
    """고정 파라미터·무메타 WebP 썸네일 바이트를 만든다."""
    from io import BytesIO

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


def _r2_key(release_id: str, variant: str, example_id: str, path: Path) -> str:
    extension = ".webp" if variant == "thumb" else path.suffix.lower()
    if extension == ".jpeg":
        extension = ".jpg"
    return f"{R2_PREFIX}/{release_id}/{variant}/{example_id}{extension}"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
    try:
        shutil.copyfile(source, temp_path)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def stage_release(
    manifest_path: Path,
    asset_root: Path,
    *,
    public_base_url: str,
    output_dir: Path | None = None,
) -> ReleaseResult:
    manifest_path = manifest_path.resolve()
    asset_root = asset_root.resolve()
    manifest = _read_json(manifest_path)
    examples, source_files, warnings = validate_manifest(
        manifest, asset_root, manifest_path=manifest_path
    )
    base = public_base_url.rstrip("/")
    if urlsplit(base).scheme not in {"http", "https"} or not urlsplit(base).netloc:
        raise ReleaseValidationError(["--public-base-url 또는 R2_PUBLIC_BASE는 절대 http(s) URL이어야 합니다"])

    release_id = manifest["releaseId"]
    destination = (output_dir or (SERVER_DIR / ".genexamples-release" / release_id)).resolve()
    if destination.exists():
        raise FileExistsError(f"스테이징 출력 경로가 이미 존재합니다: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    build_dir = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    assets: list[AssetFile] = []
    try:
        registry_assets: dict[str, dict] = {}
        catalog: list[dict] = []
        thumb_dir = build_dir / "assets" / "thumb"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        for example in sorted(examples, key=lambda item: item["id"]):
            example_id = example["id"]
            variants = example["variants"]
            staged_thumb = thumb_dir / f"{example_id}.webp"
            if "thumb" in variants:
                shutil.copyfile(source_files[(example_id, "thumb")], staged_thumb)
            else:
                staged_thumb.write_bytes(_thumbnail_bytes(source_files[(example_id, "all")]))

            entry: dict = {}
            published: list[str] = []
            for variant in _VARIANT_ORDER:
                if variant == "thumb":
                    path = staged_thumb
                elif variant in variants:
                    path = source_files[(example_id, variant)]
                    published.append(variant)
                else:
                    continue
                key = _r2_key(release_id, variant, example_id, path)
                mime = mimetypes.guess_type(key)[0] or "application/octet-stream"
                assets.append(AssetFile(example_id, variant, path, key, mime))
                entry[variant] = _public_url(base, key)
            entry.update({
                "applicableClothingTypes": example["applicableClothingTypes"],
                "cutType": example["cutType"],
                "shot": example["shot"],
                "gender": example["gender"],
            })
            registry_assets[example_id] = entry
            catalog.append({
                "id": example_id,
                "thumb": entry["thumb"],
                "cutType": example["cutType"],
                "gender": example["gender"],
                "clothingType": example["sourceClothingType"],
                "applicableClothingTypes": example["applicableClothingTypes"],
                "shot": example["shot"],
                "mood": example.get("mood"),
                "detailSubject": example.get("detailSubject"),
                "presentationMethod": example.get("presentationMethod"),
                "rank": example["rank"],
                "variants": published,
            })

        registry = {
            "_meta": {
                "schemaVersion": 2,
                "releaseId": release_id,
                "releasedAt": manifest["releasedAt"],
                "defaultBaseUrl": base,
            },
            "assets": registry_assets,
        }
        registry_path = build_dir / "example_assets.json"
        catalog_path = build_dir / "genExamples.json"
        _write_json(registry_path, registry)
        _write_json(catalog_path, catalog)
        os.replace(build_dir, destination)
    except Exception:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise

    remapped_assets = tuple(
        AssetFile(
            asset.example_id,
            asset.variant,
            destination / asset.path.relative_to(build_dir)
            if asset.path.is_relative_to(build_dir)
            else asset.path,
            asset.r2_key,
            asset.mime,
        )
        for asset in assets
    )
    return ReleaseResult(
        release_id=release_id,
        output_dir=destination,
        registry_path=destination / "example_assets.json",
        catalog_path=destination / "genExamples.json",
        assets=remapped_assets,
        warnings=tuple(warnings),
    )


def upload_release(result: ReleaseResult, *, execute: bool, r2_client=None) -> None:
    """업로드 목록을 출력하고, execute일 때만 기존 R2Client로 실제 쓴다."""
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
            f"불변 릴리스 경로에 이미 {len(existing)}개 객체가 있어 업로드를 거부합니다: {prefix}"
        )
    for asset in ordered:
        r2_client.put_bytes(
            asset.r2_key,
            asset.path.read_bytes(),
            asset.mime,
            cache="public, max-age=31536000, immutable",
        )


def apply_release(result: ReleaseResult) -> None:
    """검증·스테이징이 끝난 두 JSON만 저장소 정식 위치에 원자 교체한다."""
    _atomic_copy(result.registry_path, DEFAULT_REGISTRY_PATH)
    _atomic_copy(result.catalog_path, DEFAULT_CATALOG_PATH)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="생성예시 release manifest v1 릴리스")
    parser.add_argument("manifest", type=Path, help="release_manifest.json 경로")
    parser.add_argument("asset_root", type=Path, help="manifest file 상대 경로의 자산 루트")
    parser.add_argument("--out", type=Path, help="스테이징 출력 디렉터리")
    parser.add_argument(
        "--public-base-url",
        default=os.getenv("R2_PUBLIC_BASE"),
        help="R2 공개 서빙 URL (기본: R2_PUBLIC_BASE)",
    )
    parser.add_argument("--apply", action="store_true", help="스테이징 JSON을 저장소 정식 위치에 적용")
    parser.add_argument("--upload", action="store_true", help="R2 업로드 목록 출력(기본 dry-run)")
    parser.add_argument("--execute", action="store_true", help="--upload를 실제 R2 쓰기로 전환")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute and not args.upload:
        print("ERROR: --execute는 --upload와 함께만 사용할 수 있습니다", file=sys.stderr)
        return 2
    try:
        result = stage_release(
            args.manifest,
            args.asset_root,
            public_base_url=args.public_base_url or "",
            output_dir=args.out,
        )
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        print(f"STAGED: {result.output_dir}")
        print(f"REGISTRY: {result.registry_path}")
        print(f"CATALOG: {result.catalog_path}")
        if args.upload:
            upload_release(result, execute=args.execute)
        if args.apply:
            apply_release(result)
            print(f"APPLIED: {DEFAULT_REGISTRY_PATH}")
            print(f"APPLIED: {DEFAULT_CATALOG_PATH}")
        return 0
    except ReleaseValidationError as exc:
        print(f"ERROR: {len(exc.violations)} validation violation(s)", file=sys.stderr)
        for violation in exc.violations:
            print(f"- {violation}", file=sys.stderr)
        return 2
    except (FileExistsError, RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
