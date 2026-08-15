import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_TEXT_PRESET, TEXT_PRESETS, activeTextPreset, buildTextPresetElement, quickStylePatch, textPresetOf,
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

test('패널 축소판 크기는 실제 크기 순서를 보존한다 — 목록이 위계를 거꾸로 보여주면 안 된다', () => {
  const bySize = [...TEXT_PRESETS].sort((a, b) => b.style.size - a.style.size);
  const byPreview = [...TEXT_PRESETS].sort((a, b) => b.previewSize - a.previewSize);
  assert.deepEqual(byPreview.map((p) => p.key), bySize.map((p) => p.key));
});

test('기본 프리셋은 소제목 — 시장 최다 텍스트(65.5%)', () => {
  assert.equal(DEFAULT_TEXT_PRESET, 'subtitle');
});

test('요소 생성 — 빈 텍스트+자동 폭(즉시 입력 UX), 이미지 기둥(x=60)·캐럿 씨앗폭 12', () => {
  for (const p of TEXT_PRESETS) {
    const el = buildTextPresetElement(p.key);
    assert.equal(el.type, 'text');
    // 4e53fe7 이후 계약: 샘플 문구를 정본(el.text)에 넣지 않는다 — 셀러가 바로 타이핑.
    assert.equal(el.text, '', `${p.key}는 빈 텍스트로 시작한다`);
    assert.equal(el.textSizing, 'auto', `${p.key}는 자동 폭`);
    assert.equal(el.w, 12, `${p.key} w — 포인트 텍스트 캐럿 씨앗값(키우면 빈 상자가 넓게 그려진다)`);
    assert.equal(el.x, 60, `${p.key} x`);
    assert.ok(el.id && el.id !== buildTextPresetElement(p.key).id, 'id는 매번 달라야 한다');
    assert.deepEqual(el.style, { font: 'Pretendard', ...p.style });
  }
});

test('모르는 키·미지정은 기본 프리셋으로 — 요소와 라벨이 같은 폴백을 공유한다', () => {
  assert.equal(buildTextPresetElement('nope').style.size, SPEC.subtitle.size);
  assert.equal(textPresetOf(undefined).label, '소제목', 'T 단축키의 토스트 라벨도 소제목이어야 한다');
  assert.equal(textPresetOf('nope').key, DEFAULT_TEXT_PRESET);
});

test('빠른 스타일 전환 — 위계 속성만 바꾸고 셀러의 폰트·정렬은 남긴다', () => {
  const patch = quickStylePatch('headline');
  assert.equal(patch.size, SPEC.headline.size);
  assert.equal(patch.weight, SPEC.headline.weight);
  assert.equal(patch.color, SPEC.headline.color);
  assert.ok(!('font' in patch), '폰트는 건드리지 않는다');
  assert.ok(!('align' in patch), '정렬은 건드리지 않는다');
});

test('빠른 스타일 전환 — 행간·자간은 프리셋에 없으면 0으로 리셋된다', () => {
  // 설명글(행간 26) → 큰 제목으로 바꿀 때 행간 26이 남으면 40px 글줄이 겹친다.
  const toHeadline = quickStylePatch('headline');
  assert.equal(toHeadline.lineHeight, 0, '행간 리셋(0 = 자동 1.4배)');
  assert.equal(toHeadline.tracking, 0, '자간 리셋');
  assert.equal(quickStylePatch('body').lineHeight, SPEC.body.lineHeight);
  assert.equal(quickStylePatch('tag').tracking, SPEC.tag.tracking);
});

test('왕복 — 칩으로 입힌 스타일은 그 칩이 켜진 상태여야 한다', () => {
  for (const p of TEXT_PRESETS) {
    assert.equal(activeTextPreset(quickStylePatch(p.key)), p.key);
  }
});

test('활성 프리셋 판별 — 실효값이 다 맞을 때만, 아니면 null', () => {
  assert.equal(activeTextPreset({ ...SPEC.subtitle }), 'subtitle');
  assert.equal(activeTextPreset({ ...SPEC.subtitle, align: 'center' }), 'subtitle',
    '위계 밖 속성(정렬 등)은 판별에 영향 없다');
  assert.equal(activeTextPreset({ ...SPEC.subtitle, size: 27 }), null);
  assert.equal(activeTextPreset({ ...SPEC.body }), 'body');
  assert.equal(activeTextPreset({ ...SPEC.body, lineHeight: 30 }), null,
    '행간을 실제로 바꿨으면 더는 그 프리셋이 아니다');
  assert.equal(activeTextPreset(undefined), null);
});

test('활성 프리셋 판별 — 화면이 같으면 같은 상태로 본다(실효값 정규화)', () => {
  // 색상 UI(normalizeHexColor·hsvToHex)는 대문자로 저장한다 — 같은 색을 다시 골라도 칩이 꺼지면 안 된다.
  assert.equal(activeTextPreset({ ...SPEC.subtitle, color: '#0E0D14' }), 'subtitle');
  // 행간 자동(0/없음)과 명시된 size×1.4는 같은 화면이다 — 행간 칸을 스쳐도 칩이 꺼지면 안 된다.
  assert.equal(activeTextPreset({ ...SPEC.headline, lineHeight: 56 }), 'headline');
  assert.equal(activeTextPreset({ ...SPEC.tag, lineHeight: 27 }), 'tag');
  // 반대로 렌더 기본값과 다른 실제 변경은 구분한다: weight 없음 = 400 렌더 ≠ 소제목 600.
  assert.equal(activeTextPreset({ size: 26, color: '#0e0d14' }), null);
});
