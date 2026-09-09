import asyncio
import importlib
from types import SimpleNamespace
import io
from PIL import Image
from app.agents.gemini_image import InlineImage

import pytest

from app.agents import cut_generator, vision_llm
from app.agents.wearshot_contract import make_repair_plan, image_sha256
from test_wearshot_contract_v2 import packet, png
from test_wearshot_qc_v2 import observed, mark


def runtime():
    assert importlib.util.find_spec('app.agents.wearshot_runtime'), 'v2 runtime missing'
    return importlib.import_module('app.agents.wearshot_runtime')


def settings():
    return SimpleNamespace(openai_api_key='test', analysis_timeout_seconds=5,
                           image_high_model='gpt-image-2', mannequin_image_size='2K',
                           mannequin_aspect_ratio='2:3', cut_output_release_policy='off')


def canvas(color='green', dimensions=(1360, 2048)):
    buffer = io.BytesIO()
    Image.new('RGB', dimensions, color).save(buffer, format='PNG')
    return InlineImage('image/png', buffer.getvalue())


def test_v2_generator_uses_exact_packet_and_source_aspect_without_legacy_crop(monkeypatch):
    contract, candidate = packet(), canvas('green')
    calls = []
    class Provider:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            calls.append((model, prompt, images, kwargs))
            return SimpleNamespace(image=candidate.data, mime=candidate.mime)
    monkeypatch.setattr(cut_generator, '_resolve_generation_model', lambda *a: 'gpt-image-2')
    result = asyncio.run(cut_generator.generate(settings(), Provider(),
        {'cutType': 'styling', 'shot': 'medium', 'refScope': 'pose'}, {'clothing_type': 'bottom'},
        [ref.image for ref in contract.references], wearshot_contract=contract, output_size='1360x2048'))
    assert result == (candidate.data, candidate.mime)
    assert calls[0][2] == [ref.image for ref in contract.references]
    assert calls[0][3]['openai_preserve_input_bytes'] is True
    assert calls[0][3]['openai_output_size'] == '1360x2048'
    assert 'aspect_ratio' not in calls[0][3]
    plan = make_repair_plan(contract, candidate, ('light',), ('pose', 'background'))
    asyncio.run(cut_generator.repair(settings(), Provider(), {}, {}, candidate,
        wearshot_contract=contract, repair_plan=plan, repair_model='gpt-image-2-test', output_size='1360x2048'))
    assert calls[1][0] == 'gpt-image-2-test'
    assert calls[1][2] == [candidate, *[ref.image for ref in contract.references]]


def test_mixed_v2_and_confirmed_or_wrong_base_fail_before_provider():
    contract, candidate = packet(), png()
    with pytest.raises(ValueError):
        asyncio.run(cut_generator.generate(settings(), None, {}, {}, [],
            wearshot_contract=contract, confirmed_prompt_input=object()))
    plan = make_repair_plan(contract, candidate, ('light',), ())
    with pytest.raises(ValueError):
        asyncio.run(cut_generator.repair(settings(), None, {}, {}, png('black'),
            wearshot_contract=contract, repair_plan=plan))


@pytest.mark.parametrize('focus', ['PASS', 'FAIL', 'UNJUDGEABLE', 'wrong_hash'])
def test_focused_face_challenge_mandatory_even_release_off(monkeypatch, focus):
    rt, contract, candidate = runtime(), packet(), png('green')
    async def provider(s, model, prompt, images, schema, timeout):
        if 'garments' in schema['properties']:
            assert images[-1] == candidate
            return observed(contract)
        return dict(viewAdequate=focus != 'UNJUDGEABLE', selectedFaceRelation='clear_target' if focus == 'PASS' else 'mixed',
                    strongestTargetEvidence='Visible target-specific eyes.', strongestSourceEvidence='Source nose differs.',
                    remainingAmbiguity='No hidden regions inferred.')
    monkeypatch.setattr(vision_llm, '_call_gpt', provider)
    if focus == 'wrong_hash':
        async def wrong(*a): return {'status': 'PASS', 'candidateSha256': 'bad'}
        monkeypatch.setattr(rt.cut_identity_review, 'verdict', wrong)
    result = asyncio.run(rt.review_candidate(settings(), contract, candidate))
    assert rt.release_allowed(result, contract, candidate) is (focus == 'PASS')
    if focus != 'PASS':
        plan = rt.derive_repair_plan(contract, candidate, result)
        assert 'identity' in plan.failed_axes
        assert 'identity' not in plan.approved_axes


def test_variation_repair_leaves_selected_axis_unprotected(monkeypatch):
    rt, contract, candidate = runtime(), packet(), png()
    raw = observed(contract)
    raw['variation']['poseChanged'] = False
    async def primary(*a, **kw):
        from app.agents.wearshot_qc import validate
        return validate(raw, contract, candidate)
    async def focus(*a): return {'status': 'PASS', 'candidateSha256': image_sha256(candidate)}
    monkeypatch.setattr(rt.wearshot_qc, 'verdict', primary)
    monkeypatch.setattr(rt.cut_identity_review, 'verdict', focus)
    result = asyncio.run(rt.review_candidate(settings(), contract, candidate))
    plan = rt.derive_repair_plan(contract, candidate, result)
    assert set(plan.failed_axes) == {'variation', 'pose'}
    assert 'background' in plan.approved_axes
    assert 'pose' not in plan.approved_axes


@pytest.mark.parametrize('dimensions,expected', [((387, 515), '1536x2048'), ((720, 883), '1664x2048'), ((900,1200), '1536x2048')])
def test_source_canvas_preserves_real_photo_aspect_with_provider_quantization(dimensions, expected):
    buffer = io.BytesIO()
    Image.new('RGB', dimensions).save(buffer, format='PNG')
    assert runtime().source_output_size(InlineImage('image/png', buffer.getvalue())) == expected


def test_explicit_v2_canvas_cannot_change_source_crop_before_provider():
    with pytest.raises(ValueError):
        asyncio.run(cut_generator.generate(settings(), None, {}, {}, [r.image for r in packet().references],
            wearshot_contract=packet(), output_size='1536x1024'))


def test_hidden_face_skips_independent_challenge_without_certifying_identity(monkeypatch):
    from app.agents.wearshot_contract import FrameLock
    rt, candidate = runtime(), png('green')
    contract = packet(frame_lock=FrameLock('medium', 'hidden', 'Head outside crop'))
    async def provider(settings, model, prompt, images, schema, timeout):
        assert 'garments' in schema['properties'], 'hidden face triggered a face challenge'
        return observed(contract)
    monkeypatch.setattr(vision_llm, '_call_gpt', provider)
    result = asyncio.run(rt.review_candidate(settings(), contract, candidate))
    assert rt.release_allowed(result, contract, candidate)
    assert result['attributes']['identity']['status'] == 'NOT_APPLICABLE'
    assert 'identityReview' not in result


@pytest.mark.parametrize('output_size', ['1024x1536', '2048x3072'])
def test_explicit_v2_canvas_requires_native_2048_long_edge(output_size):
    with pytest.raises(ValueError, match='native_2k'):
        runtime().validate_output_size(png(), output_size)


@pytest.mark.parametrize('stage', ['generate', 'repair'])
@pytest.mark.parametrize('output', ['wrong_size', 'malformed'])
def test_v2_generator_rejects_provider_output_before_returning(stage, output, monkeypatch):
    contract, base = packet(), canvas('orange')
    bad = canvas(dimensions=(1024, 1536)) if output == 'wrong_size' else InlineImage('image/png', b'not an image')
    class Provider:
        async def generate_content_image(self, *args, **kwargs):
            return SimpleNamespace(image=bad.data, mime=bad.mime)
    monkeypatch.setattr(cut_generator, '_resolve_generation_model', lambda *a: 'gpt-image-2')
    with pytest.raises(ValueError):
        if stage == 'generate':
            asyncio.run(cut_generator.generate(settings(), Provider(), {}, {}, [ref.image for ref in contract.references],
                wearshot_contract=contract))
        else:
            plan = make_repair_plan(contract, base, ('light',), ('pose', 'background'))
            asyncio.run(cut_generator.repair(settings(), Provider(), {}, {}, base,
                wearshot_contract=contract, repair_plan=plan))
