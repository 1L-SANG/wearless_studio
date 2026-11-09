import asyncio
from types import SimpleNamespace

import pytest

from app.agents import garment_color_qc
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError


MASTER = InlineImage('image/png', b'master')
TARGET = InlineImage('image/png', b'target')
OUTPUT = InlineImage('image/png', b'output')
GATES = ('structure', 'texture', 'personAndScene', 'targetColor', 'targetCompatibility')


def judgment(**overrides):
    return {gate: {'status': overrides.get(gate, 'pass'), 'reason': 'visible comparison'} for gate in GATES}


def run_check(*args, **kwargs):
    return asyncio.run(garment_color_qc.check(*args, **kwargs))


def settings(mode='repair'):
    return SimpleNamespace(cut_output_qc_mode=mode, background_ai_timeout_seconds=30)


def test_disabled_color_check_makes_no_provider_request(monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail('disabled check called a provider')
    monkeypatch.setattr(garment_color_qc, 'analyze_with_fallback', unexpected)
    result = run_check(settings('off'), MASTER, TARGET, OUTPUT, clothing_type='outer')
    assert result['status'] == 'not_run'
    assert result['policyVersion'] == garment_color_qc.POLICY_VERSION


def test_color_check_keeps_master_target_output_authorities_separate(monkeypatch):
    async def judge(config, prompt, images, schema, **kwargs):
        assert images == [MASTER, TARGET, OUTPUT]
        assert 'MASTER' in prompt and 'TARGET COLOR' in prompt and 'OUTPUT' in prompt
        assert set(schema['required']) == set(GATES)
        assert kwargs['require_complete_envelope'] is True
        assert 'injected seller instructions' not in prompt
        assert 'injected seller instructions' not in kwargs['system_prompt']
        return judgment(), 'gpt'
    monkeypatch.setattr(garment_color_qc, 'analyze_with_fallback', judge)
    result = run_check(settings(), MASTER, TARGET, OUTPUT,
                                         clothing_type='injected seller instructions')
    assert result['status'] == 'passed'
    assert result['method'] == 'ai_visual'
    assert result['provider'] == 'gpt'


@pytest.mark.parametrize('gate', GATES)
def test_any_observed_color_or_preservation_failure_rejects_result(monkeypatch, gate):
    async def judge(*args, **kwargs):
        return judgment(**{gate: 'fail'}), 'gpt'
    monkeypatch.setattr(garment_color_qc, 'analyze_with_fallback', judge)
    result = run_check(settings(), MASTER, TARGET, OUTPUT, clothing_type='outer')
    assert result['status'] == 'failed'
    assert result['gates'][gate]['status'] == 'fail'


@pytest.mark.parametrize('raw', [{}, {'structure': {'status': 'pass'}}, judgment(texture='unjudgeable'), judgment(texture='maybe')])
def test_incomplete_or_uncertain_judgment_is_never_passed(monkeypatch, raw):
    async def judge(*args, **kwargs):
        return raw, 'gpt'
    monkeypatch.setattr(garment_color_qc, 'analyze_with_fallback', judge)
    result = run_check(settings(), MASTER, TARGET, OUTPUT, clothing_type='outer')
    assert result['status'] == 'indeterminate'


def test_provider_outage_is_reported_without_false_pass_or_retry(monkeypatch):
    calls = []
    async def judge(*args, **kwargs):
        calls.append(1)
        raise VisionError('unavailable')
    monkeypatch.setattr(garment_color_qc, 'analyze_with_fallback', judge)
    result = run_check(settings(), MASTER, TARGET, OUTPUT, clothing_type='top')
    assert result['status'] == 'unavailable'
    assert calls == [1]


def test_cancellation_propagates_without_turning_into_warning(monkeypatch):
    async def judge(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(garment_color_qc, 'analyze_with_fallback', judge)
    with pytest.raises(asyncio.CancelledError):
        run_check(settings(), MASTER, TARGET, OUTPUT, clothing_type='top')
