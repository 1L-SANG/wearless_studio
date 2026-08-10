import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CARE_LABEL_SENTENCE,
  DEFAULT_INFO_TEMPLATE,
  FEATURE_ITEMS_MAX,
  FEATURE_LAYOUTS,
  INFO_PRESET_TYPES,
  NEEDS_INPUT,
  applyInfoTemplate,
  applySlotFillToInfo,
  buildInfoBlock,
  careFamilyFor,
  carrySlotImages,
  defaultInfoFor,
  fillFeatureCopy,
  needsDefaultTemplate,
  presetTypeOf,
  resolveFeatureLayout,
} from '../../src/features/editor/presets/infoPresets.js';
import { SELLING_POINTS_MAX } from '../../src/features/analysis/sellingPoints.js';
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

test('needsDefaultTemplate gates auto-apply to untouched assembler docs only', () => {
  // 생성 직후 기본 문서(옛 자동 size/care, info 없음) → 적용 대상
  assert.equal(needsDefaultTemplate(baseDoc()), true);
  // 이미 템플릿이 깔린 문서 → 재적용 금지
  const templated = applyInfoTemplate(baseDoc(), CTX, seqId()).blocks;
  assert.equal(needsDefaultTemplate(templated), false);
  // info 블록은 다 지웠지만 size/care 가 폼으로 강화된 문서(사용자 손댐) → 금지
  const enriched = templated.filter((b) => b.kind !== 'info');
  assert.equal(needsDefaultTemplate(enriched), false);
  // 빈 문서/size·care 없는 문서 → 금지
  assert.equal(needsDefaultTemplate([]), false);
  assert.equal(needsDefaultTemplate([baseDoc()[0], baseDoc()[3]]), false);
});

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

test('feature icons are photo cards clamped to the min/max point count', () => {
  const two = buildInfoBlock('feature_icons', { items: [{ title: 'A' }, { title: 'B' }] }, CTX, seqId());
  assert.equal(two.elements.filter((e) => e.type === 'image').length, 2, 'one circular photo slot per point');
  assert.ok(two.elements.filter((e) => e.type === 'image').every((e) => e.radius === e.w / 2), 'slots are circles');
  const tooMany = buildInfoBlock('feature_icons', { items: Array.from({ length: FEATURE_ITEMS_MAX + 2 }, (_x, i) => ({ title: `P${i}` })) }, CTX, seqId());
  assert.equal(tooMany.info.items.length, FEATURE_ITEMS_MAX, `max ${FEATURE_ITEMS_MAX} points`);
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

const FEATURE_CTX = {
  ...CTX,
  sellingPoints: ['하이웨이스트 디자인', '섬세한 지퍼 디테일', '플리츠 안감 마감'],
};

test('feature layout falls back to compact when info.layout is missing or unknown', () => {
  assert.equal(resolveFeatureLayout({ items: [] }), 'compact');
  assert.equal(resolveFeatureLayout({ items: [], layout: null }), 'compact');
  assert.equal(resolveFeatureLayout({ items: [], layout: 'nope' }), 'compact');
  assert.equal(resolveFeatureLayout({ items: [], layout: 'stack' }), 'stack');
});

test('legacy feature block without layout renders byte-identical to explicit compact', () => {
  const items = [
    { title: '하이웨이스트 디자인', desc: '허리선이 높아 다리가 더 길어 보입니다.', src: null },
    { title: '섬세한 지퍼 디테일', desc: '', src: null },
  ];
  const legacy = buildInfoBlock('feature_icons', { items }, FEATURE_CTX, seqId());
  const explicit = buildInfoBlock('feature_icons', { items, layout: 'compact' }, FEATURE_CTX, seqId());
  assert.deepEqual(legacy.elements, explicit.elements);
  assert.equal(legacy.h, explicit.h);
});

test('FEATURE_LAYOUTS lists exactly the four supported layouts', () => {
  assert.deepEqual(FEATURE_LAYOUTS.map((l) => l.value), ['stack', 'center', 'grid', 'compact']);
  for (const l of FEATURE_LAYOUTS) assert.ok(l.label, `${l.value}: has label`);
});

/* 요소가 서로 겹치지 않고 블록 높이 안에 들어오는지 — 레이아웃 회귀의 1차 방어선.
   같은 행에 나란히 놓이는 요소(그리드의 사진|카드)가 있어 y 단조가 아니라
   "선언 높이가 마지막 요소 하단을 덮는가" 로 본다. */
function assertFitsInBlock(block, label) {
  const bottom = Math.max(...block.elements.map((el) => el.y + (el.h || 0)));
  assert.ok(block.h >= bottom, `${label}: block h ${block.h} covers last element bottom ${bottom}`);
  for (const el of block.elements) {
    assert.ok(el.x >= 60, `${label}: element x ${el.x} inside left margin`);
    assert.ok(el.x + (el.w || 0) <= 940, `${label}: element right ${el.x + (el.w || 0)} inside right margin`);
  }
}

const THREE_POINTS = [
  { title: '하이웨이스트 디자인', desc: '허리선이 높아 다리가 더 길어 보입니다.', src: null },
  { title: '섬세한 지퍼 디테일', desc: '뒤 중심에 지퍼를 달아 여미면 실루엣이 흐트러지지 않습니다.', src: null },
  { title: '플리츠 안감 마감', desc: '안감을 덧대 겉감의 라인이 곱게 잡힙니다.', src: null },
];

test('stack layout renders a heading, one image slot per point, and fits its height', () => {
  const block = buildInfoBlock('feature_icons', { layout: 'stack', items: THREE_POINTS }, FEATURE_CTX, seqId());
  assert.equal(block.info.layout, 'stack');
  const heads = block.elements.filter((el) => el.type === 'text' && el.text === 'DETAIL POINT');
  assert.equal(heads.length, 1, 'exactly one DETAIL POINT heading');
  const slots = block.elements.filter((el) => el.type === 'image');
  assert.equal(slots.length, 3, 'one image slot per point');
  for (const s of slots) assert.equal(s.w, 880, 'stack image spans the content width');
  for (const it of THREE_POINTS) {
    assert.ok(block.elements.some((el) => el.text === it.title), `title rendered: ${it.title}`);
    assert.ok(block.elements.some((el) => el.text === it.desc), `desc rendered: ${it.desc}`);
  }
  assertFitsInBlock(block, 'stack');
});

test('stack layout grows its height with a long description instead of overlapping', () => {
  const long = '안감을 덧대 겉감의 라인이 곱게 잡힙니다. '.repeat(6);
  const shortBlock = buildInfoBlock('feature_icons', { layout: 'stack', items: [{ title: 'A', desc: '짧습니다.', src: null }, { title: 'B', desc: '', src: null }] }, FEATURE_CTX, seqId());
  const longBlock = buildInfoBlock('feature_icons', { layout: 'stack', items: [{ title: 'A', desc: long, src: null }, { title: 'B', desc: '', src: null }] }, FEATURE_CTX, seqId());
  assert.ok(longBlock.h > shortBlock.h, 'long description makes the block taller');
  assertFitsInBlock(longBlock, 'stack/long');
});

test('stack layout omits the description element when a point has none', () => {
  const block = buildInfoBlock('feature_icons', { layout: 'stack', items: [{ title: 'A', desc: '', src: null }, { title: 'B', desc: '', src: null }] }, FEATURE_CTX, seqId());
  const texts = block.elements.filter((el) => el.type === 'text').map((el) => el.text);
  assert.deepEqual(texts, ['DETAIL POINT', 'A', 'B']);
});

test('center layout numbers each point with a zero-padded badge', () => {
  const block = buildInfoBlock('feature_icons', { layout: 'center', items: THREE_POINTS }, FEATURE_CTX, seqId());
  const badges = block.elements.filter((el) => el.type === 'text' && String(el.text).startsWith('DETAIL POINT '));
  assert.deepEqual(badges.map((el) => el.text), ['DETAIL POINT 01', 'DETAIL POINT 02', 'DETAIL POINT 03']);
  for (const b of badges) assert.equal(b.style.align, 'center', 'badge text centered');
  const plates = block.elements.filter((el) => el.type === 'shape');
  assert.equal(plates.length, 3, 'one badge plate per point');
  assertFitsInBlock(block, 'center');
});

test('center layout centers title and description', () => {
  const block = buildInfoBlock('feature_icons', { layout: 'center', items: THREE_POINTS }, FEATURE_CTX, seqId());
  for (const it of THREE_POINTS) {
    const title = block.elements.find((el) => el.text === it.title);
    const desc = block.elements.find((el) => el.text === it.desc);
    assert.equal(title.style.align, 'center', `title centered: ${it.title}`);
    assert.equal(desc.style.align, 'center', `desc centered: ${it.title}`);
  }
});

test('grid layout pairs each photo with a numbered card and skips descriptions', () => {
  const block = buildInfoBlock('feature_icons', { layout: 'grid', items: THREE_POINTS }, FEATURE_CTX, seqId());
  const slots = block.elements.filter((el) => el.type === 'image');
  const cards = block.elements.filter((el) => el.type === 'shape');
  const rules = block.elements.filter((el) => el.type === 'line');
  assert.equal(slots.length, 3, 'one photo per point');
  assert.equal(cards.length, 3, 'one card per point');
  assert.equal(rules.length, 3, 'one underline per point');
  for (const s of slots) { assert.equal(s.w, 400); assert.equal(s.h, 400); }
  const numbers = block.elements.filter((el) => el.type === 'text' && /^\d\d$/.test(String(el.text)));
  assert.deepEqual(numbers.map((el) => el.text), ['01', '02', '03']);
  for (const it of THREE_POINTS) {
    assert.ok(block.elements.some((el) => el.text === it.title), `title rendered: ${it.title}`);
    assert.ok(!block.elements.some((el) => el.text === it.desc), `desc NOT rendered: ${it.title}`);
  }
  assertFitsInBlock(block, 'grid');
});

test('grid layout keeps descriptions in info so switching back restores them', () => {
  const grid = buildInfoBlock('feature_icons', { layout: 'grid', items: THREE_POINTS }, FEATURE_CTX, seqId());
  assert.deepEqual(grid.info.items.map((it) => it.desc), THREE_POINTS.map((it) => it.desc));
  const back = buildInfoBlock('feature_icons', { ...grid.info, layout: 'stack' }, FEATURE_CTX, seqId());
  for (const it of THREE_POINTS) {
    assert.ok(back.elements.some((el) => el.text === it.desc), `desc restored: ${it.title}`);
  }
});

test('slot photos carry by ordinal across every feature layout', () => {
  const withPhotos = THREE_POINTS.map((it, i) => ({ ...it, src: `https://cdn.example/p${i + 1}.jpg` }));
  for (const { value } of FEATURE_LAYOUTS) {
    const built = buildInfoBlock('feature_icons', { layout: value, items: withPhotos }, FEATURE_CTX, seqId());
    const srcs = built.elements.filter((el) => el.type === 'image').map((el) => el.src);
    assert.deepEqual(srcs, withPhotos.map((it) => it.src), `${value}: photos in item order`);

    // 슬롯을 캔버스에서 채우면 elements 와 info 가 함께 갱신된다(재생성 후에도 연결 유지)
    const blank = buildInfoBlock('feature_icons', { layout: value, items: THREE_POINTS }, FEATURE_CTX, seqId());
    const third = blank.elements.filter((el) => el.type === 'image')[2];
    const filled = applySlotFillToInfo(blank, third.id, { src: 'https://cdn.example/third.jpg' });
    assert.equal(filled.info.items[2].src, 'https://cdn.example/third.jpg', `${value}: info updated at the same ordinal`);
    assert.equal(filled.info.items[0].src, null, `${value}: other ordinals untouched`);

    // 재생성 시 이전 elements 의 사진은 같은 서수로만 이월된다
    const carried = carrySlotImages(filled.elements, buildInfoBlock('feature_icons', { layout: value, items: THREE_POINTS }, FEATURE_CTX, seqId()));
    const carriedSrcs = carried.elements.filter((el) => el.type === 'image').map((el) => el.src);
    assert.deepEqual(carriedSrcs, [null, null, 'https://cdn.example/third.jpg'], `${value}: carried by ordinal`);
  }
});

test('every feature layout label is distinct and non-empty for the chip row', () => {
  const labels = FEATURE_LAYOUTS.map((l) => l.label);
  assert.equal(new Set(labels).size, labels.length, 'labels are distinct');
  for (const l of labels) assert.ok(l.trim().length > 0, 'label is non-empty');
});

test('switching layout through the form state preserves every item field', () => {
  const info = { layout: 'stack', items: THREE_POINTS };
  for (const { value } of FEATURE_LAYOUTS) {
    const next = { ...info, layout: value };
    const block = buildInfoBlock('feature_icons', next, FEATURE_CTX, seqId());
    assert.equal(block.info.layout, value, `${value}: layout stored`);
    assert.deepEqual(block.info.items, THREE_POINTS, `${value}: items untouched`);
  }
});

test('feature point defaults pull descriptions from the analysis feature copy', () => {
  const ctx = {
    ...CTX,
    sellingPoints: ['하이웨이스트 디자인', '카고 포켓', '직접 쓴 특징'],
    featureCopy: [
      { point: '하이웨이스트 디자인', desc: '허리선이 높아 다리가 더 길어 보입니다.' },
      { point: '카고 포켓', desc: '측면 카고 포켓이 밋밋함을 덜어냅니다.' },
    ],
  };
  const info = defaultInfoFor('feature_icons', ctx);
  assert.equal(info.layout, 'stack', 'new blocks default to the stacked layout');
  assert.deepEqual(info.items.map((it) => it.title), ['하이웨이스트 디자인', '카고 포켓', '직접 쓴 특징']);
  assert.deepEqual(info.items.map((it) => it.desc), [
    '허리선이 높아 다리가 더 길어 보입니다.',
    '측면 카고 포켓이 밋밋함을 덜어냅니다.',
    '',
  ]);
});

test('feature point defaults survive a missing feature copy', () => {
  const info = defaultInfoFor('feature_icons', { ...CTX, sellingPoints: ['A'], featureCopy: undefined });
  assert.equal(info.layout, 'stack');
  assert.deepEqual(info.items.map((it) => it.desc), ['', '', '']);
});

/* 블록이 한 번 지어지면 정보 템플릿은 다시 깔리지 않는다. 그래서 잡의 featureCopy 쓰기보다
   앞선 analysis 스냅샷으로 지어진 블록은 설명이 영구히 빈칸으로 남았다 — 폼을 열 때 다시
   채우는 경로가 그 구멍을 타이밍과 무관하게 막는다. */
const FEATURE_COPY_CTX = {
  ...CTX,
  featureCopy: [
    { point: '잔 스트라이프 패턴', desc: '얇은 줄무늬가 촘촘하게 들어가 있어 시각적으로 슬림해 보입니다.' },
    { point: '세미 크롭 기장', desc: '기장이 짧아 하의 허리선이 드러납니다.' },
  ],
};

test('fillFeatureCopy fills blank descriptions on a block built before the copy existed', () => {
  const stale = { layout: 'center', items: [
    { title: '잔 스트라이프 패턴', desc: '', src: null },
    { title: '세미 크롭 기장', desc: '', src: null },
    { title: '', desc: '', src: null },
  ] };
  const filled = fillFeatureCopy(stale, FEATURE_COPY_CTX);
  assert.deepEqual(filled.items.map((it) => it.desc), [
    '얇은 줄무늬가 촘촘하게 들어가 있어 시각적으로 슬림해 보입니다.',
    '기장이 짧아 하의 허리선이 드러납니다.',
    '',
  ]);
  assert.equal(filled.layout, 'center', 'layout untouched');
  assert.deepEqual(stale.items.map((it) => it.desc), ['', '', ''], 'input not mutated');
});

test('fillFeatureCopy never overwrites a description the seller wrote', () => {
  const edited = { layout: 'stack', items: [
    { title: '잔 스트라이프 패턴', desc: '셀러가 직접 쓴 문장입니다.', src: null },
    { title: '세미 크롭 기장', desc: '', src: null },
  ] };
  const filled = fillFeatureCopy(edited, FEATURE_COPY_CTX);
  assert.deepEqual(filled.items.map((it) => it.desc), [
    '셀러가 직접 쓴 문장입니다.',
    '기장이 짧아 하의 허리선이 드러납니다.',
  ]);
});

test('fillFeatureCopy is a no-op without feature copy or items', () => {
  const info = { layout: 'stack', items: [{ title: 'A', desc: '', src: null }] };
  assert.equal(fillFeatureCopy(info, { ...CTX, featureCopy: [] }), info, 'same reference when nothing to fill');
  assert.equal(fillFeatureCopy(info, {}), info, 'same reference when ctx has no featureCopy');
  assert.deepEqual(fillFeatureCopy({ layout: 'stack' }, FEATURE_COPY_CTX), { layout: 'stack' }, 'tolerates a block with no items array');
});

test('stack and center titles and descriptions are sized for the reference proportions', () => {
  for (const layout of ['stack', 'center']) {
    const block = buildInfoBlock('feature_icons', { layout, items: THREE_POINTS }, FEATURE_CTX, seqId());
    const title = block.elements.find((el) => el.text === THREE_POINTS[0].title);
    const desc = block.elements.find((el) => el.text === THREE_POINTS[0].desc);
    assert.equal(title.style.size, 34, `${layout}: title size`);
    assert.equal(desc.style.size, 19, `${layout}: desc size`);
    assert.equal(desc.style.lineHeight, 32, `${layout}: desc line height follows the font size`);
    // 제목 상자가 글자보다 작으면 다음 요소를 덮는다 (렌더는 width 고정·height auto)
    assert.ok(title.h >= title.style.size, `${layout}: title box not shorter than its glyphs`);
    assertFitsInBlock(block, `${layout}/typography`);
  }
});

test('every layout still fits its block at the raised point cap', () => {
  const eight = Array.from({ length: FEATURE_ITEMS_MAX }, (_, i) => ({
    title: `포인트 ${i + 1}`, desc: '구조를 살린 설명 한 줄입니다.', src: null,
  }));
  assert.equal(eight.length, 8, 'cap is 8');
  for (const { value } of FEATURE_LAYOUTS) {
    const block = buildInfoBlock('feature_icons', { layout: value, items: eight }, FEATURE_CTX, seqId());
    assert.equal(block.info.items.length, 8, `${value}: keeps all eight`);
    assert.equal(block.elements.filter((el) => el.type === 'image').length, 8, `${value}: one photo slot each`);
    assertFitsInBlock(block, `${value}/eight`);
  }
});

test('compact keeps its old title sizes and shrinks only past the point counts it used to allow', () => {
  const sizeAt = (n) => {
    const items = Array.from({ length: n }, (_, i) => ({ title: `포인트 ${i + 1}`, desc: '', src: null }));
    const block = buildInfoBlock('feature_icons', { layout: 'compact', items }, FEATURE_CTX, seqId());
    return block.elements.find((el) => el.text === '포인트 1').style.size;
  };
  assert.equal(sizeAt(2), 17, '2개 — 종전과 동일');
  assert.equal(sizeAt(4), 17, '4개 — 종전과 동일');
  assert.equal(sizeAt(5), 15, '5개 — 종전과 동일');
  assert.equal(sizeAt(8), 13, '8개 — 칸이 좁아져 한 단계 더 내려간다');
});

test('the block cap does not truncate below what the analysis chips can supply', () => {
  // 칩 상한이 블록 상한보다 크면 뒤쪽 특징이 조용히 잘린다
  assert.ok(FEATURE_ITEMS_MAX >= SELLING_POINTS_MAX,
    `block cap ${FEATURE_ITEMS_MAX} must hold every chip (${SELLING_POINTS_MAX})`);
});
