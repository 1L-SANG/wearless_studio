import test from 'node:test';
import assert from 'node:assert/strict';
import { draftSlot } from '../../src/lib/draftSlot.js';
import { saveProductDraft, loadDraft, queueProductDraftSave, flushProductDraftSave } from '../../src/lib/draftStore.js';
import { draftStoreFixture, drainDraftTasks } from './helpers/draft-store-fixture.mjs';

const product = (name) => ({ name, colors: [] });

test('WOC16 saving, restoring and updating a product draft keep content without photo-volume state', async (t) => {
  const storage = draftStoreFixture(t), analysis = { sellingPoints: ['부드러운 촉감'] };
  await saveProductDraft(product('저장한 니트'), analysis, '2026-11-13T00:00:00Z');
  assert.equal(Object.hasOwn(storage.saved, 'composeMode'), false);
  assert.equal(storage.saved.updatedAt, '2026-11-13T00:00:00Z');
  const restored = await loadDraft();
  assert.equal(restored.product.name, '저장한 니트');
  assert.deepEqual(restored.analysis.sellingPoints, analysis.sellingPoints);
  assert.equal(Object.hasOwn(restored, 'composeMode'), false);
  queueProductDraftSave(product('수정한 니트'), analysis, '2026-11-13T00:01:00Z');
  await flushProductDraftSave();
  assert.equal(storage.saved.product.name, '수정한 니트');
  assert.equal(storage.saved.updatedAt, '2026-11-13T00:01:00Z');
  assert.equal(Object.hasOwn(storage.saved, 'composeMode'), false);
});

for (const change of ['account', 'epoch']) {
  test(`local entry flush never writes a pending snapshot from a previous ${change}`, async (t) => {
    const storage = draftStoreFixture(t);
    queueProductDraftSave(product('이전 신원 입력'));
    if (change === 'account') draftSlot.setUserId('bob');
    else draftSlot.resetIdentity();
    await flushProductDraftSave({ userId: change === 'account' ? 'bob' : 'seller', identityEpoch: draftSlot.getIdentityEpoch(), timeoutMs: 8000 });
    assert.equal(storage.saved, null);
    assert.equal(storage.writes.length, 0);
    assert.equal(sessionStorage.getItem('wl_draftPending'), null);
    queueProductDraftSave(product('현재 신원 입력'));
    await flushProductDraftSave();
    assert.equal(storage.saved.product.name, '현재 신원 입력');
  });
}

for (const change of ['account', 'epoch']) {
  test(`local save rechecks ${change} after a slow photo read before writing IndexedDB`, async (t) => {
    const storage = draftStoreFixture(t);
    let finish;
    t.mock.method(globalThis, 'fetch', () => new Promise((resolve) => { finish = () => resolve(new Response('photo', { headers: { 'Content-Type': 'image/jpeg' } })); }));
    storage.onCleanup(() => finish?.());
    queueProductDraftSave({ name: '이전 신원 사진', colors: [{ id: 'base', images: [{ id: 'photo', src: 'blob:photo', type: 'image/jpeg' }] }] });
    const saving = flushProductDraftSave();
    await drainDraftTasks();
    assert.equal(typeof finish, 'function');
    if (change === 'account') draftSlot.setUserId('bob');
    else draftSlot.resetIdentity();
    finish();
    await saving;
    assert.equal(storage.saved, null);
    assert.equal(storage.writes.length, 0);
    assert.equal(sessionStorage.getItem('wl_draftPending'), null);
  });
}

test('a stale entry identity cannot flush another account\'s current pending snapshot', async (t) => {
  const storage = draftStoreFixture(t);
  const epoch = draftSlot.getIdentityEpoch();
  draftSlot.setUserId('bob');
  queueProductDraftSave(product('Bob의 새 입력'));
  await flushProductDraftSave({ userId: 'seller', identityEpoch: epoch, timeoutMs: 8000 });
  assert.equal(storage.saved, null);
  await flushProductDraftSave({ userId: 'bob', identityEpoch: draftSlot.getIdentityEpoch(), timeoutMs: 8000 });
  assert.equal(storage.saved.product.name, 'Bob의 새 입력');
});
