import test from 'node:test';
import assert from 'node:assert/strict';
import { draftSlot } from '../../src/lib/draftSlot.js';
import {
  clearDraft, clearDraftIfCurrent, flushProductDraftSave, hasPendingDraft, loadDraft, queueProductDraftSave,
  saveFeatureRunDraft, saveProductDraft,
} from '../../src/lib/draftStore.js';
import { draftStoreFixture, drainDraftTasks } from './helpers/draft-store-fixture.mjs';

const product = (name) => ({ name, colors: [] });
const ownedDraft = () => ({
  ownerUserId: 'seller', product: product('Account A private input'),
  analysis: { sellingPoints: ['Account A copy'] }, photos: [], updatedAt: 'revision-A',
});

test('new signed-in input is unreadable after another account logs in on a sibling origin', async (t) => {
  const storage = draftStoreFixture(t);
  queueProductDraftSave(product('Account A private input'));
  await flushProductDraftSave();
  assert.equal(storage.saved.ownerUserId, 'seller');
  assert.equal((await loadDraft()).product.name, 'Account A private input');
  draftSlot.setUserId('bob');
  assert.equal(await loadDraft(), null);
  assert.equal(storage.saved.product.name, 'Account A private input', 'A read rejection does not delete stored input');
});

test('marked input stays hidden after logout and becomes readable for its original account', async (t) => {
  const storage = draftStoreFixture(t, ownedDraft());
  draftSlot.setUserId(null);
  assert.equal(await loadDraft(), null);
  draftSlot.setUserId('seller');
  assert.equal((await loadDraft()).product.name, 'Account A private input');
  assert.equal(storage.writes.length, 0);
});

test('an account change while IndexedDB is reading cannot return the previous owner input', async (t) => {
  draftStoreFixture(t, ownedDraft());
  const reading = loadDraft();
  draftSlot.setUserId('bob');
  assert.equal(await reading, null);
});

test('an old account read cannot return the new account draft after switching identity', async (t) => {
  draftStoreFixture(t, { ...ownedDraft(), ownerUserId: 'bob', product: product('B current input') });
  const readingStartedByA = loadDraft();
  draftSlot.setUserId('bob');
  assert.equal(await readingStartedByA, null);
  assert.equal((await loadDraft()).product.name, 'B current input');
});

test('guest login during an old read retries under the new identity without losing input', async (t) => {
  const storage = draftStoreFixture(t, { ...ownedDraft(), ownerUserId: null, product: product('Guest input') });
  draftSlot.setUserId(null);
  const reading = loadDraft();
  draftSlot.setUserId('seller');
  assert.equal(await reading, null);
  assert.equal((await loadDraft()).product.name, 'Guest input');
  assert.equal(storage.saved.ownerUserId, null);
});

test('a new guest input survives login and can then save under the signed-in account', async (t) => {
  const storage = draftStoreFixture(t);
  draftSlot.setUserId(null);
  await saveProductDraft(product('Guest input'));
  assert.equal(storage.saved.ownerUserId, null);
  draftSlot.setUserId('seller');
  const restored = await loadDraft();
  assert.equal(restored.product.name, 'Guest input');
  await saveProductDraft(restored.product);
  assert.equal(storage.saved.ownerUserId, 'seller');
});

test('legacy ownerless input keeps continuation compatibility without inferred ownership', async (t) => {
  const legacy = { product: product('Legacy input'), photos: [], updatedAt: 'legacy' };
  const storage = draftStoreFixture(t, legacy);
  draftSlot.setUserId('bob');
  const restored = await loadDraft();
  assert.equal(restored.product.name, 'Legacy input');
  await saveProductDraft(restored.product);
  assert.equal(Object.hasOwn(storage.saved, 'ownerUserId'), false);
});

test('another account cannot reuse the previous owner cached photo bytes', async (t) => {
  const storage = draftStoreFixture(t, { ...ownedDraft(), photos: [{
    imageId: 'photo', colorId: 'base', mime: 'image/jpeg', blob: new Blob(['private A bytes'], { type: 'image/jpeg' }),
  }] });
  draftSlot.setUserId('bob');
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('synthetic offline'); });
  t.mock.method(console, 'warn', () => {});
  const result = await saveProductDraft({ name: 'B input', colors: [{ id: 'base', images: [{
    id: 'photo', src: 'https://images.example.test/photo', type: 'image/jpeg',
  }] }] });
  assert.equal(result.saved, 0);
  assert.equal(storage.saved.photos.length, 0);
  assert.equal(storage.saved.ownerUserId, 'bob');
});

test('a direct save cannot publish old photo bytes after an account change', async (t) => {
  const storage = draftStoreFixture(t);
  let finish;
  t.mock.method(globalThis, 'fetch', () => new Promise((resolve) => {
    finish = () => resolve(new Response('synthetic photo', { headers: { 'Content-Type': 'image/jpeg' } }));
  }));
  storage.onCleanup(() => finish?.());
  const saving = saveProductDraft({ name: 'A photo', colors: [{ id: 'base', images: [{
    id: 'photo', src: 'blob:synthetic-photo', type: 'image/jpeg',
  }] }] });
  await drainDraftTasks();
  assert.equal(typeof finish, 'function');
  draftSlot.setUserId('bob');
  finish();
  await saving;
  assert.equal(storage.saved, null);
  assert.equal(storage.writes.length, 0);
});

test('an old feature checkpoint cannot amend a draft after account switching', async (t) => {
  const original = ownedDraft();
  const storage = draftStoreFixture(t, original);
  const saving = saveFeatureRunDraft({ sellingPoints: ['Late account A response'] });
  draftSlot.setUserId('bob');
  assert.equal(await saving, false);
  assert.deepEqual(storage.saved, original);
  assert.equal(storage.writes.length, 0);
});

test('completion in another account cannot clear a marked owner draft', async (t) => {
  const original = ownedDraft();
  const storage = draftStoreFixture(t, original);
  draftSlot.setUserId('bob');
  assert.equal(await clearDraftIfCurrent(original.updatedAt), false);
  assert.deepEqual(storage.saved, original);
});

for (const owner of ['bob', null, undefined]) {
  test(`old-account cleanup preserves ${owner === undefined ? 'legacy' : owner || 'guest'} input`, async (t) => {
    const draft = ownedDraft();
    if (owner === undefined) delete draft.ownerUserId;
    else draft.ownerUserId = owner;
    const storage = draftStoreFixture(t, draft);
    await clearDraft({ ownerUserId: 'seller' });
    assert.deepEqual(storage.saved, draft);
    assert.equal(hasPendingDraft(), owner !== undefined);
    if (owner === undefined) assert.equal(await loadDraft(), null, 'Direct recovery readers must also reject the legacy input');
  });
}

test('fresh B input replaces a revoked legacy draft with B ownership', async (t) => {
  const legacy = { product: product('A legacy input'), photos: [], updatedAt: 'legacy' };
  const storage = draftStoreFixture(t, legacy);
  draftSlot.setUserId('bob');
  await clearDraft({ ownerUserId: 'seller' });
  assert.deepEqual(storage.saved, legacy);
  await saveProductDraft(product('B new input'));
  assert.equal(storage.saved.ownerUserId, 'bob');
  assert.equal((await loadDraft()).product.name, 'B new input');
  assert.equal(hasPendingDraft(), true);
});

test('scoped account cleanup removes its own draft while explicit new-project cleanup stays unchanged', async (t) => {
  const storage = draftStoreFixture(t, ownedDraft());
  await clearDraft({ ownerUserId: 'seller' });
  assert.equal(storage.saved, null);
  assert.equal(sessionStorage.getItem('wl_draftPending'), null);
  await saveProductDraft(product('Explicit new input'));
  await clearDraft();
  assert.equal(storage.saved, null);
});
