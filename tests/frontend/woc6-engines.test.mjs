import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { assembleStoryboardPreview } from '../../src/features/storyboard/storyboardPreview.js';

const product = { name: '셔츠', clothingType: 'top', colors: [{ id: 'base', isBase: true, name: '블랙' }, { id: 'other', name: '화이트' }] };
const photos = blocks => blocks.flatMap(block => block.elements).filter(el => el.type === 'image');
const design = (layout, kind = 'wear') => ({ id: 'WOC6-ENGINE-TEST', kind, padding: 60, top: 0, bottom: 0, layout,
  overflow: { type: 'photoStack', aspect: 'source', gap: 40 } });
function inputFor(count = 1, kind = 'wear') {
  return { product, storyboard: Array.from({ length: count }, (_, i) => ({ id: `photo-${i}`, taxonomyVersion: 4,
    source: 'ai', cutType: kind === 'wear' ? 'styling' : 'product', shot: kind === 'wear' ? 'full' : 'detail', direction: ['front', 'side', 'back'][i % 3],
    pageSectionKey: `emo.${kind === 'wear' ? 'wear_styling' : 'points'}`, pageSectionKind: kind, pageSectionUsage: 'styling',
    pageDesignId: 'WOC6-ENGINE-TEST', colorId: 'base', slotKey: `${kind === 'wear' ? 'wear_styling' : 'points'}.extra${i + 1}` })), cutResults: [] };
}
function assemble(custom, input) {
  const previous = catalog.designs[custom.id];
  catalog.designs[custom.id] = custom;
  try { return assemblePage(input); } finally {
    if (previous) catalog.designs[custom.id] = previous; else delete catalog.designs[custom.id];
  }
}
const flow = (pattern, groupBy = 'spaceGroup') => design([{ type: 'photoFlow', slot: '*', pattern, groupBy, aspect: '4/5', gap: 24,
  captions: [{ type: 'text', copyRole: 'cap.{direction}' }, { type: 'text', copyRole: 'colorName.n' }] }]);
const naturalPhoto = { type: 'photo', slot: '$current', fit: 'contain', aspect: 'source', fallbackAspect: '2/3' };
const natural = design([{ type: 'photoStack', slot: '*', items: [naturalPhoto] }], 'points');
const parityCases = [];

for (const [pattern, sizes] of [
  ['twoColumn', [[1], [2], [2, 1], [2, 2], [2, 2, 1], [2, 2, 2], [2, 2, 2, 1], [2, 2, 2, 2]]],
  ['heroPair', [[1], [2], [1, 2], [1, 2, 1], [1, 2, 2], [1, 2, 1, 2], [1, 2, 1, 2, 1], [1, 2, 1, 2, 2]]],
]) test(`${pattern} handles every one-through-eight-photo tail without dropping a cut`, () => {
  sizes.forEach((expected, index) => {
    const input = inputFor(index + 1), custom = flow(pattern);
    const blocks = assemble(custom, input).filter(block => block.pageSectionKey);
    assert.deepEqual(blocks.map(block => photos([block]).length), expected);
    assert.deepEqual(photos(blocks).map(el => el.sourceBlockId), input.storyboard.map(card => card.id));
    parityCases.push({ design: custom, input });
  });
});

for (const [pattern, expected] of [
  ['twoColumn', [2, 2, 1, 2, 1]], ['heroPair', [1, 2, 2, 1, 2]],
]) test(`${pattern} retains the ordered 5+3 space sets and full-width singleton tails`, () => {
  const input = inputFor(8), custom = flow(pattern);
  input.storyboard.forEach((card, i) => { card.spaceGroupId = i < 5 ? 'set-a' : 'set-b'; });
  const blocks = assemble(custom, input).filter(block => block.pageSectionKey);
  assert.deepEqual(blocks.map(block => photos([block]).length), expected);
  assert.deepEqual(photos(blocks).map(el => el.sourceBlockId), input.storyboard.map(card => card.id));
  assert.deepEqual(photos(blocks).map(el => el.slotKey), Array.from({ length: 8 }, (_, i) => `wear_styling.extra${i + 1}`));
  assert.deepEqual(blocks.flatMap(block => block.elements).filter(el => el.copyRole?.startsWith('colorName.')).map(el => el.copyRole),
    Array.from({ length: 8 }, (_, i) => `colorName.${i}`));
  assert.deepEqual(blocks.flatMap(block => block.elements).filter(el => el.copyRole?.startsWith('cap.')).map(el => el.text),
    ['Front', 'Side', 'Back', 'Front', 'Side', 'Back', 'Front', 'Side']);
  for (const block of blocks) for (const image of photos([block])) assert.equal(image.w, photos([block]).length === 1 ? 880 : 428);
  parityCases.push({ design: custom, input });
});

test('flow restarts on loose color boundaries and disjoint repeated space IDs without merging or reordering', () => {
  const input = inputFor(9), custom = flow('heroPair');
  input.storyboard.forEach((card, i) => Object.assign(card,
    [{ spaceGroupId: 'a' }, { spaceGroupId: 'a' }, { spaceGroupId: 'b' }, { spaceGroupId: 'a' },
      { colorId: 'base' }, { colorId: 'base' }, { colorId: 'other' }, { colorId: 'other' }, { colorId: 'base' }][i]));
  const blocks = assemble(custom, input).filter(block => block.pageSectionKey);
  assert.deepEqual(blocks.map(block => photos([block]).map(el => el.sourceBlockId)),
    [['photo-0', 'photo-1'], ['photo-2'], ['photo-3'], ['photo-4', 'photo-5'], ['photo-6', 'photo-7'], ['photo-8']]);
  parityCases.push({ design: custom, input });
  const ungrouped = flow('heroPair', 'none');
  assert.deepEqual(assemble(ungrouped, input).filter(block => block.pageSectionKey).map(block => photos([block]).length), [1, 2, 1, 2, 1, 2]);
  parityCases.push({ design: ungrouped, input });
});

for (const [label, metadata, height] of [
  ['known portrait', { width: 600, height: 900 }, 1320],
  ['wide source has no minimum-height crop', { width: 6000, height: 300 }, 44],
  ['thin positive source remains visible at one pixel', { width: 6000, height: 1 }, 1],
  ['tall source has no maximum-height crop', { width: 300, height: 6000 }, 17600],
  ['unknown thumbnail estimate uses fallback', { width: 1024, height: 1365, dimensionsKnown: false }, 1320],
  ['missing source uses fallback', {}, 1320],
  ['nonpositive source uses fallback', { width: -1, height: 900 }, 1320],
]) test(`natural photo: ${label}`, () => {
  const input = inputFor(1, 'points');
  input.cutResults = [{ blockId: 'photo-0', imageUrl: '/detail.webp', ...metadata }];
  const custom = structuredClone(natural); custom.layout[0].items[0].cropTop = .2;
  const image = photos(assemble(custom, input))[0];
  assert.deepEqual([image.w, image.h, image.src], [880, height, '/detail.webp']);
  assert.equal(image.crop, undefined);
  parityCases.push({ design: custom, input });
});

test('natural card photo uses its available width and retains item copy exactly once', () => {
  const custom = design([{ type: 'photoStack', slot: '*', items: [{ type: 'panel', padding: [24, 20], fill: 'panel', nodes: [naturalPhoto,
    { type: 'text', copyRole: 'items.n.name' }, { type: 'text', copyRole: 'items.n.desc' }] }] }], 'points');
  const input = inputFor(2, 'points');
  input.cutResults = input.storyboard.map(card => ({ blockId: card.id, imageUrl: `/${card.id}.webp`, width: 600, height: 900 }));
  input.copyResults = [{ blockId: 'photo-0', texts: [{ role: 'items.0.desc', text: '첫 설명' }, { role: 'items.1.desc', text: '둘 설명' }] }];
  const blocks = assemble(custom, input);
  assert.deepEqual(photos(blocks).map(el => [el.w, el.h]), [[840, 1260], [840, 1260]]);
  assert.deepEqual(blocks.flatMap(block => block.elements).filter(el => /^items\.\d+\.desc$/.test(el.copyRole)).map(el => el.text), ['첫 설명', '둘 설명']);
  parityCases.push({ design: custom, input });
});

test('natural worn crop uses ROI aspect and preserves its native-pixel cap', () => {
  const input = inputFor(1), source = input.storyboard[0];
  source.pageDesignId = 'P02-D01';
  input.storyboard.push({ id: 'points', source: 'text', taxonomyVersion: 4, pageSectionKey: 'emo.points', pageSectionKind: 'points',
    pageDesignId: natural.id, pointItems: [{ kind: 'wornCrop', sourceBlockId: source.id, bodyPart: 'shoulder', label: '어깨', cropRegion: [.2, .2, .4, .2] }] });
  input.cutResults = [{ blockId: source.id, imageUrl: '/source.webp', width: 600, height: 900 }];
  const image = photos(assemble(natural, input)).find(el => el.pointItemKind === 'wornCrop');
  assert.deepEqual([image.w, image.h], [880, 660]);
  assert.deepEqual(image.crop, { iw: 600, ih: 900, ox: 0, oy: 0 });
  assert.deepEqual(input.storyboard[1].pointItems[0].cropRegion, [.2, .2, .4, .2]);
  parityCases.push({ design: natural, input });
  const unknown = structuredClone(input);
  Object.assign(unknown.cutResults[0], { width: 1024, height: 1365, dimensionsKnown: false });
  const estimated = photos(assemble(natural, unknown)).find(el => el.pointItemKind === 'wornCrop');
  assert.deepEqual([estimated.w, estimated.h], [880, 660]);
  assert.deepEqual(estimated.crop, { iw: 2200, ih: 3300, ox: 440, oy: 660 });
  parityCases.push({ design: natural, input: unknown });
});

test('legacy source photo remains clamped and keeps cropTop', () => {
  const custom = design([{ type: 'photoStack', slot: '*', items: [{ type: 'photo', slot: '$current', aspect: 'source', cropTop: .2 }] }]);
  const input = inputFor(); input.cutResults = [{ blockId: 'photo-0', imageUrl: '/legacy.webp', width: 300, height: 6000 }];
  const image = photos(assemble(custom, input))[0];
  assert.equal(image.h, 2400); assert.ok(image.crop);
  parityCases.push({ design: custom, input });
});

test('completed generated details retain the exact normal assembly in storyboard preview', () => {
  const input = inputFor(1, 'points'); input.cutResults = [{ blockId: 'photo-0', imageUrl: '/generated.webp', width: 1200, height: 400 }];
  catalog.designs[natural.id] = natural;
  try { assert.deepEqual(assembleStoryboardPreview(input), assemblePage(input)); } finally { delete catalog.designs[natural.id]; }
});

test('natural detail preview still displays its selected source ROI without persisting preview crop', () => {
  const input = inputFor(1, 'points');
  input.storyboard[0].detailTargetId = 'button';
  input.product = { ...product, colors: [{ id: 'base', isBase: true, images: [{ slot: 'Front', src: '/original.webp', width: 3000, height: 4500 }] }] };
  input.analysis = { detailRecommendations: { status: 'ready', candidates: [{ id: 'button', label: '단추', sourceIndex: 0,
    sourceSlot: 'Front', region: { x: .2, y: .3, w: .4, h: .4 } }] } };
  const before = structuredClone(input);
  catalog.designs[natural.id] = natural;
  try {
    const image = photos(assembleStoryboardPreview(input))[0];
    assert.equal(image.src, '/original.webp');
    assert.equal(image.previewDetailCrop, true);
    assert.deepEqual(image.crop, { iw: 2200, ih: 3300, ox: 440, oy: 990 });
    assert.deepEqual([image.w, image.h], [880, 1320]);
    const actual = photos(assemblePage({ ...input, cutResults: [{ blockId: 'photo-0', imageUrl: '/detail.webp', width: 600, height: 900 }] }))[0];
    assert.equal(actual.crop, undefined);
    assert.equal(actual.previewDetailCrop, undefined);
    assert.deepEqual(input, before);
  } finally { delete catalog.designs[natural.id]; }
});

test('catalog studio layouts preserve every direction label and photo ID', () => {
  for (const id of ['WEAR-FIT-COLUMN-01', 'WEAR-FIT-2COL-01', 'WEAR-FIT-HERO-01']) {
    const input = inputFor(3);
    input.storyboard.forEach((card, index) => Object.assign(card, { pageDesignId: id, pageSectionKey: 'emo.wear_fit',
      pageSectionUsage: 'fit', slotKey: `wear_fit.${card.direction}`, spaceGroupId: 'studio' }));
    const blocks = assemblePage(input);
    assert.deepEqual(photos(blocks).map(el => el.sourceBlockId), ['photo-0', 'photo-1', 'photo-2']);
    assert.deepEqual(blocks.flatMap(block => block.elements).filter(el => el.copyRole?.startsWith('cap.')).map(el => el.text), ['Front', 'Side', 'Back']);
    parityCases.push({ design: catalog.designs[id], input });
  }
});

test('catalog point variants use real source aspect and retain each designed copy role once per cut', () => {
  for (const id of ['PT-COLUMN-01', 'P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01']) {
    const input = inputFor(2, 'points');
    input.storyboard.forEach((card, i) => Object.assign(card, { pageDesignId: id, detailTargetId: `target-${i}` }));
    input.analysis = { detailRecommendations: { candidates: [{ id: 'target-0', label: '단추' }, { id: 'target-1', label: '소매' }] } };
    input.cutResults = input.storyboard.map(card => ({ blockId: card.id, imageUrl: `/${card.id}.webp`, width: 600, height: 900 }));
    input.copyResults = [{ blockId: 'photo-0', texts: [{ role: 'items.0.desc', text: '첫 설명' }, { role: 'items.1.desc', text: '둘 설명' }] }];
    const blocks = assemblePage(input);
    assert.equal(photos(blocks).length, 2);
    for (const image of photos(blocks)) { assert.equal(image.h, image.w * 1.5); assert.equal(image.crop, undefined); }
    assert.deepEqual(blocks.flatMap(block => block.elements).filter(el => /^items\.\d+\.desc$/.test(el.copyRole)).map(el => el.text),
      id === 'PT-COLUMN-01' ? [] : ['첫 설명', '둘 설명']);
    assert.deepEqual(blocks.flatMap(block => block.elements).filter(el => /^items\.\d+\.name$/.test(el.copyRole)).map(el => el.text), ['단추', '소매']);
    parityCases.push({ design: catalog.designs[id], input });
  }
});

test('catalog color layouts render only supplied front/back cuts with their color names', () => {
  for (const id of ['CP-COLUMN-01', 'CP-EMO-01']) for (const withBack of [false, true]) {
    const input = inputFor(withBack ? 3 : 2);
    input.storyboard.forEach((card, i) => Object.assign(card, { pageDesignId: id, pageSectionKind: 'color_product',
      pageSectionKey: 'emo.color_product', cutType: 'product', shot: 'ghost', colorId: i === 1 ? 'other' : 'base',
      direction: i === 2 ? 'back' : 'front', slotKey: `color_product.${i === 2 ? 'back' : 'front'}.${card.colorId}` }));
    // Slot identity follows the assigned color, independently of original card order.
    input.storyboard.forEach(card => { card.slotKey = `color_product.${card.direction}.${card.colorId}`; });
    const blocks = assemblePage(input);
    assert.deepEqual(photos(blocks).map(el => el.sourceBlockId), withBack ? ['photo-0', 'photo-1', 'photo-2'] : ['photo-0', 'photo-1']);
    assert.deepEqual(blocks.flatMap(block => block.elements).filter(el => /^colors\.\d+\.name$/.test(el.copyRole)).map(el => el.text),
      withBack ? ['블랙', '화이트', '블랙'] : ['블랙', '화이트']);
    assert.equal(blocks.flatMap(block => block.elements).filter(el => el.copyRole === 'cap.back').length, withBack ? 1 : 0);
    if (id === 'CP-COLUMN-01') assert.ok(photos(blocks).every(el => el.w === 888));
    parityCases.push({ design: catalog.designs[id], input });
  }
});

test('existing featureCopy supplies card and below descriptions when photo copy only contains headline', () => {
  for (const id of ['P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01']) {
    const input = inputFor(2, 'points');
    input.storyboard.forEach((card, i) => Object.assign(card, { pageDesignId: id, detailTargetId: `target-${i}` }));
    input.analysis = { detailRecommendations: { candidates: [{ id: 'target-0', label: '단추' }, { id: 'target-1', label: '소매' }] },
      featureCopy: [{ point: '단추', desc: '단추 디테일을 확인할 수 있습니다.' }, { point: '소매', desc: '소매 마감을 확인할 수 있습니다.' }] };
    input.copyResults = input.storyboard.map(card => ({ blockId: card.id, texts: [{ role: 'headline', text: '사진 소제목' }] }));
    const text = assemblePage(input).flatMap(block => block.elements).filter(el => /^items\.\d+\.desc$/.test(el.copyRole));
    assert.deepEqual(text.map(el => el.text), ['단추 디테일을 확인할 수 있습니다.', '소매 마감을 확인할 수 있습니다.']);
    assert.ok(text.every(el => !el.placeholder));
    parityCases.push({ design: catalog.designs[id], input });
  }
});

for (const pattern of ['twoColumn', 'heroPair']) test(`${pattern} retains middle null-slot photos, group boundaries and caption indexes`, () => {
  for (const [groups, expected] of [
    [['a', 'a', 'b', 'b'], [[0, 1], [2, 3]]],
    [['a', null, 'a', 'a'], [[0], [1], [2, 3]]],
    [['a', 'a', 'a', 'a'], pattern === 'twoColumn' ? [[0, 1], [2, 3]] : [[0], [1, 2], [3]]],
  ]) {
    const input = inputFor(4), custom = flow(pattern);
    input.storyboard.forEach((card, i) => { card.spaceGroupId = groups[i]; });
    input.storyboard[1].slotKey = null;
    const blocks = assemble(custom, input).filter(block => block.pageSectionKey);
    assert.deepEqual(blocks.map(block => photos([block]).map(el => el.sourceBlockId)), expected.map(row => row.map(i => `photo-${i}`)));
    assert.equal(photos(blocks)[1].slotKey, 'wear_styling.extra2');
    const text = blocks.flatMap(block => block.elements);
    assert.deepEqual(text.filter(el => el.copyRole?.startsWith('colorName.')).map(el => el.copyRole), ['colorName.0', 'colorName.1', 'colorName.2', 'colorName.3']);
    assert.deepEqual(text.filter(el => el.copyRole?.startsWith('cap.')).map(el => el.text), ['Front', 'Side', 'Back', 'Front']);
    parityCases.push({ design: custom, input });
  }
});

test('curated wildcard columns include loose photos in place with their normal ratio and captions', () => {
  for (const [id, kind, size] of [
    ['P02-D01', 'wear', [888, 1110]], ['WEAR-FIT-COLUMN-01', 'wear', [888, 1110]],
    ['PT-COLUMN-01', 'points', [888, 1332]], ['P06-D01', 'points', [532, 798]],
    ['PT-COLUMN-CARD-01', 'points', [840, 1260]], ['PT-BELOW-CARD-01', 'points', [532, 798]],
  ]) {
    const input = inputFor(3, kind);
    input.storyboard.forEach((card, i) => Object.assign(card, { pageDesignId: id, colorId: 'other', detailTargetId: `target-${i}` }));
    input.storyboard[1].slotKey = null;
    input.analysis = { detailRecommendations: { candidates: [{ id: 'target-0', label: '단추' }, { id: 'target-1', label: '소매' }, { id: 'target-2', label: '밑단' }] } };
    input.cutResults = input.storyboard.map(card => ({ blockId: card.id, imageUrl: `/${card.id}.webp`, width: 600, height: 900 }));
    const blocks = assemblePage(input), text = blocks.flatMap(block => block.elements);
    assert.deepEqual(photos(blocks).map(el => el.sourceBlockId), ['photo-0', 'photo-1', 'photo-2'], id);
    assert.deepEqual(photos(blocks).map(el => [el.w, el.h]), [size, size, size], id);
    if (id === 'P02-D01') assert.deepEqual(text.filter(el => el.copyRole?.startsWith('colorName.')).map(el => el.copyRole), ['colorName.0', 'colorName.1', 'colorName.2']);
    else if (kind === 'wear') assert.deepEqual(text.filter(el => el.copyRole?.startsWith('cap.')).map(el => el.text), ['Front', 'Side', 'Back']);
    else assert.deepEqual(text.filter(el => /^items\.\d+\.name$/.test(el.copyRole)).map(el => el.text), ['단추', '소매', '밑단']);
    parityCases.push({ design: catalog.designs[id], input });
  }
});

test('saved hidden wildcard design retains its existing null-slot overflow order and geometry', () => {
  const input = inputFor(3);
  input.storyboard.forEach(card => Object.assign(card, { pageDesignId: 'P02-D02', colorId: 'other' }));
  input.storyboard[1].slotKey = null;
  input.cutResults = input.storyboard.map(card => ({ blockId: card.id, imageUrl: `/${card.id}.webp`, width: 600, height: 900 }));
  const blocks = assemblePage(input);
  assert.deepEqual(photos(blocks).map(el => [el.sourceBlockId, el.w, el.h]), [
    ['photo-0', 1000, 1333], ['photo-2', 1000, 1333], ['photo-1', 1000, 1500],
  ]);
  assert.deepEqual(blocks.flatMap(block => block.elements).filter(el => el.copyRole?.startsWith('colorName.')).map(el => el.copyRole), ['colorName.0', 'colorName.1']);
  parityCases.push({ design: catalog.designs['P02-D02'], input });
});

test('curated color columns match swatch and copy index to the actual front and back photo color', () => {
  for (const [colors, cards, names, fills, roles] of [
    [[{ id: 'black', name: '블랙', swatchId: 'black', isBase: true }, { id: 'white', name: '화이트', swatchId: 'white' }],
      [['white', 'front'], ['black', 'back']], ['화이트', '블랙'], ['#ffffff', '#15141a'], ['colors.1.name', 'colors.0.name']],
    [[{ id: 'white', name: '화이트', swatchId: 'white' }, { id: 'black', name: '블랙', swatchId: 'black', isBase: true }],
      [['black', 'front'], ['white', 'front'], ['black', 'back']], ['블랙', '화이트', '블랙'], ['#15141a', '#ffffff', '#15141a'], ['colors.1.name', 'colors.0.name', 'colors.1.name']],
  ]) {
    const input = inputFor(cards.length); input.product = { ...product, colors };
    input.storyboard.forEach((card, i) => Object.assign(card, { pageDesignId: 'CP-COLUMN-01', pageSectionKind: 'color_product',
      pageSectionKey: 'emo.color_product', cutType: 'product', shot: 'ghost', colorId: cards[i][0], direction: cards[i][1], slotKey: `color_product.${cards[i][1]}.${cards[i][0]}` }));
    const elements = assemblePage(input).filter(block => block.pageSectionKey).flatMap(block => block.elements);
    const captions = elements.filter(el => /^colors\.\d+\.name$/.test(el.copyRole));
    assert.deepEqual(captions.map(el => el.text), names);
    assert.deepEqual(captions.map(el => el.copyRole), roles);
    assert.deepEqual(elements.filter(el => el.type === 'shape' && el.shape === 'circle').map(el => el.fill), fills);
    parityCases.push({ design: catalog.designs['CP-COLUMN-01'], input });
  }
});

test('JS and Python agree exactly on flow boundaries, targets, captions and natural geometry', () => {
  const script = `import json,sys\nfrom app.agents.page_templates import CATALOG\nfrom app.agents.page_template_assembler import assemble_v4\nresults=[]\nfor case in json.load(sys.stdin):\n CATALOG['designs'][case['design']['id']]=case['design']\n v=case['input']\n results.append(assemble_v4(v['storyboard'],v.get('cutResults',[]),v.get('copyResults',[]),v['product'],True,analysis=v.get('analysis')))\njson.dump(results,sys.stdout,ensure_ascii=False)`;
  const result = spawnSync((process.env.PAGE_TEMPLATE_PYTHON || new URL('../../server/.venv/bin/python', import.meta.url).pathname), ['-c', script], {
    input: JSON.stringify(parityCases), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, env: { ...process.env, PYTHONPATH: 'server', PYTHONDONTWRITEBYTECODE: '1' } });
  assert.equal(result.status, 0, result.stderr);
  assert.ok(parityCases.length >= 60);
  assert.deepEqual(JSON.parse(result.stdout), parityCases.map(value => assemble(value.design, value.input)));
});
