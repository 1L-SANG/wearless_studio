import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { fitTitlePlateBlocks, introPanelFor } from '../../src/features/editor/editorTitlePlateFit.js';
import { isIntroAvatarElement } from '../../src/features/editor/editorIntroAvatar.js';
import { hasTitlePlate } from '../../src/lib/pageTemplates/titlePlate.js';
import { hookHarness } from './helpers/storyboardComponents.mjs';
import { editedTextElement } from '../../src/features/editor/editorTextEdit.js';
import { getBlockExportElements, expandBlockHeights } from '../../src/features/editor/editorGeometry.js';
import { withMockPartDescriptions } from '../../src/mock/partDescriptionCopy.js';

const fixture = JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json', 'utf8'));
export function intro(seller = '', body = '') {
  return assemblePage({ storyboard: [{ id: 'intro', taxonomyVersion: 4, source: 'text', sectionId: 'intro',
    pageSectionKey: 'emo.intro', pageSectionKind: 'intro', pageDesignId: 'T01-D07', slotKey: null }],
  product: { name: '셔츠', colors: [] }, analysis: { mdComment: seller },
  copyResults: body ? [{ blockId: 'intro', texts: [{ role: 'body', text: body }] }] : [] })[0];
}
const panel = block => block.elements.find(el => el.type === 'shape' && el.groupId?.startsWith('template-copy:')
  && block.elements.some(text => text.copyRole === 'body' && text.groupId === el.groupId));
const bodyOf = block => block.elements.find(el => el.copyRole === 'body');

for (const design of ['I09-D06', 'I09-SCRIM-01']) test(`${design} stars follow their intended background color`, () => {
  const value = structuredClone(fixture.cases.find(c => c.name === 'emo-2-point').input);
  value.storyboard = value.storyboard.filter(b => b.pageSectionKind === 'review').map(b => ({ ...b, pageDesignId: design }));
  const elements = assemblePage(value)[0].elements;
  const stars = elements.filter(el => el.copyRole?.endsWith('.stars'));
  assert.equal(stars.length, 3);
  for (const star of stars) {
    const text = elements.find(el => el.copyRole === star.copyRole.replace('.stars', '.text'));
    if (design === 'I09-SCRIM-01') {
      assert.equal(star.style.color, '#ffffff');
      assert.equal(star.style.color, text.style.color);
    } else assert.equal(star.style.color, '#4b648b');
  }
});

test('empty intro uses the full content width while populated intro keeps its avatar column', () => {
  const empty = intro();
  assert.deepEqual([panel(empty).x, panel(empty).w, bodyOf(empty).x, bodyOf(empty).w], [56, 888, 82, 836]);
  assert.ok(!empty.elements.some(el => !el.hidden && (el.copyRole === 'who' || el.text === 'MD')));
  const filled = intro('셀러 본문');
  assert.equal(panel(filled).x, 175);
  assert.ok(panel(filled).w <= 769);
  assert.ok(filled.elements.some(el => el.copyRole === 'who'));
});

for (const seller of ['', '원래 본문']) test(`intro background follows measured text growth and shrink, seller=${Boolean(seller)}`, () => {
  const before = [intro(seller)], block = before[0], body = bodyOf(block), background = panel(block);
  const text = '셀러가 길게 고친 상품 설명';
  const edited = [{ ...block, elements: block.elements.map(el => el.id === body.id
    ? editedTextElement(el, { text, h: body.h + 275 }) : el) }];
  const fitted = fitTitlePlateBlocks(before, edited);
  assert.equal(panel(fitted[0]).h, body.h + 275 + 44);
  assert.equal(panel(fitted[0]).w, seller ? background.w : 769);
  assert.equal(bodyOf(fitted[0]).aiDraft, undefined);
  assert.deepEqual(block, intro(seller), 'undo input remains intact');
  assert.ok(fitted[0].elements.some(el => el.copyRole === 'who' && !el.hidden));
  const exported = getBlockExportElements(expandBlockHeights(fitted)[0]);
  assert.ok(exported.some(el => el.id === background.id));
  assert.ok(bodyOf(fitted[0]).y + bodyOf(fitted[0]).h <= panel(fitted[0]).y + panel(fitted[0]).h);
  const shorter = [{ ...fitted[0], elements: fitted[0].elements.map(el => el.id === body.id
    ? editedTextElement(el, { text: '짧은 본문', h: body.h }) : el) }];
  assert.equal(panel(fitTitlePlateBlocks(fitted, shorter)[0]).h, body.h + 44);
  assert.equal(fitTitlePlateBlocks(before, before), before);
  const resized = [{ ...block, elements: block.elements.map(el => el.id === background.id ? { ...el, h: 50 } : el) }];
  assert.equal(fitTitlePlateBlocks(before, resized), resized, 'manual background resize is preserved');
});

test('mock refresh retains part and feature labels without mutating saved rows', () => {
  const value = structuredClone(fixture.cases.find(c => c.name === 'emo-2-mono').input);
  const name = '단추';
  value.analysis.detailRecommendations.candidates.find(row => row.id === 'collar').label = name;
  const analysis = { ...value.analysis,
    partDescriptionCopy: [{ point: name, desc: '기존 설명', label: '손님 말' }, { point: '미선택', desc: '보존', label: '기존 이름' }],
    featureCopy: [{ point: name, desc: '특징 설명', label: '특징 이름' }] };
  const snapshot = structuredClone(analysis);
  const result = withMockPartDescriptions(value.storyboard, analysis, true);
  assert.equal(result.partDescriptionCopy.find(row => row.point === name).label, '손님 말');
  assert.notEqual(result.partDescriptionCopy.find(row => row.point === name).desc, '기존 설명');
  assert.deepEqual(result.partDescriptionCopy.find(row => row.point === '미선택'), snapshot.partDescriptionCopy[1]);
  assert.deepEqual(result.featureCopy, snapshot.featureCopy);
  assert.deepEqual(analysis, snapshot);
});

test('typing previews the intro background and cancel restores it with the avatar and cancel restores the hidden state', () => {
  const block = intro(), element = bodyOf(block), background = panel(block);
  const nodes = block.elements.map(el => ({ dataset: { elid: el.id }, style: {} }));
  const node = nodes.find(el => el.dataset.elid === element.id);
  node.offsetHeight = element.h + 220;
  node.parentElement = { querySelectorAll: () => nodes };
  const source = readFileSync('src/features/editor/editorTitlePlatePreview.js', 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export function', 'function');
  const h = hookHarness();
  const hook = new Function('useLayoutEffect', 'useRef', 'fitTitlePlateBlocks', 'hasTitlePlate', 'introPanelFor', 'isIntroAvatarElement', source + ';return useTitlePlatePreview;')(
    h.hooks.useEffect, h.hooks.useRef, fitTitlePlateBlocks, hasTitlePlate, introPanelFor, isIntroAvatarElement);
  const render = editing => h.render(() => hook(element, block.elements, { current: node }, editing), {});
  try {
    assert.deepEqual(render(true)(node, '입력하는 긴 본문'), { h: element.h + 220 });
    const rectangle = nodes.find(el => el.dataset.elid === background.id);
    assert.equal(rectangle.style.height, `${element.h + 220 + 44}px`);
    assert.equal(rectangle.style.width, '769px');
    assert.equal(nodes.find(n => n.dataset.elid === block.elements.find(el => el.copyRole === 'who').id).style.display, '');
    render(false);
    assert.equal(rectangle.style.height, `${background.h}px`);
    assert.equal(block.elements.length, 7);
    assert.equal(nodes.find(n => n.dataset.elid === block.elements.find(el => el.copyRole === 'who').id).style.display, 'none');
  } finally { h.close(); }
});

test('growing the first paragraph moves following linked panels without changing their spacing', () => {
  const block = intro('', '첫 문단이에요\n둘째 문단이에요');
  const bodies = block.elements.filter(el => el.copyRole === 'body');
  const before = [block];
  const next = [{ ...block, elements: block.elements.map(el => el.id === bodies[0].id ? { ...el, text: '길어진 첫 문단', h: el.h + 110 } : el) }];
  const after = fitTitlePlateBlocks(before, next)[0];
  assert.equal(after.elements.find(el => el.id === bodies[1].id).y, bodies[1].y + 110);
  const secondPanel = introPanelFor(bodies[1], block.elements);
  assert.equal(after.elements.find(el => el.id === secondPanel.id).y, secondPanel.y + 110);
  assert.equal(after.elements.find(el => el.id === secondPanel.id).h, secondPanel.h);
});
