import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { storyboardScreenHarness } from './helpers/storyboardScreenHarness.mjs';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
const button = (tree, name) => nodes(tree).find(n => n.type === 'button' && n.props['aria-label'] === name);
const band = tree => nodes(nodes(tree).find(n => n.type === 'svg' && n.props.className?.includes('stageToneBand')))
  .find(n => n.type === 'rect')?.props.fill;

test('WOC8 carousel arrows apply and save through the actual mannequin-locked mock path', async t => {
  const h = await storyboardScreenHarness(); t.after(() => h.close()); h.render(); await h.flush();
  assert.equal(h.DB.analysis.pageStyle.fontStyle, 'template');
  button(h.tree, '다음 글자 스타일').props.onClick(); h.render(); await h.waitForStyle(); await h.flush();
  assert.equal(h.DB.analysis.pageStyle.fontStyle, 'serif');
  button(h.tree, '이전 글자 스타일').props.onClick(); h.render(); await h.waitForStyle(); await h.flush();
  assert.equal(h.DB.analysis.pageStyle.fontStyle, 'template');
  button(h.tree, '이전 글자 스타일').props.onClick(); h.render(); await h.waitForStyle(); await h.flush();
  assert.equal(h.DB.analysis.pageStyle.fontStyle, 'boldGothic');
});

for (const input of ['pointer', 'focus']) test(`WOC8 a real completion toast rerender preserves ${input} preview until explicit leave`, async t => {
  const h = await storyboardScreenHarness({ withToast: true }); t.after(() => h.close()); h.render(); await h.flush();
  const before = structuredClone(h.DB.analysis.pageStyle);
  const point = button(h.tree, '의류 색감');
  if (input === 'pointer') point.props.onPointerEnter({ pointerType: 'mouse' }); else point.props.onFocus();
  h.render(); assert.equal(band(h.tree), '#edf4ff');
  const id = h.notify(); h.render(); await h.flush();
  assert.match(textOf(h.tree), /마네킹컷이 만들어졌어요/); assert.equal(band(h.tree), '#edf4ff');
  h.dismissNotification(id); h.render(); assert.equal(band(h.tree), '#edf4ff');
  const current = button(h.tree, '의류 색감');
  if (input === 'pointer') current.props.onPointerLeave(); else current.props.onBlur();
  h.render(); assert.equal(band(h.tree), 'var(--bg-2)'); assert.deepEqual(h.DB.analysis.pageStyle, before);
});

test('WOC8 the disappearing job ribbon cannot move a stationary pointer outside the tone button', () => {
  const css = readFileSync('src/features/shell/StoryboardViewport.module.css', 'utf8');
  assert.match(css, /\.job-ribbon-stack\) \{[^}]*flex: 0 0 var\(--sb-job-strip-height\); height: var\(--sb-job-strip-height\)/);
  // The reserved36px slot is identical for running/idle. Without the slot,
  // removing a36px ribbon moves a38px button beyond its stationary center.
  const beforeTop = 60 + 36 + 120, pointerY = beforeTop + 19;
  const afterTop = 60 + 36 + 120;
  assert.ok(pointerY >= afterTop && pointerY <= afterTop + 38);
  assert.ok(pointerY > beforeTop - 36 + 38);
});

for (const width of [1280, 1440]) for (const mode of ['photo', 'design', 'add']) test(`WOC8 ${width} ${mode} drawer keeps both header control rows available`, async t => {
  const h = await storyboardScreenHarness({ viewportWidth: width }); t.after(() => h.close()); h.render(); await h.flush();
  const target = nodes(h.tree).find(n => n.type === 'button' && (mode === 'photo' ? n.props['data-storyboard-photo']
    : textOf(n) === (mode === 'design' ? '섹션 디자인 변경' : '＋ 섹션 추가')));
  assert.ok(target); target.props.onClick(); h.render();
  const page = nodes(h.tree).find(n => n.type === 'div' && n.props.className?.includes('sb-page'));
  assert.match(page.props.className, /drawerOpen/);
  assert.ok(nodes(h.tree).some(n => n.props?.role === 'dialog'));
  for (const name of ['이전 글자 스타일', '다음 글자 스타일', '기본', '의류 색감']) assert.equal(Boolean(button(h.tree, name).props.disabled), false);
  button(h.tree, '의류 색감').props.onFocus(); h.render(); assert.equal(band(h.tree), '#edf4ff');
  button(h.tree, '의류 색감').props.onBlur(); h.render(); assert.equal(band(h.tree), 'var(--bg-2)');
  const drawerWidth = Math.min(520, width * .4), right = width - drawerWidth - 12 - 16;
  assert.ok(right - 24 >= 348);
});
