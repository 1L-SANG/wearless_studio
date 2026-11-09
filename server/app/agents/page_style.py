"""Analysis-owned page style. No schema migration or provider calls."""
from datetime import datetime

from .page_templates import CATALOG


def default_page_style():
    return {"version": 1, **CATALOG["defaults"], "catalogVersion": CATALOG["catalogVersion"]}


def validate_page_style(value):
    allowed = {"version", "templateId", "fontStyle", "colorMode", "catalogVersion", "updatedAt"}
    if (not isinstance(value, dict) or set(value) - allowed or type(value.get("version")) is not int or value.get("version") != 1
        or value.get("templateId") not in ("emo", "show")
        or value.get("fontStyle") not in ("template", "serif", "boldGothic")
        or value.get("colorMode") not in ("mono", "point")
        or value.get("catalogVersion") != CATALOG["catalogVersion"]):
        raise ValueError("invalid_page_style")
    if "updatedAt" in value:
        try:
            if not isinstance(value["updatedAt"], str) or datetime.fromisoformat(value["updatedAt"].replace("Z", "+00:00")).tzinfo is None:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("invalid_page_style") from None
    return dict(value)
