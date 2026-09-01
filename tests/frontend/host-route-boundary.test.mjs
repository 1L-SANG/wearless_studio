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

test('shared and domain-owned routes stay on their current host', () => {
  for (const pathname of ['/', '/model', '/model/register', '/pricing', '/credits/history', '/payments/success', '/verify/license-1']) {
    assert.equal(host.domainRouteRedirect(pathname, true), null);
  }
  for (const pathname of ['/', '/create/input', '/library', '/editor/project-1', '/pricing', '/credits/history', '/payments/fail', '/verify/license-1']) {
    assert.equal(host.domainRouteRedirect(pathname, false), null);
  }
});
