"""에디터 컷 전용 모델 노브 — image_high 를 건드리지 않고 에디터만 바꾼다.

image_high 를 직접 바꾸면 마네킹(mannequin_tier)·매칭 플랫레이(matching_flatlay_tier)·
AG-07(cut_variator)까지 전부 딸려 간다. 그 회귀를 여기서 고정한다.
"""
from dataclasses import replace

from app.agents.model_routing import (
    resolve_detail_cut_model,
    resolve_editor_cut_model,
    resolve_model,
)
from app.config import Settings, load_settings


def _settings(**kw) -> Settings:
    """실제 Settings 인스턴스(필수 필드가 많아 load_settings 를 기반으로 만든다)."""
    base = replace(load_settings(), model_image_high="gemini-3-pro-image",
                   model_image_light="gemini-3.1-flash-image", model_editor_cut="",
                   model_detail_cut="", mannequin_tier="image_high",
                   matching_flatlay_tier="image_high")
    return replace(base, **kw) if kw else base


def test_empty_knob_falls_back_to_image_high():
    s = _settings()
    assert s.model_editor_cut == ""
    assert resolve_editor_cut_model(s) == "gemini-3-pro-image"
    assert resolve_editor_cut_model(s) == resolve_model(s, "image_high")


def test_knob_changes_editor_only():
    s = _settings(model_editor_cut="gpt-image-2.5flare")
    assert resolve_editor_cut_model(s) == "gpt-image-2.5flare"
    # 공용 tier 와 다른 워커는 그대로
    assert resolve_model(s, "image_high") == "gemini-3-pro-image"
    assert resolve_model(s, "image_light") == "gemini-3.1-flash-image"
    assert resolve_detail_cut_model(s) == "gemini-3-pro-image"


def test_editor_settings_copy_only_swaps_image_high():
    """워커가 만드는 Settings 복사본은 image_high 하나만 바뀐다."""
    s = _settings(model_editor_cut="gpt-image-2.5flare")
    editor = replace(s, model_image_high=resolve_editor_cut_model(s))
    assert editor.model_image_high == "gpt-image-2.5flare"
    changed = {f for f in s.__dataclass_fields__ if getattr(s, f) != getattr(editor, f)}
    assert changed == {"model_image_high"}
    # 원본 Settings 는 불변 — 마네킹·매칭이 읽는 값이 그대로다
    assert s.model_image_high == "gemini-3-pro-image"


def test_mannequin_and_matching_tiers_still_resolve_to_image_high():
    s = _settings(model_editor_cut="gpt-image-2.5flare")
    assert resolve_model(s, s.mannequin_tier) == "gemini-3-pro-image"
    assert resolve_model(s, s.matching_flatlay_tier) == "gemini-3-pro-image"


def test_editor_worker_uses_editor_settings_for_generation_calls():
    """editor_image_job 의 이미지 생성 호출은 전부 editor_settings 를 쓴다(소스 고정)."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "app" / "workers" / "editor_image_job.py"
    text = src.read_text()
    assert "editor_settings = replace(s, model_image_high=resolve_editor_cut_model(s))" in text
    import re
    bare = re.findall(r"(?<!editor_settings)(?<![\w])s, app\.state\.gemini", text)
    assert not bare, f"생성 호출이 s 를 그대로 쓰고 있다: {bare}"
    assert text.count("editor_settings, app.state.gemini") == 5
