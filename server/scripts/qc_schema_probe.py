"""운영 검사 응답 스키마를 Gemini가 받아 주는지 글자만 든 작은 요청으로 본다(사진 없음, 12건 합쳐 1센트 안쪽).

cut_output_qc.qc_schema나 칩 계약을 바꾸면 머지 전에 한 번 돌린다. 유료 호출이라 승인 뒤에 사람이나 Claude가 돌린다.
  cd server && QC_PROBE_ENV_FILE=<.env 경로> .venv/bin/python scripts/qc_schema_probe.py
하나라도 200이 아니면 종료 코드 1이다(2026-11-18: 객체 배열을 넣은 스키마가 400 too many states로 거절됐다)."""
import os, sys
from pathlib import Path
from unittest.mock import patch
from dotenv import dotenv_values
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
env = {k: v for k, v in dotenv_values(os.environ.get('QC_PROBE_ENV_FILE', str(Path(__file__).resolve().parents[1] / '.env'))).items() if v is not None}
with patch.dict(os.environ, env):
    from app.config import load_settings
    S = load_settings()
import httpx
from app.agents import cut_output_qc as q, vision_llm as v

def profile(category, gender, **axes):
    return {'category': category, 'gender': gender, 'axes': axes, 'axisSources': {k: 'seller' for k in axes}}

CASES = {
    'none': None,
    'top_all': profile('top', 'women', fit='over', length='crop', sleeve='fingers'),
    'top_sleeve': profile('top', 'men', sleeve='back_of_hand'),
    'outer': profile('outer', 'women', fit='over', sleeve='wrist'),
    'dress': profile('dress', 'women', length='knee', sleeve='cap'),
    'pants': profile('pants', 'women', length='ankle_cover'),
}
bad = 0
for name, prof in CASES.items():
    contract = q._analysis_fit_contract({'attributeOwners': {}}, prof) if prof else None
    schema = q.qc_schema(repair_parts=True, fit_contract=contract)
    for model in ('gemini-3.1-pro-preview', 'gemini-3.8-flash'):
        body = v._gemini_body('Return a minimal valid object with every gate PASS.', [], schema, 'low')
        r = httpx.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                       json=body, headers={'x-goog-api-key': S.gemini_api_key}, timeout=120)
        ok = r.status_code == 200
        bad += not ok
        print(name, model, r.status_code, '' if ok else r.json().get('error', {}).get('message', '')[:160], flush=True)
sys.exit(1 if bad else 0)
