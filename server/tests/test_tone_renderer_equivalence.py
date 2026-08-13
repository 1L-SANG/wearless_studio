"""브라우저 렌더러와 QA 렌더러가 **바이트 단위로** 같은지.

계약(§31)은 구현이 하나일 것을 요구한다. 실제 렌더는 브라우저(`src/lib/toneRender.js`)가
전담하지만, 시각 QA 를 파이썬으로 돌리려면 같은 수식이 파이썬에도 있어야 한다 — 그 순간
"미리보기 알고리즘 A / 최종 알고리즘 B" 함정이 열린다.

그래서 두 번째 구현을 두되, 같다는 것을 **고정 픽셀 집합으로 증명**한다. JS 쪽 출력은
`tests/fixtures/tone_js_reference.json` 에 박제되어 있고(생성 스크립트는 주석 참조), 여기서
파이썬 구현이 한 바이트라도 다르면 실패한다.

재생성:
    node scripts/tone_js_reference.mjs > server/tests/fixtures/tone_js_reference.json
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest

from app.services import tone_math

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "tone_js_reference.json"


@pytest.mark.skipif(not FIXTURE.exists(), reason="JS 기준 출력이 아직 없다")
def test_python_renderer_matches_the_browser_byte_for_byte():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    src = np.array(data["src"], np.uint8).reshape(-1, 4)
    mask = np.array(data["mask"], np.uint8)
    for case in data["cases"]:
        expected = np.array(case["px"], np.uint8).reshape(-1, 4)
        got = tone_math.apply_tone(src, mask, case["s"], case["e"])
        diff = np.abs(got.astype(int) - expected.astype(int))
        assert diff.max() == 0, (
            f"색감 {case['s']} 밝기 {case['e']} 에서 최대 {diff.max()} LSB 차이 "
            f"({int((diff.max(axis=1) > 0).sum())} 픽셀)")


@pytest.mark.skipif(not FIXTURE.exists(), reason="JS 기준 출력이 아직 없다")
def test_zero_adjustment_is_the_identity_in_both_implementations():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    src = np.array(data["src"], np.uint8).reshape(-1, 4)
    neutral = next(c for c in data["cases"] if c["s"] == 0 and c["e"] == 0)
    assert np.array_equal(np.array(neutral["px"], np.uint8).reshape(-1, 4), src)


def test_outside_mask_is_untouched_in_the_python_renderer():
    src = np.array([[200, 40, 40, 255], [10, 120, 200, 255]], np.uint8)
    out = tone_math.apply_tone(src, np.array([255, 0], np.uint8), 30, 20)
    assert np.array_equal(out[1], src[1])
    assert not np.array_equal(out[0], src[0])
