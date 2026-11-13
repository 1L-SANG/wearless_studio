import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import * as engine from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog as catalog, validateCatalog } from '../../src/lib/pageTemplates/catalog.js';

const layouts = [['COLUMN', 'column', 1], ['2COL', 'twoColumn', 2], ['HERO', 'heroPair', 3]];
const idFor = (layout, count) => `WEAR-STYLING-FRAME-${layout}-0${count}`;
const ids = cards => cards.map(card => card.id);
const images = blocks => blocks.flatMap(block => block.elements).filter(el => el.type === 'image');
const section = input => engine.assemblePage(input).filter(block => block.pageSectionKey);
const visible = blocks => images(blocks).filter(el => !Object.hasOwn(el, 'blur'));
const parityCases = [];
function inputFor(count = 6, layout = 'COLUMN', frames = 2) {
  return { product: { name: '코튼 카라 반팔 니트', colors: [{ id: 'base', name: '블랙', isBase: true }] }, shopName: '몰 이름', profile: { display_name: '개인 계정 실명' },
    storyboard: Array.from({ length: count }, (_, i) => ({ id: `photo-${i}`, taxonomyVersion: 4, source: 'ai', cutType: 'styling',
      shot: i % 3 ? 'medium' : 'full', direction: 'front', colorId: 'base', spaceGroupId: i < 3 ? 'a' : 'b',
      pageSectionKey: 'emo.wear_styling', pageSectionKind: 'wear', pageSectionUsage: 'styling',
      pageDesignId: idFor(layout, frames), slotKey: `wear_styling.extra${i + 1}` })),
    cutResults: Array.from({ length: count }, (_, i) => ({ blockId: `photo-${i}`, imageUrl: `/${i}.webp`, width: 600, height: 900 })) };
}

test('nine styling-only frame designs retain the selected layout minimum and count contract', () => {
  for (const [layout, pattern, minimum] of layouts) for (const count of [1, 2, 3]) {
    const design = catalog.designs[idFor(layout, count)];
    assert.ok(design, idFor(layout, count));
    assert.equal(design.usage, 'styling');
    assert.equal(design.framePhotos, count);
    assert.equal(design.minPhotos, Math.max(count, minimum));
    assert.deepEqual(design.choiceAxes, { layout: { id: pattern, name: design.choiceAxes.layout.name }, decoration: { id: 'frame', name: '액자' } });
    const frame = design.layout.find(node => node.type === 'photoFrame');
    assert.equal(frame.count, count); assert.equal(frame.pattern, pattern); assert.equal(frame.slot, '*');
  }
  assert.equal(validateCatalog(catalog), catalog);
});

test('catalog refuses inconsistent frame count, nonstyling frames and unsupported frame primitive policies', () => {
  assert.ok(catalog.designs[idFor('COLUMN', 2)]);
  for (const patch of [{ framePhotos: 4 }, { framePhotos: 1 }, { usage: 'fit' }, { minPhotos: 1 }]) {
    const value = structuredClone(catalog); Object.assign(value.designs[idFor('COLUMN', 2)], patch);
    assert.throws(() => validateCatalog(value), /frame/i);
  }
  for (const patch of [{ count: 0 }, { count: 1.5 }, { pattern: 'masonry' }, { groupBy: 'none' }, { aspect: 'source' }, { captions: {} }]) {
    const value = structuredClone(catalog); Object.assign(value.designs[idFor('COLUMN', 2)].layout.find(node => node.type === 'photoFrame'), patch);
    assert.throws(() => validateCatalog(value), /photoFrame/);
  }
});

test('frame choice uses the first sufficient contiguous group, then most medium, same-group full and remaining order', () => {
  const select = engine.selectFramePhotos;
  assert.equal(typeof select, 'function');
  const cards = inputFor(8).storyboard;
  Object.assign(cards[0], { spaceGroupId: 'a', shot: 'medium' }); Object.assign(cards[1], { spaceGroupId: 'a', shot: 'full' });
  cards.slice(2, 5).forEach((card, i) => Object.assign(card, { spaceGroupId: 'b', shot: i ? 'medium' : 'full' }));
  cards.slice(5).forEach(card => Object.assign(card, { spaceGroupId: 'a', shot: 'medium' }));
  const before = structuredClone(cards), chosen = select(cards, 2);
  assert.deepEqual(ids(chosen.frame), ['photo-3', 'photo-4']); assert.equal(chosen.background.id, 'photo-2');
  assert.deepEqual(ids(chosen.remaining), ['photo-0', 'photo-1', 'photo-2', 'photo-5', 'photo-6', 'photo-7']);
  const three = select(cards.slice(0, 5), 3);
  assert.deepEqual(ids(three.frame), ['photo-3', 'photo-4', 'photo-2']); assert.equal(three.background.id, 'photo-3');
  const other = select(cards.slice(0, 3), 3);
  assert.deepEqual(ids(other.frame), ['photo-0', 'photo-1', 'photo-2']);
  assert.deepEqual(cards, before);
});

test('unknown shots and loose color groups use stable fallback without inventing photos', () => {
  const cards = inputFor(4).storyboard;
  cards.forEach((card, i) => Object.assign(card, { shot: undefined, spaceGroupId: undefined, colorId: i < 2 ? 'base' : 'other' }));
  const result = engine.selectFramePhotos(cards, 3);
  assert.deepEqual(ids(result.frame), ['photo-0', 'photo-1', 'photo-2']);
  assert.equal(result.background.id, 'photo-0');
  assert.deepEqual(engine.selectFramePhotos([], 2), { frame: [], remaining: [], remainingGroups: [], background: null });
});

for (const [layout] of layouts) for (const count of [1, 2, 3]) test(`${layout} frame${count}: originals once, editable backdrop reuse, natural geometry and logical row blocks`, () => {
  const input = inputFor(8, layout, count), before = structuredClone(input), blocks = section(input), all = blocks.flatMap(block => block.elements);
  const chosen = engine.selectFramePhotos(input.storyboard, count), framed = visible([blocks[0]]), backdrops = images(blocks).filter(el => Object.hasOwn(el, 'blur'));
  assert.deepEqual(framed.map(el => el.sourceBlockId), ids(chosen.frame));
  assert.deepEqual(visible(blocks).slice(count).map(el => el.sourceBlockId), ids(chosen.remaining));
  assert.equal(new Set(visible(blocks).map(el => el.sourceBlockId)).size, 8);
  assert.equal(backdrops.length, 1); assert.equal(backdrops[0].sourceBlockId, chosen.background.id);
  assert.equal(backdrops[0].blur, 48); assert.notEqual(backdrops[0].locked, true);
  assert.deepEqual([backdrops[0].x, backdrops[0].w], [0, 1000]);
  assert.ok(framed.every(el => el.w === 560 && el.h === 840 && !el.crop));
  const card = all.find(el => el.type === 'shape' && el.fill === catalog.neutralColors.framePaper);
  assert.equal(card.w, 680); assert.equal(card.x, 160); assert.equal(card.y - backdrops[0].y, 110);
  const head = all.find(el => el.text === input.product.name), foot = all.find(el => el.text === input.shopName);
  assert.deepEqual([head.y - card.y, head.style.size, head.style.weight, head.style.tracking, head.style.color], [52, 22, 500, .88, catalog.neutralColors.frameInk]);
  assert.equal(framed[0].y - head.y - head.h, 34); assert.equal(foot.y - framed.at(-1).y - framed.at(-1).h, 40);
  assert.equal(card.y + card.h - foot.y - foot.h, 46); assert.equal(backdrops[0].h - card.h, 220);
  assert.equal(blocks[0].h - backdrops[0].y - backdrops[0].h, 40);
  assert.ok(visible(blocks).slice(count).every(el => el.h === Math.floor(el.w * 5 / 4 + .5)));
  assert.equal(all.filter(el => el.copyRole === 'label').length, 1);
  assert.equal(new Set(all.map(el => el.id)).size, all.length);
  assert.deepEqual(input, before); parityCases.push(input);
});

for (const [meta, expected] of [[{}, 747], [{ width: 1024, height: 1365, dimensionsKnown: false }, 747],
  [{ width: 6000, height: 300 }, 28], [{ width: 300, height: 6000 }, 11200], [{ width: 6000, height: 1 }, 1]]) {
  test(`frame natural dimensions ${JSON.stringify(meta)} are not clamped or cropped`, () => {
    const input = inputFor(1, 'COLUMN', 1); input.cutResults = [{ blockId: 'photo-0', imageUrl: '/single.webp', ...meta }];
    delete input.shopName;
    const blocks = section(input), photo = visible(blocks)[0], backdrop = images(blocks).find(el => Object.hasOwn(el, 'blur'));
    assert.deepEqual([photo.w, photo.h, photo.crop], [560, expected, undefined]);
    assert.equal(blocks.length, 1); assert.equal(backdrop.sourceBlockId, photo.sourceBlockId);
    assert.equal(blocks.flatMap(block => block.elements).some(el => el.copyRole === 'who'), false);
    const paper = blocks[0].elements.find(el => el.fill === catalog.neutralColors.framePaper);
    assert.equal(paper.y + paper.h - photo.y - photo.h, 46);
    assert.equal(blocks[0].h, backdrop.y + backdrop.h + catalog.templates[0].spacing.sectionGap);
    parityCases.push(input);
  });
}

test('removing a whole middle group preserves disjoint same-ID groups in remaining row blocks', () => {
  const input = inputFor(4, '2COL', 2);
  input.storyboard.forEach((card, i) => Object.assign(card, { spaceGroupId: ['a', 'b', 'b', 'a'][i], shot: i === 1 || i === 2 ? 'medium' : 'full' }));
  input.storyboard[3].slotKey = null;
  const blocks = section(input);
  assert.deepEqual(blocks.map(block => visible([block]).map(el => el.sourceBlockId)), [['photo-1', 'photo-2'], ['photo-0'], ['photo-3']]);
  assert.equal(visible(blocks).at(-1).slotKey, 'wear_styling.extra4'); parityCases.push(input);
});

test('frame text respects product facts with copywriting off and remains neutral under color mode', () => {
  for (const templateId of ['emo', 'show']) {
    const input = inputFor(3); input.copywriting = false; input.pageStyle = { templateId, colorMode: 'point', fontStyle: 'serif' };
    input.baseSwatchId = 'blue'; input.storyboard.forEach(card => { card.pageSectionKey = `${templateId}.wear_styling`; });
    const all = section(input).flatMap(block => block.elements);
    assert.ok(all.some(el => el.text === input.product.name)); assert.ok(all.some(el => el.text === input.shopName));
    assert.ok(all.filter(el => [input.product.name, input.shopName].includes(el.text)).every(el => el.style.color === catalog.neutralColors.frameInk));
    parityCases.push(input);
  }
});

for (const count of [2, 3]) test(`frame${count} with no remaining photos ends at the band and reuses only its first photo`, () => {
  const input = inputFor(count, 'COLUMN', count);
  const blocks = section(input), backdrop = images(blocks).find(el => Object.hasOwn(el, 'blur'));
  assert.equal(blocks.length, 1);
  assert.equal(visible(blocks).length, count);
  assert.equal(backdrop.sourceBlockId, visible(blocks)[0].sourceBlockId);
  assert.equal(blocks[0].h, backdrop.y + backdrop.h + catalog.templates[0].spacing.sectionGap);
  parityCases.push(input);
});

test('real detail and worn-part descriptions prefer their separate copy with legacy and role-copy fallbacks', () => {
  const input = inputFor(1, 'COLUMN', 1), source = input.storyboard[0];
  source.pageDesignId = 'P02-D01';
  const detail = { ...source, id: 'detail', cutType: 'product', shot: 'detail', detailTargetId: 'button',
    pageSectionKey: 'emo.points', pageSectionKind: 'points', pageDesignId: 'PT-COLUMN-CARD-01', slotKey: 'points.detail1' };
  input.storyboard.push({ id: 'points', source: 'text', taxonomyVersion: 4, pageSectionKey: 'emo.points', pageSectionKind: 'points',
    pageDesignId: detail.pageDesignId, pointItems: [{ kind: 'detail', sourceBlockId: 'detail' },
      { kind: 'wornCrop', sourceBlockId: source.id, bodyPart: 'shoulder', label: '어깨', cropRegion: [.2, .2, .4, .2] }] }, detail);
  input.analysis = { detailRecommendations: { candidates: [{ id: 'button', label: '단추' }] },
    featureCopy: [{ point: '단추', desc: '옛 단추 설명' }, { point: '어깨', desc: '판매 강점 설명' }],
    partDescriptionCopy: [{ point: '단추', desc: '실제 단추 설명' }, { point: '어깨', desc: '실제 어깨 핏 설명' }] };
  input.copyResults = [{ blockId: 'points', texts: [{ role: 'items.0.desc', text: '역할 단추 설명' }, { role: 'items.1.desc', text: '역할 어깨 설명' }] }];
  const descriptions = value => engine.assemblePage(value).flatMap(block => block.elements)
    .filter(el => /^items\.\d+\.desc$/.test(el.copyRole)).map(el => el.text);
  assert.deepEqual(descriptions(input), ['실제 단추 설명', '실제 어깨 핏 설명']); parityCases.push(structuredClone(input));
  input.analysis.partDescriptionCopy = [];
  assert.deepEqual(descriptions(input), ['옛 단추 설명', '역할 어깨 설명']); parityCases.push(structuredClone(input));
  input.analysis.featureCopy = [];
  assert.deepEqual(descriptions(input), ['역할 단추 설명', '역할 어깨 설명']); parityCases.push(structuredClone(input));
  input.copywriting = false;
  assert.deepEqual(descriptions(input), ['', '']); parityCases.push(structuredClone(input));
});

test('frame footer requires an explicit trimmed shop name and never falls back to profile', () => {
  for (const [shopName, expected] of [[undefined, []], [null, []], ['', []], ['  ', []], [123, []], [false, []], [{ name: '몰' }, []], [['몰'], []], ['  공개 상점  ', ['공개 상점']]]) {
    const input = inputFor(1, 'COLUMN', 1);
    input.shopName = shopName;
    const all = section(input).flatMap(block => block.elements);
    assert.deepEqual(all.filter(el => el.copyRole === 'who').map(el => el.text), expected);
    assert.equal(all.some(el => el.text === input.profile.display_name), false);
    parityCases.push(input);
  }
});

test('shop name does not replace the legacy profile initial or seller name', () => {
  const input = inputFor(1, 'COLUMN', 1);
  input.profile = { display_name: '김민지' }; input.shopName = '공개 상점';
  Object.assign(input.storyboard[0], { id: 'intro', source: 'text', pageSectionKey: 'emo.intro', pageSectionKind: 'intro', pageDesignId: 'T01-D07' });
  const all = section(input).flatMap(block => block.elements);
  assert.ok(all.some(el => el.type === 'text' && !el.copyRole && el.text === '김'));
  assert.ok(all.some(el => el.copyRole === 'who' && el.text === '김민지'));
  assert.equal(all.some(el => el.text === input.shopName), false);
  parityCases.push(input);
});

test('frame catalog declares the public mall shop fact without removing legacy profile facts', () => {
  assert.ok(catalog.factPaths.includes('mall_connections.shop_name'));
  assert.ok(catalog.factPaths.includes('profiles.display_name'));
  assert.equal(validateCatalog(catalog), catalog);
  const value = structuredClone(catalog);
  value.kinds.intro.textSlots.find(slot => slot.copyRole === 'who').factPath = 'mall_connections.shop_name';
  assert.equal(validateCatalog(value), value);
});

test('both frame engines exactly agree on all selected photos, logical cuts, names and natural sizes', () => {
  assert.ok(parityCases.length >= 23);
  const script = `import json,sys\nfrom app.agents.page_template_assembler import assemble_v4\nresults=[]\nfor v in json.load(sys.stdin):\n results.append(assemble_v4(v['storyboard'],v.get('cutResults',[]),v.get('copyResults',[]),v['product'],v.get('copywriting',True),analysis=v.get('analysis'),profile=v.get('profile'),shop_name=v.get('shopName'),page_style=v.get('pageStyle'),base_swatch_id=v.get('baseSwatchId')))\njson.dump(results,sys.stdout,ensure_ascii=False)`;
  const result = spawnSync('server/.venv/bin/python', ['-c', script], { input: JSON.stringify(parityCases), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: 'server', PYTHONDONTWRITEBYTECODE: '1' } });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), parityCases.map(input => engine.assemblePage(input)));
});
