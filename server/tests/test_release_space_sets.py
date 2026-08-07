import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from app.agents import space_set_assets
from tools import release_space_sets as release


RELEASE_ID = "2026-07-30-space-set-test"
PUBLIC_BASE = "https://images.example.test"


def _receipt(result, *, release_id=None, uploaded_keys=None):
    keys = (
        frozenset(asset.r2_key for asset in result.assets)
        if uploaded_keys is None
        else frozenset(uploaded_keys)
    )
    return release.UploadReceipt(
        release_id=release_id or result.release_id,
        uploaded_keys=keys,
    )


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
        "placeTypes",
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

    # 런타임은 더 이상 장소 어휘를 모듈 상수로 들고 있지 않다. 어휘는 릴리스 문서의
    # placeTypes 로 함께 이동하고, validate_space_set_registry_document 가 그 목록으로
    # 각 세트의 placeType 을 검사한다. 그래서 정본 표 == 도구 상수 == 배포된 문서 셋
    # 세 개가 같아야 한다(하나라도 어긋나면 배포본이 정본과 다른 어휘로 돈다).
    assert allowed == release._PLACE_TYPES == set(registry["placeTypes"])
    space_set_assets.validate_space_set_registry_document(registry)
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


def test_committed_catalogs_keep_the_delta_ordering_invariant():
    """delta 적용이 남기는 불변식을 배포본에 대해 검사한다.

    개수·releaseId 를 하드코딩하지 않는다 — 그렇게 두면 릴리스마다 테스트가 낡아
    (2026-08-04: 60/women-bottom-v1 로 굳어 있던 것을 정리) 정작 지켜야 할
    "두 카탈로그가 같은 순서"라는 불변식이 가려진다. 여기서 고정하는 것은
    개수가 아니라 **프론트·서버 일치와 신규 릴리스 선두 배치**다.
    """
    current_frontend = json.loads(
        release.DEFAULT_FRONTEND_CATALOG_PATH.read_text(encoding="utf-8")
    )
    current_server = json.loads(
        release.DEFAULT_SERVER_REGISTRY_PATH.read_text(encoding="utf-8")
    )
    frontend_ids = [item["setId"] for item in current_frontend["sets"]]
    server_ids = [item["setId"] for item in current_server["sets"]]

    # 1) 두 카탈로그의 setId 목록이 순서까지 동일 (delta 적용의 핵심 계약)
    assert frontend_ids == server_ids
    assert len(frontend_ids) == len(set(frontend_ids))  # 중복 없음
    # 2) 릴리스 메타데이터가 두 파일에서 일치
    assert current_frontend["_meta"]["releaseId"] == current_server["releaseId"]
    assert current_frontend["_meta"]["releasedAt"] == current_server["releasedAt"]
    # 3) 현재 릴리스에 속한 세트가 선두에 연속으로 온다(보존 세트는 그 뒤).
    release_id = current_server["releaseId"]
    owned = [
        item["setId"] for item in current_server["sets"]
        if f"/releases/{release_id}/" in item["representativePlate"]["key"]
    ] if current_server["sets"] and current_server["sets"][0].get(
        "representativePlate"
    ) else []
    if owned:
        assert server_ids[: len(owned)] == owned


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


def test_horizon_sequence_rejects_required_plate(tmp_path):
    _path, root, manifest = _fixture(
        tmp_path,
        set_id="set_horizon_women_top_sequence_required_01",
        set_type="horizon-sequence",
        members=[
            ("horizon", "full", "front"),
            ("horizon", "medium", "side"),
        ],
    )

    with pytest.raises(release.SpaceSetReleaseValidationError) as caught:
        release.validate_manifest(manifest, root)

    assert any(
        "horizon-sequence는 platePolicy=not-required" in item
        for item in caught.value.violations
    )


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
    receipt = _receipt(result)
    release.apply_release(result, receipt)
    assert frontend_target.is_file()
    assert server_target.is_file()
    with pytest.raises(FileExistsError):
        release.apply_release(result, receipt)


def test_apply_requires_upload_receipt_even_when_called_directly(tmp_path):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )

    with pytest.raises(TypeError):
        release.apply_release(result)


def test_apply_rejects_receipt_from_a_different_release(tmp_path, monkeypatch):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    monkeypatch.setattr(
        release,
        "DEFAULT_FRONTEND_CATALOG_PATH",
        tmp_path / "live" / "storyboardSpaceSets.json",
    )
    monkeypatch.setattr(
        release,
        "DEFAULT_SERVER_REGISTRY_PATH",
        tmp_path / "live" / "space_set_assets.json",
    )

    with pytest.raises(RuntimeError, match="다른 릴리스의 것"):
        release.apply_release(
            result,
            _receipt(result, release_id="different-release"),
        )


def test_apply_rejects_catalog_key_missing_from_upload_receipt(tmp_path, monkeypatch):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    thumb = next(asset for asset in result.assets if asset.variant == "thumb")
    incomplete_result = replace(
        result,
        assets=tuple(asset for asset in result.assets if asset != thumb),
    )
    monkeypatch.setattr(
        release,
        "DEFAULT_FRONTEND_CATALOG_PATH",
        tmp_path / "live" / "storyboardSpaceSets.json",
    )
    monkeypatch.setattr(
        release,
        "DEFAULT_SERVER_REGISTRY_PATH",
        tmp_path / "live" / "space_set_assets.json",
    )

    with pytest.raises(RuntimeError, match="명단이 올리지 않은 키를 참조"):
        release.apply_release(incomplete_result, _receipt(incomplete_result))


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
        # 런타임 계약(app/agents/space_set_assets.py)이 요구하는 필드 — 빠지면
        # delta 가드가 정상 레지스트리를 metadata_invalid 로 오거부한다.
        "placeTypes": sorted(release._PLACE_TYPES),
        "sets": [],
    }


def _empty_frontend_catalog(release_id="old-release") -> dict:
    return {
        "_meta": {
            "schemaVersion": 1,
            "releaseId": release_id,
            "releasedAt": "2026-07-29T00:00:00Z",
            "defaultBaseUrl": PUBLIC_BASE,
        },
        "sets": [],
    }


def _old_catalog_pair(
    result,
    *,
    set_id="set_old_women_top_01",
    release_id="old-space-release",
    keep_example_ids=False,
) -> tuple[dict, dict]:
    frontend = json.loads(result.frontend_catalog_path.read_text(encoding="utf-8"))
    registry = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    frontend_set = deepcopy(frontend["sets"][0])
    server_set = deepcopy(registry["sets"][0])
    original_set_id = server_set["setId"]
    frontend_set["setId"] = frontend_set["id"] = set_id
    server_set["setId"] = set_id
    frontend_set["representativePlate"]["url"] = (
        frontend_set["representativePlate"]["url"]
        .replace(RELEASE_ID, release_id)
        .replace(original_set_id, set_id)
    )
    server_set["representativePlate"]["key"] = (
        server_set["representativePlate"]["key"]
        .replace(RELEASE_ID, release_id)
        .replace(original_set_id, set_id)
    )
    for index, (frontend_member, server_member) in enumerate(
        zip(frontend_set["members"], server_set["members"]),
        start=1,
    ):
        original_example_id = server_member["exampleId"]
        example_id = (
            original_example_id
            if keep_example_ids
            else f"ss_{set_id}_{index:02d}"
        )
        frontend_member["exampleId"] = example_id
        server_member["exampleId"] = example_id
        for field in ("allUrl", "thumbUrl"):
            frontend_member[field] = (
                frontend_member[field]
                .replace(RELEASE_ID, release_id)
                .replace(original_example_id, example_id)
            )
        for variant in ("all", "pose"):
            server_member[variant]["key"] = (
                server_member[variant]["key"]
                .replace(RELEASE_ID, release_id)
                .replace(original_example_id, example_id)
            )
    old_frontend = _empty_frontend_catalog(release_id)
    old_frontend["sets"] = [frontend_set]
    old_registry = _empty_server_registry(release_id)
    old_registry["sets"] = [server_set]
    return old_frontend, old_registry


def _assert_apply_rejected_without_mutation(
    result,
    *,
    tmp_path,
    monkeypatch,
    frontend,
    registry,
    match,
) -> None:
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True, exist_ok=True)
    server_target.parent.mkdir(parents=True, exist_ok=True)
    frontend_target.write_text(json.dumps(frontend), encoding="utf-8")
    server_target.write_text(json.dumps(registry), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    server_before = server_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match=match):
        release.apply_release(result, _receipt(result))
    assert frontend_target.read_bytes() == frontend_before
    assert server_target.read_bytes() == server_before


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
        json.dumps(_empty_frontend_catalog()),
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
        release.apply_release(result, _receipt(result))
    assert frontend_target.read_bytes() == frontend_before
    assert server_target.read_bytes() == server_before


def test_apply_preserves_old_sets_in_frontend_and_server_in_the_same_order(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    next_frontend = json.loads(result.frontend_catalog_path.read_text(encoding="utf-8"))
    next_server = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    old_frontend, old_registry = _old_catalog_pair(result)

    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(json.dumps(old_frontend), encoding="utf-8")
    server_target.write_text(json.dumps(old_registry), encoding="utf-8")
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    release.apply_release(result, _receipt(result))

    applied_frontend = json.loads(frontend_target.read_text(encoding="utf-8"))
    applied_server = json.loads(server_target.read_text(encoding="utf-8"))
    assert [item["setId"] for item in applied_frontend["sets"]] == [
        next_frontend["sets"][0]["setId"],
        "set_old_women_top_01",
    ]
    assert [item["setId"] for item in applied_server["sets"]] == [
        next_server["sets"][0]["setId"],
        "set_old_women_top_01",
    ]
    assert (
        "old-space-release"
        in applied_frontend["sets"][1]["representativePlate"]["url"]
    )
    assert (
        "old-space-release"
        in applied_server["sets"][1]["representativePlate"]["key"]
    )


def test_apply_rejects_malformed_old_frontend_before_mutation(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    malformed_frontend = _empty_frontend_catalog()
    malformed_frontend["_meta"].pop("defaultBaseUrl")
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(json.dumps(malformed_frontend), encoding="utf-8")
    server_target.write_text(json.dumps(_empty_server_registry()), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    server_before = server_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match="프론트 계약"):
        release.apply_release(result, _receipt(result))
    assert frontend_target.read_bytes() == frontend_before
    assert server_target.read_bytes() == server_before


@pytest.mark.parametrize(
    "case",
    [
        "empty-name",
        "empty-tone",
        "empty-composition",
        "sequence-with-required-plate",
        "rotation-with-two-members",
        "boolean-order",
    ],
)
def test_apply_rejects_invalid_old_frontend_semantics_before_mutation(
    tmp_path,
    monkeypatch,
    case,
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    frontend, registry = _old_catalog_pair(result)
    space_set = frontend["sets"][0]
    if case == "empty-name":
        space_set["name"] = " "
    elif case == "empty-tone":
        space_set["tone"] = ""
    elif case == "empty-composition":
        space_set["compositionLabel"] = "\t"
    elif case == "sequence-with-required-plate":
        space_set["setType"] = "horizon-sequence"
        for member in space_set["members"]:
            member["cutType"] = "horizon"
    elif case == "rotation-with-two-members":
        space_set["setType"] = "horizon-rotation"
        space_set["members"] = space_set["members"][:2]
        for member, direction in zip(
            space_set["members"],
            ("front", "side"),
        ):
            member["cutType"] = "horizon"
            member["shot"] = "full"
            member["direction"] = direction
    else:
        space_set["members"][0]["order"] = True

    _assert_apply_rejected_without_mutation(
        result,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        frontend=frontend,
        registry=registry,
        match="프론트 계약",
    )


@pytest.mark.parametrize(
    "case",
    [
        "pose-members-swapped",
        "pose-jpeg",
        "plate-mime-mismatch",
        "all-mime-mismatch",
        "different-release-root",
        "duplicate-key",
    ],
)
def test_apply_rejects_invalid_old_server_asset_binding_before_mutation(
    tmp_path,
    monkeypatch,
    case,
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    frontend, registry = _old_catalog_pair(result)
    space_set = registry["sets"][0]
    if case == "pose-members-swapped":
        first_pose = space_set["members"][0]["pose"]
        second_pose = space_set["members"][1]["pose"]
        space_set["members"][0]["pose"] = second_pose
        space_set["members"][1]["pose"] = first_pose
    elif case == "pose-jpeg":
        pose = space_set["members"][0]["pose"]
        pose["key"] = pose["key"].removesuffix(".png") + ".jpg"
        pose["mime"] = "image/jpeg"
    elif case == "plate-mime-mismatch":
        space_set["representativePlate"]["mime"] = "image/jpeg"
    elif case == "all-mime-mismatch":
        space_set["members"][0]["all"]["mime"] = "image/jpeg"
    elif case == "different-release-root":
        pose = space_set["members"][0]["pose"]
        pose["key"] = pose["key"].replace(
            "old-space-release",
            "different-release",
        )
    else:
        space_set["members"][1]["all"]["key"] = (
            space_set["members"][0]["all"]["key"]
        )

    _assert_apply_rejected_without_mutation(
        result,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        frontend=frontend,
        registry=registry,
        match="릴리스 계약",
    )


@pytest.mark.parametrize("invalid_order", [True, 1.0])
def test_apply_rejects_non_integer_old_server_member_order_before_mutation(
    tmp_path,
    monkeypatch,
    invalid_order,
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    frontend, registry = _old_catalog_pair(result)
    registry["sets"][0]["members"][0]["order"] = invalid_order

    _assert_apply_rejected_without_mutation(
        result,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        frontend=frontend,
        registry=registry,
        match="릴리스 계약",
    )


@pytest.mark.parametrize(
    "case",
    [
        "release-id-mismatch",
        "released-at-mismatch",
        "server-released-at-missing",
        "frontend-released-at-missing",
        "server-schema-version-boolean",
        "frontend-schema-version-boolean",
    ],
)
def test_apply_rejects_frontend_server_release_metadata_mismatch_before_mutation(
    tmp_path,
    monkeypatch,
    case,
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    frontend, registry = _old_catalog_pair(result)
    if case == "release-id-mismatch":
        registry["releaseId"] = "different-release"
    elif case == "released-at-mismatch":
        registry["releasedAt"] = "2026-07-29T01:00:00Z"
    elif case == "server-released-at-missing":
        registry.pop("releasedAt")
    elif case == "frontend-released-at-missing":
        frontend["_meta"].pop("releasedAt")
    elif case == "server-schema-version-boolean":
        registry["schemaVersion"] = True
    else:
        frontend["_meta"]["schemaVersion"] = True

    _assert_apply_rejected_without_mutation(
        result,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        frontend=frontend,
        registry=registry,
        match="(릴리스 메타데이터|릴리스 계약|런타임 계약|프론트 계약)",
    )


def test_apply_rejects_old_and_new_release_base_url_mismatch(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    old_frontend = _empty_frontend_catalog()
    old_frontend["_meta"]["defaultBaseUrl"] = "https://other-images.example.test"
    old_registry = _empty_server_registry()
    old_registry["baseUrl"] = "https://other-images.example.test"
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(json.dumps(old_frontend), encoding="utf-8")
    server_target.write_text(json.dumps(old_registry), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    server_before = server_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match="기존 공간 세트 서버.*baseUrl"):
        release.apply_release(result, _receipt(result))
    assert frontend_target.read_bytes() == frontend_before
    assert server_target.read_bytes() == server_before


def test_apply_rejects_existing_frontend_server_set_order_mismatch(
    tmp_path, monkeypatch
):
    manifest_path, root, _manifest = _fixture(tmp_path)
    result = release.stage_release(
        manifest_path,
        root,
        public_base_url=PUBLIC_BASE,
        output_dir=tmp_path / "staged",
    )
    frontend_a, registry_a = _old_catalog_pair(
        result,
        set_id="set_old_women_top_a",
        release_id="old-space-release",
    )
    frontend_b, registry_b = _old_catalog_pair(
        result,
        set_id="set_old_women_top_b",
        release_id="old-space-release",
    )
    old_frontend = _empty_frontend_catalog("old-space-release")
    old_frontend["sets"] = [frontend_a["sets"][0], frontend_b["sets"][0]]
    old_registry = _empty_server_registry("old-space-release")
    old_registry["sets"] = [registry_b["sets"][0], registry_a["sets"][0]]
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(json.dumps(old_frontend), encoding="utf-8")
    server_target.write_text(json.dumps(old_registry), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    server_before = server_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match="setId 순서"):
        release.apply_release(result, _receipt(result))
    assert frontend_target.read_bytes() == frontend_before
    assert server_target.read_bytes() == server_before


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
    old_frontend = json.loads(result.frontend_catalog_path.read_text(encoding="utf-8"))
    old_registry = json.loads(result.server_registry_path.read_text(encoding="utf-8"))
    old_frontend["_meta"]["releaseId"] = "old-release"
    old_registry["releaseId"] = "old-release"
    old_frontend["sets"][0]["name"] = "같은 ID의 다른 정의"
    old_registry["sets"][0]["name"] = "같은 ID의 다른 정의"
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(json.dumps(old_frontend), encoding="utf-8")
    server_target.write_text(json.dumps(old_registry), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match="동일 setId의 정의 변경"):
        release.apply_release(result, _receipt(result))
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
    old_frontend = json.loads(result.frontend_catalog_path.read_text(encoding="utf-8"))
    old_frontend["_meta"]["releaseId"] = "old-release"
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(json.dumps(old_frontend), encoding="utf-8")
    server_target.write_text(json.dumps(malformed), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match="런타임 계약"):
        release.apply_release(result, _receipt(result))
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
    old_frontend, current = _old_catalog_pair(
        result,
        release_id="old-release",
        keep_example_ids=True,
    )
    frontend_target = tmp_path / "repo" / "src" / "data" / "storyboardSpaceSets.json"
    server_target = tmp_path / "repo" / "server" / "data" / "space_set_assets.json"
    frontend_target.parent.mkdir(parents=True)
    server_target.parent.mkdir(parents=True)
    frontend_target.write_text(json.dumps(old_frontend), encoding="utf-8")
    server_target.write_text(json.dumps(current), encoding="utf-8")
    frontend_before = frontend_target.read_bytes()
    monkeypatch.setattr(release, "DEFAULT_FRONTEND_CATALOG_PATH", frontend_target)
    monkeypatch.setattr(release, "DEFAULT_SERVER_REGISTRY_PATH", server_target)

    with pytest.raises(RuntimeError, match="example_id_invalid"):
        release.apply_release(result, _receipt(result))
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
        lambda _result, _receipt: events.append("apply"),
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

    def successful_upload(result, **_kwargs):
        events.append("upload")
        return _receipt(result)

    monkeypatch.setattr(
        release,
        "upload_release",
        successful_upload,
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

    def successful_upload(result, **_kwargs):
        events.append(("upload", result.output_dir))
        return _receipt(result)

    monkeypatch.setattr(
        release,
        "upload_release",
        successful_upload,
    )
    monkeypatch.setattr(
        release,
        "apply_release",
        lambda result, _receipt: events.append(("apply", result.output_dir)),
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
    assert release.upload_release(result, execute=False, r2_client=dry_client) is None
    assert dry_client.listed == []
    assert dry_client.puts == []
    assert "UPLOAD DRY-RUN" in capsys.readouterr().out

    blocked_client = FakeR2(existing=["already-there"])
    with pytest.raises(RuntimeError):
        release.upload_release(result, execute=True, r2_client=blocked_client)
    assert blocked_client.puts == []

    live_client = FakeR2()
    receipt = release.upload_release(result, execute=True, r2_client=live_client)
    assert live_client.listed == [f"{release.R2_PREFIX}/{RELEASE_ID}/"]
    assert len(live_client.puts) == len(result.assets)
    assert receipt == _receipt(result)
