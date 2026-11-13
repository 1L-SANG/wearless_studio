from copy import deepcopy

import pytest

from app.agents import page_style
from app.agents.page_templates import CATALOG, validate_catalog


def test_catalog_font_style_additions_removals_and_default_follow_catalog():
    catalog = deepcopy(CATALOG)
    catalog["fontStyles"]["future"] = {"labelEn": {"font": "Roboto Mono", "weight": 500}, "titleKo": None}
    del catalog["fontStyles"]["serif"]
    catalog["defaults"]["fontStyle"] = "future"
    assert validate_catalog(catalog) is catalog
    catalog["fontStyles"]["future"]["labelEn"]["font"] = "Unsupported Font"
    with pytest.raises(ValueError, match="font style"):
        validate_catalog(catalog)
    del catalog["fontStyles"]["future"]
    with pytest.raises(ValueError, match="default page style"):
        validate_catalog(catalog)


def test_saved_font_style_membership_follows_catalog(monkeypatch):
    catalog = deepcopy(CATALOG)
    catalog["fontStyles"]["future"] = {"labelEn": None, "titleKo": None}
    del catalog["fontStyles"]["serif"]
    monkeypatch.setattr(page_style, "CATALOG", catalog)
    value = {**page_style.default_page_style(), "fontStyle": "future"}
    assert page_style.validate_page_style(value) == value
    for invalid in ("serif", "unknown", None, [], {}):
        with pytest.raises(ValueError, match="invalid_page_style"):
            page_style.validate_page_style({**value, "fontStyle": invalid})
