import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import { measureEditedTemplateContrast, measureWrappedTextLines } from '../../src/features/editor/editedTemplateContrast.js';
import { waitForEditedContrast } from '../../src/features/editor/editorContrastPending.js';
import { createEditorContrastStore } from '../../src/features/editor/editorContrastStore.js';
import { pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { hookHarness } from './helpers/storyboardComponents.mjs';
import { loadEditedContrastHook } from './helpers/editorContrast.mjs';

function textNodeFor(text) {
  const textNode = { textContent: text }, rects = [];
  let row = 0, col = 0, index = 0;
  for (const char of text) {
    rects.push({ left: col * 10, right: col * 10 + 10, top: row * 30, bottom: row * 30 + 20, height: 20 });
    if (char === '\n') { row++; col = 0; }
    else if (++col === 3) { row++; col = 0; }
  }
  const bounds = { left: 0, top: 0, width: 30 };
  return { offsetWidth: 30, getBoundingClientRect: () => bounds, ownerDocument: {
    defaultView: { getComputedStyle: () => ({ lineHeight: '30px' }) },
    createTreeWalker: () => { let used = false; return { nextNode: () => used ? null : (used = true, textNode) }; },
    createRange: () => ({ setStart: (_node, i) => { index = i; }, setEnd() {}, getBoundingClientRect: () => rects[index] }),
  } };
}

for (const [text, expected] of [
  ['\n제목', ['', '제목']], ['\n\n제목', ['', '', '제목']],
  ['첫줄\n\n끝줄', ['첫줄', '', '끝줄']], ['제목\n', ['제목', '']],
  ['제목\n\n', ['제목', '', '']], ['\n\n', ['', '', '']],
  ['\n가나다라마\n\n끝\n', ['', '가나다', '라마', '', '끝', '']],
]) test(`WOC15 measured lines preserve explicit blanks: ${JSON.stringify(text)}`, () => {
  assert.deepEqual(measureWrappedTextLines(textNodeFor(text)).map(line => line.text), expected);
});

const title = { id: 'title', type: 'text', text: '밝고\n어두운 제목', keepWords: false,
  x: 10, y: 20, w: 200, style: { color: 'white', size: 30 }, contrastFor: { style: { color: 'white' } } };
const lines = [{ text: '밝고', color: '#111111', shade: false }, { text: '어두운 제목', color: '#ffffff', shade: true }];

test('WOC15 measured ink is shared by editor, full preview, mini preview and the export source without changing saved elements', async () => {
  const store = createEditorContrastStore();
  const h = [hookHarness(), hookHarness(), hookHarness()];
  const node = {}, root = { contains: item => item === node };
  let finish;
  const outputs = [], before = structuredClone(title);
  let measurements = 0;
  const hooks = h.map(harness => loadEditedContrastHook(harness,
    () => { measurements++; return new Promise(resolve => { finish = resolve; }); }, store));
  const run = (i, element = title) => h[i].render(() => hooks[i](element, { current: node }, { editing: false, preview: i > 0 }), {});
  try {
    run(0); run(1); run(2);
    finish(lines); await waitForEditedContrast([root]);
    for (let i = 0; i < 3; i++) outputs.push(run(i));
    for (const output of outputs) assert.deepEqual(output.contrastLines, lines);
    assert.deepEqual(title, before, 'temporary ink must never enter persisted blocks');
    assert.equal(measurements, 1, 'previews use the editor measurement instead of scaled pixels');
    const renderLines = await contrastMarkup();
    const markup = outputs.map(renderLines);
    assert.equal(markup[0], markup[1]); assert.equal(markup[0], markup[2]);
    assert.match(markup[0], /is-dark/); assert.match(markup[0], /is-light has-shade/);
    assert.equal(await exportCloneMarkup(markup[0]), markup[0]);
    const changed = { ...title, text: '새 제목' };
    assert.equal(run(1, changed), changed, 'old measurements must not color changed text');
    const customInk = { ...title, style: { ...title.style, color: 'red' } };
    assert.equal(run(2, customInk), customInk, 'seller ink takes priority over the shared cache');
    assert.equal(run(1, { ...title, id: 'other' }).contrastLines, undefined, 'cache is keyed by element id');
    const other = hookHarness();
    try {
      const hook = loadEditedContrastHook(other, () => assert.fail('preview must not measure'), createEditorContrastStore());
      assert.equal(other.render(() => hook(title, { current: node }, { preview: true }), {}), title, 'projects never share ink');
    } finally { other.close(); }
  } finally { h.forEach(harness => harness.close()); }
});

async function contrastMarkup() {
  const raw = readFileSync('src/features/editor/TemplateContrastText.jsx', 'utf8')
    .replace(/^import .*;\n/gm, '').replaceAll('export function', 'function');
  const { code } = await transformWithEsbuild(raw, 'TemplateContrastText.jsx', { jsx: 'transform' });
  const Component = new Function('React', 'Fragment', 'pageTemplatesCatalog', `${code}; return TemplateContrastText;`)(React, React.Fragment, pageTemplatesCatalog);
  return element => renderToStaticMarkup(React.createElement(Component, { element }));
}

async function exportCloneMarkup(markup) {
  const raw = readFileSync('src/features/editor/editorExport.js', 'utf8');
  const source = raw.slice(raw.indexOf('async function prepareClone('), raw.indexOf('export function flattenOnWhite'));
  const document = { createElement: () => ({ style: {}, appendChild() {}, remove() {} }), body: { appendChild() {} } };
  const prepare = new Function('document', 'EXPORT_WIDTH', 'CHROME_SELECTOR', 'replaceGifPosters', 'preflightImages', `${source};return prepareClone;`)
    (document, 1000, '.editor-only', () => {}, async () => ({ softFailed: 0 }));
  const result = await prepare({ cloneNode: () => ({ innerHTML: markup, style: {}, dataset: {}, classList: { remove() {} }, querySelectorAll: () => [] }) });
  result.dispose();
  return result.clone.innerHTML;
}

test('WOC15 leading, middle and trailing blank lines stay in the screen and download DOM', async () => {
  const renderLines = await contrastMarkup();
  const node = textNodeFor('\n\n가\n\n나\n');
  node.isConnected = true; node.closest = () => ({ querySelectorAll: () => [] });
  const contrastLines = await measureEditedTemplateContrast(node);
  assert.deepEqual(contrastLines.map(line => line.text), ['', '', '가', '', '나', '']);
  const html = renderLines({ contrastLines });
  assert.equal((html.match(/<br\/>/g) || []).length, 5);
  assert.equal((html.match(/<span /g) || []).length, 6);
  assert.equal(await exportCloneMarkup(html), html);
});

test('WOC15 an older asynchronous measurement cannot replace the latest title ink', async () => {
  const h = hookHarness(), store = createEditorContrastStore(), jobs = [], node = {};
  const hook = loadEditedContrastHook(h, () => new Promise(resolve => jobs.push(resolve)), store);
  const run = element => h.render(() => hook(element, { current: node }, { editing: false, preview: false }), {});
  try {
    run(title);
    const next = { ...title, text: '다음 제목' }; run(next);
    jobs[1]([{ text: next.text, color: '#111111', shade: true }]);
    await waitForEditedContrast([{ contains: item => item === node }]);
    jobs[0](lines); await Promise.resolve();
    assert.deepEqual(run(next).contrastLines, [{ text: '다음 제목', color: '#111111', shade: true }]);
  } finally { h.close(); }
});
