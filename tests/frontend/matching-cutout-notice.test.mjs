import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';

const source = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
const start = source.indexOf('export function MatchingCutoutPreview(');
const preview = source.slice(start, source.indexOf('export function AnalysisForm(', start)).replace('export ', '');
const { code } = await transformWithEsbuild(preview, 'MatchingCutoutPreview.jsx', { jsx: 'transform' });

// React의 effect 경계만 대체한다. 실제 카드와 타이머 콜백을 실행한다.
function mountPreview(item) {
  let slow = false;
  let previousDeps;
  let pendingEffect;
  let cleanup;
  const useState = () => [slow, (value) => { slow = value; }];
  const useEffect = (effect, deps) => {
    if (!previousDeps || deps.some((value, index) => value !== previousDeps[index])) {
      previousDeps = deps;
      pendingEffect = effect;
    }
  };
  const Icon = () => React.createElement('svg', { 'aria-label': '처리 중' });
  const Preview = new Function('React', 'useState', 'useEffect', 'Icon', `${code}; return MatchingCutoutPreview;`)(
    React, useState, useEffect, Icon,
  );
  const render = (next = item) => {
    item = next;
    let tree = Preview({ m: item });
    if (pendingEffect) {
      cleanup?.();
      const effect = pendingEffect;
      pendingEffect = undefined;
      cleanup = effect();
      tree = Preview({ m: item });
    }
    return renderToStaticMarkup(tree);
  };
  return { render, unmount: () => cleanup?.() };
}

const pending = { id: 'custom_1', isCustom: true, name: '내 바지', thumb: '/original.jpg', cutoutStatus: 'processing' };
const initial = '이미지 업로드됐어요! 지금 배경 정리 중이에요';
const delayed = '생각보다 오래 걸려요. 기다리지 않고 다음으로 넘어가도 돼요.';
const failed = '배경 정리를 못 해서 원본으로 보여 드려요. 다시 올리면 다시 시도해요.';

test('처리 중 60초 전에는 기본 안내, 60초부터는 지연 안내를 표시한다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = mountPreview(pending);
  assert.ok(view.render().includes(initial));
  t.mock.timers.tick(59999);
  assert.ok(view.render().includes(initial));
  t.mock.timers.tick(1);
  const html = view.render();
  assert.ok(html.includes(delayed));
  assert.ok(!html.includes(initial));
  view.unmount();
});

test('5초 상태 조회로 같은 옷이 갱신돼도 60초 대기를 처음부터 세지 않는다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = mountPreview(pending);
  view.render();
  for (let index = 0; index < 12; index += 1) {
    t.mock.timers.tick(5000);
    view.render({ ...pending });
  }
  assert.ok(view.render().includes(delayed));
  view.unmount();
});

test('실패하면 원본 이미지와 다시 올리는 방법을 함께 표시한다', () => {
  const view = mountPreview({ ...pending, cutoutStatus: 'failed' });
  const html = view.render();
  assert.ok(html.includes('src="/original.jpg"'));
  assert.ok(html.includes(failed));
  assert.ok(!html.includes('custom-match-cutout-pending'));
});

test('ready와 기존 추천 의류는 안내 없이 기존 이미지만 표시한다', () => {
  for (const status of ['ready', null]) {
    const view = mountPreview({ ...pending, thumb: '/cutout.jpg', cutoutStatus: status });
    assert.equal(view.render(), '<img src="/cutout.jpg" alt="내 바지"/>');
  }
});

test('실패나 완료 뒤에는 지연 타이머를 지우고 다시 올린 옷은 새로 센다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = mountPreview(pending);
  view.render();
  t.mock.timers.tick(60000);
  assert.ok(view.render().includes(delayed));
  assert.ok(view.render({ ...pending, cutoutStatus: 'failed' }).includes(failed));
  assert.ok(view.render({ ...pending, id: 'custom_2' }).includes(initial));
  t.mock.timers.tick(59999);
  assert.ok(view.render().includes(initial));
  t.mock.timers.tick(1);
  assert.ok(view.render().includes(delayed));
  assert.equal(view.render({ ...pending, cutoutStatus: 'ready' }), '<img src="/original.jpg" alt="내 바지"/>');
  t.mock.timers.tick(60000);
  assert.equal(view.render(), '<img src="/original.jpg" alt="내 바지"/>');
  view.unmount();
});
