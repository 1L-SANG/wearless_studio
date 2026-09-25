import test from 'node:test';
import assert from 'node:assert/strict';
import { eventually, findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';
import { sponsorshipDraft } from '../../src/features/model/sponsorshipOptions.js';
import {
  SPONSORSHIP_OFF_NOTICE, SPONSORSHIP_UNAVAILABLE_MESSAGE, adminSponsorshipCredentialLine,
  shouldPollSponsorshipCredential, shortVcId, sponsorshipCredentialCopy, sponsorshipInterestErrorMessage,
  sponsorshipOffNeedsNotice, verifySponsorshipLine,
} from '../../src/features/model/sponsorshipCredential.js';

const textOf = node => node == null || typeof node === 'boolean' ? '' : typeof node !== 'object' ? String(node) : Array.isArray(node) ? node.map(textOf).join('') : textOf(node.props?.children);
const cred = (status, extra = {}) => ({ featureEnabled: true, status, vcId: null, issuedAt: null, ...extra });

test('증서 상태별 모델 화면 문구와 확인 주기를 골라요', () => {
  assert.equal(sponsorshipCredentialCopy(null), null);
  assert.equal(sponsorshipCredentialCopy({ ...cred('active'), featureEnabled: false }), null);
  assert.equal(sponsorshipCredentialCopy(cred('none')).text, '협찬 동의 증명서를 준비하고 있어요.');
  assert.equal(sponsorshipCredentialCopy(cred('waiting_license')).text, '등록이 끝나면 협찬 동의 증명서가 발급돼요.');
  assert.equal(sponsorshipCredentialCopy(cred('pending')).text, '협찬 동의 증명서를 발급하고 있어요 (1~2분)');
  const active = sponsorshipCredentialCopy(cred('active', { vcId: 'urn:uuid:12345678-aaaa-bbbb-cccc-1234567890ab' }));
  assert.equal(active.text, '협찬 동의 증명서가 발급됐어요.');
  assert.equal(active.vcId, 'urn:uuid:123…7890ab');
  assert.equal(shortVcId('short-id'), 'short-id');
  assert.equal(shouldPollSponsorshipCredential(cred('pending')), true);
  assert.equal(shouldPollSponsorshipCredential(cred('waiting_license')), true);
  assert.equal(shouldPollSponsorshipCredential(cred('active')), false);
  assert.equal(shouldPollSponsorshipCredential({ ...cred('pending'), featureEnabled: false }), false);
  assert.equal(sponsorshipOffNeedsNotice(cred('active')), true);
  assert.equal(sponsorshipOffNeedsNotice(cred('pending')), true);
  assert.equal(sponsorshipOffNeedsNotice(cred('waiting_license')), false);
});

test('셀러 협찬 요청 404 는 받을 수 없는 모델 문구로 바꿔요', () => {
  assert.equal(sponsorshipInterestErrorMessage({ status: 404, message: '찾을 수 없어요.' }, 'x'), SPONSORSHIP_UNAVAILABLE_MESSAGE);
  assert.equal(sponsorshipInterestErrorMessage({ status: 500, message: '서버 오류' }, 'x'), '서버 오류');
  assert.equal(sponsorshipInterestErrorMessage({ status: 500 }, '기본 문구'), '기본 문구');
});

test('공개 검증과 관리자 한 줄은 한국 시간 날짜와 상태 이름을 써요', () => {
  // 2026-09-24T16:30Z 는 한국 시간으로 25일이에요.
  const line = verifySponsorshipLine({ active: true, vcId: 'vc-1', consentedAt: '2026-09-24T16:30:00Z', consentDocVersion: 'v1' });
  assert.equal(line.text, '협찬 동의 · 증명서 유효 · 동의일 2026-09-25 (KST)');
  assert.equal(line.vcId, 'vc-1');
  assert.equal(verifySponsorshipLine(null).text, '협찬 동의 없음');
  assert.equal(verifySponsorshipLine({ active: false, vcId: 'vc-1' }).text, '협찬 동의 없음');
  assert.equal(adminSponsorshipCredentialLine(null), null);
  assert.equal(adminSponsorshipCredentialLine({ status: 'active', vcId: 'vc-1', attempts: 1, lastErrorCode: null }), '유효 · vc-1');
  assert.equal(adminSponsorshipCredentialLine({ status: 'pending', vcId: null, attempts: 3, lastErrorCode: 'holder_timeout' }), '발급 중 · 마지막 오류 holder_timeout · 시도 3회');
  assert.equal(adminSponsorshipCredentialLine({ status: 'waiting_license', attempts: 0 }), '발급 대기');
  assert.equal(adminSponsorshipCredentialLine({ status: 'revoked', vcId: 'vc-2' }), '무효 · vc-2');
});

for (const [name, sponsorship, expected] of [
  ['active', { active: true, vcId: 'vc-sp-1', consentedAt: '2026-09-24T16:30:00Z', consentDocVersion: 'v1' }, '협찬 동의 · 증명서 유효 · 동의일 2026-09-25 (KST)vc-sp-1'],
  ['null', null, '협찬 동의 없음'],
]) {
  test(`공개 검증 페이지는 협찬 줄을 그려요: ${name}`, async () => {
    const h = await modelComponentHarness({ entry: '/src/features/verify/PublicVerify.jsx', exportName: 'PublicVerify', initialStates: ['ok', { status: 'active', valid: true, validUntil: null, sponsorship }, null], api: {} });
    try {
      const row = findTree(h.render(), node => node.type === 'div' && textOf(node).startsWith('협찬'));
      assert.ok(row);
      assert.equal(textOf(findTree(row, node => node.type === 'dd')), expected);
    } finally { await h.close(); }
  });
}

test('공개 검증 응답에 협찬 필드가 없으면 지금처럼 줄을 안 그려요', async () => {
  const h = await modelComponentHarness({ entry: '/src/features/verify/PublicVerify.jsx', exportName: 'PublicVerify', initialStates: ['ok', { status: 'active', valid: true, validUntil: null }, null], api: {} });
  try { assert.equal(findTree(h.render(), node => node.type === 'dt' && node.props.children === '협찬'), null); }
  finally { await h.close(); }
});

const model = { id: 'm1', sponsorshipEnabled: true, instagramHandle: 'daily', instagramFollowers: 1200, sizeTop: 'M', sizeBottomWaist: 28, sponsorshipProfileConsentAt: '2026-09-22T00:00:00Z' };
const purged = { ...model, sponsorshipEnabled: false, instagramHandle: null, instagramFollowers: null, sizeTop: null, sizeBottomWaist: null, sponsorshipProfileConsentAt: null };
const settingsHarness = api => modelComponentHarness({ entry: '/src/features/model/SponsorshipSettings.jsx', exportName: 'ModelSponsorshipSettings', initialStates: [], honorHookDependencies: true, api });
const fields = tree => findTree(tree, node => node.type?.name === 'SponsorshipFields');
const runEffects = h => { const effects = h.runtime.effects.splice(0); return effects.map(effect => effect()).filter(Boolean); };

test('마이페이지는 발급된 증서를 보여주고, 끄면 무효 안내 뒤 상태를 다시 확인해요', async () => {
  const lookups = [];
  let status = 'active';
  const h = await settingsHarness({
    getSponsorshipCredential: async id => { lookups.push(id); return cred(status, { vcId: 'vc-1' }); },
    updateModelSponsorship: async () => { status = 'none'; return purged; },
  });
  let current = model;
  const props = () => ({ model: current, onModelChange: next => { current = next; } });
  const cleanups = [];
  // 이 하네스는 렌더마다 effect 목록을 새로 받아요. 렌더할 때마다 바로 돌려야 놓치지 않아요.
  const render = () => { const tree = h.render(props()); cleanups.push(...runEffects(h)); return tree; };
  try {
    await eventually(() => fields(render()).props.notice, 'credential notice appears');
    const notice = fields(render()).props.notice;
    assert.match(textOf(notice), /협찬 동의 증명서가 발급됐어요\./);
    assert.ok(textOf(notice).includes(SPONSORSHIP_OFF_NOTICE));
    fields(render()).props.onChange({ ...sponsorshipDraft(model), sponsorshipEnabled: false });
    await eventually(() => /무효 처리했어요/.test(textOf(render())), 'off message mentions revocation');
    await eventually(() => lookups.length === 2 && render(), 'credential refetched after save');
    assert.equal(fields(render()).props.notice, null);
  } finally { cleanups.forEach(cleanup => cleanup()); await h.close(); }
});

test('마이페이지는 기능이 꺼져 있으면 지금처럼 아무 안내도 안 붙여요', async () => {
  const h = await settingsHarness({});
  const cleanups = [];
  try {
    h.render({ model }); cleanups.push(...runEffects(h));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(fields(h.render({ model })).props.notice, null);
  } finally { cleanups.forEach(cleanup => cleanup()); await h.close(); }
});

test('셀러 협찬 요청이 404 면 받을 수 없는 모델이라고 알려요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/facemarket-landing/sections/ModelDetailDialog.jsx', exportName: 'ModelDetailDialog',
    initialStates: [], api: {
      getSponsorshipInterest: async () => ({ interested: false }),
      requestSponsorshipInterest: async () => { throw Object.assign(new Error('찾을 수 없어요.'), { status: 404, code: 'not_found' }); },
    },
  });
  h.runtime.session = { user: { id: 'seller-1' } };
  const props = { model: { id: 'model-1', kind: 'real', name: '모델', sponsorship: { enabled: true, instagramHandle: 'daily', instagramFollowers: 1200, sizeTop: 'M', sizeBottomWaist: 28 } }, onClose: () => {} };
  try {
    await findTree(h.render(props), node => node.type === 'button' && node.props.className === 'interestButton').props.onClick();
    assert.equal(textOf(findTree(h.render(props), node => node.props?.role === 'alert')), SPONSORSHIP_UNAVAILABLE_MESSAGE);
  } finally { await h.close(); }
});

test('발급 중이면 10초마다 다시 확인하고, 발급되면 멈춰요', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const answers = ['pending', 'pending', 'active'];
  let lookups = 0;
  const h = await settingsHarness({ getSponsorshipCredential: async () => cred(answers[Math.min(lookups++, answers.length - 1)]) });
  const cleanups = [];
  const render = () => { const tree = h.render({ model }); cleanups.push(...runEffects(h)); return tree; };
  const notice = () => textOf(fields(render()).props.notice);
  try {
    await eventually(() => /발급하고 있어요/.test(notice()), 'pending copy');
    t.mock.timers.tick(9_999);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(lookups, 1);
    t.mock.timers.tick(1);
    await eventually(() => lookups === 2, 'second check');
    t.mock.timers.tick(10_000);
    await eventually(() => /발급됐어요/.test(notice()), 'active copy');
    t.mock.timers.tick(60_000);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(lookups, 3);
  } finally { cleanups.forEach(cleanup => cleanup()); await h.close(); }
});

test('poll delay: 발급 중 10초, 등록 대기 1분, 30분 뒤·끝난 상태는 멈춰요', async () => {
  const { sponsorshipCredentialPollDelay, SPONSORSHIP_CREDENTIAL_POLL_LIMIT_MS } = await import('../../src/features/model/sponsorshipCredential.js');
  const c = status => ({ featureEnabled: true, status, vcId: null, issuedAt: null });
  assert.equal(sponsorshipCredentialPollDelay(c('pending')), 10_000);
  assert.equal(sponsorshipCredentialPollDelay(c('waiting_license')), 60_000);
  assert.equal(sponsorshipCredentialPollDelay(c('active')), null);
  assert.equal(sponsorshipCredentialPollDelay(c('none')), null);
  assert.equal(sponsorshipCredentialPollDelay(c('none'), { sponsorshipEnabled: true }), 10_000);
  assert.equal(sponsorshipCredentialPollDelay(c('pending'), { elapsedMs: SPONSORSHIP_CREDENTIAL_POLL_LIMIT_MS }), null);
});

test('협찬은 켜졌는데 증서가 없으면 준비 중 문구, 검증 줄은 서버 날짜(consentedOn)를 그대로 써요', async () => {
  const { sponsorshipCredentialCopy, verifySponsorshipLine } = await import('../../src/features/model/sponsorshipCredential.js');
  assert.equal(sponsorshipCredentialCopy({ featureEnabled: true, status: 'none' }).text, '협찬 동의 증명서를 준비하고 있어요.');
  const line = verifySponsorshipLine({ active: true, vcId: 'vc-1', consentedOn: '2026-09-26', consentDocVersion: 'v1' });
  assert.equal(line.text, '협찬 동의 · 증명서 유효 · 동의일 2026-09-26 (KST)');
});
