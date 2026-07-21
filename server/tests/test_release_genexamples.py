import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

from tools import release_genexamples as release


def _write_png(path: Path, *, alpha: bool = False, color=(80, 120, 160)) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "RGBA" if alpha else "RGB"
    value = (*color, 180) if alpha else color
    Image.new(mode, (36, 24), value).save(path, format="PNG")
    data = path.read_bytes()
    return {
        "file": str(path.relative_to(path.parents[2])),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": 36,
        "height": 24,
    }


def _fixture(tmp_path: Path, *, count: int = 1) -> tuple[Path, Path, dict]:
    root = tmp_path / "assets-root"
    examples = []
    for index in range(count):
        example_id = f"ex_styling_women_top_full_daily_{index + 1:02d}"
        all_spec = _write_png(root / "assets" / "all" / f"{example_id}.png")
        pose_spec = _write_png(
            root / "assets" / "pose" / f"{example_id}.png", alpha=True
        )
        examples.append({
            "id": example_id,
            "serviceGroupKey": "styling:women:top:full:daily",
            "rank": index + 1,
            "cutType": "styling",
            "gender": "women",
            "shot": "full",
            "mood": "daily",
            "detailSubject": None,
            "presentationMethod": None,
            "direction": "front",
            "sourceClothingType": "top",
            "applicableClothingTypes": ["top"],
            "variants": {"all": all_spec, "pose": pose_spec},
        })
    manifest = {
        "schemaVersion": 1,
        "releaseId": "test-release-01",
        "releasedAt": "2026-07-20T00:00:00Z",
        "source": {
            "anchors": "anchors.json",
            "qcCompletion": ["qc/completion.json"],
        },
        "examples": examples,
    }
    manifest_path = root / "release_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path, root, manifest


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_happy_release_stages_registry_catalog_and_deterministic_thumb(tmp_path):
    manifest_path, root, _manifest = _fixture(tmp_path)
    first = release.stage_release(
        manifest_path,
        root,
        public_base_url="https://images.example.test",
        output_dir=tmp_path / "out-1",
    )
    second = release.stage_release(
        manifest_path,
        root,
        public_base_url="https://images.example.test",
        output_dir=tmp_path / "out-2",
    )

    registry = json.loads(first.registry_path.read_text(encoding="utf-8"))
    catalog = json.loads(first.catalog_path.read_text(encoding="utf-8"))
    example_id = catalog[0]["id"]
    assert registry["_meta"]["schemaVersion"] == 2
    assert registry["assets"][example_id]["applicableClothingTypes"] == ["top"]
    assert registry["assets"][example_id]["direction"] == "front"
    assert catalog[0]["direction"] == "front"
    assert catalog[0]["variants"] == ["all", "pose"]
    assert catalog[0]["thumb"].endswith(f"/thumb/{example_id}.webp")
    assert (
        first.output_dir / "assets" / "thumb" / f"{example_id}.webp"
    ).read_bytes() == (
        second.output_dir / "assets" / "thumb" / f"{example_id}.webp"
    ).read_bytes()
    with Image.open(first.output_dir / "assets" / "thumb" / f"{example_id}.webp") as thumb:
        assert thumb.format == "WEBP"
        assert max(thumb.size) <= release.THUMB_MAX_SIDE


def test_manifest_thumb_is_validated_against_fixed_derivation(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    all_path = root / example["variants"]["all"]["file"]
    thumb_path = root / "assets" / "thumb" / f"{example['id']}.webp"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_path.write_bytes(release._thumbnail_bytes(all_path))
    example["variants"]["thumb"] = {
        "file": str(thumb_path.relative_to(root)),
        "sha256": hashlib.sha256(thumb_path.read_bytes()).hexdigest(),
        "width": 36,
        "height": 24,
    }
    _write_manifest(manifest_path, manifest)

    release.validate_manifest(manifest, root, manifest_path=manifest_path)

    Image.new("RGB", (36, 24), (200, 20, 20)).save(
        thumb_path, format="WEBP", quality=release.THUMB_QUALITY, method=6
    )
    example["variants"]["thumb"]["sha256"] = hashlib.sha256(
        thumb_path.read_bytes()
    ).hexdigest()
    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)
    assert any("고정 파라미터로 파생한 바이트" in item for item in caught.value.violations)


def test_missing_file_reports_all_violations_without_outputs(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    manifest["examples"][0]["variants"]["all"]["file"] = "assets/all/missing.png"
    _write_manifest(manifest_path, manifest)
    output = tmp_path / "out"

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.stage_release(
            manifest_path, root,
            public_base_url="https://images.example.test",
            output_dir=output,
        )

    assert any("존재하지 않습니다" in item for item in caught.value.violations)
    assert not output.exists()


def test_sha_mismatch_rejected(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    manifest["examples"][0]["variants"]["all"]["sha256"] = "0" * 64
    _write_manifest(manifest_path, manifest)

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any("sha256 불일치" in item for item in caught.value.violations)


def test_validation_collects_group_and_rank_violations_together(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    manifest["examples"][0]["serviceGroupKey"] = ""
    manifest["examples"][0]["rank"] = 0

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any("serviceGroupKey는" in item for item in caught.value.violations)
    assert any("rank는 1 이상의" in item for item in caught.value.violations)


@pytest.mark.parametrize("ranks", ([1, 1], [1, 3]))
def test_duplicate_or_discontinuous_rank_rejected(tmp_path, ranks):
    manifest_path, root, manifest = _fixture(tmp_path, count=2)
    for example, rank in zip(manifest["examples"], ranks, strict=True):
        example["rank"] = rank
    _write_manifest(manifest_path, manifest)

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any("rank는 1부터 연속·유일" in item for item in caught.value.violations)


def test_product_with_pose_is_rejected(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    example.update({
        "serviceGroupKey": "product:women:top:ghost:ghost",
        "cutType": "product",
        "gender": None,
        "shot": "ghost",
        "mood": None,
        "presentationMethod": "ghost",
    })
    _write_manifest(manifest_path, manifest)

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any("제품컷에는 pose·bg" in item for item in caught.value.violations)


@pytest.mark.parametrize("cut_type", ("styling", "horizon", "mirror", "product"))
@pytest.mark.parametrize("direction", ("front", "back", "side", None))
def test_direction_observation_metadata_is_cut_type_independent(
    tmp_path, cut_type, direction
):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    if cut_type == "product":
        example.update({
            "serviceGroupKey": "product:any:top:ghost:ghost",
            "cutType": "product",
            "gender": None,
            "shot": "ghost",
            "mood": None,
            "presentationMethod": "ghost",
            "direction": direction,
            "variants": {"all": example["variants"]["all"]},
        })
    else:
        example.update({
            "serviceGroupKey": f"{cut_type}:women:top:full",
            "cutType": cut_type,
            "mood": "daily" if cut_type == "styling" else None,
            "direction": direction,
        })

    examples, _files, _warnings = release.validate_manifest(
        manifest, root, manifest_path=manifest_path
    )

    assert examples[0]["direction"] == direction


def test_gender_neutral_product_is_valid_and_staged_as_null(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    example.update({
        "serviceGroupKey": "product:any:top:ghost:ghost",
        "cutType": "product",
        "gender": None,
        "shot": "ghost",
        "mood": None,
        "presentationMethod": "ghost",
        "direction": "back",
        "variants": {"all": example["variants"]["all"]},
    })
    _write_manifest(manifest_path, manifest)

    result = release.stage_release(
        manifest_path,
        root,
        public_base_url="https://images.example.test",
        output_dir=tmp_path / "out",
    )
    registry = json.loads(result.registry_path.read_text(encoding="utf-8"))
    catalog = json.loads(result.catalog_path.read_text(encoding="utf-8"))

    assert registry["assets"][example["id"]]["gender"] is None
    assert catalog[0]["gender"] is None


def test_product_gender_value_is_rejected(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    example.update({
        "serviceGroupKey": "product:any:top:ghost:ghost",
        "cutType": "product",
        "gender": "women",
        "shot": "ghost",
        "mood": None,
        "presentationMethod": "ghost",
        "direction": "front",
        "variants": {"all": example["variants"]["all"]},
    })

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any("gender는 성별 공용 제품컷에서 null" in item for item in caught.value.violations)


def test_direction_outside_observation_metadata_values_is_rejected(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    manifest["examples"][0]["direction"] = "diagonal"

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any("direction이 허용값이 아닙니다: diagonal" in item for item in caught.value.violations)


@pytest.mark.parametrize("cut_type", ("styling", "horizon", "mirror"))
def test_worn_gender_null_is_rejected(tmp_path, cut_type):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    example.update({
        "serviceGroupKey": f"{cut_type}:women:top:full",
        "cutType": cut_type,
        "gender": None,
        "mood": "daily" if cut_type == "styling" else None,
        "direction": None if cut_type == "mirror" else "front",
    })

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any("gender는 착용컷에서 women|men" in item for item in caught.value.violations)


def test_image_extension_must_match_actual_format(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    old_path = root / example["variants"]["pose"]["file"]
    wrong_path = old_path.with_suffix(".jpg")
    old_path.rename(wrong_path)
    example["variants"]["pose"]["file"] = str(wrong_path.relative_to(root))
    _write_manifest(manifest_path, manifest)

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any("확장자와 실제 이미지 형식" in item for item in caught.value.violations)


def test_manifest_extra_file_is_warning_only(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    _write_png(root / "assets" / "all" / "not-publishable.png")

    examples, _files, warnings = release.validate_manifest(
        manifest, root, manifest_path=manifest_path
    )

    assert len(examples) == 1
    assert len(warnings) == 1
    assert "manifest 밖 파일 1개" in warnings[0]
    assert "not-publishable.png" in warnings[0]


def test_r2_upload_is_dry_run_by_default_and_mocked_on_execute(tmp_path, capsys):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url="https://images.example.test",
        output_dir=tmp_path / "out",
    )

    class FakeR2:
        def __init__(self):
            self.listed = []
            self.puts = []

        def list_prefix(self, prefix):
            self.listed.append(prefix)
            return []

        def put_bytes(self, key, data, mime, cache=None):
            self.puts.append((key, data, mime, cache))

    fake = FakeR2()
    release.upload_release(result, execute=False, r2_client=fake)
    assert fake.listed == [] and fake.puts == []
    assert "UPLOAD DRY-RUN: 3 objects" in capsys.readouterr().out

    release.upload_release(result, execute=True, r2_client=fake)
    assert fake.listed == ["seed/genexamples/v1/releases/test-release-01/"]
    assert len(fake.puts) == 3
    assert all(put[3] == "public, max-age=31536000, immutable" for put in fake.puts)


def test_r2_execute_rejects_existing_release_prefix_before_put(tmp_path):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url="https://images.example.test",
        output_dir=tmp_path / "out",
    )

    class ExistingR2:
        puts = []

        def list_prefix(self, _prefix):
            return ["seed/genexamples/v1/releases/test-release-01/all/existing.png"]

        def put_bytes(self, *args, **kwargs):
            self.puts.append((args, kwargs))

    fake = ExistingR2()
    with pytest.raises(RuntimeError, match="업로드를 거부"):
        release.upload_release(result, execute=True, r2_client=fake)
    assert fake.puts == []


def test_apply_copies_only_staged_json_to_configured_repo_targets(tmp_path, monkeypatch):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url="https://images.example.test",
        output_dir=tmp_path / "out",
    )
    registry_target = tmp_path / "repo" / "server" / "example_assets.json"
    catalog_target = tmp_path / "repo" / "src" / "genExamples.json"
    monkeypatch.setattr(release, "DEFAULT_REGISTRY_PATH", registry_target)
    monkeypatch.setattr(release, "DEFAULT_CATALOG_PATH", catalog_target)

    release.apply_release(result)

    assert registry_target.read_bytes() == result.registry_path.read_bytes()
    assert catalog_target.read_bytes() == result.catalog_path.read_bytes()


def test_cli_synthetic_fixture_end_to_end_staging_and_upload_dry_run(tmp_path):
    manifest_path, root, _manifest = _fixture(tmp_path)
    output = tmp_path / "cli-stage"
    command = [
        sys.executable,
        str(release.SERVER_DIR / "tools" / "release_genexamples.py"),
        str(manifest_path),
        str(root),
        "--out", str(output),
        "--public-base-url", "https://images.example.test",
        "--upload",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")

    assert completed.returncode == 0
    assert (output / "example_assets.json").is_file()
    assert (output / "genExamples.json").is_file()
    assert "UPLOAD DRY-RUN: 3 objects" in completed.stdout
    assert "/all/" in completed.stdout
    assert "/pose/" in completed.stdout
    assert "/thumb/" in completed.stdout
