import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { fitTitlePlateBlocks, introPanelFor } from '../../src/features/editor/editorTitlePlateFit.js';
import { editedTextElement } from '../../src/features/editor/editorTextEdit.js';
import { getBlockExportElements, getBlockRenderHeight } from '../../src/features/editor/editorGeometry.js';
import { decorateGenBlocks, fillGenBlocks, mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';

const fixture = JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json', 'utf8'));
const input = name => structuredClone(fixture.cases.find(c => c.name === name).input);
const bodies = block => block.elements.filter(el => el.copyRole === 'body');
const who = block => block.elements.find(el => el.copyRole === 'who');
function intro(text = '') {
  return assemblePage({ storyboard: [{ id: 'intro', taxonomyVersion: 4, source: 'text', sectionId: 'intro',
    pageSectionKey: 'emo.intro', pageSectionKind: 'intro', pageDesignId: 'T01-D07', slotKey: null }],
  product: { name: '셔츠', colors: [] }, analysis: {},
  copyResults: [{ blockId: 'intro', texts: [{ role: 'body', text }] }] })[0];
}
function edit(block, index, text, h = 85) {
  const body = bodies(block)[index];
  return fitTitlePlateBlocks([block], [{ ...block, elements: block.elements.map(el => el.id === body.id
    ? editedTextElement(el, { text, h }) : el) }])[0];
}

test('intro retains hidden avatar, restores on typing, hides on erase and restores after save/reload', () => {
  const empty = intro();
  assert.equal(who(empty)?.hidden, true);
  assert.equal(empty.elements.filter(el => el.hidden).length, 3);
  assert.ok(!getBlockExportElements(empty).some(el => el.copyRole === 'who' || el.text === 'MD'));
  const populated = edit(empty, 0, '허리가 밴딩이라 편하고 끈으로 맞춰 입을 수 있어요');
  assert.ok(who(populated) && !who(populated).hidden);
  const panel = introPanelFor(bodies(populated)[0], populated.elements);
  assert.equal(panel.x, 175);
  assert.ok(panel.w <= 769);
  assert.equal(bodies(populated)[0].x, panel.x + 26);
  assert.ok(bodies(populated)[0].y + bodies(populated)[0].h <= panel.y + panel.h);
  const exported = getBlockExportElements(populated);
  assert.equal(exported.filter(el => el.text === 'MD').length, 2);
  assert.ok(getBlockRenderHeight(populated, { forExport: true }) >= panel.y + panel.h);
  const erased = edit(JSON.parse(JSON.stringify(populated)), 0, '   ', 48);
  assert.equal(who(erased).hidden, true);
  const wide = introPanelFor(bodies(erased)[0], erased.elements);
  assert.deepEqual([wide.x, wide.w], [56, 888]);
  assert.ok(!getBlockExportElements(erased).some(el => el.text === 'MD'));
  const again = edit(erased, 0, '다시 쓴 본문이에요', 80);
  assert.ok(!who(again).hidden);
  assert.equal(again.elements.length, empty.elements.length);
  assert.deepEqual(empty, intro(), 'undo source is not mutated');
});

test('avatar follows all body paragraphs, not the title or only the first paragraph', () => {
  const initial = intro('첫 문장이에요\n둘째 문장이에요');
  const firstDeleted = edit(initial, 0, '');
  assert.ok(!who(firstDeleted).hidden);
  const bothDeleted = edit(firstDeleted, 1, '');
  assert.equal(who(bothDeleted).hidden, true);
  const restored = edit(bothDeleted, 1, '둘째 칸에 다시 쓴 글');
  assert.ok(!who(restored).hidden);
  assert.equal(introPanelFor(bodies(restored)[1], restored.elements).x, 175);
});

test('T08-D07 aligns only answer bodies left', () => {
  const value = input('emo-2-mono');
  value.storyboard = value.storyboard.filter(b => b.pageSectionKind === 'faq');
  value.copyResults = [{ blockId: value.storyboard[0].id, texts: [
    { role: 'items.0.q', text: '세탁은 어떻게 하면 되나요?' },
    { role: 'items.0.a', text: '단독으로 찬물 세탁하시고 건조기 사용은 피해 주세요' },
  ] }];
  const elements = assemblePage(value).flatMap(b => b.elements);
  assert.equal(elements.find(el => el.copyRole === 'items.0.a').style.align, 'left');
  assert.equal(elements.find(el => el.copyRole === 'items.0.q').style.align, 'center');
  assert.equal(elements.find(el => el.text === 'A.').style.align, 'left');
  assert.equal(elements.find(el => el.text === 'Q.').style.align, 'left');
  assert.equal(elements.find(el => el.copyRole === 'title').style.align, 'center');
});

for (const kind of ['faq', 'review', 'for_you']) test(`${kind} compacts completed copy and leaves no placeholder cards`, () => {
  const value = input('emo-2-mono');
  value.storyboard = value.storyboard.filter(b => b.pageSectionKind === kind);
  const texts = kind === 'faq' ? [
    { role: 'items.1.a', text: '질문 없는 답' },
    { role: 'items.2.q', text: '세탁은 어떻게 하면 되나요?' },
    { role: 'items.2.a', text: '단독으로 찬물 세탁하시고 건조기 사용은 피해 주세요' },
  ] : kind === 'review' ? [{ role: 'items.0.who', text: '글 없는 사람' },
    { role: 'items.2.text', text: '색이 예뻐서 자주 입어요' }, { role: 'items.2.who', text: '구매자' }]
    : [{ role: 'items.2', text: '허리가 조이는 옷을 피하는 분' }];
  value.copyResults = [{ blockId: value.storyboard[0].id, texts }];
  const blocks = assemblePage(value), items = blocks.flatMap(b => b.elements).filter(el => el.copyRole?.startsWith('items.'));
  assert.ok(items.length > 0);
  assert.ok(items.every(el => el.copyRole.startsWith('items.0')));
  assert.ok(items.every(el => el.text.trim()));
  const skeletonValue = { ...value, copyResults: [] };
  const pending = decorateGenBlocks(assemblePage(skeletonValue), { cuts: {}, copy: {}, status: 'running' });
  const live = fillGenBlocks(pending, { cuts: {}, copy: { [value.storyboard[0].id]: texts }, status: 'running' });
  const merged = mergeServerBlocks(live, blocks);
  assert.deepEqual(merged.map(b => b.elements.map(el => [el.copyRole, el.text, el.x, el.y, el.w, el.h])),
    blocks.map(b => b.elements.map(el => [el.copyRole, el.text, el.x, el.y, el.w, el.h])));
  value.copyResults[0].texts = [];
  assert.ok(!assemblePage(value).flatMap(b => b.elements).some(el => el.copyRole?.startsWith('items.')));
});

for (const template of ['emo', 'show']) test(`${template} missing first detail keeps surviving photo and name together`, () => {
  const value = input(`${template}-2-mono`);
  value.storyboard = value.storyboard.filter(card => card.pageSectionKind === 'points');
  value.analysis.partDescriptionCopy = [{ point: '소매 단', label: '소매 끝', desc: '소매 끝을 잡아주는 골지 짜임' }];
  value.analysis.featureCopy = [];
  value.copyResults = [{ blockId: value.storyboard[0].id, texts: [] }];
  const completed = assemblePage(value);
  const elements = completed.flatMap(b => b.elements);
  assert.deepEqual(elements.filter(el => el.type === 'image').map(el => el.sourceBlockId), [`${template}.points.detail2`]);
  assert.equal(elements.find(el => el.copyRole === 'items.0.name').text, '소매 끝');
  assert.equal(elements.find(el => el.copyRole === 'items.0.desc').text, value.analysis.partDescriptionCopy[0].desc);
  assert.ok(!elements.some(el => el.copyRole?.startsWith('items.1')));
  const pending = decorateGenBlocks(assemblePage({ ...value, copyResults: [], analysis: { ...value.analysis, partDescriptionCopy: [] } }),
    { cuts: {}, copy: {}, status: 'running' });
  const merged = mergeServerBlocks(pending, completed);
  assert.deepEqual(merged.filter(b => b.pageSectionKey), completed.filter(b => b.pageSectionKey));
  assert.equal(new Set(merged.map(b => b.id)).size, merged.length);
});

test('editing nonempty body preserves a manually hidden avatar', () => {
  const block = intro('원래 본문이에요');
  const hidden = { ...block, elements: block.elements.map(el => el.groupId?.startsWith('template-intro-avatar:')
    ? { ...el, hidden: true } : el) };
  assert.ok(who(edit(hidden, 0, '고친 본문이에요')).hidden);
});

test('explicit empty intro completion cannot restore rejected seller text', () => {
  const value = input('emo-2-mono');
  value.storyboard = value.storyboard.filter(b => b.pageSectionKind === 'intro');
  value.analysis.mdComment = '군살을 완벽하게 커버해요';
  value.copyResults = [{ blockId: value.storyboard[0].id, texts: [] }];
  assert.equal(bodies(assemblePage(value)[0])[0].text, '');
});

test('typing remeasures body after restoring the narrower avatar column', async () => {
  const { hookHarness } = await import('./helpers/storyboardComponents.mjs');
  const { isIntroAvatarElement } = await import('../../src/features/editor/editorIntroAvatar.js');
  const { hasTitlePlate } = await import('../../src/lib/pageTemplates/titlePlate.js');
  const block = intro(), element = bodies(block)[0];
  const nodes = block.elements.map(el => ({ dataset: { elid: el.id }, style: {} }));
  const node = nodes.find(el => el.dataset.elid === element.id);
  Object.defineProperty(node, 'offsetHeight', { get: () => node.style.width === '717px' ? 165 : 55 });
  node.parentElement = { querySelectorAll: () => nodes };
  const source = readFileSync('src/features/editor/editorTitlePlatePreview.js', 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export function', 'function');
  const h = hookHarness();
  const hook = new Function('useLayoutEffect', 'useRef', 'fitTitlePlateBlocks', 'hasTitlePlate', 'introPanelFor', 'isIntroAvatarElement', source + ';return useTitlePlatePreview;')(
    h.hooks.useEffect, h.hooks.useRef, fitTitlePlateBlocks, hasTitlePlate, introPanelFor, isIntroAvatarElement);
  try {
    const preview = h.render(() => hook(element, block.elements, { current: node }, true), {});
    assert.deepEqual(preview(node, '폭이 바뀌면 다시 재야 하는 본문'), { h: 165 });
    const panel = introPanelFor(element, block.elements);
    assert.equal(nodes.find(el => el.dataset.elid === panel.id).style.height, `${165 + 44}px`);
  } finally { h.close(); }
});
