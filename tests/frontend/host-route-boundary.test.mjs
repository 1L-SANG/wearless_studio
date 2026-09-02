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
  for (const pathname of ['/models', '/status', '/license', '/payout', '/register', '/model-info', '/licensing']) {
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

/* 쿼리 강제(?facemarket=1)는 로컬·프리뷰 전용이다. 프로덕션에서 열려 있으면
   ai.wearless.kr?facemarket=1 한 번으로 그 탭이 모델 등록 화면이 되고, 세션스토리지에
   남아 쿼리를 지운 뒤에도 유지된다 — 라우트로 막아 둔 도메인 경계를 우회하는 구멍이다. */
test('facemarket 쿼리 강제는 프로덕션 호스트에서 통하지 않는다', () => {
  for (const hostname of ['ai.wearless.kr', 'facemarket.wearless.kr', 'wearless.kr']) {
    assert.equal(host.isOverrideAllowedHost(hostname), false, hostname);
  }
});

test('로컬·프리뷰에서는 통한다', () => {
  for (const hostname of ['localhost', '127.0.0.1', 'wearless-git-feat-x.vercel.app', 'mac.local']) {
    assert.equal(host.isOverrideAllowedHost(hostname), true, hostname);
  }
  // VITE_FACEMARKET_HOST 를 지정한 환경은 그 자체가 테스트 지정이라 허용한다.
  assert.equal(host.isOverrideAllowedHost('staging.example.com', 'staging.example.com'), true);
});
