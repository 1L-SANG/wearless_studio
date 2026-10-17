import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agents import (
    copy_qc, copywriter, cut_output_qc, feature_copy, feature_extractor,
    garment_color_observer, image_qc, input_consistency,
    mannequin_base_fidelity_qc, mannequin_fit_qc, mannequin_series_qc,
    mannequin_untuck, page_output_qc, points_merge, product_analyst, vision_llm,
)
from app.agents.gemini_image import InlineImage
from app.workers import detail_page_job
from conftest import fake_worker_app, make_settings, worker_job
from test_copywriter import _blocks
from test_cut_output_qc import _plan
from test_page_output_qc import plan


class CapturedCall(BaseException):
    """Stop at the provider, before agent validation or optional-copy error handling."""


def _invoke(name, s, image):
    callers = {
        "image_qc": lambda: image_qc.verdict(s, [image], image),
        "scene_qc": lambda: image_qc.scene_verdict(s, image, image),
        "fit_qc": lambda: mannequin_fit_qc.verdict(s, [image], image, {
            "category": "top", "gender": "women", "axes": {"fit": "slim"}}),
        "base_qc": lambda: mannequin_base_fidelity_qc.verdict(s, image, image),
        "series_qc": lambda: mannequin_series_qc.judge(s, image, [image]),
        "untuck": lambda: mannequin_untuck.judge_gate(s, image),
        "cut_qc": lambda: cut_output_qc.verdict(s, _plan(), [
            cut_output_qc.LabeledReference("product", image)], image),
        "page_qc": lambda: page_output_qc.judge(
            s, plan(), [image] * 3, product_truth_refs=[image, image]),
        "copywriter": lambda: copywriter.generate(s, blocks=_blocks(), product={}, analysis={}),
        "copy_qc": lambda: copy_qc.review(s, [{"blockId": "b1", "text": "소맷단 버튼"}], {}),
        "feature_copy": lambda: feature_copy.generate(s, ["빈티지한 워싱감"], {}, {}),
        "product_analyst": lambda: product_analyst.analyze(s, {}, [image]),
        "wash_care": lambda: product_analyst.draft_wash_care(s, {}, {}),
        "feature_extractor": lambda: feature_extractor.extract(s, {}, [image]),
        "input_consistency": lambda: input_consistency._judge_pair(s, image, image, "Front"),
        "points_merge": lambda: points_merge.merge_points(s, ["코튼 소재"], ["허리 핀턱"]),
        "garment_color_observer": lambda: garment_color_observer.observe(s, [{
            "data": b"image", "mime": "image/png", "sourceIndex": 0, "slot": "Front"}]),
    }
    return callers[name]()


@pytest.mark.parametrize("background_timeout", [120, 137])
@pytest.mark.parametrize("name,expected", [
    ("image_qc", "background"), ("scene_qc", "background"),
    ("fit_qc", "background"), ("base_qc", "background"),
    ("series_qc", "background"), ("untuck", "background"),
    ("cut_qc", "background"), ("page_qc", "background"),
    ("copywriter", "background"), ("copy_qc", "background"), ("feature_copy", 19),
    ("product_analyst", 19), ("wash_care", 19), ("feature_extractor", 19),
    ("input_consistency", 19), ("points_merge", 8), ("garment_color_observer", 19),
])
def test_provider_timeout_follows_caller_policy(monkeypatch, name, expected, background_timeout):
    seen = []

    async def capture(settings, model, prompt, images, schema, timeout, **kwargs):
        seen.append(timeout)
        raise CapturedCall

    _, model_of, key_of = vision_llm._PROVIDERS["gemini"]
    monkeypatch.setitem(vision_llm._PROVIDERS, "gemini", (capture, model_of, key_of))
    monkeypatch.setattr(mannequin_series_qc, "_call_gemini", capture)
    monkeypatch.setattr(garment_color_observer, "_observer_image", lambda *args: image)
    image = InlineImage("image/png", b"image")
    settings = make_settings(gemini_api_key="test", analysis_model_order="gemini",
                             analysis_timeout_seconds=19,
                             background_ai_timeout_seconds=background_timeout)
    with pytest.raises(CapturedCall):
        asyncio.run(_invoke(name, settings, image))
    assert seen == [background_timeout if expected == "background" else expected]


@pytest.mark.parametrize("background_timeout", [120, 137])
def test_detail_page_feature_copy_passes_background_timeout(monkeypatch, background_timeout):
    seen = []

    async def capture(settings, prompt, schema, *, timeout_seconds=None):
        seen.append(timeout_seconds)
        raise CapturedCall

    for name, value in (
        ("get_project", {"copywriting": False}),
        ("get_storyboard", []),
        ("get_product", {"name": "셔츠"}),
        ("get_analysis", {"sellingPoints": ["빈티지한 워싱감"]}),
    ):
        monkeypatch.setattr(detail_page_job.repo, name, AsyncMock(return_value=value))
    monkeypatch.setattr(detail_page_job, "_emit", AsyncMock())
    monkeypatch.setattr(feature_copy, "complete_json", capture)
    app = fake_worker_app(make_settings(
        analysis_timeout_seconds=19, background_ai_timeout_seconds=background_timeout))

    with pytest.raises(CapturedCall):
        asyncio.run(detail_page_job.run_detail_page_job(app, worker_job()))

    assert seen == [background_timeout]
