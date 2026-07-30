import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

from app.agents import space_set_assets
from tools import release_space_sets as release


RELEASE_ID = "2026-07-30-space-set-test"
PUBLIC_BASE = "https://images.example.test"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt_lineage(root: Path, owner_id: str, variant: str) -> dict:
    path = root / "prompts" / f"{owner_id}_{variant}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"prompt for {owner_id} {variant}\n", encoding="utf-8")
    return {
        "promptPath": str(path.relative_to(root)),
        "sha256": _hash(path),
        "model": "test-image-model",
    }


def _image_asset(
    root: Path,
    owner_id: str,
    variant: str,
    *,
    alpha: bool = False,
) -> dict:
    path = root / "assets" / variant / f"{owner_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "RGBA" if alpha else "RGB"
    if alpha:
        image = Image.new(mode, (36, 48), (0, 0, 0, 0))
        image.paste((70, 100, 130, 255), (8, 5, 28, 45))
    else:
        image = Image.new(mode, (36, 48), (70, 100, 130))
    image.save(path, format="PNG")
    return {
        "localPath": str(path.relative_to(root)),
        "key": (
            f"{release.R2_PREFIX}/{RELEASE_ID}/{variant}/{owner_id}.png"
        ),
        "sha256": _hash(path),
        "width": 36,
        "height": 48,
        "promptLineage": _prompt_lineage(root, owner_id, variant),
    }


def _qc() -> dict:
    return {
        "status": "pass",
        "reviewedAt": "2026-07-30T12:00:00Z",
        "reviewedBy": "owner",
        "gates": {
            "sameSpace": True,
            "sourceSimilarity": True,
            "naturalBodyPose": True,
            "lightingIntegration": True,
            "identityGarmentIntegrity": True,
        },
    }


def _fixture(
    tmp_path: Path,
    *,
    set_id: str = "set_style_women_top_test_01",
    set_type: str = "styling",
    plate_policy: str = "required",
    members: list[tuple[str, str, str | None]] | None = None,
) -> tuple[Path, Path, dict]:
    root = tmp_path / "asset-root"
    root.mkdir()
    definitions = members or [
        ("styling", "full", "front"),
        ("styling", "medium", "side"),
    ]
    member_values = []
    for index, (cut_type, shot, direction) in enumerate(definitions, start=1):
        example_id = f"ss_{set_id}_{index:02d}"
        member_values.append({
            "exampleId": example_id,
            "order": index,
            "cutType": cut_type,
            "shot": shot,
            "direction": direction,
            "all": _image_asset(root, example_id, "all"),
            "pose": _image_asset(root, example_id, "pose", alpha=True),
        })
    representative_plate = (
        _image_asset(root, set_id, "plate") if plate_policy == "required" else None
    )
    manifest = {
        "schemaVersion": 1,
        "releaseId": RELEASE_ID,
        "releasedAt": "2026-07-30T12:00:00Z",
        "sets": [{
            "setId": set_id,
            "name": "테스트 공간",
            "setType": set_type,
            "gender": "women",
            "applicableClothingTypes": ["top"],
            "placeType": "cafe-shop-interior",
            "tone": "daily-snapshot",
            "compositionLabel": "테스트 구성",
            "spaceVariation": "subtle",
            "platePolicy": plate_policy,
            "representativePlate": representative_plate,
            "qc": _qc(),
            "members": member_values,
        }],
    }
    manifest_path = root / "release_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path, root, manifest


def _write_manifest(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def test_valid_manifest_stages_dedicated_catalog_registry_and_deterministic_thumbs(
    tmp_path, monkeypatch,
):
    manifest_path, root, manifest = _fixture(tmp_path)

    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )

    frontend = json.loads(result.frontend_catalog_path.read_text(encoding="utf-8"))
    registry = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    set_id = manifest["sets"][0]["setId"]
    example_id = manifest["sets"][0]["members"][0]["exampleId"]
    assert frontend["_meta"]["releaseId"] == RELEASE_ID
    assert frontend["sets"][0]["id"] == set_id
    assert frontend["sets"][0]["members"][0]["exampleId"] == example_id
    assert frontend["sets"][0]["members"][0]["thumbUrl"].endswith(
        f"/thumb/{example_id}.webp"
    )
    assert frontend["sets"][0]["members"][0]["allUrl"].endswith(
        f"/all/{example_id}.png"
    )
    assert set(registry) == {
        "schemaVersion",
        "releaseId",
        "releasedAt",
        "baseUrl",
        "sets",
    }
    assert registry["baseUrl"] == PUBLIC_BASE
    assert isinstance(registry["sets"], list)
    server_set = registry["sets"][0]
    assert server_set["setId"] == set_id
    assert server_set["representativePlate"]["key"].endswith(
        f"/plate/{set_id}.png"
    )
    assert server_set["members"][0]["pose"]["key"].endswith(
        f"/pose/{example_id}.png"
    )
    assert "url" not in server_set["members"][0]["all"]
    assert "localPath" not in server_set["members"][0]["all"]
    assert "promptLineage" not in server_set["members"][0]["all"]
    assert audit["sets"][0]["qc"]["gates"]["sameSpace"] is True
    assert "promptLineage" in audit["sets"][0]["members"][0]["all"]
    staged_thumb = result.output_dir / "assets" / "thumb" / f"{example_id}.webp"
    staged_all = result.output_dir / "assets" / "all" / f"{example_id}.png"
    staged_pose = result.output_dir / "assets" / "pose" / f"{example_id}.png"
    staged_plate = result.output_dir / "assets" / "plate" / f"{set_id}.png"
    all_path = root / manifest["sets"][0]["members"][0]["all"]["localPath"]
    assert staged_thumb.read_bytes() == release._thumbnail_bytes(all_path)
    assert staged_all.read_bytes() == all_path.read_bytes()
    assert staged_pose.is_file()
    assert staged_plate.is_file()
    assert (result.output_dir / "release_stage.json").is_file()
    assert all(asset.path.is_relative_to(result.output_dir) for asset in result.assets)
    assert not (result.output_dir / "genExamples.json").exists()
    assert not (result.output_dir / "example_assets.json").exists()

    monkeypatch.setattr(
        space_set_assets,
        "_DEFAULT_SPACE_SET_ASSETS",
        str(result.server_registry_path),
    )
    space_set_assets.load_space_set_registry.cache_clear()
    base_url, loaded = space_set_assets.load_space_set_registry()
    assert base_url == PUBLIC_BASE
    assert loaded[set_id]["placeType"] == "cafe-shop-interior"
    assert loaded[set_id]["members"][0]["exampleId"] == example_id
    space_set_assets.load_space_set_registry.cache_clear()


def test_runtime_registry_rejects_noncanonical_place_type(tmp_path):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    registry = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    registry["sets"][0]["placeType"] = "indoor"

    with pytest.raises(ValueError, match="space_set_registry_place_type_invalid"):
        space_set_assets.validate_space_set_registry_document(registry)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("setId", "bad.id"),
        ("setId", "bad__id"),
        ("setId", "a" * 201),
        ("exampleId", "flat_example_01"),
        ("exampleId", "ss_bad__id"),
        ("exampleId", "ss_" + "a" * 198),
    ],
)
def test_set_and_example_ids_share_backend_safe_grammar(
    tmp_path, field, value
):
    _path, root, manifest = _fixture(tmp_path)
    if field == "setId":
        manifest["sets"][0]["setId"] = value
    else:
        manifest["sets"][0]["members"][0]["exampleId"] = value

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    assert any(f".{field}" in item for item in caught.value.violations)


@pytest.mark.parametrize(
    "place_type",
    ["indoor", "05. 작은 해변·항구", "A2 · 여성 하의 회전 세트", " cafe-shop-interior "],
)
def test_manifest_rejects_noncanonical_place_type(tmp_path, place_type):
    _path, root, manifest = _fixture(tmp_path)
    manifest["sets"][0]["placeType"] = place_type

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    assert any(".placeType" in item for item in caught.value.violations)


def test_committed_catalogs_share_the_canonical_place_vocabulary():
    place_table = json.loads(release.PLACE_TYPES_PATH.read_text(encoding="utf-8"))
    allowed = {item["value"] for item in place_table["placeTypes"]}
    frontend = json.loads(
        release.DEFAULT_FRONTEND_CATALOG_PATH.read_text(encoding="utf-8")
    )
    registry = json.loads(
        release.DEFAULT_SERVER_REGISTRY_PATH.read_text(encoding="utf-8")
    )
    front_by_id = {item["setId"]: item for item in frontend["sets"]}
    server_by_id = {item["setId"]: item for item in registry["sets"]}

    assert allowed == release._PLACE_TYPES == space_set_assets._PLACE_TYPES
    assert front_by_id.keys() == server_by_id.keys()
    assert all(item["placeType"] in allowed for item in front_by_id.values())
    assert all(
        item["place"] == item["placeType"]
        for item in front_by_id.values()
    )
    assert all(
        front_by_id[set_id]["placeType"] == server_by_id[set_id]["placeType"]
        for set_id in front_by_id
    )


def test_flat_id_source_is_union_of_frontend_catalog_and_server_registry(tmp_path):
    frontend = tmp_path / "genExamples.json"
    server = tmp_path / "example_assets.json"
    frontend.write_text(
        json.dumps([{"id": "flat_frontend_only"}, {"id": "flat_shared"}]),
        encoding="utf-8",
    )
    server.write_text(
        json.dumps({
            "_meta": {"schemaVersion": 2},
            "assets": {
                "flat_server_only": {},
                "flat_shared": {},
            },
        }),
        encoding="utf-8",
    )

    assert release.load_flat_example_ids(frontend, server) == {
        "flat_frontend_only",
        "flat_server_only",
        "flat_shared",
    }


def test_manifest_rejects_men_dress_set(tmp_path):
    _path, root, manifest = _fixture(tmp_path)
    manifest["sets"][0]["gender"] = "men"
    manifest["sets"][0]["applicableClothingTypes"] = ["dress"]

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    assert any(
        "남성 원피스 적용 범위는 지원하지 않습니다" in item
        for item in caught.value.violations
    )


def test_space_set_example_id_collision_with_actual_flat_ids_is_rejected(tmp_path):
    _path, root, manifest = _fixture(tmp_path)
    example_id = manifest["sets"][0]["members"][0]["exampleId"]

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(
            manifest,
            root,
            flat_example_ids={example_id},
        )

    assert any(
        f"기존 flat 생성예시 ID와 충돌합니다: {example_id}" in item
        for item in caught.value.violations
    )


def test_schema_qc_lineage_key_and_plate_violations_are_all_reported(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    manifest["schemaVersion"] = "1"
    space_set = manifest["sets"][0]
    space_set["qc"]["gates"]["lightingIntegration"] = False
    space_set["representativePlate"] = None
    member = space_set["members"][0]
    member["all"]["key"] = "wrong/key.png"
    member["pose"]["promptLineage"]["sha256"] = "0" * 64
    _write_manifest(manifest_path, manifest)

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    text = "\n".join(caught.value.violations)
    assert "schemaVersion은 정수 1" in text
    assert "lightingIntegration" in text
    assert "representativePlate는 required" in text
    assert ".key가 경로 규약과 다릅니다" in text
    assert "promptLineage.sha256 불일치" in text


def test_pose_requires_actual_transparent_background(tmp_path):
    _path, root, manifest = _fixture(tmp_path)
    pose = manifest["sets"][0]["members"][0]["pose"]
    pose_path = root / pose["localPath"]
    Image.new("RGBA", (36, 48), (70, 100, 130, 255)).save(
        pose_path,
        format="PNG",
    )
    pose["sha256"] = _hash(pose_path)

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    assert any(
        "실제 투명 배경과 보이는 인물" in item
        for item in caught.value.violations
    )


def test_horizon_sequence_can_explicitly_omit_plate(tmp_path):
    manifest_path, root, manifest = _fixture(
        tmp_path,
        set_id="set_horizon_women_top_sequence_01",
        set_type="horizon-sequence",
        plate_policy="not-required",
        members=[
            ("horizon", "full", "front"),
            ("horizon", "full", "side"),
        ],
    )

    sets, files, _warnings = release.validate_manifest(manifest, root)

    assert sets[0]["representativePlate"] is None
    assert not any(key[2] == "plate" for key in files)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    registry = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    assert registry["sets"][0]["setId"] == sets[0]["setId"]
    assert registry["sets"][0]["representativePlate"] is None


def test_non_sequence_cannot_opt_out_of_representative_plate(tmp_path):
    _path, root, manifest = _fixture(tmp_path)
    space_set = manifest["sets"][0]
    space_set["platePolicy"] = "not-required"
    space_set["representativePlate"] = None

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    assert any(
        "not-required는 horizon-sequence에만" in item
        for item in caught.value.violations
    )


def test_legacy_prompt_exception_is_only_allowed_for_named_set_all_assets(tmp_path):
    legacy_id = "set-style-women-dress-neighborhood-garage-modimood-3266-root04"
    _path, root, manifest = _fixture(tmp_path, set_id=legacy_id)
    asset = manifest["sets"][0]["members"][0]["all"]
    asset.pop("promptLineage")
    asset["reviewedProvenanceException"] = {
        "code": "legacy-approved-missing-prompt",
        "reason": "승인 당시 실제 프롬프트 파일이 보존되지 않음",
        "reviewedBy": "owner",
        "reviewedAt": "2026-07-30T12:00:00Z",
    }

    sets, _files, _warnings = release.validate_manifest(manifest, root)
    assert sets[0]["setId"] == legacy_id
    manifest_path = root / "release_manifest.json"
    _write_manifest(manifest_path, manifest)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "legacy-staged",
    )
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert (
        audit["sets"][0]["members"][0]["all"]["reviewedProvenanceException"]["code"]
        == "legacy-approved-missing-prompt"
    )

    invalid = deepcopy(manifest)
    invalid["sets"][0]["setId"] = "set_style_women_dress_not_allowlisted"
    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(invalid, root)
    assert any(
        "두 legacy 세트의 all 자산에만" in item
        for item in caught.value.violations
    )


def test_supplied_thumb_must_equal_deterministic_derivative(tmp_path):
    _path, root, manifest = _fixture(tmp_path)
    member = manifest["sets"][0]["members"][0]
    example_id = member["exampleId"]
    thumb_path = root / "assets" / "thumb" / f"{example_id}.webp"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 20), (250, 10, 10)).save(thumb_path, format="WEBP")
    member["thumb"] = {
        "localPath": str(thumb_path.relative_to(root)),
        "key": f"{release.R2_PREFIX}/{RELEASE_ID}/thumb/{example_id}.webp",
        "sha256": _hash(thumb_path),
        "width": 20,
        "height": 20,
        "derivedFrom": "all",
    }

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    assert any(
        "thumb가 all에서 고정 파라미터로 파생한 바이트와 다릅니다" in item
        for item in caught.value.violations
    )


def test_top_outer_shared_set_with_medium_member_is_rejected(tmp_path):
    _path, root, manifest = _fixture(tmp_path)
    manifest["sets"][0]["applicableClothingTypes"] = ["top", "outer"]

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    assert any(
        "[top,outer] 공용 세트는 모든 멤버가 full" in item
        for item in caught.value.violations
    )


def test_rotation_requires_front_side_back_full_order(tmp_path):
    _path, root, manifest = _fixture(
        tmp_path,
        set_id="set_horizon_women_top_rotation_01",
        set_type="horizon-rotation",
        members=[
            ("horizon", "full", "front"),
            ("horizon", "full", "back"),
            ("horizon", "full", "side"),
        ],
    )

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    assert any(
        "front→side→back full 3장" in item for item in caught.value.violations
    )


def test_staging_and_apply_refuse_same_release_overwrite(tmp_path, monkeypatch):
    manifest_path, root, _manifest = _fixture(tmp_path)
    output = tmp_path / "staged"
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=output,
    )
    with pytest.raises(FileExistsError):
        release.stage_release(
            manifest_path,
            root,
            public_base_url=PUBLIC_BASE,
            output_dir=output,
        )

    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)
    release.apply_release(result)
    assert frontend_target.is_file()
    assert server_target.is_file()
    with pytest.raises(FileExistsError):
        release.apply_release(result)


def test_sealed_stage_is_reused_and_tampering_is_rejected(tmp_path):
    manifest_path, root, _manifest = _fixture(tmp_path)
    output = tmp_path / "staged"
    staged = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=output,
    )

    reused = release.load_staged_release(output)
    assert reused.release_id == staged.release_id
    assert sorted(asset.r2_key for asset in reused.assets) == sorted(
        asset.r2_key for asset in staged.assets
    )

    reused.assets[0].path.write_bytes(reused.assets[0].path.read_bytes() + b"tamper")
    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.load_staged_release(output)
    assert any(
        "승인된 스테이징 바이트와 다릅니다" in item
        for item in caught.value.violations
    )


def test_upload_uses_staged_asset_copies_after_sources_change(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    all_spec = manifest["sets"][0]["members"][0]["all"]
    source_all = root / all_spec["localPath"]
    staged_asset = next(
        asset
        for asset in result.assets
        if asset.variant == "all"
        and asset.owner_id == manifest["sets"][0]["members"][0]["exampleId"]
    )
    original_staged_bytes = staged_asset.path.read_bytes()
    Image.new("RGB", (36, 48), (255, 0, 0)).save(source_all, format="PNG")
    assert source_all.read_bytes() != original_staged_bytes

    class FakeR2:
        def __init__(self):
            self.puts = {}

        def list_prefix(self, _prefix):
            return []

        def put_bytes(self, key, data, *_args, **_kwargs):
            self.puts[key] = data

    client = FakeR2()
    release.upload_release(result, execute=True, r2_client=client)
    assert client.puts[staged_asset.r2_key] == original_staged_bytes


def test_staged_copy_is_rehashed_and_corruption_is_rejected(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    original_copy = release.shutil.copyfile

    def corrupt_all_copy(source, destination):
        result = original_copy(source, destination)
        if "/assets/all/" in str(destination):
            Path(destination).write_bytes(Path(destination).read_bytes() + b"corrupt")
        return result

    monkeypatch.setattr(release.shutil, "copyfile", corrupt_all_copy)
    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.stage_release(
            manifest_path,
            root,
            public_base_url=PUBLIC_BASE,
            output_dir=tmp_path / "staged",
        )
    assert any(
        "스테이징 복제본 sha256 불일치" in item
        for item in caught.value.violations
    )


def _empty_server_registry(release_id="old-release") -> dict:
    return {
        "schemaVersion": 1,
        "releaseId": release_id,
        "releasedAt": "2026-07-29T00:00:00Z",
        "baseUrl": PUBLIC_BASE,
        "sets": [],
    }


def test_apply_rolls_back_frontend_when_server_copy_fails(tmp_path, monkeypatch):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(
        json.dumps({"_meta": {"releaseId": "old-release"}, "sets": []}),
        encoding="utf-8",
    )
    server_target.write_text(
        json.dumps(_empty_server_registry()),
        encoding="utf-8",
    )
    frontend_before = frontend_target.read_bytes()
    server_before = server_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)
    original_atomic_copy = release._atomic_copy
    call_count = 0

    def fail_second_copy(source, destination):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("injected second-copy failure")
        original_atomic_copy(source, destination)

    monkeypatch.setattr(release, "_atomic_copy", fail_second_copy)
    with pytest.raises(OSError, match="injected"):
        release.apply_release(result)
    assert frontend_target.read_bytes() == frontend_before
    assert server_target.read_bytes() == server_before


def test_apply_preserves_server_only_old_sets_but_frontend_is_current_only(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    next_server = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    old_set = deepcopy(next_server["sets"][0])
    old_set["setId"] = "set_old_women_top_01"
    old_release_id = "old-space-release"
    old_set["representativePlate"]["key"] = old_set["representativePlate"]["key"].replace(
        RELEASE_ID,
        old_release_id,
    )
    for index, member in enumerate(old_set["members"], start=1):
        member["exampleId"] = f"ss_set_old_women_top_01_{index:02d}"
        for variant in ("all", "pose"):
            member[variant]["key"] = member[variant]["key"].replace(
                RELEASE_ID,
                old_release_id,
            )
    old_registry = _empty_server_registry(old_release_id)
    old_registry["sets"] = [old_set]

    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    server_target.write_text(json.dumps(old_registry), encoding="utf-8")
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    release.apply_release(result)

    applied_frontend = json.loads(frontend_target.read_text(encoding="utf-8"))
    applied_server = json.loads(server_target.read_text(encoding="utf-8"))
    assert [item["setId"] for item in applied_frontend["sets"]] == [
        next_server["sets"][0]["setId"]
    ]
    assert [item["setId"] for item in applied_server["sets"]] == [
        next_server["sets"][0]["setId"],
        "set_old_women_top_01",
    ]
    assert old_release_id in applied_server["sets"][1]["representativePlate"]["key"]


def test_apply_rejects_changed_definition_for_existing_set_id_before_mutation(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    old_registry = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    old_registry["releaseId"] = "old-release"
    old_registry["sets"][0]["name"] = "같은 ID의 다른 정의"
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(
        json.dumps({"_meta": {"releaseId": "old-release"}, "sets": []}),
        encoding="utf-8",
    )
    server_target.write_text(json.dumps(old_registry), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match="동일 setId의 정의 변경"):
        release.apply_release(result)
    assert frontend_target.read_bytes() == frontend_before


def test_apply_rejects_malformed_preserved_registry_before_mutation(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    malformed = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    malformed["releaseId"] = "old-release"
    malformed["sets"][0]["members"][0]["pose"].pop("mime")
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(
        json.dumps({"_meta": {"releaseId": "old-release"}, "sets": []}),
        encoding="utf-8",
    )
    server_target.write_text(json.dumps(malformed), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match="런타임 계약"):
        release.apply_release(result)
    assert frontend_target.read_bytes() == frontend_before


def test_apply_rejects_example_id_collision_with_preserved_set(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    current = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    current["releaseId"] = "old-release"
    current["sets"][0]["setId"] = "set_old_women_top_01"
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(
        json.dumps({"_meta": {"releaseId": "old-release"}, "sets": []}),
        encoding="utf-8",
    )
    server_target.write_text(json.dumps(current), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match="example_id_invalid"):
        release.apply_release(result)
    assert frontend_target.read_bytes() == frontend_before


def test_cli_apply_requires_execute_upload_and_upload_happens_first(
    tmp_path, monkeypatch, capsys
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    assert release.main([str(manifest_path), str(root), "--apply"]) == 2
    assert "--upload --execute" in capsys.readouterr().err

    staged = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    events = []

    def failed_upload(*_args, **_kwargs):
        events.append("upload")
        raise RuntimeError("injected upload failure")

    monkeypatch.setattr(release, "upload_release", failed_upload)
    monkeypatch.setattr(
        release,
        "apply_release",
        lambda _result: events.append("apply"),
    )
    code = release.main([
        "--from-stage",
        str(staged.output_dir),
        "--upload",
        "--execute",
        "--apply",
    ])
    assert code == 2
    assert events == ["upload"]

    events.clear()
    monkeypatch.setattr(
        release,
        "upload_release",
        lambda *_args, **_kwargs: events.append("upload"),
    )
    assert release.main([
        "--from-stage",
        str(staged.output_dir),
        "--upload",
        "--execute",
        "--apply",
    ]) == 0
    assert events == ["upload", "apply"]


def test_cli_can_execute_and_apply_the_exact_previously_sealed_stage(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    staged = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    events = []
    monkeypatch.setattr(
        release,
        "upload_release",
        lambda result, **_kwargs: events.append(("upload", result.output_dir)),
    )
    monkeypatch.setattr(
        release,
        "apply_release",
        lambda result: events.append(("apply", result.output_dir)),
    )

    assert release.main([
        "--from-stage",
        str(staged.output_dir),
        "--upload",
        "--execute",
        "--apply",
    ]) == 0
    assert events == [
        ("upload", staged.output_dir),
        ("apply", staged.output_dir),
    ]


def test_release_id_and_public_base_match_runtime_contract(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    manifest["releaseId"] = "bad.release"
    _write_manifest(manifest_path, manifest)
    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)
    assert any("releaseId는 200자 이하" in item for item in caught.value.violations)

    base_fixture = tmp_path / "base"
    base_fixture.mkdir()
    _manifest_path, valid_root, _valid_manifest = _fixture(base_fixture)
    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.stage_release(
            _manifest_path,
            valid_root,
            public_base_url=f"{PUBLIC_BASE}/images",
            output_dir=tmp_path / "bad-base-stage",
        )
    assert any("http(s) origin" in item for item in caught.value.violations)


def test_upload_is_dry_run_by_default_and_execute_refuses_existing_prefix(
    tmp_path, capsys
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )

    class FakeR2:
        def __init__(self, existing=None):
            self.existing = existing or []
            self.puts = []
            self.listed = []

        def list_prefix(self, prefix):
            self.listed.append(prefix)
            return self.existing

        def put_bytes(self, *args, **kwargs):
            self.puts.append((args, kwargs))

    dry_client = FakeR2()
    release.upload_release(result, execute=False, r2_client=dry_client)
    assert dry_client.listed == []
    assert dry_client.puts == []
    assert "UPLOAD DRY-RUN" in capsys.readouterr().out

    blocked_client = FakeR2(existing=["already-there"])
    with pytest.raises(RuntimeError):
        release.upload_release(result, execute=True, r2_client=blocked_client)
    assert blocked_client.puts == []

    live_client = FakeR2()
    release.upload_release(result, execute=True, r2_client=live_client)
    assert live_client.listed == [f"{release.R2_PREFIX}/{RELEASE_ID}/"]
    assert len(live_client.puts) == len(result.assets)
