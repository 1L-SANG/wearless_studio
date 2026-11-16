import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { getTemplate, pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { stageAdditions, addStageAddition, removePairedPhoto } from '../../src/lib/pageTemplates/stageAdditions.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';

const product = { name: '데일리 니트', clothingType: 'top', colors: [{ id: 'base', name: '아이보리', swatchId: 'ivory', isBase: true }] };
const analysisFor = templateId => ({ targetGenders: ['women'], mdComment: '단정하게 입기 좋은 니트입니다.', pageStyle: { ...defaultPageStyle(), templateId } });
const kinds = blocks => sectionRuns(blocks).map(run => run.kind);
const introOf = blocks => blocks.filter(block => block.pageSectionKind === 'intro');
const pageOrder = blocks => [...new Set(blocks.map(block => block.pageSectionKey).filter(Boolean))];
const board = template => seedStoryboard(product, analysisFor(template), { projectId: 'woc23-order' });
const withGhost = (blocks, template) => {
  const tile = stageAdditions(blocks, `${template}.first`, { product, analysis: analysisFor(template) }).find(item => item.id === 'firstGhost');
  assert.ok(tile);
  return addStageAddition(blocks, `${template}.first`, tile, { product, analysis: analysisFor(template) });
};
const inputOf = (storyboard, template) => ({ storyboard, product, analysis: analysisFor(template), copyResults: [],
  cutResults: storyboard.filter(block => block.source === 'ai').map(block => ({ blockId: block.id, imageUrl: `photo:${block.id}`, width: 1200, height: 1600 })) });

function bothAssemblers(input) {
  const blocks = assemblePage(input);
  const result = spawnSync(process.env.PAGE_TEMPLATE_PYTHON || 'server/.venv/bin/python', ['-c',
    "import json,sys;sys.path[:0]=['server','server/tests'];from page_template_cases import assemble_input;json.dump(assemble_input(json.load(sys.stdin)),sys.stdout,ensure_ascii=False)"],
  { input: JSON.stringify(input), encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), blocks);
  return blocks;
}

for (const template of ['emo', 'show']) for (const design of ['P01-SHOW-01', 'P01-SHOW-COMMENT-01']) {
  for (const ghost of [false, true]) for (const missing of [false, true]) {
    test(`WOC23 opening order ${template} ${design} ghost=${ghost} missing=${missing}`, () => {
      let initial = changeTemplateSectionDesign(board(template), `${template}.first`, 'P01-D09', { product });
      if (missing) initial = initial.filter(block => block.pageSectionKind !== 'intro');
      if (ghost) initial = withGhost(initial, template);
      const snapshot = structuredClone(initial), intro = introOf(initial);
      const next = changeTemplateSectionDesign(initial, `${template}.first`, design, { product });
      assert.deepEqual(kinds(next).slice(0, 2), ['first', 'intro']);
      assert.deepEqual(kinds(next).filter(kind => kind !== 'intro'), kinds(initial).filter(kind => kind !== 'intro'));
      assert.ok(next.filter(block => block.pageSectionKind === 'first').every(block => block.pageDesignId === `${design}${ghost ? '-G' : ''}`));
      if (missing) {
        assert.equal(introOf(next).length, 1);
        const definition = getTemplate(template).sections.find(section => section.kind === 'intro');
        assert.equal(introOf(next)[0].pageDesignId, definition.designId);
        assert.equal(introOf(next)[0].pageSectionKey, `${template}.${definition.instanceKey}`);
        assert.equal(introOf(next)[0].source, 'text');
      } else assert.deepEqual(introOf(next), intro);
      assert.equal(validateStoryboardV4(next), next);
      assert.deepEqual(initial, snapshot);
      const page = bothAssemblers(inputOf(next, template));
      assert.deepEqual(pageOrder(page).slice(0, 2), [`${template}.first`, `${template}.intro`]);
      assert.ok(page.filter(block => block.pageSectionKind === 'intro').every(block => block.pageDesignId === pageTemplatesCatalog.designs[design].introDesignId));
    });
  }
}

for (const template of ['emo', 'show']) test(`WOC23 ${template} switching back to an unlinked opening preserves the new saved order`, () => {
  const initial = changeTemplateSectionDesign(board(template), `${template}.first`, 'P01-SHOW-COMMENT-01', { product });
  const next = changeTemplateSectionDesign(initial, `${template}.first`, 'P01-D09', { product });
  assert.deepEqual(next.map(block => block.id), initial.map(block => block.id));
  assert.deepEqual(kinds(next).slice(0, 2), ['first', 'intro']);
  assert.deepEqual(pageOrder(bothAssemblers(inputOf(next, template))), pageOrder(assemblePage(inputOf(initial, template))));
});

for (const template of ['emo', 'show']) test(`WOC23 ${template} ghost attachment add/remove keeps the intro after the entire first run`, () => {
  const selected = changeTemplateSectionDesign(board(template), `${template}.first`, 'P01-SHOW-COMMENT-01', { product });
  const noProductPhotos = selected.filter(block => block.pageSectionKind !== 'color_product');
  const paired = withGhost(noProductPhotos, template);
  assert.equal(paired.filter(block => block.pageSectionKind === 'first').length, 2);
  assert.deepEqual(kinds(paired).slice(0, 2), ['first', 'intro']);
  assert.deepEqual(pageOrder(bothAssemblers(inputOf(paired, template))).slice(0, 2), [`${template}.first`, `${template}.intro`]);
  const plain = removePairedPhoto(paired, `${template}.first`, product);
  assert.deepEqual(plain, noProductPhotos);
});

test('WOC23 showroom default order and unrelated section order stay unchanged', () => {
  const initial = board('show');
  const next = changeTemplateSectionDesign(initial, 'show.first', 'P01-SHOW-COMMENT-01', { product });
  assert.deepEqual(next.map(block => block.id), initial.map(block => block.id));
  assert.deepEqual(introOf(next), introOf(initial));
});

test('WOC23 assemblers keep an old saved nonadjacent intro in its saved position', () => {
  const initial = board('emo').map(block => block.pageSectionKind === 'first' ? { ...block, pageDesignId: 'P01-SHOW-COMMENT-01' } : block);
  assert.deepEqual(kinds(initial).slice(0, 3), ['first', 'review', 'intro']);
  assert.deepEqual(pageOrder(bothAssemblers(inputOf(initial, 'emo'))).slice(0, 3), ['emo.first', 'emo.review', 'emo.intro']);
});

function designHarness(initial) {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
  let state = initial;
  const writes = [], undoEntryRef = { current: null };
  const scope = { blocks: initial, changeTemplateSectionDesign, drawer: { mode: 'design', runKey: 'emo.first' }, catalogs: { detailProduct: product },
    detailPageSubmitRef: { current: false }, undoEntryRef, setBlocks(value) { state = value; writes.push(value); }, setUndoExiting() {}, setUndoEntry() {}, scheduleUndoDismiss() {},
    dismissUndo() { undoEntryRef.current = null; }, setPicker: null, setInsertedSectionKey() {}, toast: { push() {} } };
  const handlers = new Function('scope', `with (scope) {
    ${slice('  const showUndo =', '  const clearUndoForSnapshot =')}
    ${slice('  const undoLatest =', '  const enterUndo =')}
    ${slice('  const changeDesign =', '  const selectSection =')}
    return { changeDesign, undoLatest };
  }`)(scope);
  return { get blocks() { return state; }, get undo() { return undoEntryRef.current; }, writes,
    choose(id) { scope.blocks = state; handlers.changeDesign(id); }, undoLatest() { scope.blocks = state; handlers.undoLatest(); } };
}

for (const missing of [false, true]) test(`WOC23 real design action changes the whole board once and one undo restores design and intro placement missing=${missing}`, () => {
  const initial = board('emo').filter(block => !missing || block.pageSectionKind !== 'intro'), snapshot = structuredClone(initial);
  const harness = designHarness(initial);
  harness.choose('P01-SHOW-COMMENT-01');
  assert.equal(harness.writes.length, 1);
  assert.equal(harness.undo.operationCount, 1);
  assert.equal(harness.undo.standalone, true);
  assert.deepEqual(harness.undo.before, snapshot);
  assert.deepEqual(kinds(harness.blocks).slice(0, 2), ['first', 'intro']);
  assert.equal(harness.blocks[0].pageDesignId, 'P01-SHOW-COMMENT-01');
  harness.undoLatest();
  assert.deepEqual(harness.blocks, snapshot);
  assert.equal(harness.undo, null);
  assert.equal(harness.writes.length, 2);
});
