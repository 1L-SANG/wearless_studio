import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { decorateGenBlocks, fillGenBlocks, mergeServerBlocks, patchTemplateElement } from '../../src/lib/editorWaitSkeleton.js';

const fixture = JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json', 'utf8'));
const job = copy => ({ cuts: {}, copy, status: 'running' });
const section = blocks => blocks.filter(b => b.pageSectionKey);
const elements = blocks => section(blocks).flatMap(b => b.elements);
const items = blocks => elements(blocks).filter(e => /^items\.\d+(?:\.|$)/.test(e.copyRole || ''));
const itemIndexes = blocks => [...new Set(items(blocks).map(e => e.copyRole.match(/^items\.(\d+)/)[1]))];
function input(kind, template = 'emo') {
  const value = structuredClone(fixture.cases.find(c => c.name === `${template}-2-mono`).input);
  value.storyboard = value.storyboard.filter(b => b.pageSectionKind === kind);
  value.copyResults = [];
  value.analysis.partDescriptionCopy = [];
  value.analysis.featureCopy = [];
  return value;
}
function texts(kind) {
  return kind === 'faq' ? [{ role: 'items.0.q', text: '사이즈는 어떻게 고르나요?' }, { role: 'items.0.a', text: '실측표를 확인해 주세요' }]
    : kind === 'review' ? [{ role: 'items.0.text', text: '색이 마음에 들어요' }, { role: 'items.0.who', text: '구매자' }]
      : [{ role: 'items.0', text: '넓은 목선을 찾는 분' }];
}
function edit(blocks, predicate, patch) {
  const element = elements(blocks).find(predicate);
  assert.ok(element, 'target editor element exists');
  return blocks.map(block => block.elements.some(e => e.id === element.id)
    ? patchTemplateElement(block, element.id, typeof patch === 'function' ? patch(element) : patch) : block);
}
function complete(value, content) {
  return assemblePage({ ...value, copyResults: [{ blockId: value.storyboard[0].id, texts: content }] });
}

for (const template of ['emo']) for (const kind of ['faq', 'review', 'for_you']) {
  test(`${template} edited ${kind} removes untouched trailing groups and compacts height`, () => {
    const value = input(kind, template), content = texts(kind), final = complete(value, content);
    let waiting = decorateGenBlocks(assemblePage(value), job({}));
    waiting = fillGenBlocks(waiting, job({ [value.storyboard[0].id]: content }));
    const role = content[0].role;
    waiting = edit(waiting, e => e.copyRole === role, e => ({ text: '직접 고친 문구', style: { ...e.style, weight: 777 } }));
    const original = structuredClone(waiting);
    const merged = mergeServerBlocks(waiting, final);
    assert.deepEqual(itemIndexes(merged), ['0']);
    const retained = items(merged).find(e => e.copyRole === role);
    assert.equal(retained.text, '직접 고친 문구');
    assert.equal(retained.style.weight, 777);
    assert.equal(section(merged).length, section(final).length);
    assert.equal(section(merged)[0].h, section(final)[0].h);
    assert.deepEqual(waiting, original);
    assert.equal(new Set(merged.map(b => b.id)).size, merged.length);
  });
  test(`${template} completed empty ${kind} removes groups while preserving a heading edit`, () => {
    const value = input(kind, template), final = complete(value, []);
    let waiting = decorateGenBlocks(assemblePage(value), job({}));
    waiting = edit(waiting, e => e.copyRole === 'title' || e.copyRole === 'lead', { text: '직접 쓴 제목' });
    const merged = mergeServerBlocks(waiting, final);
    assert.equal(items(merged).length, 0);
    assert.ok(elements(merged).some(e => e.text === '직접 쓴 제목'));
    assert.equal(section(merged)[0].h, section(final)[0].h);
  });
}

for (const kind of ['faq', 'review', 'for_you']) test(`seller-written late ${kind} item survives an empty final list and moves to item zero`, () => {
  const value = input(kind), final = complete(value, []);
  let waiting = decorateGenBlocks(assemblePage(value), job({}));
  const role = kind === 'faq' ? 'items.2.q' : kind === 'review' ? 'items.2.text' : 'items.2';
  waiting = edit(waiting, e => e.copyRole === role, e => ({ text: '판매자가 직접 채운 문구', style: { ...e.style, weight: 777 } }));
  const merged = mergeServerBlocks(waiting, final);
  assert.deepEqual(itemIndexes(merged), ['0']);
  const manual = items(merged).find(e => e.text === '판매자가 직접 채운 문구');
  assert.ok(manual);
  assert.equal(manual.style.weight, 777);
  if (kind === 'for_you') assert.ok(section(merged)[0].h < section(waiting)[0].h);
});

for (const template of ['emo', 'show']) test(`${template} edited surviving point stays with its original photo after first card is removed`, () => {
  const value = input('points', template);
  let waiting = decorateGenBlocks(assemblePage(value), job({}));
  waiting = edit(waiting, e => e.copyRole === 'items.1.name', e => ({ text: '판매자 소매 이름', style: { ...e.style, weight: 777 } }));
  waiting = edit(waiting, e => e.type === 'image' && e.sourceBlockId === `${template}.points.detail2`, { crop: { x: 0.25, y: 0.2, w: 0.7, h: 0.6 } });
  value.analysis.partDescriptionCopy = [{ point: '소매 단', label: '소매 끝', desc: '소매에만 해당하는 최종 설명이에요' }];
  const final = complete(value, []), merged = mergeServerBlocks(waiting, final);
  assert.deepEqual(elements(merged).filter(e => e.type === 'image').map(e => e.sourceBlockId), [`${template}.points.detail2`]);
  assert.equal(items(merged).find(e => e.copyRole === 'items.0.name').text, '판매자 소매 이름');
  assert.equal(items(merged).find(e => e.copyRole === 'items.0.name').style.weight, 777);
  assert.equal(items(merged).find(e => e.copyRole === 'items.0.desc').text, '소매에만 해당하는 최종 설명이에요');
  assert.deepEqual(elements(merged).find(e => e.type === 'image').crop, { x: 0.25, y: 0.2, w: 0.7, h: 0.6 });
  assert.equal(new Set(merged.map(b => b.id)).size, merged.length);
});

test('seller-written rejected point is retained and never receives the other photo description', () => {
  const value = input('points');
  let waiting = decorateGenBlocks(assemblePage(value), job({}));
  waiting = edit(waiting, e => e.copyRole === 'items.0.desc', { text: '판매자가 쓴 카라 설명' });
  value.analysis.partDescriptionCopy = [{ point: '소매 단', desc: '소매에만 해당하는 최종 설명이에요' }];
  const merged = mergeServerBlocks(waiting, complete(value, []));
  const cards = section(merged).filter(b => b.elements.some(e => e.type === 'image'));
  assert.equal(cards.length, 2);
  assert.equal(cards[0].elements.find(e => e.type === 'image').sourceBlockId, 'emo.points.detail1');
  assert.equal(cards[0].elements.find(e => e.copyRole?.endsWith('.desc')).text, '판매자가 쓴 카라 설명');
  assert.equal(cards[1].elements.find(e => e.type === 'image').sourceBlockId, 'emo.points.detail2');
  assert.equal(cards[1].elements.find(e => e.copyRole?.endsWith('.desc')).text, '소매에만 해당하는 최종 설명이에요');
  assert.deepEqual(itemIndexes(merged), ['0', '1']);
});

for (const kind of ['faq', 'review', 'for_you']) test(`blank style-only ${kind} group is removed when final copy is empty`, () => {
  const value = input(kind);
  const role = kind === 'faq' ? 'items.2.q' : kind === 'review' ? 'items.2.text' : 'items.2';
  const waiting = edit(decorateGenBlocks(assemblePage(value), job({})), e => e.copyRole === role,
    e => ({ style: { ...e.style, weight: 777 } }));
  assert.equal(items(mergeServerBlocks(waiting, complete(value, []))).length, 0);
});

for (const design of ['PT-TEXT-PHOTO-01', 'PT-PHOTO-CARD-01']) test(`${design} identifies the entire source-bound card before the image`, () => {
  const value = input('points');
  value.storyboard = value.storyboard.map(card => ({ ...card, pageDesignId: design }));
  let waiting = decorateGenBlocks(assemblePage(value), job({}));
  waiting = edit(waiting, e => e.copyRole === 'items.1.name', { text: '판매자 소매 이름' });
  if (design === 'PT-PHOTO-CARD-01') {
    const sourceCard = section(waiting).find(block => block.elements.some(e => e.sourceBlockId === 'emo.points.detail2'));
    const background = sourceCard.elements.find(e => e.type === 'shape');
    waiting = waiting.map(block => block.id === sourceCard.id
      ? { ...block, elements: block.elements.map(e => e.id === background.id ? { ...e, opacity: 0.45 } : e) } : block);
  }
  value.analysis.partDescriptionCopy = [{ point: '소매 단', desc: '소매 전용 최종 설명' }];
  const merged = mergeServerBlocks(waiting, complete(value, []));
  assert.deepEqual(elements(merged).filter(e => e.type === 'image').map(e => e.sourceBlockId), ['emo.points.detail2']);
  assert.equal(items(merged).find(e => e.copyRole === 'items.0.name').text, '판매자 소매 이름');
  assert.equal(items(merged).find(e => e.copyRole === 'items.0.desc').text, '소매 전용 최종 설명');
  assert.ok(!items(merged).some(e => /카라/.test(e.text)));
  if (design === 'PT-PHOTO-CARD-01') assert.equal(elements(merged).filter(e => e.opacity === 0.45).length, 1);
  assert.equal(new Set(elements(merged).map(e => e.id)).size, elements(merged).length);
});

test('manual deletion stays deleted when completed recommendation groups are packed', () => {
  const value = input('for_you');
  let waiting = fillGenBlocks(decorateGenBlocks(assemblePage(value), job({})), job({ [value.storyboard[0].id]: texts('for_you') }));
  waiting = edit(waiting, e => e.copyRole === 'items.0', { text: '' });
  const merged = mergeServerBlocks(waiting, complete(value, texts('for_you')));
  assert.deepEqual(itemIndexes(merged), ['0']);
  assert.equal(items(merged)[0].text, '');
  assert.equal(new Set(elements(merged).map(e => e.id)).size, elements(merged).length);
});

for (const template of ['emo', 'show']) test(`${template} full-page point compaction keeps block and element ids globally unique`, () => {
  const value = structuredClone(fixture.cases.find(c => c.name === `${template}-2-mono`).input);
  value.copyResults = [];
  value.analysis.partDescriptionCopy = [];
  value.analysis.featureCopy = [];
  let waiting = decorateGenBlocks(assemblePage(value), job({}));
  waiting = edit(waiting, e => e.copyRole === 'items.1.name' && e.sourceBlockId === `${template}.points.text`, { text: '직접 쓴 소매 이름' });
  value.analysis.partDescriptionCopy = [{ point: '소매 단', desc: '소매 전용 최종 설명' }];
  value.copyResults = [{ blockId: `${template}.points.text`, texts: [] }];
  const merged = mergeServerBlocks(waiting, assemblePage(value));
  assert.equal(new Set(merged.map(b => b.id)).size, merged.length);
  const all = merged.flatMap(b => b.elements);
  assert.equal(new Set(all.map(e => e.id)).size, all.length);
  assert.ok(all.some(e => e.text === '직접 쓴 소매 이름'));
});

for (const kind of ['faq', 'points']) test(`cleared ${kind} photo stays empty after stable final image arrives`, () => {
  const value = input(kind);
  let waiting = decorateGenBlocks(assemblePage(value), job({}));
  const photo = elements(waiting).find(e => e.type === 'image');
  waiting = edit(waiting, e => e.id === photo.id, { src: null, slotCleared: true });
  if (kind === 'points') value.analysis.partDescriptionCopy = [{ point: '카라', desc: '카라 설명' }, { point: '소매 단', desc: '소매 설명' }];
  const final = complete(value, kind === 'faq' ? texts(kind) : []);
  for (const element of elements(final)) if (element.type === 'image' && element.sourceBlockId === photo.sourceBlockId) element.src = 'stable.jpg';
  const merged = mergeServerBlocks(waiting, final);
  const saved = elements(merged).find(e => e.type === 'image' && e.sourceBlockId === photo.sourceBlockId);
  assert.equal(saved.slotCleared, true);
  assert.equal(saved.src, null);
});

test('retained seller point card clears pending markers and preserves failed-image reasons', () => {
  const value = input('points');
  let waiting = decorateGenBlocks(assemblePage(value), job({}));
  waiting = edit(waiting, e => e.copyRole === 'items.0.desc', { text: '판매자가 쓴 설명' });
  waiting = edit(waiting, e => e.type === 'image' && e.sourceBlockId === 'emo.points.detail1',
    { src: null, genFailed: true, genFailReason: 'provider_failed', genFailRetryable: false, genFailSourceBlockId: 'original-input' });
  const merged = mergeServerBlocks(waiting, complete(value, []));
  assert.ok(section(merged).every(block => !Object.hasOwn(block, 'genTemplateBaseline')));
  const image = elements(merged).find(e => e.type === 'image');
  assert.equal(image.genFailed, true);
  assert.equal(image.genFailReason, 'provider_failed');
  assert.equal(image.genFailRetryable, false);
  assert.equal(image.genFailSourceBlockId, 'original-input');
  assert.ok(!Object.hasOwn(image, 'genPending'));
});

test('two worn crops of one source photo match by source and slot instead of duplicating a survivor', () => {
  const value = input('points');
  value.storyboard = [
    { id: 'worn', taxonomyVersion: 4, source: 'mine', ownImages: ['/worn.jpg'], sectionId: 'wear',
      pageSectionKey: 'emo.wear_1', pageSectionKind: 'wear', pageSectionUsage: 'styling', pageDesignId: 'P02-D01', slotKey: null },
    { id: 'points', taxonomyVersion: 4, source: 'text', sectionId: 'points', pageSectionKey: 'emo.points',
      pageSectionKind: 'points', pageDesignId: 'PT-BELOW-CARD-01', slotKey: null,
      pointItems: [
        { kind: 'wornCrop', sourceBlockId: 'worn', label: '어깨선', bodyPart: 'shoulder', cropRegion: [.1, .1, .4, .2] },
        { kind: 'wornCrop', sourceBlockId: 'worn', label: '허리선', bodyPart: 'waistline', cropRegion: [.1, .5, .4, .2] },
      ] },
  ];
  let waiting = decorateGenBlocks(assemblePage(value), job({}));
  waiting = edit(waiting, e => e.copyRole === 'items.1.name', { text: '판매자 허리선' });
  value.analysis.partDescriptionCopy = [{ point: '허리선', desc: '허리선 전용 설명' }];
  const final = assemblePage({ ...value, copyResults: [{ blockId: 'points', texts: [] }] });
  const merged = mergeServerBlocks(waiting, final);
  const cards = merged.filter(block => block.pageSectionKind === 'points' && block.elements.some(e => e.type === 'image'));
  assert.equal(cards.length, 1);
  assert.equal(cards[0].elements.find(e => e.type === 'image').slotKey, 'points.detail2');
  assert.equal(cards[0].elements.find(e => e.copyRole === 'items.0.name').text, '판매자 허리선');
  assert.equal(cards[0].elements.find(e => e.copyRole === 'items.0.desc').text, '허리선 전용 설명');
});

test('seller-retained point cannot persist an expired preview when final image is absent', () => {
  const value = input('points');
  let waiting = decorateGenBlocks(assemblePage(value), job({}));
  waiting = edit(waiting, e => e.copyRole === 'items.0.desc', { text: '판매자가 쓴 카라 설명' });
  waiting = edit(waiting, e => e.type === 'image' && e.sourceBlockId === 'emo.points.detail1',
    { src: 'temporary-signed-preview.jpg', genAutoSrc: 'temporary-signed-preview.jpg' });
  value.analysis.partDescriptionCopy = [{ point: '소매 단', desc: '소매 최종 설명' }];
  const merged = mergeServerBlocks(waiting, complete(value, []), new Set());
  const retained = elements(merged).find(e => e.type === 'image' && e.sourceBlockId === 'emo.points.detail1');
  assert.equal(retained.src, null);
  assert.ok(!Object.hasOwn(retained, 'genAutoSrc'));
  assert.ok(elements(merged).some(e => e.text === '판매자가 쓴 카라 설명'));
});
