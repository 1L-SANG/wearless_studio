import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { loadStoryboardComponent, hookHarness, nodes, textOf } from './helpers/storyboardComponents.mjs';
const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const layout = read('src/features/storyboard/StoryboardLayout.module.css');
const panelSource = read('src/features/storyboard/StoryboardPanels.module.css');
const panels = panelSource.replaceAll('.drawer:global(.sb-v5-theme)', '.drawer').replaceAll(':global(.sb-v5-theme) ', '');
const tokens = read('src/styles/tokens.css');
const theme = tokens.slice(tokens.indexOf('.sb-v5-theme {'), tokens.indexOf('\n}', tokens.indexOf('.sb-v5-theme {')));
const variables = new Map([...tokens.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, key, value]) => [key, value.trim()]));
const resolve = value => value.replace(/var\((--[\w-]+)\)/g, (_match, key) => resolve(variables.get(key) || key));
const rule = (css, selector) => {
  const start = css.indexOf(`${selector} {`); assert.ok(start >= 0, selector);
  return css.slice(start + selector.length + 2, css.indexOf('}', start));
};

test('WOC1b visual type roles resolve to six sizes and pastel ring tokens remain inherited', () => {
  const sizes = ['title', 'head', 'sub', 'body', 'btn', 'note', 'meta'].map(role => resolve(`var(--sb-fs-${role})`));
  assert.deepEqual(sizes, ['28px', '17px', '15px', '14px', '13px', '13px', '12px']);
  assert.equal(resolve('var(--sb-t-fast)'), '150ms'); assert.equal(resolve('var(--sb-t-base)'), '200ms'); assert.equal(resolve('var(--sb-spin)'), '9s');
  assert.doesNotMatch(theme + layout + panels, /--ring-flow-[1-6]\s*:/);
  assert.doesNotMatch(layout + panels, /#[\da-fA-F]{3,8}\b|rgba?\(|word-break\s*:/);
  assert.match(rule(layout, '.selectedSection .sectionSelect'), /border-color: var\(--fg-1\)/);
});
test('WOC1b visual outline, stage heading and metadata use the specified type and spacing', () => {
  assert.match(rule(layout, '.page:global(.sb-page) > :global(.page-head) h1'), /var\(--sb-fs-title\).*700.*1\.25.*-\.03em/);
  assert.match(resolve(rule(layout, '.sectionTitle em')), /600 13px\/1\.35/);
  assert.match(rule(layout, '.sectionTitle em'), /var\(--fg-2\)/);
  assert.match(resolve(rule(layout, '.stageHead h2 em')), /500 15px\/1\.2/);
  assert.match(rule(layout, '.sectionSelect'), /width: var\(--sb-toc-thumb\).*var\(--r-4\)/);
  assert.match(resolve(rule(layout, '.stageBody')), /padding: 16px 20px 24px/);
  assert.match(resolve(rule(layout, '.photoCaption')), /margin-top: 8px.*12px/);
  assert.match(rule(layout, '.photoCaption strong'), /font-weight: 500/);
  assert.match(rule(layout, '.dragging .insert::after'), /var\(--ring-flow-5\)/);
});
test('WOC1b visual drawer has an independent 48px header, the specified bounds, and themed body', () => {
  assert.match(resolve(rule(panels, '.drawer')), /top: 12px; right: 12px; bottom: 12px/);
  assert.match(rule(panels, '.drawer'), /width: var\(--sb-drawer-width\)/);
  assert.match(rule(panels, '.drawer'), /var\(--r-6\).*var\(--bg-1\).*var\(--sb-pop-shadow\)/);
  assert.match(rule(panels, '.drawerBar'), /height: 48px/);
  assert.match(resolve(rule(panels, '.drawerBody')), /padding: 4px 20px 24px.*overflow-y: auto/);
  assert.match(rule(panels, '.drawerScrolled'), /var\(--ring\)/);
  assert.match(rule(panels, '.close'), /width: 32px; height: 32px/);
});
test('WOC1b visual controls, selected photographs and previews use the approved dimensions', () => {
  assert.match(rule(layout, '.selectedPhoto::after'), /border: 2px solid var\(--fg-1\)/);
  for (const selector of ['.gallery .example:global(.sel), .sameSetPhotos .example:global(.sel)', '.setSelected']) {
    assert.match(rule(panels, selector), /inset 0 0 0 3px var\(--fg-1\)/);
  }
  assert.match(resolve(rule(panels, '.exampleModes:global(.modal-actions)')), /padding: 4px.*border-radius: 12px/);
  assert.match(rule(panels, '.exampleModes button'), /min-height: 40px.*var\(--r-4\)/);
  assert.match(rule(panels, '.shotPills button'), /min-height: 32px.*var\(--sb-line-control\).*var\(--r-pill\)/);
  assert.match(rule(panels, '.photoPanel :global(.color-dot)'), /width: 34px; height: 34px/);
  const design = read('src/features/storyboard/StoryboardDesignPanel.module.css');
  assert.match(rule(design, '.thumbnails'), /repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(rule(design, '.thumbnail svg'), /width: 100%; height: auto/);
  assert.match(rule(panels, '.menuPreview'), /width: 64px; height: 84px/);
  assert.match(rule(panels, '.roundPlus'), /width: 32px; height: 32px/);
});
test('WOC1b visual pagination cannot wrap arrows out of view and scrolls overflowing dots', () => {
  assert.match(rule(panels, '.gallery :global(.sb-excontrols)'), /flex-wrap: nowrap/);
  assert.match(rule(panels, '.gallery :global(.sb-expage-hit)'), /flex: 0 0 32px; width: 32px; height: 26px/);
  assert.match(rule(panels, '.gallery :global(.sb-expages)'), /flex-wrap: nowrap.*overflow-x: auto/);
  assert.match(rule(panels, '.gallery :global(.sb-expages button)'), /7px; width: 7px; height: 7px/);
  assert.match(rule(panels, '.gallery :global(.sb-expages button.on)'), /18px/);
  assert.match(rule(panels, '.gallery :global(.sb-expage-hit:disabled)'), /\.28/);
  assert.match(panels, /data-overflow.*mask-image/);
});
test('WOC1b visual next-step glass and inline undo stay at the bottom and hide accessibly for drawers', async () => {
  assert.match(rule(layout, '.continueBar'), /position: fixed/);
  assert.match(rule(layout, '.continueBar'), /var\(--glass-bg\).*var\(--glass-blur\).*var\(--elev-lift\)/);
  assert.match(resolve(rule(layout, '.continueBar')), /padding: 8px 8px 8px 20px/);
  assert.match(rule(layout, '.continueBar .continue'), /var\(--cta-height\).*var\(--sb-fs-sub\).*22px/);
  const Continue = await loadStoryboardComponent('StoryboardContinue.jsx', 'StoryboardContinue');
  const clicks = [], props = { count: 10, credits: 190, undoEntry: { operationCount: 2 }, onUndo: () => clicks.push('undo') };
  const tree = Continue(props); assert.match(textOf(tree), /2가지 바꿨어요·되돌리기/);
  nodes(tree).find(node => node.type === 'button' && textOf(node) === '되돌리기').props.onClick(); assert.deepEqual(clicks, ['undo']);
  const hidden = Continue({ ...props, hidden: true }); assert.equal(hidden.props['aria-hidden'], true);
  assert.ok(nodes(hidden).filter(node => node.type === 'button').every(node => node.props.disabled && node.props.tabIndex === -1));
  assert.doesNotMatch(read('src/features/storyboard/Storyboard.jsx'), /sb-undo-bar/);
});
test('WOC1b drawer scroll changes only its fixed header divider and retains focus handling', async () => {
  const h = hookHarness(), document = { activeElement: { focus() {} }, body: { classList: { add() {}, remove() {} } }, addEventListener() {}, removeEventListener() {} };
  const Drawer = await loadStoryboardComponent('StoryboardDrawer.jsx', 'StoryboardDrawer', { ...h.hooks, document, createPortal: value => value, Icon: 'svg' });
  let tree = h.render(Drawer, { mode: 'add' });
  const body = nodes(tree).find(node => node.props.className === 'drawerBody');
  assert.ok(body.props['data-storyboard-scroll']); assert.ok(!tree.props['data-storyboard-scroll']);
  body.props.onScroll({ currentTarget: { scrollTop: 200 } }); tree = h.render(Drawer, { mode: 'add' });
  assert.ok(nodes(tree).some(node => node.props.className === 'drawerBar drawerScrolled')); h.close();
});
test('WOC1b reduced motion disables new transitions and animations in the page and portals', () => {
  assert.match(layout, /prefers-reduced-motion: reduce[\s\S]*?\.continueBar \*.*transition: none !important; animation: none !important/);
  assert.match(panels, /prefers-reduced-motion: reduce[\s\S]*?animation: none !important; transition: none !important/);
});
test('WOC1b shared gallery and inspector CSS is limited to the v5 theme, including state rules', () => {
  const require = createRequire(createRequire(import.meta.url).resolve('vite'));
  const root = require('postcss').parse(panelSource);
  root.walkRules(rule => {
    if (rule.parent.type === 'atrule' && /keyframes$/i.test(rule.parent.name)) return;
    assert.ok(rule.selectors.every(selector => selector.includes(':global(.sb-v5-theme)') || selector.startsWith(':global(body.storyboard-drawer-open)') || selector.startsWith(':global(body.sb-v5-theme)')), rule.selector);
  });
});
test('WOC1b theme tokens reach sibling chrome only while the v4 storyboard is active and are removed on navigation', async () => {
  const h = hookHarness(), classes = new Set(), document = { body: { classList: { add: name => classes.add(name), remove: name => classes.delete(name) } } };
  const useTheme = await loadStoryboardComponent('useStoryboardTheme.js', 'useStoryboardTheme', { ...h.hooks, document });
  h.render(useTheme, true); assert.equal(classes.has('sb-v5-theme'), true);
  h.render(useTheme, false); assert.equal(classes.has('sb-v5-theme'), false);
  h.render(useTheme, true); h.close(); assert.equal(classes.has('sb-v5-theme'), false);
  assert.match(read('src/features/storyboard/Storyboard.jsx'), /useStoryboardTheme\(v4CopyReady\)/);
  assert.match(rule(panelSource, ':global(body.sb-v5-theme) :global(.topnav)'), /var\(--sb-t-base\) var\(--sb-ease\)/);
});
