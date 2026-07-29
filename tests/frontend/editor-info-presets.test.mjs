import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CARE_LABEL_SENTENCE,
  INFO_PRESET_TYPES,
  INFO_TEMPLATES,
  NEEDS_INPUT,
  applyInfoTemplate,
  buildInfoBlock,
  defaultInfoFor,
  presetTypeOf,
  templateStyleFor,
} from '../../src/features/editor/presets/infoPresets.js';
import { normalizeEditorBlockRole } from '../../src/lib/storyboardTaxonomy.js';

const seqId = () => { let n = 0; return (p) => `${p}${(n += 1)}`; };

const CTX = {
  productName: '테스트 티셔츠',
  clothingType: 'top',
  measurementSchema: { top: ['totalLength', 'shoulderWidth', 'chestWidth', 'sleeveLength'] },
  measurementLabels: { totalLength: '총장', shoulderWidth: '어깨너비', chestWidth: '가슴단면', sleeveLength: '소매길이' },
  measurements: [{ key: 'totalLength', value: 67 }, { key: 'chestWidth', value: 55 }],
  materials: [{ name: '면', ratio: 100 }],
  sellingPoints: ['소프트 터치', '롤업 소매'],
  fit: 'regular',
  fits: [
    { value: 'slim', label: '슬림핏' }, { value: 'regular', label: '정핏' },
    { value: 'semi_over', label: '세미오버' }, { value: 'over', label: '오버핏' },
  ],
  colorLabels: ['블랙', '아이보리'],
};

const CONTRACT_ELEMENT_TYPES = new Set(['text', 'shape', 'line', 'image']);

test('every preset builds with defaults using only contract element types and unique ids', () => {
  for (const preset of INFO_PRESET_TYPES) {
    const block = buildInfoBlock(preset.type, defaultInfoFor(preset.type, CTX), CTX, seqId());
    assert.ok(block.id, `${preset.type}: block id`);
    assert.ok(block.elements.length > 0, `${preset.type}: has elements`);
    assert.ok(block.h > 0, `${preset.type}: has height`);
    assert.ok(block.info && typeof block.info === 'object', `${preset.type}: form state stored on block.info`);
    const ids = new Set();
    for (const el of block.elements) {
      assert.ok(CONTRACT_ELEMENT_TYPES.has(el.type), `${preset.type}: element type ${el.type} in contract`);
      assert.ok(!ids.has(el.id), `${preset.type}: duplicate element id ${el.id}`);
      ids.add(el.id);
    }
    // 콘텐츠가 블록 높이를 넘지 않는다 (expandBlockHeights 하한과 일관)
    const bottom = block.elements.reduce((b, el) => Math.max(b, (el.y || 0) + (el.h || 0)), 0);
    assert.ok(block.h >= bottom, `${preset.type}: h(${block.h}) >= content bottom(${bottom})`);
  }
});

test('builders are deterministic given the same info and id factory', () => {
  for (const preset of INFO_PRESET_TYPES) {
    const info = defaultInfoFor(preset.type, CTX);
    const a = buildInfoBlock(preset.type, info, CTX, seqId());
    const b = buildInfoBlock(preset.type, info, CTX, seqId());
    assert.deepEqual(a, b, `${preset.type}: rebuild from info is deterministic`);
  }
});

test('size table grows with rows, prefills measurements, renders — for blanks', () => {
  const one = buildInfoBlock('size_table', defaultInfoFor('size_table', CTX), CTX, seqId());
  const info3 = { ...defaultInfoFor('size_table', CTX), rows: [
    { label: 'S', values: { totalLength: 65 } },
    { label: 'M', values: { totalLength: 67 } },
    { label: 'L', values: {} },
  ] };
  const three = buildInfoBlock('size_table', info3, CTX, seqId());
  assert.ok(three.h > one.h, 'more rows → taller block');
  assert.equal(one.kind, 'size');
  assert.equal(one.auto, true);
  const texts = one.elements.filter((e) => e.type === 'text').map((e) => e.text);
  assert.ok(texts.includes('67'), 'prefilled measurement value rendered');
  const threeTexts = three.elements.filter((e) => e.type === 'text').map((e) => e.text);
  assert.ok(threeTexts.includes('—'), 'missing value renders —');
});

test('required notice renders 정보 입력 필요 for empty fields and keeps prefills', () => {
  const block = buildInfoBlock('required_notice', defaultInfoFor('required_notice', CTX), CTX, seqId());
  const texts = block.elements.filter((e) => e.type === 'text').map((e) => e.text);
  assert.ok(texts.includes(NEEDS_INPUT), 'empty field placeholder');
  assert.ok(texts.includes('테스트 티셔츠'), '품명 prefill');
  assert.ok(texts.includes('면 100%'), '소재 prefill');
  assert.equal(block.kind, 'info');
  assert.equal(block.infoType, 'required_notice');
});

test('care block always contains the care-label sentence', () => {
  const stripped = buildInfoBlock('care', { family: 'cotton', text: '면 소재라 부드러워요.' }, CTX, seqId());
  const body = stripped.elements.find((e) => e.type === 'text' && e.text.includes('면 소재라'));
  assert.ok(body.text.includes('케어라벨'), 'appended when missing');
  const fromDefault = buildInfoBlock('care', defaultInfoFor('care', CTX), CTX, seqId());
  assert.ok(fromDefault.elements.some((e) => e.type === 'text' && e.text.includes(CARE_LABEL_SENTENCE)));
  assert.equal(fromDefault.kind, 'care');
  assert.equal(fromDefault.auto, true);
});

test('presetTypeOf round-trips every built preset back to its type', () => {
  for (const preset of INFO_PRESET_TYPES) {
    const block = buildInfoBlock(preset.type, defaultInfoFor(preset.type, CTX), CTX, seqId());
    assert.equal(presetTypeOf(block), preset.type, `${preset.type} round-trip`);
  }
  assert.equal(presetTypeOf({ kind: 'fit', contentRole: 'hero' }), null);
});

test('normalizeEditorBlockRole passes info blocks through untouched (reload guard)', () => {
  const block = buildInfoBlock('header', { nameKo: '테스트', nameEn: 'Test' }, CTX, seqId());
  const normalized = normalizeEditorBlockRole(block);
  assert.equal(normalized, block, 'same reference — byte identical');
  assert.equal(normalized.kind, 'info');
});

test('templateStyleFor derives brand only for all-men targets', () => {
  assert.equal(templateStyleFor(['men']), 'brand');
  assert.equal(templateStyleFor(['women']), 'soho');
  assert.equal(templateStyleFor(['men', 'women']), 'soho');
  assert.equal(templateStyleFor([]), 'soho');
  assert.equal(templateStyleFor(undefined), 'soho');
});

const baseDoc = () => [
  { id: 'b0', name: '첫 장면', kind: 'benefit', contentRole: 'hero', bg: '#ffffff', h: 800, elements: [{ id: 'b0e0', type: 'image', x: 60, y: 50, w: 880, h: 700, src: '/cut.jpg' }] },
  { id: 'b1', name: '사이즈 안내', kind: 'size', auto: true, bg: '#ffffff', h: 260, elements: [] },
  { id: 'b2', name: '세탁 안내', kind: 'care', auto: true, bg: '#f5f5f5', h: 200, elements: [] },
  { id: 'b3', name: 'AI 생성 안내', kind: 'ai-notice', auto: true, bg: '#ffffff', h: 140, elements: [] },
];

test('soho template inserts top blocks first, flows before anchors, replaces size/care in place', () => {
  const doc = baseDoc();
  const { blocks, inserted, skipped } = applyInfoTemplate(doc, 'soho', CTX, seqId());
  assert.equal(skipped.length, 0);
  assert.equal(inserted.length, INFO_TEMPLATES.soho.top.length + INFO_TEMPLATES.soho.flow.length);
  const kinds = blocks.map((b) => `${b.kind}${b.infoType ? ':' + b.infoType : ''}`);
  assert.deepEqual(kinds, [
    'info:shipping_returns', 'info:header',            // top: 공지 → 헤더
    'benefit',                                          // 컷 블록 (그대로)
    'info:benefit_copy', 'info:model_info',             // size 앵커 앞 플러시
    'size', 'care',                                     // 제자리 강화
    'ai-notice',
  ]);
  // 컷 블록 불변 — 같은 참조
  assert.equal(blocks.find((b) => b.kind === 'benefit'), doc[0]);
  // size 제자리 강화 — info 부착 + auto 유지
  const size = blocks.find((b) => b.kind === 'size');
  assert.ok(size.info, 'size block carries form state');
  assert.equal(size.auto, true);
});

test('re-applying a template skips duplicate info types but re-enriches anchors', () => {
  const first = applyInfoTemplate(baseDoc(), 'soho', CTX, seqId());
  const second = applyInfoTemplate(first.blocks, 'soho', CTX, seqId());
  assert.deepEqual(second.skipped.sort(), ['모델 정보', '배송·교환 안내', '상품명 헤더', '특징 포인트 3종'].sort());
  assert.equal(second.blocks.length, first.blocks.length, 'no duplicate blocks added');
});

test('brand template on a doc without anchors appends the flow before ai-notice in order', () => {
  const doc = [baseDoc()[0], baseDoc()[3]];
  const { blocks } = applyInfoTemplate(doc, 'brand', CTX, seqId());
  const kinds = blocks.map((b) => `${b.kind}${b.infoType ? ':' + b.infoType : ''}`);
  assert.deepEqual(kinds, [
    'info:header',
    'benefit',
    'info:fit_guide', 'size', 'info:size_matrix', 'care', 'info:required_notice',
    'ai-notice',
  ]);
});
