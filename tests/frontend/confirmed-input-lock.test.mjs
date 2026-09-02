import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  clearFlowSession,
  isProductInfoConfirmed,
  markProductInfoConfirmed,
  readFlowSession,
  registerConfirmedInputEntry,
} from '../../src/lib/flowSession.js';

const storage = new Map();
globalThis.sessionStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
};

test('confirmed input redirects once, then starts a new project on a quick repeat', () => {
  clearFlowSession();
  markProductInfoConfirmed('project-a');
  assert.equal(isProductInfoConfirmed('project-a'), true);
  assert.equal(registerConfirmedInputEntry('project-a', 1000), 'redirect');
  assert.equal(registerConfirmedInputEntry('project-a', 7999), 'start-new');
  assert.equal(readFlowSession().confirmedInputEntryCount, 2);
});

test('confirmed input repeat intent expires and another project does not inherit the lock', () => {
  clearFlowSession();
  markProductInfoConfirmed('project-a');
  assert.equal(registerConfirmedInputEntry('project-a', 1000), 'redirect');
  assert.equal(registerConfirmedInputEntry('project-a', 8001), 'redirect');
  assert.equal(isProductInfoConfirmed('project-b'), false);
  assert.equal(registerConfirmedInputEntry('project-b', 8002), 'continue');
});

test('one browser history entry is counted once across React StrictMode remounts', () => {
  clearFlowSession();
  markProductInfoConfirmed('project-a');
  assert.equal(registerConfirmedInputEntry('project-a', 1000, 'document:entry-a'), 'redirect');
  assert.equal(registerConfirmedInputEntry('project-a', 1001, 'document:entry-a'), 'redirect');
  assert.equal(registerConfirmedInputEntry('project-a', 1002, 'document:entry-b'), 'start-new');
});

test('an auth-expiry redirect is not counted as a confirmed-input re-entry', () => {
  clearFlowSession();
  markProductInfoConfirmed('project-a');
  assert.equal(registerConfirmedInputEntry(
    'project-a',
    1000,
    'document:auth-redirect',
    { countAsUserEntry: false },
  ), 'continue');
  assert.equal(readFlowSession().confirmedInputEntryCount, 0);
});

test('http flow persistence restores the confirmed flag and project resets clear it', () => {
  const storeSource = readFileSync(new URL('../../src/store/useAppStore.js', import.meta.url), 'utf8');
  const appSource = readFileSync(new URL('../../src/apps/seller/App.jsx', import.meta.url), 'utf8');
  assert.match(storeSource, /productInfoConfirmed: s\.productInfoConfirmed/);
  assert.match(storeSource, /productInfoConfirmed: false/);
  assert.match(storeSource, /confirmProductInfo\(projectId = get\(\)\.projectId\)/);
  assert.match(appSource, /productInfoConfirmed \|\| isProductInfoConfirmed\(projectId\)/);
  assert.match(appSource, /if \(!isProductInfoConfirmed\(projectId\)\) markProductInfoConfirmed\(projectId\)/);
  assert.match(appSource, /if \(!isMockMode && !session\) \{[^]*countAsUserEntry: false/);
});
