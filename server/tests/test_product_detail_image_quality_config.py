from pathlib import Path

import pytest
import yaml

from app.config import Settings, load_settings


def test_product_detail_image_quality_defaults_to_medium(monkeypatch):
    monkeypatch.delenv("PRODUCT_DETAIL_IMAGE_QUALITY", raising=False)

    assert Settings.__dataclass_fields__["product_detail_image_quality"].default == "medium"
    assert load_settings().product_detail_image_quality == "medium"


@pytest.mark.parametrize("value, expected", [
    ("medium", "medium"),
    ("HIGH ", "high"),
    (" high ", "high"),
    ("", "medium"),
    ("   ", "medium"),
    ("low", "medium"),
    ("unexpected", "medium"),
])
def test_product_detail_image_quality_normalizes_and_rejects_invalid_values(monkeypatch, value, expected):
    monkeypatch.setenv("PRODUCT_DETAIL_IMAGE_QUALITY", value)

    assert load_settings().product_detail_image_quality == expected


@pytest.mark.parametrize("service", ["api", "detail-worker"])
def test_production_product_detail_image_quality_is_high_in_both_services(service):
    manifest_path = Path(__file__).resolve().parents[2] / f"copilot/{service}/manifest.yml"
    variables = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["variables"]

    assert variables["PRODUCT_DETAIL_IMAGE_QUALITY"] == "high"
