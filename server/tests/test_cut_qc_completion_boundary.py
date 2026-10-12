"""현재 origin/main의 실제 최종컷 검사에서 잘린 성공 JSON을 거절하는지 재현."""
import asyncio,json
import httpx
import pytest
from app.agents import cut_output_qc as qc,vision_llm
from conftest import make_settings
from test_cut_output_qc import _raw,_plan,_img,_model_refs


def evaluate(monkeypatch,finish='STOP',copies=1,blocked=False):
    candidate={'finishReason':finish,'content':{'parts':[{'text':json.dumps(_raw())}]}}
    payload={'modelVersion':'gemini-3.1-pro-preview','candidates':[candidate]*copies}
    if blocked:payload['promptFeedback']={'blockReason':'SAFETY'}
    async def simulated_http_response(*args,metadata=None,**kwargs):
        response=httpx.Response(200,json=payload)
        return vision_llm._parse_gemini_response(response,metadata,'gemini-3.1-pro-preview')
    monkeypatch.setitem(vision_llm._PROVIDERS,'gemini',(simulated_http_response,lambda s:s.model_text_gemini,lambda s:True))
    settings=make_settings(analysis_model_order='gemini',gemini_api_key='fake-no-network',openai_api_key='')
    refs=[qc.LabeledReference('product',_img(b'PRODUCT')),*_model_refs(),qc.LabeledReference('example',_img(b'EXAMPLE'))]
    return asyncio.run(qc.verdict(settings,_plan(),refs,_img(b'GENERATED')))


def test_complete_single_pass_remains_accepted(monkeypatch):
    assert evaluate(monkeypatch)['passed'] is True


@pytest.mark.parametrize('finish',['MAX_TOKENS','SAFETY',None])
def test_incomplete_envelope_is_not_a_pass_even_if_json_has_every_gate(monkeypatch,finish):
    with pytest.raises(vision_llm.VisionError):evaluate(monkeypatch,finish=finish)


def test_multiple_candidates_are_not_silently_accepted(monkeypatch):
    with pytest.raises(vision_llm.VisionError):evaluate(monkeypatch,copies=2)


def test_blocked_prompt_is_not_a_pass(monkeypatch):
    with pytest.raises(vision_llm.VisionError):evaluate(monkeypatch,blocked=True)
