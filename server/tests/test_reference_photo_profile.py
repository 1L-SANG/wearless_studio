"""Provider-boundary tests for the ordinary selected-input styling profile."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from conftest import make_settings
from test_confirmed_gpt_prompt import _dress_request
from app.agents import cut_generator as cg
from app.agents.confirmed_gpt_prompt import compile_reference_photo_prompt


SPEC = {'cutType': 'styling', 'shot': 'full', 'direction': 'front',
        'exampleId': 'photo-fixture', 'refScope': 'all', 'modelId': 'mE'}
PRODUCT = {'clothingType': 'dress', 'name': 'Fixture dress', 'colors': []}


def settings(profile='reference-v2'):
    values = vars(make_settings(model_image_high='gpt-image-2.5-flare',
        detail_cut_image_size='2K', mannequin_aspect_ratio='2:3')).copy()
    values['styling_photo_profile'] = profile
    return SimpleNamespace(**values)


class CaptureProvider:
    def __init__(self):
        self.requests = []

    async def generate_content_image(self, model, prompt, images, image_size, **kwargs):
        self.requests.append({'model': model, 'prompt': prompt, 'images': images,
                              'size': image_size, 'kwargs': kwargs})
        return SimpleNamespace(image=b'candidate', mime='image/png')


def test_reference_photo_route_changes_rendering_not_selected_image_bytes_or_size():
    # Catch a wrong model, reordered/re-encoded packet, or unrequested resolution change.
    provider = CaptureProvider()
    images = [cg.InlineImage('image/jpeg', b'mannequin-original'),
              cg.InlineImage('image/png', b'face-original'),
              cg.InlineImage('image/jpeg', b'body-original'),
              cg.InlineImage('image/png', b'seller-grid-original'),
              cg.InlineImage('image/jpeg', b'example-original')]
    result = asyncio.run(cg.generate(settings(), provider, SPEC, PRODUCT, images,
                                    confirmed_prompt_input=_dress_request()))
    assert result == (b'candidate', 'image/png')
    assert len(provider.requests) == 1
    wire = provider.requests[0]
    assert wire['model'] == 'gpt-image-2.5-sunburst'
    assert [(x.mime, x.data) for x in wire['images']] == [
        ('image/jpeg', b'mannequin-original'), ('image/png', b'face-original'),
        ('image/jpeg', b'body-original'), ('image/png', b'seller-grid-original'),
        ('image/jpeg', b'example-original')]
    assert wire['size'] == '2K'
    assert wire['kwargs'] == {'aspect_ratio': '2:3', 'openai_preserve_input_bytes': True}
    assert 'REFERENCE-OWNED CAPTURE' in wire['prompt']
    assert 'RECENT IPHONE DEFAULT PHOTO CONTRACT' not in wire['prompt']
    assert 'parallel vertical pintuck panels' in wire['prompt']
    assert 'footwear' in wire['prompt'].lower()


def test_legacy_profile_is_a_real_rollback_of_both_model_and_prompt():
    provider = CaptureProvider()
    asyncio.run(cg.generate(settings('legacy'), provider, SPEC, PRODUCT, [],
                            confirmed_prompt_input=_dress_request()))
    assert provider.requests[0]['model'] == 'gpt-image-2.5-flare'
    assert 'RECENT IPHONE DEFAULT PHOTO CONTRACT' in provider.requests[0]['prompt']


@pytest.mark.parametrize('spec', [
    {'cutType': 'horizon', 'shot': 'full'},
    {'cutType': 'product', 'shot': 'ghost'},
    {**SPEC, 'spaceGroupId': 'ssg1__fixture__one'},
    {'cutType': 'styling', 'shot': 'full'},
])
def test_other_input_paths_keep_their_caller_selected_model(spec):
    provider = CaptureProvider()
    asyncio.run(cg.generate(settings(), provider, spec, PRODUCT, []))
    assert provider.requests[0]['model'] == 'gpt-image-2.5-flare'
    assert 'REFERENCE-OWNED CAPTURE' not in provider.requests[0]['prompt']


def test_local_repair_keeps_the_new_model_without_adding_reference_images():
    provider = CaptureProvider()
    image = cg.InlineImage('image/png', b'first-result')
    asyncio.run(cg.repair(settings(), provider, SPEC, PRODUCT, image,
        confirmed_prompt_input=_dress_request(),
        qc_corrections=('Correct the detached contact shadow only.',)))
    assert len(provider.requests) == 1
    assert provider.requests[0]['model'] == 'gpt-image-2.5-sunburst'
    assert provider.requests[0]['images'] == [image]
    assert 'Correct the detached contact shadow only.' in provider.requests[0]['prompt']


def test_new_profile_cannot_reuse_a_legacy_generation_checkpoint():
    images = [cg.InlineImage('image/png', b'fixed-input')]
    old = cg.base_fingerprint(settings('legacy'), SPEC, PRODUCT, images,
                              confirmed_prompt_input=_dress_request())
    new = cg.base_fingerprint(settings(), SPEC, PRODUCT, images,
                              confirmed_prompt_input=_dress_request())
    assert new != old


def test_new_profile_still_rejects_an_invalid_identity_packet_scope_before_call():
    provider = CaptureProvider()
    request = _dress_request()
    request = replace(request, scope=replace(request.scope, space_group_id='wrong'))
    with pytest.raises(ValueError, match='confirmed_gpt_excludes_space_groups'):
        asyncio.run(cg.generate(settings(), provider, SPEC, PRODUCT, [],
                                confirmed_prompt_input=request))
    assert provider.requests == []


def test_serialized_role_values_keep_the_same_ordered_authority_contract():
    request = _dress_request()
    serialized = replace(request, ordered_roles=tuple(role.value for role in request.ordered_roles))
    assert compile_reference_photo_prompt(serialized) == compile_reference_photo_prompt(request)


def test_dynamic_framing_pose_and_uncertainty_are_not_replaced_with_fixture_rules():
    request = _dress_request()
    request = replace(request, cut_lock=replace(request.cut_lock,
        requested_framing='Retain the example-specific headroom and intentional knee crop.'),
        pose_semantics=replace(request.pose_semantics, gaze='eyes lowered toward the supported hand'))
    prompt = compile_reference_photo_prompt(request)
    assert 'Retain the example-specific headroom and intentional knee crop.' in prompt
    assert 'eyes lowered toward the supported hand' in prompt
    assert all(item.value in prompt for item in request.hard_facts)
    assert all(item.reason in prompt for item in request.uncertainties)
    assert 'selected capture arm alone' not in prompt
    assert 'RECENT IPHONE' not in prompt


def test_environment_policy_reaches_the_real_request_builder(monkeypatch):
    from app.config import load_settings
    monkeypatch.setenv('MODEL_ROUTING_IMAGE_HIGH', 'gpt-image-2.5-flare')
    monkeypatch.setenv('STYLING_PHOTO_PROFILE', 'reference-v2')
    new = cg._base_request(load_settings(), SPEC, PRODUCT, confirmed_prompt_input=_dress_request())
    monkeypatch.setenv('STYLING_PHOTO_PROFILE', 'legacy')
    old = cg._base_request(load_settings(), SPEC, PRODUCT, confirmed_prompt_input=_dress_request())
    assert new.model == 'gpt-image-2.5-sunburst'
    assert old.model == 'gpt-image-2.5-flare'
    assert new.provider_kwargs == old.provider_kwargs


def test_ordinary_generic_styling_uses_actual_attached_reference_without_repacking():
    # Editor/side-view packets differ from confirmed packets but still belong to
    # ordinary styling. Excluding them would leave the approved feature half wired.
    provider = CaptureProvider()
    images = [cg.InlineImage('image/png', str(i).encode()) for i in range(5)]
    manifest = cg.build_manifest([{'slot': 'Front'}, {'slot': 'Back'}],
        has_mannequin=False, has_match=False, mood_count=0,
        has_model_face=True, has_model_full_body=True, example_scope='all')
    asyncio.run(cg.generate(settings(), provider, SPEC, PRODUCT, images, manifest=manifest))
    wire = provider.requests[0]
    assert wire['model'] == 'gpt-image-2.5-sunburst'
    assert [x.data for x in wire['images']] == [b'0', b'1', b'2', b'3', b'4']
    assert 'REFERENCE-OWNED CAPTURE' in wire['prompt']
    assert 'slightly cool,' not in wire['prompt']
    assert 'EXAMPLE REFERENCE (scope: all)' in wire['prompt']


def test_generic_and_confirmed_profile_repairs_use_the_same_renderer():
    provider = CaptureProvider()
    image = cg.InlineImage('image/png', b'generic-first-result')
    asyncio.run(cg.repair(settings(), provider, SPEC, PRODUCT, image,
        qc_corrections=('Restore the physically grounded support shadow.',)))
    assert provider.requests[0]['model'] == 'gpt-image-2.5-sunburst'
    assert provider.requests[0]['images'] == [image]
