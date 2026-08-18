"""생성 프롬프트의 기장 보존 가드 (2026-08-19 오너 피드백).

관찰: 매칭 하의가 붙는 상의 컷의 기장이 원본보다 길게 그려진다(캘빈클라인 실컷 3장 —
표준 기장 티가 엉덩이까지 내려옴). 원인 가설: 언턱 지시("밑단이 끊김 없이 보여야 하며
스타일링 관례보다 우선")를 만족시키는 가장 쉬운 방법이 기장을 늘리는 것이라, 모델이
언턱을 '기장 연장'으로 달성한다. 언턱 **편집** 프롬프트에는 이미 길이 보존 제약이 있는데
**생성** 프롬프트에는 없었다 — 이 테스트가 그 가드의 존재를 고정한다.
"""

from app.workers.mannequin_job import load_prompt_template
from tests.conftest import make_settings


def test_generation_prompt_forbids_lengthening_to_achieve_untuck():
    template = load_prompt_template(make_settings())
    # 언턱 지시와 같은 블록(MATCHING BOTTOM)에 기장 보존이 함께 있어야 한다 —
    # 서로 다른 곳에 있으면 모델이 언턱 문단만 읽고 기장을 늘린다.
    assert "not permission to lengthen" in template, \
        "언턱이 기장 연장의 면허가 아니라는 명시가 생성 프롬프트에 있어야 한다"
    bottom_block = next(
        (p for p in template.split("\n- ") if p.startswith("MATCHING BOTTOM")), None)
    assert bottom_block is not None
    assert "not permission to lengthen" in bottom_block, \
        "기장 보존 가드는 MATCHING BOTTOM(언턱 지시) 블록 안에 있어야 한다"
    assert "product photos" in bottom_block and "length" in bottom_block
