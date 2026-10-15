import test from 'node:test';
import assert from 'node:assert/strict';
import { getMobileGuestSave, getMobileGuestDraft, rememberMobileGuestSave, clearMobileGuestSave } from '../../src/lib/mobileGuestSave.js';
import * as guestSave from '../../src/lib/mobileGuestSave.js';
import { clearDraft, loadDraft, hasPendingDraft } from '../../src/lib/draftStore.js';
import { draftStoreFixture } from './helpers/draft-store-fixture.mjs';

const key = 'wl_mobileGuestSave';
function storageFixture(t) {
  t.after(() => { clearMobileGuestSave(); guestSave.clearMobileGuestDraftOwner?.(); });
  const shared = new Map(), tab = new Map();
  for (const [name, values] of [['localStorage', shared], ['sessionStorage', tab]]) {
    const original = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, value: {
      getItem: (k) => values.get(k) ?? null, setItem: (k, v) => values.set(k, v), removeItem: (k) => values.delete(k),
    } });
    t.after(() => { if (original) Object.defineProperty(globalThis, name, original); else delete globalThis[name]; });
  }
  return { shared, tab };
}
const session = (id, when) => ({ user: { id, last_sign_in_at: new Date(when).toISOString() } });
const read = (user, now) => getMobileGuestSave(user, { mobile: true, now });

test('G6 same-browser marker contains no photos and works in a fresh tab for 30 minutes', (t) => {
  const s = storageFixture(t);
  const intent = rememberMobileGuestSave('local-version');
  const user = session('seller', intent.createdAt + 1000);
  s.tab.clear();
  assert.deepEqual(Object.keys(JSON.parse(s.shared.get(key))).sort(), ['createdAt', 'draftUpdatedAt', 'excludedUserId', 'id', 'userId']);
  assert.equal(read(user, intent.createdAt + 30 * 60 * 1000 - 1).id, intent.id);
  assert.equal(read(user, intent.createdAt + 30 * 60 * 1000), null);
  assert.equal(s.shared.has(key), false);
});

test('G6 desktop never adopts a mobile browser marker', (t) => {
  storageFixture(t);
  const intent = rememberMobileGuestSave('version');
  assert.equal(getMobileGuestSave(session('seller', intent.createdAt + 1000), { mobile: false }), null);
});

test('R2 timing rejection beyond five minutes preserves same-tab pending', (t) => {
  const s = storageFixture(t);
  const intent = rememberMobileGuestSave('version');
  s.tab.set('wl_draftPending', '1');
  assert.equal(read(session('earlier-account', intent.createdAt - 5 * 60 * 1000 - 1), intent.createdAt + 1000), null);
  assert.equal(s.tab.has('wl_draftPending'), true);
  assert.equal(read(session('new-account', intent.createdAt + 1000), intent.createdAt + 2000).userId, 'new-account');
});

test('G6 stored account before the tap and account changes after binding cannot adopt input', (t) => {
  const s = storageFixture(t);
  const intent = rememberMobileGuestSave('version', 'already-signed-in');
  s.tab.set('wl_draftPending', '1');
  const when = intent.createdAt + 1000;
  assert.equal(read(session('already-signed-in', when), when), null);
  assert.equal(s.tab.has('wl_draftPending'), false);
  assert.equal(read(session('first', when), when).userId, 'first');
  s.tab.set('wl_draftPending', '1');
  assert.equal(read(session('second', when), when), null);
  assert.equal(s.tab.has('wl_draftPending'), false);
  assert.equal(read(session('first', when), when).userId, 'first');
});

test('G6 success clears only its own marker and logout clears every pending marker', (t) => {
  const s = storageFixture(t);
  const old = rememberMobileGuestSave('old');
  const next = rememberMobileGuestSave('new');
  clearMobileGuestSave(old.id);
  assert.equal(s.shared.has(key), true);
  clearMobileGuestSave(next.id);
  assert.equal(s.shared.has(key), false);
  rememberMobileGuestSave('logout'); clearMobileGuestSave();
  assert.equal(s.shared.has(key), false);
});

test('G7 failed or cancelled login leaves the local intent retryable', (t) => {
  storageFixture(t);
  const intent = rememberMobileGuestSave('photos');
  assert.equal(read(null, intent.createdAt + 1000).id, intent.id);
  const retry = rememberMobileGuestSave('photos');
  assert.notEqual(retry.id, intent.id);
  assert.equal(read(null, retry.createdAt + 1000).id, retry.id);
});

test('R4 a claimed save attempt stays consumed across fresh intent reads and storage failure', (t) => {
  const s = storageFixture(t);
  const intent = rememberMobileGuestSave('photos');
  const user = session('seller', intent.createdAt + 1000);
  const claimed = read(user, intent.createdAt + 1000);
  assert.equal(guestSave.claimMobileGuestSave(claimed), true);
  assert.equal(guestSave.claimMobileGuestSave(read(user, intent.createdAt + 2000)), false);
  assert.equal(JSON.parse(s.shared.get(key)).saveStarted, true);
  const fresh = rememberMobileGuestSave('new-photos');
  assert.equal(guestSave.claimMobileGuestSave(fresh), true);
  assert.equal(guestSave.claimMobileGuestSave(fresh), false);
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); } });
  const memory = rememberMobileGuestSave('memory-photos');
  assert.equal(guestSave.claimMobileGuestSave(memory), true);
  assert.equal(guestSave.claimMobileGuestSave(read(user, memory.createdAt + 1000)), false);
});

test('G6 clearing the local draft also drops an obsolete mobile save intent', async (t) => {
  draftStoreFixture(t); const s = storageFixture(t);
  rememberMobileGuestSave('old');
  await clearDraft();
  assert.equal(s.shared.has(key), false);
});

for (const skew of [1, 3000, 5 * 60 * 1000]) {
  test(`R2 phone clock ahead by ${skew}ms restores the same-tab draft`, (t) => {
    const s = storageFixture(t);
    const intent = rememberMobileGuestSave('photos');
    s.tab.set('wl_draftPending', '1');
    assert.equal(read(session('new-login', intent.createdAt - skew), intent.createdAt + 1).id, intent.id);
    assert.equal(s.tab.get('wl_draftPending'), '1');
  });
}

test('G3 phone save intent also works when crypto.randomUUID is unavailable', (t) => {
  storageFixture(t);
  const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {} });
  t.after(() => Object.defineProperty(globalThis, 'crypto', original));
  assert.doesNotThrow(() => rememberMobileGuestSave('photos'));
  assert.ok(read(null, Date.now()).id);
});


test('R2 memory photos are never stored in the browser marker and do not survive redirect', async (t) => {
  const s = storageFixture(t);
  const payload = { product: { name: 'phone' }, photos: [{ blob: new Blob(['photo'], { type: 'image/jpeg' }) }] };
  const intent = rememberMobileGuestSave('version', null, Date.now(), Promise.resolve(payload));
  assert.equal(await getMobileGuestDraft(intent), payload);
  const stored = JSON.parse(s.shared.get(key));
  assert.equal(stored.memoryOnly, true);
  assert.equal(stored.product, undefined); assert.equal(stored.photos, undefined);
  s.tab.set('wl_draftPending', '1');
  const returnedPage = await import(`../../src/lib/mobileGuestSave.js?redirect=${intent.id}`);
  assert.equal(returnedPage.getMobileGuestSave(session('seller', intent.createdAt), { mobile: true }), null);
  assert.equal(s.tab.has('wl_draftPending'), false);
  assert.equal(s.shared.has(key), true);
  assert.equal(returnedPage.getMobileGuestSave(null, { mobile: true }).id, intent.id);
  assert.equal(read(null, intent.createdAt).id, intent.id);
  assert.equal(await getMobileGuestDraft(intent), payload);
});

test('R2 failed browser marker storage still permits same-document memory restore', async (t) => {
  storageFixture(t);
  const original = localStorage.setItem;
  localStorage.setItem = () => { throw new DOMException('unavailable', 'QuotaExceededError'); };
  const payload = { product: { name: 'phone' }, photos: [] };
  const intent = rememberMobileGuestSave('version', null, Date.now(), Promise.resolve(payload));
  const claimed = read(session('seller', intent.createdAt), intent.createdAt);
  assert.equal(claimed.id, intent.id); assert.equal(await getMobileGuestDraft(claimed), payload);
  localStorage.setItem = original;
  clearMobileGuestSave(intent.id);
  assert.equal(getMobileGuestDraft(intent), null);
});

test('R2 another-tab success cannot resurrect a removed save marker from memory', (t) => {
  const s = storageFixture(t);
  const intent = rememberMobileGuestSave('version', null, Date.now(), Promise.resolve({ product: {} }));
  s.shared.delete(key);
  assert.equal(read(session('seller', intent.createdAt), intent.createdAt), null);
  assert.equal(getMobileGuestDraft(intent), null);
});


for (const failBinding of [false, true]) {
  test(`R3 first account stays attached to the local draft after the intent clears (write fails=${failBinding})`, async (t) => {
    const fixture = draftStoreFixture(t, { updatedAt: 'guest-version', userId: null, product: { name: 'phone', colors: [] }, photos: [] });
    storageFixture(t);
    if (failBinding) fixture.failWrites(new DOMException('read only', 'QuotaExceededError'));
    const intent = rememberMobileGuestSave('guest-version');
    assert.equal(read(session('A', intent.createdAt), intent.createdAt).userId, 'A');
    clearMobileGuestSave(intent.id);
    assert.equal((await loadDraft()).userId, 'A');
    await new Promise(setImmediate);
    if (!failBinding) assert.equal(fixture.saved.userId, 'A');
    const other = rememberMobileGuestSave('guest-version');
    assert.equal(read(session('B', other.createdAt), other.createdAt), null);
    assert.equal((await loadDraft()).userId, 'A');
    const next = rememberMobileGuestSave('next-version');
    assert.equal(read(session('B', next.createdAt), next.createdAt).userId, 'B');
    clearMobileGuestSave(next.id);
    assert.equal((await loadDraft()).userId, 'A');
  });
}

test('R3 first account binds even an unresolved memory snapshot before clearing the intent', async (t) => {
  storageFixture(t);
  let resolveDraft;
  const draft = new Promise((resolve) => { resolveDraft = resolve; });
  const intent = rememberMobileGuestSave('memory-version', null, Date.now(), draft);
  const claimed = read(session('A', intent.createdAt), intent.createdAt);
  const owned = getMobileGuestDraft(claimed);
  clearMobileGuestSave(intent.id);
  resolveDraft({ updatedAt: 'memory-version', userId: null, product: { name: 'phone' }, photos: [] });
  assert.equal((await owned).userId, 'A');
});

test('R3 blocked session storage means no pending draft', (t) => {
  storageFixture(t);
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); } });
  assert.equal(hasPendingDraft(), false);
});
