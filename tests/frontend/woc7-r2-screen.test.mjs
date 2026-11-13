import test from 'node:test';
import assert from 'node:assert/strict';
import { storyboardScreenHarness } from './helpers/storyboardScreenHarness.mjs';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
import { readFileSync } from 'node:fs';
const button = (tree, label) => nodes(tree).find(n => n.type === 'button' && n.props['aria-label'] === label);
const toneValue = tree => nodes(tree).find(n => n.props?.['data-color-mode'])?.props['data-color-mode'];
const fontValue = tree => nodes(tree).find(n => n.props?.['data-font-style'])?.props['data-font-style'];
const band = tree => nodes(tree).find(n => n.type === 'svg' && n.props.className?.includes('stageToneBand'))?.props.children;
const bandFill = tree => nodes(band(tree)).find(n => n.type === 'rect')?.props.fill;

test('WOC9 pointer/focus keeps the selected tone; WOC12 design illustrations stay silver across tone changes', async t => {
  const h = await storyboardScreenHarness({ pairedFirst: true }); t.after(() => h.close());
  h.render(); await h.flush();
  const before = structuredClone(h.DB.analysis.pageStyle);
  nodes(h.tree).find(n => n.type === 'button' && textOf(n) === '섹션 디자인 변경').props.onClick(); h.render();
  const outline = () => nodes(h.tree).find(n => n.type === 'nav' && n.props['aria-label'] === '페이지 구성');
  const dialog = () => nodes(h.tree).find(n => n.props?.role === 'dialog' && n.props['aria-label'] === '섹션 디자인 바꾸기');
  const tinted = subtree => nodes(subtree).filter(n => n.type === 'rect' && (n.props.fill === '#edf4ff' || n.props.fill === 'color-mix(in srgb, var(--sb-wire-panel) 70%, #edf4ff)')).length;
  for (const event of ['onPointerEnter', 'onPointerLeave', 'onMouseEnter', 'onMouseLeave', 'onFocus', 'onBlur']) {
    const next = button(h.tree, '다음 페이지 색감');
    assert.equal(next.props[event], undefined); next.props[event]?.({ pointerType: 'mouse' }); h.render();
    assert.equal(bandFill(h.tree), 'var(--bg-2)');
    assert.equal(tinted(outline()), 0); assert.equal(tinted(dialog()), 0);
    assert.deepEqual(h.DB.analysis.pageStyle, before);
  }
  const silver=()=>nodes(dialog()).filter(n=>n.type==='svg'&&n.props['data-design-illustration']).map(svg=>nodes(svg).filter(n=>n.type==='rect').map(n=>({x:n.props.x,y:n.props.y,width:n.props.width,height:n.props.height,fill:n.props.fill})));
  const beforePictures=silver();assert.equal(beforePictures.length,2);
  button(h.tree, '다음 페이지 색감').props.onClick(); h.render();
  assert.equal(bandFill(h.tree), '#edf4ff');
  assert.ok(tinted(outline()) > 0); assert.equal(tinted(dialog()), 0);assert.deepEqual(silver(),beforePictures);
  await h.waitForStyle(); await h.flush();
  assert.equal(h.DB.analysis.pageStyle.colorMode, 'point');
});

test('R2 actual screen restores its last saved font and tone on the latest failure', async t => {
  const h = await storyboardScreenHarness(); t.after(() => h.close());
  h.render(); await h.flush();
  button(h.tree, '다음 글자 스타일').props.onClick(); h.render(); await h.waitForStyle(); await h.flush();
  const saved = structuredClone(h.DB.analysis.pageStyle), save = h.api.saveAnalysis;
  // Inject a transport failure only for this negative case. Success tests above
  // never replace the API/save function or its lock checks.
  h.api.saveAnalysis = async () => { throw new Error('test transport unavailable'); };
  t.after(() => { h.api.saveAnalysis = save; });
  button(h.tree, '다음 페이지 색감').props.onClick(); h.render(); await h.waitForStyle(); await h.flush();
  assert.deepEqual(h.DB.analysis.pageStyle, saved);
  assert.equal(fontValue(h.tree), 'plainGothic');
  assert.equal(toneValue(h.tree) === 'mono', true);
  assert.equal(bandFill(h.tree), 'var(--bg-2)');
  assert.match(textOf(h.tree), /페이지 스타일을 저장하지 못했어요/);
  button(h.tree, '다음 글자 스타일').props.onClick(); h.render(); await h.waitForStyle(); await h.flush();
  assert.equal(fontValue(h.tree), 'plainGothic');
  assert.notEqual(fontValue(h.tree), 'serif');
});

test('R2 a first failed selection restores prefetched non-default style before project loading completes', async t => {
  const h = await storyboardScreenHarness({ savedStyle: { fontStyle: 'boldGothic', colorMode: 'point' }, pauseLoad: true });
  t.after(() => h.close()); h.render(); await h.flush();
  const original = h.api.saveAnalysis;
  h.api.saveAnalysis = async () => { throw new Error('first request offline'); };
  t.after(() => { h.api.saveAnalysis = original; });
  button(h.tree, '이전 페이지 색감').props.onClick(); h.render(); await h.waitForStyle(); await h.flush();
  assert.equal(toneValue(h.tree) === 'point', true);
  assert.equal(fontValue(h.tree) === 'boldGothic', true);
  assert.equal(bandFill(h.tree), '#edf4ff');
  assert.match(textOf(h.tree), /페이지 스타일을 저장하지 못했어요/);
  assert.equal(h.DB.analysis.pageStyle.colorMode, 'point');
  await h.releaseLoad(); await h.flush();
  assert.equal(toneValue(h.tree) === 'point', true);
});

test('WOC8 R2 adjacent controls share one width and keep the heading copy top aligned', () => {
  const css = readFileSync('src/features/storyboard/StoryboardTone.module.css', 'utf8');
  assert.match(css, /grid-auto-columns: var\(--sb-style-controls-width\)/);
  const layout = readFileSync('src/features/storyboard/StoryboardLayout.module.css', 'utf8');
  assert.match(layout, /\.pageHeadWithTheme \{[^}]*align-items: start/);
  const source = readFileSync('src/features/storyboard/Storyboard.jsx', 'utf8');
  assert.match(source, /className=\{styles.pageHeadingCopy\}><h1>상세페이지 초안 구성<\/h1>\s*<p className=\{styles.introCopy\}/);
  const tokens = readFileSync('src/styles/tokens.css', 'utf8');
  assert.match(tokens, /--sb-style-controls-width: 256px/);
  assert.equal(256 * 2 + 20, 532);
});

for (const lockSource of ['cut', 'job']) test(`R2 actual Storyboard saves through the unchanged mock API with a mannequin ${lockSource}`, async t => {
  const h = await storyboardScreenHarness({ lockSource }); t.after(() => h.close());
  h.render(); await h.flush();
  button(h.tree, '다음 글자 스타일').props.onClick(); h.render(); await h.waitForStyle(); await h.flush();
  assert.equal(h.DB.analysis.pageStyle.fontStyle, 'plainGothic');
  button(h.tree, '다음 페이지 색감').props.onClick(); h.render(); await h.waitForStyle(); await h.flush();
  assert.equal(h.DB.analysis.pageStyle.colorMode, 'point');
  assert.equal(toneValue(h.tree) === 'point', true);
  assert.doesNotMatch(textOf(h.tree), /스타일을 저장하지 못했어요/);
});

for (const failure of ['first', 'last']) test(`R2 rapid actual selections use only the latest result when the ${failure} save fails`, async t => {
  const h = await storyboardScreenHarness(); t.after(() => h.close()); h.render(); await h.flush();
  const original = h.api.saveAnalysis, pending = [];
  h.api.saveAnalysis = (...args) => new Promise((resolve, reject) => pending.push({
    succeed: async () => resolve(await original(...args)), fail: () => reject(new Error('temporary failure')),
  }));
  t.after(() => { h.api.saveAnalysis = original; });
  button(h.tree, '다음 글자 스타일').props.onClick(); h.render();
  button(h.tree, '다음 페이지 색감').props.onClick(); h.render(); await h.flush();
  assert.equal(pending.length, 1);
  if (failure === 'first') pending[0].fail(); else await pending[0].succeed();
  await h.flush(); assert.equal(pending.length, 2);
  assert.equal(toneValue(h.tree) === 'point', true, 'an earlier response cannot replace the newer choice');
  if (failure === 'last') pending[1].fail(); else await pending[1].succeed();
  await h.waitForStyle(); await h.flush();
  assert.equal(h.DB.analysis.pageStyle.fontStyle, 'plainGothic');
  assert.equal(fontValue(h.tree), 'plainGothic');
  assert.equal(h.DB.analysis.pageStyle.colorMode, failure === 'last' ? 'mono' : 'point');
  assert.equal(toneValue(h.tree) === 'point', failure !== 'last');
  assert.equal(bandFill(h.tree), failure === 'last' ? 'var(--bg-2)' : '#edf4ff');
  assert.equal(textOf(h.tree).includes('페이지 스타일을 저장하지 못했어요'), failure === 'last');
});
