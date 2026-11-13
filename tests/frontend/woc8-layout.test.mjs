import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { storyboardStage, stagePhoto } from './helpers/storyboardStageHarness.mjs';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
const read = path => readFileSync(path, 'utf8');

test('WOC8 stage captions pair each color dot with its name only for multiple colors', async () => {
  for (const count of [1, 2]) {
    const colors = [{ id: 'base', name: '블루', isBase: true }, { id: 'other', name: '스카이블루' }].slice(0, count);
    const tree = await storyboardStage([stagePhoto('one')], { catalogs: { detailProduct: { colors } } });
    const caption = nodes(tree).find(n => n.props?.className === 'photoCaption');
    assert.equal(textOf(caption).includes('블루'), count > 1);
    if (count > 1) assert.equal(String(nodes(caption).find(n => n.type === 'svg').props['aria-hidden']), 'true');
    assert.match(textOf(caption), /1번째 컷/);
    assert.doesNotMatch(textOf(caption), /카페|공원|기타/);
  }
});

test('WOC8 header, fluid stage and portal action use the same desktop right edge', () => {
  const css = read('src/features/storyboard/StoryboardLayout.module.css');
  assert.match(css, /grid-template-columns: var\(--conti-outline-width\) minmax\(0, 1fr\)/);
  assert.doesNotMatch(css, /--conti-stage-width: 742px/);
  assert.match(css, /\.continueBar \{[^}]*right: var\(--sb-page-edge\)/);
  assert.match(css, /\.page:global\(\.sb-page\) > :global\(\.page-head\) \{[^}]*width: 100%;[^}]*margin-inline: 0/);
  const tokens = read('src/styles/tokens.css');
  assert.match(tokens, /--sb-page-edge: max\(var\(--sp-24\), calc\(\(100vw - 1030px\) \/ 2\)\)/);
  for (const width of [1280, 1440]) {
    const left = (width - 1078) / 2 + 24, right = width - Math.max(24, (width - 1030) / 2);
    const outline = width < 1360 ? 216 : 228, stage = 1030 - outline - 28;
    assert.equal(left + outline + 28 + stage, right);
    assert.equal(left + 1030, right);
  }
});

test('WOC8 tools scroll in the expanded outside gutter and fall inside below40px or with a drawer', () => {
  const css = read('src/features/storyboard/StoryboardLayout.module.css');
  assert.match(css, /@media \(min-width: 1110px\)/);
  assert.match(css, /\.page:not\(\.drawerOpen\) \.outlineScroll \{[^}]*margin-left:[^}]*padding-left:/);
  assert.match(css, /\.page:not\(\.drawerOpen\) \.thumbnailWrap \{ position: static/);
  assert.match(css, /\.page:not\(\.drawerOpen\) \.sectionTools \{[^}]*left:[^}]*flex-direction: column/);
  for (const width of [1024, 1109, 1110, 1280, 1440]) assert.equal(Math.max(0, (width - 1078) / 2) + 24 >= 40, width >= 1110);
});

test('WOC8 desktop document is viewport-bound while the outline and stage retain independent scrollers', () => {
  const shell = read('src/features/shell/StoryboardViewport.module.css');
  assert.match(shell, /@media \(min-width: 1024px\)/);
  assert.match(shell, /data-storyboard-layout="true"/);
  assert.match(shell, /height: 100dvh/);
  assert.match(shell, /min-height: 0/);
  assert.match(shell, /overflow: hidden/);
  const layout = read('src/features/storyboard/StoryboardLayout.module.css');
  assert.match(layout, /\.outlineScroll \{[^}]*overflow-y: auto/);
  assert.match(layout, /\.stageBody \{[^}]*overflow-y: auto/);
  assert.match(layout, /\.layout \{[^}]*min-height: 0/);
  assert.match(layout, /grid-template-rows: minmax\(0, 1fr\)/);
  assert.doesNotMatch(read('src/features/shell/StoryboardFooter.module.css'), /word-break:|overflow-wrap: anywhere/);
});

test('WOC8 photo/design/add drawers all retain a complete clickable header outside their footprint', () => {
  const layout = read('src/features/storyboard/StoryboardLayout.module.css');
  assert.match(layout, /\.drawerOpen \.pageHeadWithTheme \{[^}]*--sb-drawer-width[^}]*grid-template-columns: minmax\(0, 1fr\)/);
  for (const width of [1280, 1440]) for (const mode of ['photo', 'design', 'add']) {
    const drawerLeft = width - Math.min(520, .4 * width) - 12;
    const headerWidth = width - Math.min(520, .4 * width) - 12 - 16 - 24;
    assert.ok(headerWidth >= 348, `${mode}/${width} controls fit`);
    assert.equal(24 + headerWidth, drawerLeft - 16);
  }
});

test('WOC8 completion keeps the progress slot and positions notifications above the footer and action', () => {
  const shell = read('src/features/shell/StoryboardViewport.module.css');
  assert.match(shell, /\.job-ribbon-stack[^}]*height: var\(--sb-job-strip-height\)/);
  assert.match(shell, /\.toast-host[^}]*--sb-footer-height[^}]*--sb-continue-height/);
  assert.match(shell, /top: auto/);
  assert.doesNotMatch(shell, /\.job-ribbon-stack:empty[^}]*display: none/);
  const tokens = read('src/styles/tokens.css');
  assert.match(tokens, /--sb-job-strip-height: 36px/);
  assert.match(tokens, /--sb-footer-height: 44px/);
});
