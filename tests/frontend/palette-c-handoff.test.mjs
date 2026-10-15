import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

function runHead(path) {
  const html = read('seller.html');
  const script = html.match(/<head>\s*<script>([\s\S]*?)<\/script>/)?.[1];
  assert.ok(script, '위상과 from 소비 스크립트는 head 맨 앞에서 동기 실행한다');
  const url = new URL(path, 'https://ai.wearless.kr');
  const attributes = new Map();
  const properties = new Map();
  const replacements = [];
  const state = { keep: 'router-state' };
  vm.runInNewContext(script, {
    document: { documentElement: {
      style: { setProperty: (key, value) => properties.set(key, value) },
      setAttribute: (key, value) => attributes.set(key, value),
    } },
    window: { location: url },
    history: { state, replaceState: (...args) => replacements.push(args) },
    performance: { timeOrigin: Date.UTC(2026, 0, 2), now: () => 125 },
    Date, URL, URLSearchParams,
  });
  return { attributes, properties, replacements, state };
}

test('랜딩 도착은 from만 소비하고 경로, 다른 쿼리, 해시와 history state를 보존한다', () => {
  const result = runHead('/pricing?plan=seller&from=landing&coupon=a%20b&tag=x&tag=y#checkout');
  assert.equal(result.attributes.get('data-entry'), 'landing');
  assert.deepEqual(result.replacements, [[result.state, '', '/pricing?plan=seller&coupon=a%20b&tag=x&tag=y#checkout']]);
  assert.equal(result.properties.get('--wl-t'), '86400.000');
});

test('루트 랜딩 도착은 RootRedirect 앞에서 표식을 남기고 from을 지운다', () => {
  const result = runHead('/?from=landing#start');
  assert.equal(result.attributes.get('data-entry'), 'landing');
  assert.equal(result.replacements[0][2], '/#start');
});

test('from이 없거나 landing이 아니면 주소와 진입 표식을 건드리지 않는다', () => {
  for (const path of ['/pricing?plan=seller#checkout', '/?from=email&code=oauth#token']) {
    const result = runHead(path);
    assert.equal(result.attributes.size, 0);
    assert.equal(result.replacements.length, 0);
    assert.equal(result.properties.get('--wl-t'), '86400.000');
  }
});

function structure(markup) {
  return [...markup.matchAll(/<(\/?)(div|i)\b([^>]*)>/g)].map(([, close, tag, attrs]) =>
    `${close}${tag}:${attrs.match(/class(?:Name)?="([^"]*)"/)?.[1] || ''}`);
}

test('부트 배경과 ChromeLayout 배경은 같은 클래스와 겹 순서를 쓴다', () => {
  const boot = read('seller.html').match(/<body>\s*([\s\S]*?)<div id="root">/)?.[1];
  const chrome = read('src/features/shell/ChromeLayout.jsx').match(/(<div className="app-bg"[\s\S]*?)\s*<TopNav/)?.[1];
  assert.ok(boot?.includes('id="seller-boot-bg"'), 'root 앞에 부트 배경이 있다');
  assert.ok(chrome, 'ChromeLayout의 배경을 찾는다');
  assert.deepEqual(structure(boot), structure(chrome));
  assert.equal((boot.match(/class="wl-rb /g) || []).length, 6);
  assert.equal((boot.match(/class="wl-orb__l/g) || []).length, 3);
  assert.doesNotMatch(boot + chrome, /style=/);
});

test('마운트 시 세 겹과 리본의 생성 시각을 벽시계에 맞추고 부트 사본을 지운다', async () => {
  const { syncSellerGlowPhase, removeSellerBootBackground } = await import('../../src/features/shell/sellerGlow.js');
  const animationA = { currentTime: 0 };
  const animationB = { currentTime: 0 };
  const originalNow = Date.now;
  Date.now = () => performance.timeOrigin + 1250;
  try {
    syncSellerGlowPhase({ querySelectorAll: () => [
      { getAnimations: () => [animationA, animationB] }, { getAnimations: () => [] }, {},
    ] });
    assert.equal(animationA.currentTime, 1250);
    assert.equal(animationB.currentTime, 1250);
  } finally { Date.now = originalNow; }
  const previousDocument = globalThis.document;
  let boot = { remove() { boot = null; } };
  globalThis.document = { getElementById: (id) => id === 'seller-boot-bg' ? boot : null };
  try {
    removeSellerBootBackground();
    assert.equal(boot, null);
    removeSellerBootBackground(); // StrictMode와 부모 layout effect의 두 번째 정리도 안전하다.
  } finally { globalThis.document = previousDocument; }
});

test('CSS를 늦게 받은 부트 배경도 앱 교대 전에 현재 벽시계 위상으로 그린다', () => {
  const html = read('seller.html');
  const script = html.match(/<\/div>\s*<script>([\s\S]*?)<\/script>\s*<div id="root">/)?.[1];
  assert.ok(script, '부트 마크업 바로 뒤에서 모듈 실행 전에 위상을 보정한다');
  for (const entry of ['landing', null]) {
    const animation = { currentTime: 0 };
    vm.runInNewContext(script, {
      document: {
        documentElement: { getAttribute: () => entry },
        getElementById: () => ({ querySelectorAll: () => [{ getAnimations: () => [animation] }] }),
      },
      performance: { timeOrigin: 1000 }, Date: { now: () => 3500 },
    });
    assert.equal(animation.currentTime, entry === 'landing' ? 2500 : 0);
  }
});
