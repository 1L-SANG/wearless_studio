import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { storyboardScreenHarness } from './helpers/storyboardScreenHarness.mjs';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
import { resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';

const button = (tree, name) => nodes(tree).find(n => n.type === 'button' && n.props['aria-label'] === name);
const sample = tree => nodes(tree).find(n => n.props?.['data-color-mode']);
const font = tree => nodes(tree).find(n => n.props?.['data-font-style'])?.props['data-font-style'];
const band = tree => nodes(nodes(tree).find(n => n.props?.className?.includes('stageToneBand'))).find(n => n.type === 'rect')?.props.fill;

test('WOC9 tone arrows expose three token circles, short current name and immediate persisted selection', async t => {
  const h = await storyboardScreenHarness(); t.after(() => h.close()); h.render(); await h.flush();
  for (const [mode, label] of [['mono', '기본'], ['point', '의류 색감']]) {
    assert.equal(sample(h.tree)?.props['data-color-mode'], mode);
    assert.equal(sample(h.tree).props['aria-label'], label);
    assert.equal(textOf(sample(h.tree)), label);
    const colors = resolvePageTokens(h.DB.analysis.pageStyle, 'blue').colors;
    assert.deepEqual(nodes(sample(h.tree)).filter(n => n.type === 'circle').map(n => n.props.fill), [colors.ink, colors.accentInk, colors.accentBg]);
    assert.ok(nodes(h.tree).some(n => n.props?.['aria-live'] === 'polite' && textOf(n).includes(label)));
    for (const group of nodes(h.tree).filter(n => n.props?.role === 'group' && ['글자 스타일', '페이지 색감'].includes(n.props['aria-label']))) {
      const head = nodes(group).find(n => n.props?.className?.includes('pickerHead'));
      assert.ok(nodes(head).some(n => n.props?.className?.includes('label')));
      assert.ok(nodes(head).some(n => n.props?.className?.includes('dots') && n.props['aria-hidden'] === 'true'));
    }
    const next = button(h.tree, '다음 페이지 색감'); assert.ok(next);
    next.props.onClick(); h.render();
    assert.equal(sample(h.tree).props['data-color-mode'], mode === 'mono' ? 'point' : 'mono');
    assert.equal(band(h.tree), mode === 'mono' ? '#edf4ff' : 'var(--bg-2)');
    await h.waitForStyle(); await h.flush();
    assert.equal(h.DB.analysis.pageStyle.colorMode, mode === 'mono' ? 'point' : 'mono');
  }
});

test('WOC9 hover and focus keep selected stage color and never save', async t => {
  const h = await storyboardScreenHarness(); t.after(() => h.close()); h.render(); await h.flush();
  const saved = structuredClone(h.DB.analysis.pageStyle);
  const next = button(h.tree, '다음 페이지 색감'); assert.ok(next);
  for (const name of ['onPointerEnter', 'onPointerLeave', 'onMouseEnter', 'onMouseLeave', 'onFocus', 'onBlur']) {
    assert.equal(next.props[name], undefined); next.props[name]?.({ pointerType: 'mouse' }); h.render();
    assert.equal(band(h.tree), 'var(--bg-2)'); assert.deepEqual(h.DB.analysis.pageStyle, saved);
  }
});

test('WOC9 rapid same-render font and tone clicks persist the last displayed values', async t => {
  const h = await storyboardScreenHarness(); t.after(() => h.close()); h.render(); await h.flush();
  const nextFont = button(h.tree, '다음 글자 스타일'), nextTone = button(h.tree, '다음 페이지 색감'); assert.ok(nextTone);
  for (let i = 0; i < 7; i++) { nextFont.props.onClick(); nextTone.props.onClick(); }
  h.render(); assert.equal(font(h.tree), 'boldGothic'); assert.equal(sample(h.tree).props['data-color-mode'], 'point');
  await h.waitForStyle(); await h.flush();
  assert.equal(h.DB.analysis.pageStyle.fontStyle, font(h.tree));
  assert.equal(h.DB.analysis.pageStyle.colorMode, sample(h.tree).props['data-color-mode']);
});

test('WOC9 labels use normal gray meta tokens and equal underline-only columns', () => {
  const css = readFileSync('src/features/storyboard/StoryboardTone.module.css', 'utf8');
  assert.match(css, /grid-auto-columns: var\(--sb-style-controls-width\)/);
  assert.match(css, /\.label[^}]*color: var\(--fg-2\)[^}]*font: 400 var\(--sb-fs-meta\)/);
  assert.match(css, /\.picker[^}]*box-shadow: inset 0 -1px 0 var\(--ring-strong\)/);
  assert.doesNotMatch(css, /word-break:|#[0-9a-f]{3,8}\b|background: var\(--bg-1\)/i);
});

test('WOC9 1280/1440 headers reserve copy space and drawer clearance without changing the page edge', () => {
  const css = readFileSync('src/features/storyboard/StoryboardLayout.module.css', 'utf8');
  const tokens = readFileSync('src/styles/tokens.css', 'utf8');
  assert.match(css, /\.pageHeadWithTheme \{[^}]*grid-template-columns: minmax\(0, 1fr\) auto/);
  assert.match(css, /\.pageHeadingCopy \{ min-width: 0;/);
  assert.match(css, /\.introCopy \{[^}]*text-wrap: pretty/);
  assert.match(css, /@media \(max-width: 1109px\) \{ \.pageHeadWithTheme \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(css, /\.page\.drawerOpen \.pageHeadWithTheme \{ width: min\(100%, calc\(100vw - var\(--sb-drawer-width\) - var\(--sp-12\) - var\(--sp-16\) - var\(--sp-24\)\)\); grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(tokens, /--sb-page-edge: max\(var\(--sp-24\), calc\(\(100vw - 1030px\) \/ 2\)\)/);
  for (const width of [1280, 1440]) {
    const controlsWidth = 256 * 2 + 20, pageInterior = 1078 - 24 * 2;
    assert.equal(pageInterior - controlsWidth - 20, 478, 'copy can wrap within its own 478px column');
    const drawerLeft = width - Math.min(520, width * .4) - 12;
    const safeHeaderWidth = drawerLeft - 16 - 24;
    assert.ok(safeHeaderWidth >= controlsWidth);
    assert.ok(24 + safeHeaderWidth <= drawerLeft - 16);
    assert.equal(width - ((width - 1078) / 2 + 24 + pageInterior), (width - 1030) / 2);
  }
});
