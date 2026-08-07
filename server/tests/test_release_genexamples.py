import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

from tools import release_genexamples as release


_LOAD_PUBLIC_COMBINATIONS = release.load_public_combinations
_GENERATION_EXAMPLE_COVERAGE = release.generation_example_coverage


@pytest.fixture(autouse=True)
def _isolate_manifest_schema_tests_from_production_coverage(monkeypatch):
    monkeypatch.setattr(release, "load_public_combinations", lambda path=None: [])
    monkeypatch.setattr(release, "generation_example_coverage", lambda examples, combinations=None: ({}, [], []))


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


def test_men_dress_generation_example_is_rejected(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    example.update({
        "serviceGroupKey": "styling:men:dress:full:daily",
        "gender": "men",
        "sourceClothingType": "dress",
        "applicableClothingTypes": ["dress"],
    })

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any(
        "남성 원피스 생성예시는 지원하지 않습니다" in item
        for item in caught.value.violations
    )


@pytest.mark.parametrize(
    ("gender", "shot", "applicable"),
    (
        ("women", "full", ["top", "bottom", "outer", "dress"]),
        ("men", "full", ["top", "bottom", "outer"]),
        ("women", "medium", ["top", "outer", "dress"]),
        ("men", "medium", ["top", "outer"]),
    ),
)
def test_horizon_shared_scopes_are_allowed(tmp_path, gender, shot, applicable):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    example.update({
        "serviceGroupKey": f"horizon:{gender}:top:{shot}",
        "cutType": "horizon",
        "gender": gender,
        "shot": shot,
        "mood": None,
        "applicableClothingTypes": applicable,
    })

    release.validate_manifest(manifest, root, manifest_path=manifest_path)


def test_horizon_bottom_medium_cannot_use_the_upper_crop_scope(tmp_path):
    manifest_path, root, manifest = _fixture(tmp_path)
    example = manifest["examples"][0]
    example.update({
        "serviceGroupKey": "horizon:women:bottom:medium",
        "cutType": "horizon",
        "shot": "medium",
        "mood": None,
        "sourceClothingType": "bottom",
        "applicableClothingTypes": ["top", "bottom", "outer", "dress"],
    })

    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)

    assert any(
        "호리존 상단 중간샷 규칙" in item
        for item in caught.value.violations
    )


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

    # 업로드가 끝났다는 증거로만 적용된다. 실제 스테이징 산출물이 강화된 전체 스키마
    # 검증(_validate_release_documents)을 그대로 통과하는지도 여기서 함께 지켜진다.
    receipt = release.UploadReceipt(
        release_id=result.release_id,
        uploaded_keys=frozenset(asset.r2_key for asset in result.assets),
    )
    release.apply_release(result, receipt)

    assert registry_target.read_bytes() == result.registry_path.read_bytes()
    assert catalog_target.read_bytes() == result.catalog_path.read_bytes()


def test_declared_zero_count_is_a_hard_validation_failure(tmp_path, monkeypatch):
    manifest_path, root, manifest = _fixture(tmp_path)
    monkeypatch.setattr(release, "generation_example_coverage", _GENERATION_EXAMPLE_COVERAGE)
    monkeypatch.setattr(release, "load_public_combinations", lambda path=None: [{
        "cutType": "horizon", "shot": "full", "clothingType": "top", "gender": "women",
    }])
    with pytest.raises(release.ReleaseValidationError) as caught:
        release.validate_manifest(manifest, root, manifest_path=manifest_path)
    assert any("공개 선언 조합에 all 발행 예시가 0장입니다" in item
               for item in caught.value.violations)


def test_undeclared_published_combination_is_reported_not_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "generation_example_coverage", _GENERATION_EXAMPLE_COVERAGE)
    manifest_path, root, manifest = _fixture(tmp_path)
    examples, _files, warnings = release.validate_manifest(
        manifest, root, manifest_path=manifest_path
    )
    assert examples
    assert any("미선언 조합은 UI에서 비활성화합니다" in item for item in warnings)


def test_public_combination_file_validates_owner_editable_schema():
    combinations = _LOAD_PUBLIC_COMBINATIONS()
    assert combinations
    assert all(set(item) == {"cutType", "shot", "clothingType", "gender"}
               for item in combinations)


def test_cli_synthetic_fixture_end_to_end_staging_and_upload_dry_run(tmp_path, capsys):
    manifest_path, root, _manifest = _fixture(tmp_path)
    output = tmp_path / "cli-stage"
    return_code = release.main([
        str(manifest_path),
        str(root),
        "--out", str(output),
        "--public-base-url", "https://images.example.test",
        "--upload",
    ])
    output_text = capsys.readouterr().out
    assert return_code == 0
    assert (output / "example_assets.json").is_file()
    assert (output / "genExamples.json").is_file()
    assert "UPLOAD DRY-RUN: 3 objects" in output_text
    assert "/all/" in output_text
    assert "/pose/" in output_text
    assert "/thumb/" in output_text


# ── 적용(apply) 가드 ─────────────────────────────────────────────────────────
# 2026-08-02 사고: 판정 문서만 보고 명단을 갈아끼웠는데 그 사이 R2 실물이 지워져
# 화면에 깨진 썸네일 12칸이 남았다. 아래 테스트들은 "적용 전에 멈추는가"를 고정한다.


def _valid_registry(base="https://images.example.test", release_id="rel-01"):
    return {
        "_meta": {
            "schemaVersion": 2,
            "releaseId": release_id,
            "releasedAt": "2026-08-02T00:00:00Z",
            "defaultBaseUrl": base,
        },
        "assets": {
            "ex_a": {"all": f"{base}/all/ex_a.png", "thumb": f"{base}/thumb/ex_a.webp"},
        },
    }


def _catalog_item(example_id="ex_a", base="https://images.example.test", **over):
    item = {
        "id": example_id,
        "thumb": f"{base}/thumb/{example_id}.webp",
        "cutType": "styling",
        "gender": "women",
        "clothingType": "top",
        "applicableClothingTypes": ["top"],
        "shot": "full",
        "direction": "front",
        "mood": None,
        "detailSubject": None,
        "presentationMethod": None,
        "rank": 1,
        "variants": ["all"],
    }
    item.update(over)
    return item


def _valid_catalog(base="https://images.example.test"):
    return [_catalog_item(base=base)]


def _receipt(release_id="rel-01", keys=()):
    return release.UploadReceipt(
        release_id=release_id, uploaded_keys=frozenset(keys)
    )


def test_apply_requires_upload_execute_in_the_same_run(capsys):
    """사진을 올리지 않고 명단만 바꾸는 순서를 CLI 단에서 막는다."""
    assert release.main(["m.json", "assets", "--apply"]) == 2
    assert "--upload --execute" in capsys.readouterr().err
    # dry-run 업로드만으로도 안 된다 — 실제로 올라간 뒤여야 한다.
    assert release.main(["m.json", "assets", "--apply", "--upload"]) == 2
    assert "--upload --execute" in capsys.readouterr().err


def test_validate_rejects_catalog_registry_id_mismatch():
    catalog = _valid_catalog() + [_catalog_item("ex_ghost")]
    with pytest.raises(RuntimeError, match="프론트·서버 목록이 다릅니다"):
        release._validate_release_documents(catalog, _valid_registry(), label="테스트")


def test_validate_rejects_thumb_url_mismatch_between_files():
    registry = _valid_registry()
    registry["assets"]["ex_a"]["thumb"] = "https://images.example.test/thumb/ex_other.webp"
    with pytest.raises(RuntimeError, match="url_mismatch"):
        release._validate_release_documents(_valid_catalog(), registry, label="테스트")


def test_validate_rejects_thumb_outside_registry_base():
    catalog = [_catalog_item(base="https://other.example.test")]
    with pytest.raises(RuntimeError, match="base_mismatch"):
        release._validate_release_documents(catalog, _valid_registry(), label="테스트")


def test_validate_rejects_malformed_registry_metadata():
    registry = _valid_registry()
    del registry["_meta"]["releasedAt"]
    with pytest.raises(RuntimeError, match="metadata_invalid"):
        release._validate_release_documents(_valid_catalog(), registry, label="테스트")


def test_validate_rejects_duplicate_catalog_ids():
    catalog = _valid_catalog() * 2
    with pytest.raises(RuntimeError, match="중복"):
        release._validate_catalog_document(catalog, label="테스트")


def test_apply_refuses_and_leaves_files_untouched_when_new_pair_disagrees(tmp_path, monkeypatch):
    """적용 도중이 아니라 적용 전에 멈춰야 한다 — 반쯤 바뀐 상태가 가장 위험하다."""
    catalog_path = tmp_path / "genExamples.json"
    registry_path = tmp_path / "example_assets.json"
    bad_catalog = _valid_catalog() + [_catalog_item("ex_missing")]
    catalog_path.write_text(json.dumps(bad_catalog), encoding="utf-8")
    registry_path.write_text(json.dumps(_valid_registry()), encoding="utf-8")

    live_catalog = tmp_path / "live_genExamples.json"
    live_registry = tmp_path / "live_example_assets.json"
    live_catalog.write_text(json.dumps(_valid_catalog()), encoding="utf-8")
    live_registry.write_text(json.dumps(_valid_registry()), encoding="utf-8")
    monkeypatch.setattr(release, "DEFAULT_CATALOG_PATH", live_catalog)
    monkeypatch.setattr(release, "DEFAULT_REGISTRY_PATH", live_registry)

    before = (live_catalog.read_bytes(), live_registry.read_bytes())
    result = release.ReleaseResult(
        release_id="rel-01", output_dir=tmp_path,
        registry_path=registry_path, catalog_path=catalog_path,
        assets=(), warnings=(),
    )
    with pytest.raises(RuntimeError):
        release.apply_release(result, _receipt())
    assert (live_catalog.read_bytes(), live_registry.read_bytes()) == before


def test_apply_rolls_back_catalog_when_registry_copy_fails(tmp_path, monkeypatch):
    catalog_path = tmp_path / "genExamples.json"
    registry_path = tmp_path / "example_assets.json"
    catalog_path.write_text(json.dumps(_valid_catalog()), encoding="utf-8")
    registry_path.write_text(json.dumps(_valid_registry()), encoding="utf-8")

    live_catalog = tmp_path / "live_genExamples.json"
    live_registry = tmp_path / "live_example_assets.json"
    old_catalog = _valid_catalog()
    live_catalog.write_text(json.dumps(old_catalog), encoding="utf-8")
    live_registry.write_text(json.dumps(_valid_registry()), encoding="utf-8")
    monkeypatch.setattr(release, "DEFAULT_CATALOG_PATH", live_catalog)
    monkeypatch.setattr(release, "DEFAULT_REGISTRY_PATH", live_registry)

    before = live_catalog.read_bytes()
    real_copy = release._atomic_copy

    def flaky(source, destination):
        if destination == live_registry:
            raise OSError("디스크 오류")
        return real_copy(source, destination)

    monkeypatch.setattr(release, "_atomic_copy", flaky)
    result = release.ReleaseResult(
        release_id="rel-01", output_dir=tmp_path,
        registry_path=registry_path, catalog_path=catalog_path,
        assets=(), warnings=(),
    )
    with pytest.raises(OSError):
        release.apply_release(result, _receipt())
    assert live_catalog.read_bytes() == before   # 프론트가 되돌아왔다


# ── codex 리뷰 반영 (2026-08-04) ────────────────────────────────────────────
# 지적 1: --apply 게이트가 CLI 인자 검사뿐이라 apply_release() 직접 호출로 우회 가능
# 지적 2: 적용 전 검증이 id·thumb 중심의 얕은 검사


def test_apply_release_requires_an_upload_receipt_even_when_called_directly(tmp_path):
    """CLI 를 거치지 않는 호출 경로에서도 업로드 증거 없이는 적용되지 않는다."""
    result = release.ReleaseResult(
        release_id="rel-01", output_dir=tmp_path,
        registry_path=tmp_path / "r.json", catalog_path=tmp_path / "c.json",
        assets=(), warnings=(),
    )
    with pytest.raises(TypeError):
        release.apply_release(result)          # 영수증 인자 자체가 필수


def test_apply_rejects_receipt_from_a_different_release(tmp_path, monkeypatch):
    catalog_path = tmp_path / "c.json"
    registry_path = tmp_path / "r.json"
    catalog_path.write_text(json.dumps(_valid_catalog()), encoding="utf-8")
    registry_path.write_text(json.dumps(_valid_registry()), encoding="utf-8")
    monkeypatch.setattr(release, "DEFAULT_CATALOG_PATH", tmp_path / "live_c.json")
    monkeypatch.setattr(release, "DEFAULT_REGISTRY_PATH", tmp_path / "live_r.json")
    result = release.ReleaseResult(
        release_id="rel-01", output_dir=tmp_path,
        registry_path=registry_path, catalog_path=catalog_path,
        assets=(), warnings=(),
    )
    with pytest.raises(RuntimeError, match="다른 릴리스의 것"):
        release.apply_release(result, _receipt(release_id="rel-02"))


def test_apply_rejects_when_catalog_references_an_unuploaded_key(tmp_path, monkeypatch):
    """2026-08-02 사고의 정확한 형태 — 명단이 가리키는 실물이 안 올라간 경우."""
    base = "https://images.example.test"
    key = f"{release.R2_PREFIX}/rel-01/all/ex_a.png"
    registry = _valid_registry()
    registry["assets"]["ex_a"]["all"] = f"{base}/{key}"
    catalog_path = tmp_path / "c.json"
    registry_path = tmp_path / "r.json"
    catalog_path.write_text(json.dumps(_valid_catalog()), encoding="utf-8")
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(release, "DEFAULT_CATALOG_PATH", tmp_path / "live_c.json")
    monkeypatch.setattr(release, "DEFAULT_REGISTRY_PATH", tmp_path / "live_r.json")
    result = release.ReleaseResult(
        release_id="rel-01", output_dir=tmp_path,
        registry_path=registry_path, catalog_path=catalog_path,
        assets=(), warnings=(),
    )
    with pytest.raises(RuntimeError, match="올리지 않은 키를 참조"):
        release.apply_release(result, _receipt())     # 영수증에 그 키가 없다


def test_upload_release_returns_no_receipt_on_dry_run(tmp_path):
    result = release.ReleaseResult(
        release_id="rel-01", output_dir=tmp_path,
        registry_path=tmp_path / "r.json", catalog_path=tmp_path / "c.json",
        assets=(), warnings=(),
    )
    assert release.upload_release(result, execute=False) is None


@pytest.mark.parametrize("drop", ["cutType", "shot", "clothingType", "rank", "variants"])
def test_validate_rejects_catalog_item_missing_a_classification_field(drop):
    """id·thumb 만 보던 얕은 검사로는 통과하던 문서를 잡는다."""
    item = _catalog_item()
    del item[drop]
    with pytest.raises(RuntimeError, match="필드가 계약과 다릅니다"):
        release._validate_catalog_document([item], label="테스트")


def test_validate_rejects_registry_asset_without_all_or_thumb():
    registry = _valid_registry()
    del registry["assets"]["ex_a"]["all"]
    with pytest.raises(RuntimeError, match="variant_missing"):
        release._validate_registry_document(registry, label="테스트")
