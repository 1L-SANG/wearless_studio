"""실제 편집 빌더까지 연결하되 이미지 생성·운영 검사·D4 호출은 모두 가짜다."""

import asyncio
from types import SimpleNamespace

import pytest

from app.agents import cut_generator as cg, cut_repair_pair_qc as pair, cut_fit_chip_qc as chips
from app.agents.gemini_image import InlineImage
from app.workers import detail_page_job as dpj
from conftest import fake_worker_app, make_settings, worker_job
from test_cut_output_qc_wiring import _RecordingR2, _qc_result
from test_cut_fit_chip_qc import profile, report


def run_cut(monkeypatch, *, chip_mode="off", pair_mode="shadow", mannequin=False, regenerate=False, mixed=False):
    calls, comparisons, events = [], [], []
    class ImageClient:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            calls.append({"model": model, "prompt": prompt, "images": images})
            return SimpleNamespace(image=b"EDITED", mime="image/png")
    generated = []
    async def fake_generate(*args, **kwargs):
        generated.append(kwargs)
        return (b"STAGE1" if len(generated) == 1 else b"REGENERATED"), "image/png"
    async def operating(settings, plan, references, candidate, **kwargs):
        failure = "garmentConstruction" if regenerate else "framingCrop"
        value = _qc_result(failure) if candidate.data == b"STAGE1" and (chip_mode == "off" or mixed) else _qc_result()
        value["gates"]["garmentColor"]["evidence"] = "JUDGE ATTACK"
        return value
    async def fake_pair(settings, prompt, images, schema, **kwargs):
        comparisons.append((prompt, images))
        return {"target_resolved": False, "protected_regions_unchanged": False,
                "regression_reasons": ["a new slit"]}, "gemini"
    async def fake_chips(settings, p, candidate, **kwargs):
        return report("sleeve", "fingers", "wrist" if candidate.data == b"STAGE1" else "fingers", value="fingers")
    async def emit(*args, **kwargs):
        return None
    monkeypatch.setattr(cg, "generate", fake_generate)
    monkeypatch.setattr(dpj.cut_output_qc, "verdict", operating)
    monkeypatch.setattr(pair, "analyze_with_fallback", fake_pair)
    monkeypatch.setattr(chips, "verdict", fake_chips)
    monkeypatch.setattr(dpj, "_emit", emit)
    r2 = _RecordingR2(events)
    app = fake_worker_app(make_settings(gemini_api_key="fake", r2_bucket="b", cut_output_qc_mode="repair",
        cut_fit_chip_qc_mode=chip_mode, cut_repair_pair_qc_mode=pair_mode,
        model_detail_cut="gpt-image-2.5-flare", model_cut_repair="gpt-image-2.5-sunburst"), r2=r2, gemini=ImageClient())
    images = [InlineImage("image/png", b"PRODUCT")]
    labels = ["1. PRODUCT — front"]
    if mannequin:
        images.append(InlineImage("image/png", b"MANNEQUIN"))
        labels.append("2. MANNEQUIN — selected")
    images.append(InlineImage("image/png", b"EXAMPLE"))
    labels.append(f"{len(images)}. EXAMPLE REFERENCE (scope: all) — selected")
    spec = {"id": "worn", "cutType": "horizon", "shot": "full", "direction": "front", "faceExposure": "show"}
    analysis = {"fitProfile": profile(axes={"sleeve": "fingers"}, sources={"sleeve": "seller"})} if chip_mode != "off" else {}
    result = asyncio.run(dpj._gen_cuts(app, worker_job(), [(spec, images, "\n".join(labels), False, images[:1])],
                                     {"clothingType": "top", "name": "SELLER ATTACK"}, analysis))
    return result, calls, comparisons, r2, generated


@pytest.mark.parametrize("chip_mode", ["off", "repair"])
def test_d4_regression_is_recorded_but_does_not_veto_adoption(monkeypatch, chip_mode):
    result, calls, comparisons, r2, generated = run_cut(monkeypatch, chip_mode=chip_mode)
    assert r2.saved == [b"EDITED"]
    assert len(calls) == len(comparisons) == 1
    assert calls[0]["model"] == "gpt-image-2.5-sunburst"
    assert [im.data for im in calls[0]["images"]] == ([b"STAGE1"] if chip_mode == "repair" else [b"STAGE1", b"EXAMPLE"])
    assert [im.data for im in comparisons[0][1]] == [b"STAGE1", b"EDITED"]
    assert "ATTACK" not in calls[0]["prompt"] + comparisons[0][0]
    repair = result[3][0]["repair"]
    assert repair["accepted"] is True
    assert repair["editPairQc"]["protected_regions_unchanged"] is False
    if chip_mode == "repair":
        command = "Make the sleeves of the sold top longer so that each sleeve passes the wrist"
        assert calls[0]["prompt"].startswith(command)
        assert command in comparisons[0][0]
        assert "KEEP FROM STAGE-1" not in calls[0]["prompt"]
    else:
        assert "garmentColor" in calls[0]["prompt"].split("KEEP FROM STAGE-1", 1)[1]


def test_mixed_repair_keeps_example_and_operating_context(monkeypatch):
    result, calls, comparisons, r2, _ = run_cut(monkeypatch, chip_mode="repair", mixed=True)
    assert result[3][0]["repair"]["accepted"] is True and r2.saved == [b"EDITED"]
    assert [im.data for im in calls[0]["images"]] == [b"STAGE1", b"EXAMPLE"]
    assert calls[0]["prompt"].startswith("Make the sleeves of the sold top longer")
    assert "FRAMING TARGET FROM CUT PLAN" in calls[0]["prompt"]
    assert "KEEP FROM STAGE-1" in calls[0]["prompt"]
    assert len(comparisons) == 1


@pytest.mark.parametrize("case", ["off", "mannequin", "regenerate"])
def test_d4_never_calls_for_off_mannequin_or_scratch_regeneration(monkeypatch, case):
    result, calls, comparisons, r2, generated = run_cut(monkeypatch,
        pair_mode="off" if case == "off" else "shadow", mannequin=case == "mannequin", regenerate=case == "regenerate")
    assert comparisons == []
    assert "editPairQc" not in result[3][0]["repair"]
    if case == "mannequin":
        assert calls[0]["model"] == "gpt-image-2.5-flare"
        assert [im.data for im in calls[0]["images"]] == [b"STAGE1"]
    if case == "regenerate":
        assert calls == [] and len(generated) == 2
