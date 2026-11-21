import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage, wrapText } from '../../src/lib/pageTemplates/assemble.js';
import { getBlockExportElements, getBlockRenderHeight } from '../../src/features/editor/editorGeometry.js';
const source = JSON.parse(readFileSync('tests/fixtures/page_copy_b3c.json'));
const fixtures = JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json'));
const input = name => structuredClone(fixtures.cases.find(c => c.name === name).input);

test('preview3 BROWN uses a box at least as wide as its bundled glyph advances and cannot break internally', () => {
  const blocks = assemblePage(source.colorInput);
  const brown = blocks.flatMap(b => b.elements).find(e => e.text === 'BROWN');
  assert.ok(brown.keepWords);
  const required = source.brownAdvanceUnits * brown.style.size / 1000 + 5 * brown.style.tracking;
  assert.ok(brown.w >= Math.ceil(required), `${brown.w} < ${required}`);
  assert.equal(brown.text, 'BROWN');
});

test('a single long English color word shrinks within the available column', () => {
  const value = structuredClone(source.colorInput);
  const word = 'MIDNIGHTBLUE'.repeat(6);
  value.product.colors[0].name = word;
  const text = assemblePage(value).flatMap(b => b.elements).find(e => e.text === word);
  assert.ok(text.keepWords);
  assert.ok(text.style.size < 28);
  assert.ok(text.x + text.w <= 1000);
  assert.equal(text.text.split('\n').length, 1);
});

test('shared wrapping and the opening English label only break at whitespace', () => {
  assert.deepEqual(wrapText('BROWN DARK BROWN', 100, 28), ['BROWN', 'DARK', 'BROWN']);
  const value = input('emo-2-mono');
  const label = assemblePage(value).flatMap(b => b.elements).find(e => e.copyRole === 'label' && /cotton/i.test(e.text));
  assert.ok(label.keepWords);
  assert.deepEqual(label.text.split(/\s+/), ['Daily', 'cotton', 'shirt']);
});

for (const name of ['copy-b-emo-I09-SCRIM-01-static-stars', 'copy-b-show-I09-SCRIM-01-static-stars']) test(`${name} fades from transparent and reaches readable opacity before its first line`, () => {
  const block = assemblePage(input(name))[0];
  const scrim = block.elements.find(e => e.fill?.startsWith('linear-gradient'));
  const stops = [...scrim.fill.matchAll(/rgba\(0, 0, 0, ([\d.]+)\) ([\d.]+)%/g)].map(m => ({ opacity: +m[1], offset: +m[2] / 100 }));
  assert.equal(stops.length, 3);
  assert.equal(stops[0].opacity, 0);
  assert.equal(stops[1].opacity, .56);
  assert.ok(stops[1].offset > 0 && stops[1].offset <= .3);
  const first = Math.min(...block.elements.filter(e => e.type === 'text' && !e.hidden && (e.copyRole === 'lead' || e.copyRole?.startsWith('items.'))).map(e => e.y));
  assert.ok(first >= scrim.y + scrim.h * stops[1].offset);
  const c = 1 - stops[1].opacity, luminance = ((c + .055) / 1.055) ** 2.4;
  assert.ok(1.05 / (luminance + .05) >= 4.5);
});

function titleOnly() {
  const value = input('copy-b-emo-empty-md');
  value.copyResults = [{ blockId: value.storyboard[0].id, texts: [{ role: 'title', text: '골지 니트 이야기' }] }];
  return assemblePage(value)[0];
}

test('export folds the empty intro body while the editor keeps its placeholder and geometry', () => {
  const block = titleOnly(), before = structuredClone(block);
  const body = block.elements.find(e => e.copyRole === 'body');
  const heading = block.elements.find(e => e.copyRole === 'title');
  const editorPanel = block.elements.find(e => e.type === 'shape' && e.groupId === body.groupId);
  assert.equal(body.placeholder, 'MD 코멘트');
  assert.ok(!body.hidden && body.h > 0);
  const exported = getBlockExportElements(block);
  const panel = exported.find(e => e.id === editorPanel.id);
  assert.equal(panel.h, heading.h + 44);
  assert.equal(panel.y + panel.h - heading.y - heading.h, 22);
  assert.ok(!exported.some(e => e.copyRole === 'body'));
  assert.equal(getBlockRenderHeight(block, { forExport: true }), block.h - body.h - 26);
  assert.deepEqual(block, before);
});

test('download DOM clone receives exactly the same compact geometry as preview', async () => {
  const { getBlockExportLayout } = await import('../../src/features/editor/editorGeometry.js');
  const { applyExportLayout } = await import('../../src/features/editor/editorExport.js');
  const block = titleOnly();
  const exported = getBlockExportElements(block);
  const layout = getBlockExportLayout(block);
  const nodes = block.elements.map(el => ({ dataset: { elid: el.id }, style: { top: `${el.y}px`, height: `${el.h}px` } }));
  applyExportLayout({ dataset: { exportLayout: JSON.stringify(layout) }, querySelectorAll: () => nodes });
  for (const el of exported) {
    const node = nodes.find(n => n.dataset.elid === el.id);
    assert.equal(node.style.top, `${el.y}px`);
    assert.equal(node.style.height, `${el.h}px`);
  }
  assert.match(readFileSync('src/features/editor/Editor.jsx', 'utf8'), /data-export-layout=/);
  assert.match(readFileSync('src/features/editor/editorExport.js', 'utf8'), /applyExportLayout\(clone\)/);
});

test('empty first paragraph is removed only from export and later panels move up together', () => {
  const value = input('copy-b-emo-empty-md');
  value.copyResults = [{ blockId: value.storyboard[0].id, texts: [{ role: 'body', text: '첫 문단이에요\n둘째 문단이에요' }] }];
  const block = assemblePage(value)[0];
  const bodies = block.elements.filter(el => el.copyRole === 'body');
  const empty = { ...block, elements: block.elements.map(el => el.id === bodies[0].id ? { ...el, text: '' } : el) };
  const originalPanels = block.elements.filter(el => el.type === 'shape' && el.groupId?.startsWith('template-copy:'));
  const result = getBlockExportElements(empty);
  const second = result.find(el => el.id === originalPanels[1].id);
  assert.equal(second.y, originalPanels[0].y);
  assert.equal(result.find(el => el.id === bodies[1].id).y, second.y + 22);
  assert.equal(empty.elements.find(el => el.id === originalPanels[1].id).y, originalPanels[1].y);
});
