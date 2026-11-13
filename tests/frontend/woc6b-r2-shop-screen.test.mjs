import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { storyboardScreenHarness } from './helpers/storyboardScreenHarness.mjs';
import { nodes } from './helpers/storyboardComponents.mjs';
const mall = name => ({ mall: 'cafe24', status: 'connected', shopName: name });
const outlineHeight = h => nodes(h.tree).find(node => node.type === 'svg' && node.props['aria-label'] === '일상 착용컷 실제 모습')?.props.viewBox;
const settle = async h => { for (let i = 0; i < 20; i++) { await new Promise(resolve => setTimeout(resolve, 2)); await h.flush(); h.render(); } };

test('R2 non-frame Storyboard never fetches mall connections or reads an account name for the footer', async t => {
  const h = await storyboardScreenHarness(); t.after(() => h.close());
  let calls = 0; const original = h.api.listMalls;
  h.api.listMalls = (...args) => { calls++; return original(...args); };
  t.after(() => { h.api.listMalls = original; });
  h.render(); await settle(h);
  assert.equal(calls, 0);
  const source = readFileSync('src/features/storyboard/Storyboard.jsx', 'utf8');
  assert.doesNotMatch(source, /profileName|profile:\s*\{ display_name|account\?\.name/);
});

test('R2 actual frame Storyboard loads connected shop via the real mock service and reuses fresh cache', async t => {
  const h = await storyboardScreenHarness({ hasFrame: true, mallConnections: { cafe24: mall('데일리 상점') } }); t.after(() => h.close());
  let calls = 0; const original = h.api.listMalls;
  h.api.listMalls = (...args) => { calls++; return original(...args); }; t.after(() => { h.api.listMalls = original; });
  h.render(); const withoutFooter = outlineHeight(h); await settle(h);
  assert.equal(calls, 1); assert.notEqual(outlineHeight(h), withoutFooter, 'connected name adds the optional footer line');
  h.render(); await settle(h); assert.equal(calls, 1);
  assert.ok(!JSON.stringify(h.tree).includes('공개하면 안 되는 회원 실명'));
});

test('R2 existing settings cache supplies the frame name without another request', async t => {
  const h = await storyboardScreenHarness({ hasFrame: true }); t.after(() => h.close());
  h.queryClient.setQueryData(['mall-connections', 'mock-owner'], { items: [mall('캐시 상점')] });
  let calls = 0; const original = h.api.listMalls;
  h.api.listMalls = (...args) => { calls++; return original(...args); }; t.after(() => { h.api.listMalls = original; });
  h.render(); await settle(h); const named = outlineHeight(h); assert.equal(calls, 0);
  h.queryClient.setQueryData(['mall-connections', 'mock-owner'], { items: [] });
  h.render(); await h.flush(); assert.notEqual(outlineHeight(h), named);
});

test('R2 failed refresh hides stale shop footer and changing account cannot reuse another owner name', async t => {
  const h = await storyboardScreenHarness({ hasFrame: true }); t.after(() => h.close());
  h.queryClient.setQueryData(['mall-connections', 'mock-owner'], { items: [mall('옛 연결 이름')] });
  h.render(); await settle(h); const named = outlineHeight(h);
  const original = h.api.listMalls; h.api.listMalls = async () => { throw new Error('connection lookup failed'); };
  t.after(() => { h.api.listMalls = original; });
  await h.queryClient.invalidateQueries({ queryKey: ['mall-connections', 'mock-owner'] }); await settle(h);
  assert.notEqual(outlineHeight(h), named);
  h.store.setState({ accountUserId: 'other-owner', account: { name: '다른 회원 실명' } });
  h.render(); await settle(h);
  assert.equal(h.queryClient.getQueryData(['mall-connections', 'other-owner']), undefined);
  assert.ok(!JSON.stringify(h.tree).includes('다른 회원 실명'));
});

test('R2 local mock without a signed-in identity still uses mock connected malls, never the account name', async t => {
  const h = await storyboardScreenHarness({ hasFrame: true, mallConnections: { cafe24: mall('로컬 목 상점') } }); t.after(() => h.close());
  h.store.setState({ accountUserId: null });
  let calls = 0; const original = h.api.listMalls;
  h.api.listMalls = (...args) => { calls++; return original(...args); }; t.after(() => { h.api.listMalls = original; });
  h.render(); const withoutFooter = outlineHeight(h); await settle(h);
  assert.equal(calls, 1); assert.notEqual(outlineHeight(h), withoutFooter);
});

test('R2 a late old-owner mall response cannot label the next owner frame', async t => {
  const h = await storyboardScreenHarness({ hasFrame: true }); t.after(() => h.close());
  const original = h.api.listMalls; let releaseOld, calls = 0;
  h.api.listMalls = () => ++calls === 1 ? new Promise(resolve => { releaseOld = resolve; }) : Promise.resolve({ items: [] });
  t.after(() => { h.api.listMalls = original; });
  h.render(); await h.flush(); assert.equal(calls, 1);
  h.store.setState({ accountUserId: 'next-owner', account: { name: '다음 회원 실명' } });
  h.render(); await settle(h); const unnamed = outlineHeight(h);
  releaseOld({ items: [mall('이전 계정 상점')] }); await settle(h);
  assert.equal(calls, 2); assert.equal(outlineHeight(h), unnamed);
  assert.deepEqual(h.queryClient.getQueryData(['mall-connections', 'next-owner']), { items: [] });
});
