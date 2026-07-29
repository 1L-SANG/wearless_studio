import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CARE_LABEL_SENTENCE,
  DEFAULT_INFO_TEMPLATE,
  INFO_PRESET_TYPES,
  NEEDS_INPUT,
  applyInfoTemplate,
  applySlotFillToInfo,
  buildInfoBlock,
  careFamilyFor,
  carrySlotImages,
  defaultInfoFor,
  presetTypeOf,
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

const baseDoc = () => [
  { id: 'b0', name: '첫 장면', kind: 'benefit', contentRole: 'hero', bg: '#ffffff', h: 800, elements: [{ id: 'b0e0', type: 'image', x: 60, y: 50, w: 880, h: 700, src: '/cut.jpg' }] },
  { id: 'b1', name: '사이즈 안내', kind: 'size', auto: true, bg: '#ffffff', h: 260, elements: [] },
  { id: 'b2', name: '세탁 안내', kind: 'care', auto: true, bg: '#f5f5f5', h: 200, elements: [] },
  { id: 'b3', name: 'AI 생성 안내', kind: 'ai-notice', auto: true, bg: '#ffffff', h: 140, elements: [] },
];

test('default template inserts top blocks first, flows before anchors, replaces size/care in place', () => {
  const doc = baseDoc();
  const { blocks, inserted, skipped } = applyInfoTemplate(doc, CTX, seqId());
  assert.equal(skipped.length, 0);
  assert.equal(inserted.length, DEFAULT_INFO_TEMPLATE.top.length + DEFAULT_INFO_TEMPLATE.flow.length);
  const kinds = blocks.map((b) => `${b.kind}${b.infoType ? ':' + b.infoType : ''}`);
  assert.deepEqual(kinds, [
    'info:shipping_returns', 'info:header',            // top: 공지 → 헤더
    'benefit',                                          // 컷 블록 (그대로)
    'info:benefit_copy',                                // size 앵커 앞 플러시
    'size', 'care',                                     // 제자리 강화
    'info:required_notice',                             // care 뒤
    'ai-notice',
  ]);
  // 컷 블록 불변 — 같은 참조
  assert.equal(blocks.find((b) => b.kind === 'benefit'), doc[0]);
  // size 제자리 강화 — info 부착 + auto 유지
  const size = blocks.find((b) => b.kind === 'size');
  assert.ok(size.info, 'size block carries form state');
  assert.equal(size.auto, true);
});

test('re-applying the template skips duplicate info types but re-enriches anchors', () => {
  const first = applyInfoTemplate(baseDoc(), CTX, seqId());
  const second = applyInfoTemplate(first.blocks, CTX, seqId());
  assert.deepEqual(second.skipped.sort(), ['배송·교환 안내', '상품명 헤더', '상품 고시정보', '특징 포인트'].sort());
  assert.equal(second.blocks.length, first.blocks.length, 'no duplicate blocks added');
});

test('template re-apply preserves user-entered anchor info and block ids (no data wipe)', () => {
  const doc = baseDoc();
  const userRows = [{ label: 'S', values: { totalLength: 65 } }, { label: 'M', values: { totalLength: 67 } }];
  doc[1] = { ...doc[1], info: { ...defaultInfoFor('size_table', CTX), rows: userRows } };
  doc[2] = { ...doc[2], info: { family: 'cotton', text: `내가 쓴 케어 문구\n${CARE_LABEL_SENTENCE}` } };
  const { blocks } = applyInfoTemplate(doc, CTX, seqId());
  const size = blocks.find((b) => b.kind === 'size');
  const care = blocks.find((b) => b.kind === 'care');
  assert.equal(size.id, 'b1', 'size block id preserved');
  assert.equal(care.id, 'b2', 'care block id preserved');
  assert.deepEqual(size.info.rows.map((r) => r.label), ['S', 'M'], 'user size rows preserved');
  assert.ok(care.info.text.includes('내가 쓴 케어 문구'), 'user care text preserved');
  assert.ok(size.elements.some((e) => e.type === 'text' && e.text === '65'), 'user value rendered');
});

test('care anchor above size does not scramble flow order (cursor never moves backward)', () => {
  const doc = [baseDoc()[0], baseDoc()[2], baseDoc()[1], baseDoc()[3]]; // cut, care, size, ai-notice
  const { blocks } = applyInfoTemplate(doc, CTX, seqId());
  const kinds = blocks.map((b) => `${b.kind}${b.infoType ? ':' + b.infoType : ''}`);
  assert.deepEqual(kinds, [
    'info:shipping_returns', 'info:header',
    'benefit',
    'care',                                       // 사용자가 올려둔 위치 유지 (제자리 강화)
    'info:benefit_copy', 'size', 'info:required_notice',
    'ai-notice',
  ]);
});

test('feature icons keep raw form state — empty slots survive, placeholder never persisted', () => {
  const partial = buildInfoBlock('feature_icons', { items: [{ title: 'A', desc: '' }, { title: '', desc: '' }, { title: '', desc: '' }] }, CTX, seqId());
  assert.equal(partial.info.items.length, 3, 'raw padded items preserved');
  assert.deepEqual(partial.info.items[1], { title: '', desc: '', src: null }, 'empty slot survives round-trip');
  const empty = buildInfoBlock('feature_icons', { items: [] }, CTX, seqId());
  assert.ok(!empty.info.items.some((it) => it.title.includes('핵심 장점')), 'placeholder not stored as content');
  assert.ok(empty.elements.some((e) => e.type === 'text' && e.text.includes('핵심 장점')), 'placeholder still rendered');
});

test('feature icons are photo cards clamped to 2~5 points', () => {
  const two = buildInfoBlock('feature_icons', { items: [{ title: 'A' }, { title: 'B' }] }, CTX, seqId());
  assert.equal(two.elements.filter((e) => e.type === 'image').length, 2, 'one circular photo slot per point');
  assert.ok(two.elements.filter((e) => e.type === 'image').every((e) => e.radius === e.w / 2), 'slots are circles');
  const seven = buildInfoBlock('feature_icons', { items: Array.from({ length: 7 }, (_x, i) => ({ title: `P${i}` })) }, CTX, seqId());
  assert.equal(seven.info.items.length, 5, 'max 5 points');
  const one = buildInfoBlock('feature_icons', { items: [{ title: 'only' }] }, CTX, seqId());
  assert.equal(one.info.items.length, 2, 'min 2 points (padded)');
  const withSrc = buildInfoBlock('feature_icons', { items: [{ title: 'A', src: '/img.jpg' }, { title: 'B' }] }, CTX, seqId());
  assert.equal(withSrc.elements.find((e) => e.type === 'image').src, '/img.jpg', 'stored src rendered into slot');
});

test('carrySlotImages carries photos by ordinal — photo stays on its own point', () => {
  const prev = buildInfoBlock('feature_icons', { items: [{ title: 'A' }, { title: 'B' }, { title: 'C' }] }, CTX, seqId());
  // 3번 포인트 슬롯만 채움 → 재생성 후에도 3번 슬롯에 있어야 한다 (압축 채움이면 1번으로 이사)
  const imgIds = prev.elements.filter((e) => e.type === 'image').map((e) => e.id);
  const filled = { ...prev, elements: prev.elements.map((e) => (e.id === imgIds[2] ? { ...e, src: '/point3.jpg' } : e)) };
  const rebuilt = buildInfoBlock('feature_icons', filled.info, CTX, seqId());
  const merged = carrySlotImages(filled.elements, rebuilt);
  const slots = merged.elements.filter((e) => e.type === 'image');
  assert.equal(slots[0].src, null, 'point 1 stays empty');
  assert.equal(slots[2].src, '/point3.jpg', 'point 3 keeps its photo');
  // 템플릿 재적용 경로: size 실측도 슬롯 사진 보존
  const doc = baseDoc();
  doc[1] = { ...doc[1], info: { ...defaultInfoFor('size_table', CTX), withDiagram: true },
    elements: [{ id: 'd1', type: 'image', x: 300, y: 400, w: 400, h: 300, src: '/diagram.png', radius: 8 }] };
  const { blocks } = applyInfoTemplate(doc, CTX, seqId());
  const size = blocks.find((b) => b.kind === 'size');
  assert.equal(size.elements.find((e) => e.type === 'image').src, '/diagram.png', 'diagram photo carried across template re-apply');
});

test('applySlotFillToInfo writes slot photos into info so rebuilds restore them exactly', () => {
  const block = buildInfoBlock('feature_icons', { items: [{ title: 'A' }, { title: 'B' }, { title: 'C' }] }, CTX, seqId());
  const imgIds = block.elements.filter((e) => e.type === 'image').map((e) => e.id);
  const synced = applySlotFillToInfo(block, imgIds[2], { src: '/p3.jpg', cutType: 'product' });
  assert.equal(synced.info.items[2].src, '/p3.jpg', 'info index matches slot ordinal');
  assert.equal(synced.info.items[0].src, null);
  const rebuilt = buildInfoBlock('feature_icons', synced.info, CTX, seqId());
  assert.equal(rebuilt.elements.filter((e) => e.type === 'image')[2].src, '/p3.jpg', 'rebuild from info restores the right point');
  // size 실측도: 토글 off→on 왕복에도 info.diagramSrc 로 사진 복원
  const size = buildInfoBlock('size_table', { ...defaultInfoFor('size_table', CTX), withDiagram: true }, CTX, seqId());
  const slotId = size.elements.find((e) => e.type === 'image').id;
  const sizeSynced = applySlotFillToInfo(size, slotId, { src: '/diagram.png' });
  assert.equal(sizeSynced.info.diagramSrc, '/diagram.png');
  const off = buildInfoBlock('size_table', { ...sizeSynced.info, withDiagram: false }, CTX, seqId());
  assert.ok(!off.elements.some((e) => e.type === 'image'), 'diagram slot removed while off');
  const backOn = buildInfoBlock('size_table', { ...off.info, withDiagram: true }, CTX, seqId());
  assert.equal(backOn.elements.find((e) => e.type === 'image').src, '/diagram.png', 'photo survives off→on round trip');
});

test('model info prefills from the model actually used by the project', () => {
  const withModel = { ...CTX, selectedModel: { name: '모델 A', thumb: '/models/women/w1.webp' } };
  const info = defaultInfoFor('model_info', withModel);
  assert.equal(info.models[0].name, '모델 A');
  assert.equal(info.models[0].src, '/models/women/w1.webp');
  const block = buildInfoBlock('model_info', info, withModel, seqId());
  assert.equal(block.elements.find((e) => e.type === 'image').src, '/models/women/w1.webp', 'model photo rendered into card slot');
  const bare = defaultInfoFor('model_info', CTX);
  assert.equal(bare.models[0].name, 'MODEL A', 'fallback without selected model');
});

test('careFamilyFor does not false-match 모달/기모 as knit', () => {
  assert.equal(careFamilyFor([{ name: '모달', ratio: 100 }]), 'poly');
  assert.notEqual(careFamilyFor([{ name: '기모 원단' }]), 'knit');
  assert.equal(careFamilyFor([{ name: '캐시미어' }]), 'knit');
});

test('long unbroken text grows block height (wrap-aware estimation)', () => {
  const short = buildInfoBlock('policy', { sections: [{ title: '배송', body: '짧은 문구' }] }, CTX, seqId());
  const long = buildInfoBlock('policy', { sections: [{ title: '배송', body: '아'.repeat(200) }] }, CTX, seqId());
  assert.ok(long.h > short.h + 40, `wrapped single line grows height (${short.h} → ${long.h})`);
});

test('template on a doc without anchors appends the flow before ai-notice in order', () => {
  const doc = [baseDoc()[0], baseDoc()[3]];
  const { blocks } = applyInfoTemplate(doc, CTX, seqId());
  const kinds = blocks.map((b) => `${b.kind}${b.infoType ? ':' + b.infoType : ''}`);
  assert.deepEqual(kinds, [
    'info:shipping_returns', 'info:header',
    'benefit',
    'info:benefit_copy', 'size', 'care', 'info:required_notice',
    'ai-notice',
  ]);
});
