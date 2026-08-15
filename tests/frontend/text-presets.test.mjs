import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_TEXT_PRESET, TEXT_PRESETS, activeTextPreset, buildTextPresetElement, quickStylePatch,
} from '../../src/features/editor/presets/textPresets.js';

/* 값의 출처: docs/superpowers/specs/2026-08-04-editor-text-slots-design.md
   (셀러 텍스트 568건 실측 기반 타이포 위계). 이 테스트는 프리셋이 스펙 값에서
   조용히 어긋나는 것을 막는 계약 고정이다. */
const SPEC = {
  headline: { size: 40, weight: 600, color: '#0e0d14' },
  subtitle: { size: 26, weight: 600, color: '#0e0d14' },
  body: { size: 17, weight: 400, color: '#6b6b73', lineHeight: 26 },
  tag: { size: 19, weight: 700, color: '#9a9aa2', tracking: 3 },
};

test('프리셋 4종이 스펙 타이포 값과 일치한다', () => {
  assert.equal(TEXT_PRESETS.length, 4);
  for (const [key, expected] of Object.entries(SPEC)) {
    const p = TEXT_PRESETS.find((x) => x.key === key);
    assert.ok(p, `${key} 프리셋이 있어야 한다`);
    for (const [prop, value] of Object.entries(expected)) {
      assert.equal(p.style[prop], value, `${key}.style.${prop}`);
    }
  }
});

test('기본 프리셋은 소제목 — 시장 최다 텍스트(65.5%)', () => {
  assert.equal(DEFAULT_TEXT_PRESET, 'subtitle');
});

test('요소 생성 — 빈 텍스트+자동 폭(즉시 입력 UX), 이미지 기둥(x=60) 시작', () => {
  for (const p of TEXT_PRESETS) {
    const el = buildTextPresetElement(p.key);
    assert.equal(el.type, 'text');
    // 4e53fe7 이후 계약: 샘플 문구를 정본(el.text)에 넣지 않는다 — 셀러가 바로 타이핑.
    assert.equal(el.text, '', `${p.key}는 빈 텍스트로 시작한다`);
    assert.equal(el.textSizing, 'auto', `${p.key}는 자동 폭`);
    assert.equal(el.x, 60, `${p.key} x`);
    assert.ok(el.id && el.id !== buildTextPresetElement(p.key).id, 'id는 매번 달라야 한다');
    assert.deepEqual(el.style, { font: 'Pretendard', ...p.style });
  }
});

test('모르는 키는 기본 프리셋으로 — 호출부 오타가 흰 화면이 되면 안 된다', () => {
  const el = buildTextPresetElement('nope');
  assert.equal(el.style.size, SPEC.subtitle.size);
});

test('빠른 스타일 전환 — 위계 속성만 바꾸고 셀러의 폰트·정렬은 남긴다', () => {
  const patch = quickStylePatch('headline');
  assert.equal(patch.size, 40);
  assert.equal(patch.weight, 600);
  assert.equal(patch.color, '#0e0d14');
  assert.ok(!('font' in patch), '폰트는 건드리지 않는다');
  assert.ok(!('align' in patch), '정렬은 건드리지 않는다');
});

test('빠른 스타일 전환 — 행간·자간은 프리셋에 없으면 0으로 리셋된다', () => {
  // 설명글(행간 26) → 큰 제목으로 바꿀 때 행간 26이 남으면 40px 글줄이 겹친다.
  const toHeadline = quickStylePatch('headline');
  assert.equal(toHeadline.lineHeight, 0, '행간 리셋(0 = 자동 1.4배)');
  assert.equal(toHeadline.tracking, 0, '자간 리셋');
  const toBody = quickStylePatch('body');
  assert.equal(toBody.lineHeight, 26);
  const toTag = quickStylePatch('tag');
  assert.equal(toTag.tracking, 3);
});

test('활성 프리셋 판별 — 크기·굵기·색이 다 맞을 때만, 아니면 null', () => {
  assert.equal(activeTextPreset({ size: 26, weight: 600, color: '#0e0d14' }), 'subtitle');
  assert.equal(activeTextPreset({ size: 26, weight: 600, color: '#0e0d14', align: 'center' }), 'subtitle',
    '위계 밖 속성(정렬 등)은 판별에 영향 없다');
  assert.equal(activeTextPreset({ size: 27, weight: 600, color: '#0e0d14' }), null);
  assert.equal(activeTextPreset({ size: 17, weight: 400, color: '#6b6b73', lineHeight: 26 }), 'body');
  assert.equal(activeTextPreset({ size: 17, weight: 400, color: '#6b6b73', lineHeight: 30 }), null,
    '행간을 바꿨으면 더는 그 프리셋이 아니다');
  assert.equal(activeTextPreset(undefined), null);
});
