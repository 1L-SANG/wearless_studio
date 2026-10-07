import pytest
from types import SimpleNamespace

from app.agents import garment_desquiggle as gd
from app.workers import detail_page_job as dpj
from test_cut_output_qc_wiring import _run_detail_cut, _run_editor, _qc_result


PLAIN = {"sellingPoints": ["무지", "매끈한 표면"]}
SPEC = {"id": "b1", "cutType": "styling", "shot": "full", "direction": "front",
        "faceExposure": "same", "pose": "auto", "refScope": "all"}


@pytest.mark.parametrize("route", ["first", "regenerate", "edit"])
@pytest.mark.parametrize("point", ["무지", "스트라이프", "코듀로이"])
def test_confirmed_generation_and_qc_retries_forward_separate_protection(monkeypatch, route, point):
    edits = []
    prompt_input = object()

    async def qc(settings, plan, references, image, **kwargs):
        if image.data == b"INITIAL" and route != "first":
            return _qc_result("framingCrop" if route == "edit" else "garmentConstruction")
        return _qc_result()

    async def repair(settings, gemini, spec, product, source, **kwargs):
        edits.append(kwargs)
        return b"EDITED", "image/png"

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", qc)
    monkeypatch.setattr(dpj.cut_generator, "repair", repair)
    run = _run_detail_cut(
        monkeypatch, qc_mode="repair", spec=SPEC,
        confirmed_packet=SimpleNamespace(prompt_input=prompt_input),
        generated_outputs=[b"INITIAL", b"REGENERATED"],
        settings_overrides={"garment_desquiggle_mode": "on"},
        analysis={"sellingPoints": [point, "매끈한 표면"]},
    )
    calls = [*run["generateKwargs"], *edits]
    assert len(calls) == (1 if route == "first" else 2)
    protection = calls[0]["garment_protection"]
    # 매칭 사진이 없어도 프롬프트가 기본 옷을 채운다. 그 질감은 확인되지 않았다.
    assert len(protection.matching) == 1
    assert protection.matching[0].has_texture is None
    assert protection.product.has_pattern is {"무지": False, "스트라이프": True, "코듀로이": None}[point]
    assert protection.product.has_texture is (point == "코듀로이")
    for kwargs in calls:
        assert kwargs["garment_protection"] == protection
        assert kwargs["confirmed_prompt_input"] is prompt_input
        assert "analysis" not in kwargs and "manifest" not in kwargs
    assert run["r2"].saved == [{"first": b"INITIAL", "regenerate": b"REGENERATED", "edit": b"EDITED"}[route]]


def test_detail_worker_passes_all_selected_matching_garments(monkeypatch):
    run = _run_detail_cut(
        monkeypatch, qc_mode="off", spec={**SPEC, "matchIds": ["plain", "striped"]},
        analysis=PLAIN, matching_metadata={"plain": PLAIN, "striped": {"name": "스트라이프 팬츠"}},
        settings_overrides={"garment_desquiggle_mode": "on"},
    )
    protection = run["generateKwargs"][0]["garment_protection"]
    assert len(protection.matching) == 2
    assert protection.matching[1].has_pattern is True
    assert gd.should_apply(SimpleNamespace(garment_desquiggle_mode="on"), "gpt-image-2", SPEC,
                           protection) == (False, "matching_pattern")


@pytest.mark.parametrize("matching", [{"name": "체크 팬츠"}, None])
def test_editor_new_image_receives_product_analysis_and_matching_protection(monkeypatch, matching):
    run, _r2 = _run_editor(
        monkeypatch, qc_mode="off", cut_type="styling", analysis=PLAIN,
        matching_ids=["m1"], matching_metadata={"m1": matching},
        settings_overrides={"garment_desquiggle_mode": "on"},
    )
    kwargs = run["generateKwargs"][0]
    assert kwargs["analysis"] == PLAIN
    protection = kwargs["garment_protection"]
    assert protection.product.has_pattern is False and protection.product.has_texture is False
    assert len(protection.matching) == 1
    assert protection.matching[0].has_pattern is (True if matching else None)
    assert gd.should_apply(SimpleNamespace(garment_desquiggle_mode="on"), "gpt-image-2", SPEC,
                           protection)[0] is False
