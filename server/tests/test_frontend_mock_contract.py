import ast
import json
import re
from pathlib import Path


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
        assert item["variants"] == [
            variant for variant in ("all", "pose", "bg") if variant in entry
        ]
        if item["cutType"] == "product":
            assert item["variants"] == ["all"]
