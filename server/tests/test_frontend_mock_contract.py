import ast
import json
import re
from pathlib import Path

from tools import release_genexamples as release


MOCK_DB = Path(__file__).resolve().parents[2] / "src/mock/db.js"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _analysis_seed_block() -> str:
    text = MOCK_DB.read_text(encoding="utf-8")
    start = text.index("  const analysis = {")
    end = text.index("  const fitProfile", start)
    return text[start:end]


def _array_field(name: str) -> list:
    match = re.search(rf"{name}:\s*(\[[^\]]*\])", _analysis_seed_block(), re.S)
    assert match, f"{name} field missing from frontend mock analysis seed"
    return ast.literal_eval(match.group(1))


def test_mock_analysis_starts_with_ai_keyword_suggestions_only():
    assert _array_field("sellingPoints") == []

    points = _array_field("aiSuggestedPoints")
    assert 0 < len(points) <= 2
    for point in points:
        compact = re.sub(r"[\s/]+", "", point)
        assert 0 < len(compact) <= 12
        assert not re.search(r"[.!?。]|(합니다|습니다|해요|있어요|가능)$", point)


def _seed_script_models() -> dict:
    """seed_virtual_models.py 의 MODELS 딕셔너리를 소스에서 그대로 읽는다.

    앵커 파일명 같은 운영 사실을 테스트에 손으로 다시 적으면(2026-08-17 리뷰 지적),
    스크립트만 고치고 테스트를 안 고쳐도 초록이라 시드가 R2 업로드 도중
    FileNotFoundError 로 죽는다(일부만 올라간 채 manifest 미기록). 출처를 하나로 묶는다.
    """
    source = (REPO_ROOT / "server/scripts/seed_virtual_models.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "MODELS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("seed_virtual_models.py 에서 MODELS 를 찾지 못했다")


def test_virtual_model_catalogs_and_public_assets_stay_in_sync():
    manifest = json.loads(
        (REPO_ROOT / "server/app/data/virtual_models.json").read_text(encoding="utf-8")
    )
    # 프론트 카탈로그 단일 출처(2026-08-17) — AnalysisForm 과 modelSelection 이 함께 읽는다.
    catalog_source = (
        REPO_ROOT / "src/features/analysis/aiModels.js"
    ).read_text(encoding="utf-8")
    mock_source = MOCK_DB.read_text(encoding="utf-8")
    expected = {
        "mA": ("Mia", "women", "w1"),
        "mB": ("Leo", "men", "m1"),
        "mC": ("도윤", "men", "m2"),
        "mD": ("수혁", "men", "m3"),
        "mE": ("지안", "women", "w2"),
        # 2026-08-17 여성 2차 배치 — 앵커는 전원 {sid}-face.webp 분리형
        "mF": ("하린", "women", "w3"),
        "mG": ("세아", "women", "w4"),
        "mH": ("예린", "women", "w5"),
        "mI": ("다인", "women", "w6"),
        "mJ": ("소윤", "women", "w7"),
        "mK": ("유나", "women", "w8"),
        "mL": ("채원", "women", "w9"),
        "mM": ("나윤", "women", "w10"),
        "mN": ("Nora", "women", "w11"),
    }
    seed_models = _seed_script_models()
    assert set(seed_models) == set(expected)
    assert set(manifest["models"]) == set(expected)

    for model_id, (name, gender, sid) in expected.items():
        model = manifest["models"][model_id]
        thumb = f"/models/{gender}/{sid}.webp"
        assert (model["name"], model["gender"], model["thumb"]) == (name, gender, thumb)
        assert set(model["views"]) == {
            "face_front", "grid_sedcard", "three_quarter", "profile",
            "body_front", "body_back",
        }
        # 행 단위로 id·표시명·성별·썸네일이 **한 줄 안에서** 짝지어져 있는지 본다.
        # 셋을 따로 substring 검사하면 이름이 서로 바뀌거나 썸네일이 남의 것이어도
        # 통과한다(2026-08-17 리뷰 지적) — 선택 화면과 생성 아이덴티티가 어긋나는 사고다.
        assert re.search(
            rf"\{{\s*id:\s*'{model_id}',\s*displayName:\s*'{re.escape(name)}',"
            rf"\s*gender:\s*'{gender}',\s*thumb:\s*'{re.escape(thumb)}'\s*\}}",
            catalog_source,
        ), f"{model_id} row mismatched in aiModels.js"
        assert re.search(
            rf"\{{\s*id:\s*'{model_id}',\s*name:\s*'{re.escape(name)}',"
            rf"\s*gender:\s*'{gender}',\s*thumb:\s*'{re.escape(thumb)}',",
            mock_source,
        ), f"{model_id} row mismatched in mock/db.js"

        # 시드 스크립트가 실제로 읽는 파일이 저장소에 있어야 한다. 앵커 파일명은
        # 스크립트에서 그대로 가져온다(기본값 = 썸네일과 동일 파일).
        assert (seed_models[model_id]["sid"], seed_models[model_id]["gender"]) == (sid, gender)
        assert (REPO_ROOT / f"public/models/{gender}/{sid}.webp").is_file()
        anchor = seed_models[model_id].get("anchor", f"{sid}.webp")
        assert (REPO_ROOT / f"public/models/{gender}/{anchor}").is_file(), (
            f"{model_id} 앵커 {anchor} 없음 — 시드가 업로드 도중 죽는다"
        )


def test_dev_generation_example_catalog_matches_server_registry_v2():
    catalog = json.loads(
        (REPO_ROOT / "src/data/genExamples.json").read_text(encoding="utf-8")
    )
    registry = json.loads(
        (REPO_ROOT / "server/app/data/example_assets.json").read_text(encoding="utf-8")
    )
    assets = registry["assets"]

    assert registry["_meta"]["schemaVersion"] == 2
    assert {item["id"] for item in catalog} == set(assets)
    for item in catalog:
        entry = assets[item["id"]]
        assert item["thumb"].endswith(entry["thumb"])
        assert item["applicableClothingTypes"] == entry["applicableClothingTypes"]
        assert item["cutType"] == entry["cutType"]
        assert item["shot"] == entry["shot"]
        assert item["gender"] == entry["gender"]
        assert item["direction"] == entry["direction"]
        assert item["variants"] == [
            variant for variant in ("all", "pose", "bg") if variant in entry
        ]
        if item["cutType"] == "product":
            assert item["variants"] == ["all"]


def test_horizon_catalog_uses_the_owner_confirmed_shared_clothing_scopes():
    catalog = json.loads(
        (REPO_ROOT / "src/data/genExamples.json").read_text(encoding="utf-8")
    )
    all_by_gender = {
        "women": ["top", "bottom", "outer", "dress"],
        "men": ["top", "bottom", "outer"],
    }
    upper_by_gender = {
        "women": ["top", "outer", "dress"],
        "men": ["top", "outer"],
    }

    horizon = [item for item in catalog if item["cutType"] == "horizon"]
    assert horizon
    for item in horizon:
        if item["shot"] == "full":
            expected = all_by_gender[item["gender"]]
        elif item["clothingType"] == "bottom":
            expected = ["bottom"]
        else:
            expected = upper_by_gender[item["gender"]]
        assert item["applicableClothingTypes"] == expected, item["id"]


def test_storyboard_pose_direction_tooltip_matches_service_copy():
    source = (REPO_ROOT / "src/features/storyboard/Storyboard.jsx").read_text(encoding="utf-8")
    assert "이 예시의 포즈는 ${label} 전용이에요" in source


def test_committed_catalog_and_registry_cover_the_single_public_combination_table():
    catalog = json.loads(
        (REPO_ROOT / "src/data/genExamples.json").read_text(encoding="utf-8")
    )
    registry = json.loads(
        (REPO_ROOT / "server/app/data/example_assets.json").read_text(encoding="utf-8")
    )
    combinations = release.load_public_combinations()

    catalog_counts, catalog_missing, catalog_undeclared = (
        release.generation_example_coverage(catalog, combinations)
    )
    registry_counts, registry_missing, registry_undeclared = (
        release.generation_example_coverage(list(registry["assets"].values()), combinations)
    )

    assert release.PUBLIC_COMBINATIONS_PATH == (
        REPO_ROOT / "data/genexamples_public_combinations.json"
    )
    assert catalog_missing == []
    assert registry_missing == []
    assert catalog_counts == registry_counts
    assert catalog_undeclared == registry_undeclared

    frontend_source = (REPO_ROOT / "src/lib/generationExamples.js").read_text(
        encoding="utf-8"
    )
    assert "../../data/genexamples_public_combinations.json" in frontend_source
