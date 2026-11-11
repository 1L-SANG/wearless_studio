import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';

async function harness(t, { reduced = false } = {}) {
  const h = hookHarness(), saves = [];
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const motion = { matches: reduced, addEventListener(_, cb) { this.listener = cb; }, removeEventListener(_, cb) { if (this.listener === cb) this.listener = null; } };
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { matchMedia: () => motion } });
  const Carousel = await loadStoryboardComponent('StoryboardStyleCarousel.jsx', 'StoryboardStyleCarousel', {
    ...h.hooks, useLayoutEffect: h.hooks.useEffect, Icon: 'icon',
  });
  const props = { label: '페이지 색감', value: 'mono', values: ['mono', 'point'], valueLabel: '기본', contextKey: 'emo:blue',
    dotAttribute: 'data-color-mode-dot', renderSample: value => React.createElement('span', { 'data-value': value }, value),
    onChange(value) { saves.push(value); props.value = value; props.valueLabel = value === 'point' ? '의류 색감' : '기본'; } };
  t.after(() => { h.close(); if (descriptor) Object.defineProperty(globalThis, 'window', descriptor); else delete globalThis.window; });
  return { props, saves, motion, render: () => h.render(Carousel, props), close: () => h.close() };
}
const button = (tree, name) => nodes(tree).find(n => n.type === 'button' && n.props['aria-label'] === name);
const slide = (tree, kind) => nodes(tree).find(n => n.props?.['data-slide'] === kind);
const end = node => { const target = {}; node.props.onAnimationEnd({ target, currentTarget: target }); };

test('WOC9 right arrow exits left and enters right; left reverses both with one accessible current value', async t => {
  const h = await harness(t);
  for (const [arrow, entering, exiting, next] of [['다음', 'inRight', 'outLeft', 'point'], ['이전', 'inLeft', 'outRight', 'mono']]) {
    button(h.render(), `${arrow} 페이지 색감`).props.onClick(); const tree = h.render();
    assert.equal(h.props.value, next); assert.equal(textOf(slide(tree, 'current')), next);
    assert.match(slide(tree, 'current').props.className, new RegExp(entering));
    assert.match(slide(tree, 'outgoing').props.className, new RegExp(exiting));
    assert.equal(slide(tree, 'outgoing').props['aria-hidden'], 'true');
    assert.ok(textOf(nodes(tree).find(n => n.props?.['aria-live'] === 'polite')).includes(h.props.valueLabel));
    end(slide(tree, 'current')); assert.equal(slide(h.render(), 'outgoing'), undefined);
  }
  assert.deepEqual(h.saves, ['point', 'mono']);
});

test('WOC9 interrupted transitions keep only the last pair and stale animationend cannot remove it or save', async t => {
  const h = await harness(t);
  button(h.render(), '다음 페이지 색감').props.onClick();
  const previous = h.render(), staleEnd = slide(previous, 'current');
  const next = button(previous, '다음 페이지 색감');
  for (let i = 0; i < 6; i++) next.props.onClick();
  let tree = h.render(); assert.equal(h.saves.length, 7); assert.equal(h.props.value, 'point');
  assert.equal(nodes(tree).filter(n => n.props?.['data-slide']).length, 2);
  end(staleEnd); tree = h.render(); assert.ok(slide(tree, 'outgoing'));
  assert.equal(textOf(slide(tree, 'current')), 'point');
  end(slide(tree, 'current')); tree = h.render();
  assert.equal(slide(tree, 'outgoing'), undefined); assert.equal(h.saves.length, 7);
});

test('WOC9 reduced motion applies without outgoing layers and enabling it cancels movement without saving', async t => {
  const h = await harness(t, { reduced: true });
  button(h.render(), '다음 페이지 색감').props.onClick(); let tree = h.render();
  assert.equal(textOf(slide(tree, 'current')), 'point'); assert.equal(slide(tree, 'outgoing'), undefined);
  assert.doesNotMatch(slide(tree, 'current').props.className, /inRight|inLeft/);
  h.motion.matches = false; h.motion.listener({ matches: false });
  button(h.render(), '이전 페이지 색감').props.onClick(); assert.ok(slide(h.render(), 'outgoing'));
  h.motion.matches = true; h.motion.listener({ matches: true }); tree = h.render();
  assert.equal(textOf(slide(tree, 'current')), 'mono'); assert.equal(slide(tree, 'outgoing'), undefined);
  assert.deepEqual(h.saves, ['point', 'mono']);
  h.close(); assert.equal(h.motion.listener, null);
});

test('WOC9 rollback and garment/template changes discard outgoing samples and reset the next input', async t => {
  const h = await harness(t);
  button(h.render(), '다음 페이지 색감').props.onClick(); assert.ok(slide(h.render(), 'outgoing'));
  h.props.value = 'mono'; let tree = h.render();
  assert.equal(textOf(slide(tree, 'current')), 'mono'); assert.equal(slide(tree, 'outgoing'), undefined);
  button(tree, '다음 페이지 색감').props.onClick(); tree = h.render(); assert.equal(h.props.value, 'point');
  h.props.contextKey = 'show:red'; tree = h.render(); assert.equal(slide(tree, 'outgoing'), undefined);
  assert.equal(textOf(slide(tree, 'current')), 'point'); assert.deepEqual(h.saves, ['point', 'point']);
});

test('WOC9 tone arrow keys wrap, keep unrelated keys native and respect disabled state', async t => {
  const h = await harness(t), event = key => ({ key, currentTarget: { contains: () => true }, prevented: false, preventDefault() { this.prevented = true; } });
  let tree = h.render(); const right = event('ArrowRight'); tree.props.onKeyDown(right); tree = h.render();
  assert.equal(right.prevented, true); assert.equal(h.props.value, 'point');
  const left = event('ArrowLeft'); tree.props.onKeyDown(left); tree = h.render(); assert.equal(h.props.value, 'mono');
  const down = event('ArrowDown'); tree.props.onKeyDown(down); assert.equal(down.prevented, false);
  h.props.disabled = true; tree = h.render();
  for (const btn of nodes(tree).filter(n => n.type === 'button')) { assert.equal(btn.props.disabled, true); btn.props.onClick(); }
  const blocked = event('ArrowRight'); tree.props.onKeyDown(blocked); assert.equal(blocked.prevented, false);
  assert.deepEqual(h.saves, ['point', 'mono']);
});

test('WOC9 motion is clipped, 200ms, bounded deceleration and has an immediate CSS reduction fallback', () => {
  const css = readFileSync('src/features/storyboard/StoryboardTone.module.css', 'utf8');
  const tokens = readFileSync('src/styles/tokens.css', 'utf8');
  assert.match(tokens, /--sb-t-base: 200ms/); assert.match(tokens, /--sb-ease: cubic-bezier\(\.2, \.8, \.2, 1\)/);
  for (const name of ['InRight', 'InLeft', 'OutLeft', 'OutRight']) assert.match(css, new RegExp(`animation: sample${name} var\\(--sb-t-base\\) var\\(--sb-ease\\) both`));
  assert.match(css, /\.viewport[^}]*overflow: hidden/);
  assert.match(css, /@keyframes sampleOutLeft[^\n]*translateX\(-100%\)/);
  assert.match(css, /@keyframes sampleInRight[^\n]*from \{ transform: translateX\(100%\)/);
  assert.match(css, /prefers-reduced-motion: reduce[^]*\.slide \{ animation: none; transform: none; \}[^]*\.outgoing \{ display: none; \}/);
  const source = readFileSync('src/features/storyboard/Storyboard.jsx', 'utf8');
  assert.doesNotMatch(source, /onTonePreview|beginPreview|endPreview|tone\.wave|tonePages|styleRollback/);
});
