"""Check actual worker request routing without paid generation or storage."""
import asyncio
from types import SimpleNamespace

import pytest

from app.config import load_settings
from app.agents.gemini_image import InlineImage
from app.agents.model_routing import resolve_model
from app.agents.product_reference import ProductReference
from app.workers import mannequin_job as job
from conftest import make_settings


def test_default_mannequin_model_does_not_change_shared_image_models():
    settings = make_settings()
    assert resolve_model(settings, settings.mannequin_tier) == 'gpt-image-2.5-flare'
    assert resolve_model(settings, 'image_high') == 'gemini-3-pro-image'
    assert resolve_model(settings, 'image_light') == 'gemini-3.1-flash-image'


def test_default_stripes_and_checks_do_not_raise_output_above_2k():
    settings = make_settings()
    for name in ('스트라이프 셔츠', '체크 셔츠', '무지 바지'):
        assert job.effective_image_size(settings, {'name': name}, {}) == '2K'


def test_env_routing_and_legacy_rollback(monkeypatch):
    for key in ('MANNEQUIN_TIER', 'MANNEQUIN_ADJUST_TIER', 'MODEL_ROUTING_IMAGE_MANNEQUIN',
                'MANNEQUIN_PATTERN_IMAGE_SIZE'):
        monkeypatch.delenv(key, raising=False)
    settings = load_settings()
    assert resolve_model(settings, settings.mannequin_tier) == 'gpt-image-2.5-flare'
    assert job.effective_image_size(settings, {'name': '스트라이프 셔츠'}, {}) == '2K'
    monkeypatch.setenv('MODEL_ROUTING_IMAGE_MANNEQUIN', 'gemini-3-pro-image')
    assert resolve_model(load_settings(), 'image_mannequin') == 'gemini-3-pro-image'
    monkeypatch.setenv('MANNEQUIN_TIER', 'image_high')
    assert resolve_model(load_settings(), load_settings().mannequin_tier) == 'gemini-3-pro-image'


@pytest.mark.parametrize('edit,overrides,expected_model', [
    (False, {}, 'gpt-image-2.5-flare'),
    (True, {}, 'gpt-image-2.5-flare'),
    (True, {'mannequin_tier': 'image_light'}, 'gemini-3-pro-image'),
    (True, {'mannequin_tier': 'image_high'}, 'gemini-3-pro-image'),
    (True, {'mannequin_tier': 'image_light', 'mannequin_adjust_tier': 'image_mannequin'}, 'gpt-image-2.5-flare'),
    (True, {'mannequin_adjust_tier': 'image_high'}, 'gemini-3-pro-image'),
    (False, {'mannequin_adjust_tier': 'image_high'}, 'gpt-image-2.5-flare'),
])
def test_worker_routes_first_and_adjust_models_with_original_input_order(monkeypatch, edit, overrides, expected_model):
    class Captured(BaseException):
        pass

    requests = []

    class Provider:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            requests.append((model, [i.data for i in images], size, kwargs))
            raise Captured

    async def emit(*args, **kwargs):
        pass

    monkeypatch.setattr(job, '_emit', emit)
    settings = make_settings(**overrides)
    source = [InlineImage('image/png', b'front'), InlineImage('image/png', b'back')]
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, pool=None, r2=None, gemini=Provider()))
    with pytest.raises(Captured):
        asyncio.run(job._run_candidate(
            app=app, job={'id': 'j', 'user_id': 'u', 'project_id': 'p',
                          'payload': {'mode': 'regenerate' if edit else 'generate'}},
            candidate='A', base_fit='regular', base_gender='women',
            base_img=InlineImage('image/png', b'base'), prod_imgs=source,
            match_img=InlineImage('image/png', b'matching'), product_count=3,
            template='${imageManifest} ${clothingType}',
            product={'name': '스트라이프 바지'}, analysis={}, clothing_type='bottom',
            image_manifest=job._build_manifest([{'slot': 'Front'}, {'slot': 'Back'}], True, 'bottom'),
            product_refs=[ProductReference('Front', 'f', source[0]), ProductReference('Back', 'b', source[1])],
            generation_path='edit' if edit else 'fresh',
            parent_cut_img=InlineImage('image/png', b'parent') if edit else None,
            adjust_directives='MAIN PRODUCT length: reach the top of the foot' if edit else '',
        ))
    assert len(requests) == 1
    model, images, size, options = requests[0]
    assert model == expected_model
    assert images == [b'parent' if edit else b'base', b'front', b'back', b'matching']
    assert size == '2K'
    assert options['aspect_ratio'] == '2:3'
