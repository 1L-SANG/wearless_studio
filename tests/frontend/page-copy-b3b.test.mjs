import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { fitTitlePlateBlocks, introPanelFor } from '../../src/features/editor/editorTitlePlateFit.js';
import { editedTextElement } from '../../src/features/editor/editorTextEdit.js';
import { getBlockExportElements, getBlockRenderHeight, expandBlockHeights } from '../../src/features/editor/editorGeometry.js';
import { hasTitlePlate } from '../../src/lib/pageTemplates/titlePlate.js';
import { isIntroAvatarElement } from '../../src/features/editor/editorIntroAvatar.js';
import { hookHarness } from './helpers/storyboardComponents.mjs';
const fixtures = JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json', 'utf8'));
const body = block => block.elements.find(el => el.copyRole === 'body');
const title = block => block.elements.find(el => el.copyRole === 'title');
const panel = block => introPanelFor(body(block), block.elements);
const sentence = '겨울까지 오래 입을 수 있는 기본 골지 니트를 찾다가 만든 제품이에요.';
function intro(text = '', heading = '', seller = '') {
  return assemblePage({ storyboard: [{ id: 'intro-b3b', taxonomyVersion: 4, source: 'text', sectionId: 'intro',
    pageSectionKey: 'emo.intro', pageSectionKind: 'intro', pageDesignId: 'T01-D07', slotKey: null }],
    product: { name: '니트', colors: [] }, analysis: { mdComment: seller },
    copyResults: seller ? [] : [{ blockId: 'intro-b3b', texts: [{ role: 'body', text }, { role: 'title', text: heading }] }] })[0];
}
function edit(block, text, h = 55, role = 'body') {
  return expandBlockHeights(fitTitlePlateBlocks([block], [{ ...block, elements: block.elements.map(el => el.copyRole === role
    ? editedTextElement(el, { text, h }) : el) }]))[0];
}

for (const seller of ['', sentence]) test(`empty title does not reserve a line above body; seller=${Boolean(seller)}`, () => {
  const block = intro(sentence, '', seller);
  assert.equal(body(block).y - panel(block).y, 22);
  assert.equal(panel(block).y + panel(block).h - body(block).y - body(block).h, 22);
  assert.ok(title(block).hidden);
  assert.ok(!getBlockExportElements(block).includes(title(block)));
});

test('write, erase and write again preserves an editable placeholder and restores automatic height', () => {
  const empty = intro();
  const filled = edit(empty, sentence, 700);
  assert.equal(body(filled).y - panel(filled).y, 22);
  assert.ok(filled.h >= panel(filled).y + panel(filled).h + 140);
  const erased = edit(filled, '', 0);
  assert.equal(erased.h, empty.h);
  assert.equal(body(erased).placeholder, 'MD 코멘트');
  assert.ok(body(erased).h >= body(erased).style.lineHeight);
  assert.equal(getBlockRenderHeight(erased, { forExport: true }), getBlockRenderHeight(empty, { forExport: true }));
  const again = edit(erased, '다시 쓴 본문이에요');
  assert.equal(body(again).y - panel(again).y, 22);
});

test('deleting an initially populated AI body retains the placeholder and click height', () => {
  const block = intro('사실 한 문장 초안이에요', '니트 이야기');
  assert.equal(body(block).placeholder, 'MD 코멘트');
  const empty = edit(block, '', 0);
  assert.ok(body(empty).h >= body(empty).style.lineHeight);
  assert.ok(!getBlockExportElements(empty).some(el => el.copyRole === 'body'));
});

test('title restored after typing is laid out above the body again', () => {
  const block = intro(sentence);
  const filled = edit(block, '새 제목', 63, 'title');
  assert.ok(!title(filled).hidden);
  assert.equal(body(filled).y - title(filled).y - title(filled).h, 26);
  assert.equal(body(edit(filled, '', 0, 'title')).y - panel(block).y, 22);
});

test('automatic text expansion can shrink back without consuming manual section height', () => {
  const empty = intro();
  const resized = fitTitlePlateBlocks([empty], [{ ...empty, h: empty.h + 300 }])[0];
  const filled = edit(resized, sentence, 1000);
  assert.ok(getBlockRenderHeight(filled) > resized.h);
  assert.equal(edit(filled, '', 0).h, resized.h);
  const manualWhileFilled = fitTitlePlateBlocks([filled], [{ ...filled, h: filled.h + 100 }])[0];
  assert.equal(edit(manualWhileFilled, '', 0).h, manualWhileFilled.h);
});

for (const template of ['emo', 'show']) test(`${template} missing review author collapses only its line`, () => {
  const input = structuredClone(fixtures.cases.find(c => c.name === `${template}-2-mono`).input);
  input.storyboard = input.storyboard.filter(b => b.pageSectionKind === 'review');
  if (!input.storyboard.length) input.storyboard = structuredClone(fixtures.cases.find(c => c.name === 'emo-2-mono').input.storyboard)
    .filter(b => b.pageSectionKind === 'review').map(b => ({ ...b, pageSectionKey: `${template}.review` }));
  const id = input.storyboard[0].id;
  input.copyResults = [{ blockId: id, texts: [{ role: 'items.0.text', text: '골이 촘촘해서 몸에 붙지 않고 편해요' }] }];
  const block = assemblePage(input).find(b => b.pageSectionKind === 'review');
  const text = block.elements.find(el => el.copyRole === 'items.0.text');
  const who = block.elements.find(el => el.copyRole === 'items.0.who');
  const bg = block.elements.find(el => el.type === 'shape' && el.groupId === text.groupId);
  assert.ok(!who || who.hidden);
  assert.equal(bg.y + bg.h - text.y - text.h, 16);
  assert.equal(text.text, input.copyResults[0].texts[0].text);
});

test('live typing expands the canvas block with its panel and cancel restores model height', () => {
  const block = intro(), element = body(block);
  const blockNode = { style: { height: `${block.h}px` }, dataset: { exportHeight: '198' } };
  const nodes = block.elements.map(el => ({ dataset: { elid: el.id }, style: {} }));
  const node = nodes.find(n => n.dataset.elid === element.id);
  node.offsetHeight = 700;
  node.parentElement = { querySelectorAll: () => nodes, scrollTop: 40 };
  node.closest = selector => selector === '.canvas-block' ? blockNode : null;
  const source = readFileSync('src/features/editor/editorTitlePlatePreview.js', 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export function', 'function');
  const h = hookHarness();
  const hook = new Function('useLayoutEffect', 'useRef', 'fitTitlePlateBlocks', 'hasTitlePlate', 'introPanelFor', 'isIntroAvatarElement', 'getBlockRenderHeight', source+';return useTitlePlatePreview;')(
    h.hooks.useEffect, h.hooks.useRef, fitTitlePlateBlocks, hasTitlePlate, introPanelFor, isIntroAvatarElement, getBlockRenderHeight);
  const render = editing => h.render(() => hook(element, block.elements, { current: node }, editing, block), {});
  try {
    render(true)(node, sentence);
    const p = nodes.find(n => n.dataset.elid === panel(block).id);
    assert.ok(parseFloat(blockNode.style.height) >= parseFloat(p.style.top) + parseFloat(p.style.height) + 140);
    assert.equal(node.parentElement.scrollTop, 0);
    render(false);
    assert.equal(blockNode.style.height, `${block.h}px`);
  } finally { h.close(); }
});

test('clearing a review author folds the line and pulls the next card up', () => {
  const input = structuredClone(fixtures.cases.find(c => c.name === 'emo-2-mono').input);
  input.storyboard = input.storyboard.filter(b => b.pageSectionKind === 'review');
  input.copyResults = [{ blockId: input.storyboard[0].id, texts: [
    { role: 'items.0.text', text: '골이 촘촘해서 몸에 붙지 않고 편해요' }, { role: 'items.0.who', text: '구매자' },
    { role: 'items.1.text', text: '소매를 걷어도 안 내려와서 좋아요' }, { role: 'items.1.who', text: '다른 구매자' },
  ] }];
  const block = assemblePage(input)[0];
  const who = block.elements.find(el => el.copyRole === 'items.0.who');
  const bg = block.elements.find(el => el.type === 'shape' && el.groupId === who.groupId);
  const next = edit(block, '', 0, 'items.0.who');
  const author = next.elements.find(el => el.id === who.id);
  assert.ok(author.hidden);
  assert.equal(next.elements.find(el => el.id === bg.id).h, bg.h - who.h - 6);
  const text2 = block.elements.find(el => el.copyRole === 'items.1.text');
  assert.equal(next.elements.find(el => el.id === text2.id).y, text2.y - who.h - 6);
});

test('seller can reveal a folded title from Layers and then write into it', () => {
  const block = intro(sentence), heading = title(block);
  const revealed = fitTitlePlateBlocks([block], [{ ...block, elements: block.elements.map(el => el.id === heading.id
    ? { ...el, hidden: false } : el) }])[0];
  assert.ok(!title(revealed).hidden);
  assert.equal(body(revealed).y - title(revealed).y - title(revealed).h, 26);
  assert.equal(title(revealed).placeholder, '상품 소개 제목');
  assert.equal(title(edit(revealed, '셀러가 쓴 제목', 63, 'title')).text, '셀러가 쓴 제목');
});

test('hidden title measurement cannot inflate a short-body block or its height floor', () => {
  const block = intro('짧아요');
  assert.equal(block.h, panel(block).y + panel(block).h + 140);
  const erased = edit(block, '', 0);
  assert.equal(erased.h, intro().h);
  assert.equal(getBlockRenderHeight(erased, { forExport: true }), getBlockRenderHeight(intro(), { forExport: true }));
});

test('painting body before title in Layers does not reverse their layout on edit', () => {
  const block = intro('본문이에요', '제목이에요');
  const b = body(block), t = title(block);
  const reordered = { ...block, elements: block.elements.filter(el => el.id !== b.id) };
  reordered.elements.splice(reordered.elements.findIndex(el => el.id === t.id), 0, b);
  const changed = edit(reordered, '수정한 본문이에요');
  assert.equal(title(changed).y, t.y);
  assert.equal(body(changed).y, b.y);
});

test('manual section height survives save/reload even while long text expands its render height', () => {
  const initial = intro();
  const manual = fitTitlePlateBlocks([initial], [{ ...initial, h: 800 }])[0];
  const long = edit(manual, sentence, 1000);
  assert.ok(getBlockRenderHeight(long) > 800);
  const reloaded = JSON.parse(JSON.stringify(long));
  assert.equal(edit(reloaded, '', 0).h, 800);
});

test('manual-height overflow keeps intro tail spacing and new text uses visible space', () => {
  const initial = intro();
  const manual = fitTitlePlateBlocks([initial], [{ ...initial, h: 800 }])[0];
  const block = edit(manual, sentence, 1000);
  const bottom = Math.max(...block.elements.filter(el => !el.hidden).map(el => el.y + el.h));
  assert.equal(getBlockRenderHeight(block) - bottom, 140);
  const source = readFileSync('src/features/editor/Editor.jsx', 'utf8');
  const start = source.indexOf('    const contentBottom = getBlockContentBottom(block);', source.indexOf('const addText ='));
  const snippet = source.slice(start, source.indexOf('    const el =', start));
  const y = new Function('block', 'getBlockRenderHeight', 'getBlockContentBottom', snippet + ';return y;')(
    block, getBlockRenderHeight, () => bottom);
  assert.equal(y, bottom + 32);
});

test('manual height also preserves the tail when only the padding overflows', () => {
  const initial = intro();
  const manual = fitTitlePlateBlocks([initial], [{ ...initial, h: 800 }])[0];
  const block = edit(manual, sentence, 550);
  assert.equal(getBlockRenderHeight(block), panel(block).y + panel(block).h + 140);
  assert.equal(edit(block, '', 0).h, 800);
});

test('a folded review author can be revealed in Layers without overflowing its bubble', () => {
  const input = structuredClone(fixtures.cases.find(c => c.name === 'b3b-review-no-author-emo').input);
  const block = assemblePage(input)[0];
  const author = block.elements.find(el => el.copyRole === 'items.0.who');
  const bg = block.elements.find(el => el.type === 'shape' && el.groupId === author.groupId);
  const revealed = fitTitlePlateBlocks([block], [{ ...block, elements: block.elements.map(el => el.id === author.id
    ? { ...el, hidden: false } : el) }])[0];
  assert.equal(revealed.elements.find(el => el.id === bg.id).h, bg.h + author.h + 6);
  assert.ok(!revealed.elements.find(el => el.id === author.id).hidden);
});
