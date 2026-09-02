import test from 'node:test';
import assert from 'node:assert/strict';

import * as host from '../../src/lib/host.js';

test('FaceMarket redirects Wearless-only routes to model registration', () => {
  assert.equal(typeof host.domainRouteRedirect, 'function');
  for (const pathname of ['/create/input', '/library', '/editor/project-1', '/unknown']) {
    assert.equal(host.domainRouteRedirect(pathname, true), '/model/register');
  }
});

test('Wearless redirects FaceMarket-only routes to product input', () => {
  for (const pathname of ['/model', '/model/register', '/model/license']) {
    assert.equal(host.domainRouteRedirect(pathname, false), '/create/input');
  }
});

/* 랜딩 라우트가 이 목록에서 빠지면 상단바 링크가 전부 /model/register 로 튕긴다.
   화면은 멀쩡한데 링크만 죽는 종류의 사고라 눈으로는 늦게 발견된다 — 여기서 막는다. */
test('FaceMarket landing routes stay put', () => {
  for (const pathname of ['/models', '/license', '/payout', '/register', '/model-info', '/licensing']) {
    assert.equal(host.domainRouteRedirect(pathname, true), null, pathname);
  }
});

test('shared and domain-owned routes stay on their current host', () => {
  for (const pathname of ['/', '/model', '/model/register', '/pricing', '/credits/history', '/payments/success', '/verify/license-1']) {
    assert.equal(host.domainRouteRedirect(pathname, true), null);
  }
  for (const pathname of ['/', '/create/input', '/library', '/editor/project-1', '/pricing', '/credits/history', '/payments/fail', '/verify/license-1']) {
    assert.equal(host.domainRouteRedirect(pathname, false), null);
  }
});
