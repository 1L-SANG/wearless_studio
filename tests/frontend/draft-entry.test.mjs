import test from 'node:test';
import assert from 'node:assert/strict';
import { getDraftDeviceNoun } from '../../src/lib/draftDeviceNoun.js';
import {
  createDraftEntryActions, getInputEntryDecision, getFlowDraftSources,
  openRemoteDraft, remoteDraftSource,
} from '../../src/lib/draftEntry.js';
import { createDraftSlotSync } from '../../src/lib/draftSlot.js';
import { clearFlowSession, markProductInfoConfirmed, registerConfirmedInputEntry } from '../../src/lib/flowSession.js';

const remote = (patch = {}) => ({
  meta: { hasContent: true, updatedAt: 'v1', deviceLabel: 'iPhone Safari', photoCount: 3, ...patch },
  payload: { product: { name: '휴대폰 상품', colors: [] } },
});
const flowSource = { id: 'flow', title: '만들던 상세페이지', description: '콘티 화면까지 진행했어요' };

function harness(initial = remote()) {
  let slot = initial;
  const calls = [];
  const errors = [];
  let dialog = null;
  let staged = null;
  let flow = 'existing';
  const slotClient = {
    async get() { calls.push('get'); if (slot instanceof Error) throw slot; return slot; },
    hasPendingRemoval: () => false,
    getServerSyncedAt: () => null,
    async takeover() { calls.push('takeover'); if (slot instanceof Error) throw slot; return slot; },
    async removeForNewFlow() { calls.push('remove'); },
    stage(value) { calls.push('stage'); staged = value; },
    hasStaged: () => Boolean(staged?.payload),
  };
  const beginProject = async () => { calls.push('begin'); flow = 'new'; };
  const actions = createDraftEntryActions({
    slotClient, beginProject,
    onChange: (value) => { dialog = value; },
    onError: (message) => errors.push(message),
    onOpened: () => calls.push('open'),
    onStarted: () => calls.push('start'),
  });
  return { actions, slotClient, beginProject, calls, errors,
    setSlot(value) { slot = value; }, get dialog() { return dialog; },
    get flow() { return flow; }, get staged() { return staged; } };
}

test('C1 device noun table distinguishes phones, tablets and other labels', () => {
  for (const [label, expected] of [
    ['iPhone', '스마트폰'], ['iPhone Safari', '스마트폰'], ['iPhone Chrome', '스마트폰'],
    ['Android', '스마트폰'], ['Android Chrome', '스마트폰'], ['Android Firefox', '스마트폰'],
    ['Android Edge', '스마트폰'], ['iPad', '태블릿'], ['iPad Safari', '태블릿'],
    ['Android 태블릿', '태블릿'], ['Android 태블릿 Chrome', '태블릿'],
    ['AndroidTV', null], ['Mac Safari', null], ['Windows Chrome', null], ['', null], [null, null],
  ]) assert.equal(getDraftDeviceNoun(label), expected, String(label));
  assert.equal(remoteDraftSource(remote().meta).title, '스마트폰 작업 이어가기');
  assert.equal(remoteDraftSource(remote({ deviceLabel: 'iPad Safari' }).meta).title, '태블릿 작업 이어가기');
  assert.equal(remoteDraftSource(remote({ deviceLabel: 'Mac Chrome' }).meta).title, '입력하던 상품 정보');
  assert.equal(remoteDraftSource(remote({ deviceLabel: 'Mac Chrome' }).meta, '다른 기기에서 입력하던 내용').title, '다른 기기에서 입력하던 내용');
});

test('shared remote open obtains payload before resetting flow and staging', async () => {
  const h = harness();
  await openRemoteDraft(h);
  assert.deepEqual(h.calls, ['takeover', 'begin', 'stage']);
  assert.equal(h.staged.payload.product.name, '휴대폰 상품');
});

for (const unavailable of [new Error('offline'), null, { meta: {} }]) {
  test(`remote takeover failure preserves the existing flow: ${String(unavailable)}`, async () => {
    const h = harness(unavailable);
    await assert.rejects(openRemoteDraft(h));
    assert.deepEqual(h.calls, ['takeover']);
    assert.equal(h.flow, 'existing');
    assert.equal(h.staged, null);
  });
}

test('C2 confirmed flow offers two cards, even for same-tab entry, without resetting flow', async () => {
  const h = harness();
  assert.equal(getInputEntryDecision({ slotClient: h.slotClient, confirmed: true }), 'confirmed');
  const sources = await getFlowDraftSources({ slotClient: h.slotClient, enabled: true, flowSource });
  assert.deepEqual(sources.map(({ id }) => id), ['flow', 'remote']);
  assert.equal(sources[1].notice, '지금 만들던 상세페이지 대신 이 상품으로 시작해요');
  assert.equal(h.flow, 'existing');
  assert.deepEqual(h.calls, ['get']);
});

for (const [name, value, enabled, pending] of [
  ['absent', null, true, false], ['empty', remote({ hasContent: false }), true, false],
  ['query failure', new Error('offline'), true, false], ['disabled', remote(), false, false],
  ['pending removal', remote(), true, true],
]) {
  test(`C2 ${name} retains confirmed redirect and seven-second start-new rule`, async () => {
    const storage = new Map();
    globalThis.sessionStorage = { getItem: (k) => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
    clearFlowSession();
    markProductInfoConfirmed('p');
    const h = harness(value);
    h.slotClient.hasPendingRemoval = () => pending;
    const sources = await getFlowDraftSources({ slotClient: h.slotClient, enabled, flowSource });
    assert.equal(sources, null);
    assert.equal(registerConfirmedInputEntry('p', 1000, 'first'), 'redirect');
    assert.equal(registerConfirmedInputEntry('p', 7999, 'second'), 'start-new');
    assert.equal(registerConfirmedInputEntry('p', 15000, 'third'), 'redirect');
    assert.equal(h.flow, 'existing');
  });
}

test('C3 TopNav remote selection stages content and input entry bypasses the second modal', async () => {
  const h = harness();
  const sources = await getFlowDraftSources({ slotClient: h.slotClient, enabled: true, flowSource });
  await h.actions.openRemote(sources[1]);
  assert.deepEqual(h.calls, ['get', 'takeover', 'begin', 'stage', 'open']);
  assert.equal(getInputEntryDecision({ slotClient: h.slotClient, confirmed: false }), 'continue');
  assert.equal(h.staged.payload.product.name, '휴대폰 상품');
});

test('draftSlot staging can be inspected without consuming the payload', () => {
  const client = createDraftSlotSync();
  assert.equal(client.hasStaged(), false);
  client.stage(remote());
  assert.equal(client.hasStaged(), true);
  assert.equal(client.hasStaged(), true);
  assert.deepEqual(client.consumeStaged(), remote());
  assert.equal(client.hasStaged(), false);
});

const local = { meta: { photoCount: 1 }, draft: { product: { name: 'local' } } };
for (const choice of ['entry-new', 'nav-new', 'local']) {
  const choose = (h) => choice === 'local'
    ? h.actions.openLocal(local, { protectRemote: true, resetFlow: true })
    : h.actions.startNew();
  const mutations = choice === 'local' ? ['begin', 'stage', 'takeover', 'open'] : ['remove', 'begin', 'start'];
  test(`C4 ${choice}: this browser's last write executes without confirmation`, async () => {
    const h = harness();
    h.slotClient.getServerSyncedAt = () => 'v1';
    await choose(h);
    assert.deepEqual(h.calls, ['get', ...mutations]);
    assert.equal(h.dialog, null);
  });
  test(`C4 ${choice}: confirmation re-query recognizes this browser's newer write`, async () => {
    const h = harness();
    await choose(h);
    assert.equal(h.dialog.kind, 'confirm');
    h.setSlot(remote({ updatedAt: 'v2' }));
    h.slotClient.getServerSyncedAt = () => 'v2';
    await h.actions.confirm();
    assert.deepEqual(h.calls, ['get', 'get', ...mutations]);
    assert.equal(h.dialog, null);
  });
  test(`C4 ${choice}: no mutation before confirmation, changed version requires new confirmation`, async () => {
    const h = harness();
    await choose(h);
    assert.deepEqual(h.calls, ['get']);
    assert.equal(h.dialog.kind, 'confirm');
    assert.equal(h.dialog.message, '스마트폰에서 업로드한 사진 3장이 지워져요.');
    h.setSlot(remote({ updatedAt: 'v2', photoCount: 5, deviceLabel: 'iPad Safari' }));
    await h.actions.confirm();
    assert.deepEqual(h.calls, ['get', 'get']);
    assert.equal(h.dialog.meta.updatedAt, 'v2');
    assert.equal(h.dialog.message, '태블릿에서 업로드한 사진 5장이 지워져요.');
    await h.actions.confirm();
    assert.deepEqual(h.calls, ['get', 'get', 'get', ...mutations]);
    assert.equal(h.dialog, null);
  });
  test(`C4 ${choice}: query failure blocks both initial action and confirmation`, async () => {
    const h = harness(new Error('offline'));
    await choose(h);
    assert.deepEqual(h.calls, ['get']);
    assert.deepEqual(h.errors, ['잠시 후 다시 시도해 주세요.']);
    h.setSlot(remote());
    await choose(h);
    h.setSlot(new Error('offline'));
    await h.actions.confirm();
    assert.deepEqual(h.calls, ['get', 'get', 'get']);
    assert.equal(h.dialog.kind, 'confirm');
    assert.equal(h.flow, 'existing');
  });
  for (const value of [null, remote({ hasContent: false })]) {
    test(`C4 ${choice}: no visible slot executes directly (${value ? 'empty' : 'absent'})`, async () => {
      const h = harness(value);
      await choose(h);
      assert.deepEqual(h.calls, ['get', ...mutations]);
      assert.equal(h.dialog, null);
    });
  }
  test(`C4 ${choice}: cancel does not execute and a disappeared slot can proceed`, async () => {
    const h = harness();
    await choose(h);
    h.actions.cancel();
    await h.actions.confirm();
    assert.deepEqual(h.calls, ['get']);
    await choose(h);
    h.setSlot(null);
    await h.actions.confirm();
    assert.deepEqual(h.calls, ['get', 'get', 'get', ...mutations]);
  });
}

test('local-only card preserves immediate stage and background takeover behavior', async () => {
  const h = harness(new Error('offline'));
  await h.actions.openLocal(local);
  assert.deepEqual(h.calls, ['stage', 'takeover', 'open']);
  assert.deepEqual(h.staged.payload, local.draft);
  assert.deepEqual(h.errors, []);
});

test('C5 pending card never takes over, refresh re-queries and updates the count', async () => {
  const h = harness(remote({ photosPending: true }));
  await h.actions.openRemote(remoteDraftSource(remote({ photosPending: true }).meta));
  assert.deepEqual(h.calls, []);
  assert.equal(h.dialog.kind, 'pending');
  assert.equal(h.dialog.message, '스마트폰에서 아직 사진을 올리고 있어요. 잠시 후 다시 확인해 주세요.');
  h.setSlot(remote({ photosPending: true, updatedAt: 'v2', photoCount: 4 }));
  await h.actions.refreshRemote();
  assert.deepEqual(h.calls, ['get']);
  assert.equal(h.dialog.meta.photoCount, 4);
  h.setSlot(remote({ photosPending: false }));
  await h.actions.refreshRemote();
  assert.deepEqual(h.calls, ['get', 'get', 'takeover', 'begin', 'stage', 'open']);
});

test('C5 partial open requires confirmation and a stable updatedAt', async () => {
  const h = harness(remote({ photosPending: true }));
  await h.actions.openRemote(remoteDraftSource(remote({ photosPending: true }).meta));
  await h.actions.openPartial();
  assert.deepEqual(h.calls, ['get']);
  assert.equal(h.dialog.kind, 'confirm');
  assert.equal(h.dialog.message, '아직 올라오지 않은 사진은 빠져요.');
  h.setSlot(remote({ photosPending: true, photoCount: 5, updatedAt: 'v2' }));
  await h.actions.confirm();
  assert.deepEqual(h.calls, ['get', 'get']);
  assert.equal(h.dialog.meta.photoCount, 5);
  await h.actions.confirm();
  assert.deepEqual(h.calls, ['get', 'get', 'get', 'takeover', 'begin', 'stage', 'open']);
});

test('C5 partial open still requires explicit consent for this browser\'s pending slot', async () => {
  const h = harness(remote({ photosPending: true }));
  h.slotClient.getServerSyncedAt = () => 'v1';
  await h.actions.openRemote(remoteDraftSource(remote({ photosPending: true }).meta));
  await h.actions.openPartial();
  assert.deepEqual(h.calls, ['get']);
  assert.equal(h.dialog.kind, 'confirm');
  await h.actions.confirm();
  assert.deepEqual(h.calls, ['get', 'get', 'takeover', 'begin', 'stage', 'open']);
});

test('C5 refresh and partial query errors keep the flow untouched', async () => {
  const h = harness(new Error('offline'));
  await h.actions.openRemote(remoteDraftSource(remote({ photosPending: true }).meta));
  await h.actions.refreshRemote();
  await h.actions.openPartial();
  assert.deepEqual(h.calls, ['get', 'get']);
  assert.equal(h.dialog.kind, 'pending');
  assert.equal(h.flow, 'existing');
  assert.deepEqual(h.errors, ['잠시 후 다시 시도해 주세요.', '잠시 후 다시 시도해 주세요.']);
});

test('remote card uses product name, thumbnail and grammatical save time', () => {
  const card = remoteDraftSource({ productName: '여름 니트', thumbnailUrl: '/knit.jpg', updatedAt: new Date().toISOString() });
  assert.equal(card.title, '여름 니트');
  assert.equal(card.thumbnailUrl, '/knit.jpg');
  assert.match(card.description, /^방금 저장/);
});

test('pending dialog carries the last save time', async () => {
  const h = harness();
  await h.actions.openRemote(remoteDraftSource(remote({ photosPending: true, updatedAt: new Date(Date.now() - 180000).toISOString() }).meta));
  assert.equal(h.dialog.savedAt, '3분 전 저장');
});

for (const deviceLabel of ['iPhone Safari', 'Android Chrome', 'iPad Safari', 'Android 태블릿 Chrome', 'Mac Chrome', 'Windows Edge', undefined]) {
  test(`writer helper uses deviceLabel: ${deviceLabel}`, async () => {
    const { isMobileDraftWriter } = await import('../../src/lib/draftDeviceNoun.js');
    assert.equal(isMobileDraftWriter({ deviceLabel }), Boolean(getDraftDeviceNoun(deviceLabel)));
    assert.equal(isMobileDraftWriter({ productName: '스마트폰 작업 이어가기' }), false);
  });
}

test('D1 destructive recheck refreshes changed own-write meta, unchanged removes then starts', async () => {
  const h = harness();
  await h.actions.nextMobile();
  assert.equal(h.dialog.kind, 'mobile');
  h.setSlot(remote({ updatedAt: 'v2', photoCount: 8 }));
  h.slotClient.getServerSyncedAt = () => 'v2';
  await h.actions.confirm();
  assert.equal(h.dialog.meta.photoCount, 8);
  assert.deepEqual(h.calls, ['get', 'get']);
  await h.actions.confirm();
  assert.deepEqual(h.calls, ['get', 'get', 'get', 'remove', 'begin', 'start']);
});

for (const value of [null, remote({ hasContent: false })]) {
  test(`next product without visible slot starts empty without asking: ${Boolean(value)}`, async () => {
    const h = harness(value);
    h.slotClient.resetIdentity = () => h.calls.push('reset');
    await h.actions.nextMobile();
    assert.equal(h.dialog, null);
    assert.deepEqual(h.calls, ['get', 'reset', 'begin', 'start']);
  });
}

test('D1 keep from B or C only closes, and gone during confirmation starts without deleting', async () => {
  const h = harness();
  h.slotClient.resetIdentity = () => h.calls.push('reset');
  await h.actions.nextMobile();
  await h.actions.keepMobile();
  assert.equal(h.dialog, null);
  assert.deepEqual(h.calls, ['get']);
  await h.actions.nextMobile();
  h.setSlot(null);
  await h.actions.confirm();
  assert.deepEqual(h.calls, ['get', 'get', 'get', 'reset', 'begin', 'start']);
});
