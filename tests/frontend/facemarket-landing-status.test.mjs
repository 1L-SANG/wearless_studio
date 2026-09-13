import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { registerCta, APPLY_LABEL } from '../../src/features/facemarket-landing/registerCta.js';
import { eventually, findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.endsWith('.svg')) return { url: new URL(specifier, context.parentURL).href, shortCircuit: true };
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith('.svg')) return { format: 'module', shortCircuit: true, source: `export default ${JSON.stringify(url)};` };
    return nextLoad(url, context);
  },
});
const { landingStatusPill } = await import('../../src/features/facemarket-landing/landingStatus.js');
const text = node => Array.isArray(node) ? node.map(text).join('')
  : node && typeof node === 'object' ? text(node.props?.children) : String(node ?? '');
const byClass = (tree, name) => findTree(tree, node => node.props?.className?.split(' ').includes(name));
const verified = { id: 'm1', status: 'verified' };
const activeLicense = { id: 'l1', modelId: 'm1', status: 'active', licenseValidUntil: null };

// These cases catch incorrect precedence, zero/failure conflation and route/copy drift.
const cases = [
  ['verified usage', { ownedModel: verified, license: activeLicense, settlement: { monthCount: 3 } }, 'live', true, '공개 중', { before: '이번 달 ', strong: '3건', after: ' 쓰였어요' }],
  ['verified zero', { ownedModel: verified, license: activeLicense, settlement: { monthCount: 0 } }, 'live', true, '공개 중', { before: '이번 달 ', strong: '0건', after: '' }],
  ['verified failed summary', { ownedModel: verified, license: activeLicense, settlement: null }, 'live', true, '공개 중', null],
  ['verified missing count', { ownedModel: verified, license: activeLicense, settlement: {} }, 'live', true, '공개 중', null],
  ['verified legacy future license', { ownedModel: verified, license: { ...activeLicense, licenseValidUntil: '2099-01-01T00:00:00Z' }, settlement: { monthCount: 1 } }, 'live', true, '공개 중', { before: '이번 달 ', strong: '1건', after: ' 쓰였어요' }],
  ['verified takes precedence', { ownedModel: verified, license: activeLicense, enrollment: { status: 'photos_pending' }, application: { status: 'rejected' } }, 'live', true, '공개 중', null],
  ['revoked license', { ownedModel: verified, license: { ...activeLicense, status: 'revoked' } }, 'rejected', false, '라이선스 종료', { before: '새로운 사용은 ', strong: '중단됐어요', after: '' }],
  ['expired legacy license', { ownedModel: verified, license: { ...activeLicense, licenseValidUntil: '2020-01-01T00:00:00Z' } }, 'rejected', false, '라이선스 종료', { before: '새로운 사용은 ', strong: '중단됐어요', after: '' }],
  ['verified without license', { ownedModel: verified }, 'progress', false, '공개 준비 중', { before: '다음은 ', strong: '라이선스 확인', after: '' }],
  ['owner paused', { ownedModel: { ...verified, status: 'suspended', suspensionSource: 'owner' }, license: activeLicense }, 'paused', false, '활동 일시 중지', { before: '지금은 ', strong: '새 요청을 받지 않아요', after: '' }],
  ['admin paused', { ownedModel: { ...verified, status: 'suspended', suspensionSource: 'admin' }, license: activeLicense }, 'paused', false, '활동 일시 중지', { before: '', strong: '운영팀 확인이 필요해요', after: '' }],
  ['awaiting test cuts', { ownedModel: { ...verified, status: 'awaiting_confirm' }, license: activeLicense }, 'confirm', false, '테스트컷 확인', { before: '다음은 ', strong: '프로필 이미지 확정', after: '' }],
  ['confirm pending enrollment', { ownedModel: { ...verified, status: 'pending' }, enrollment: { status: 'confirm_pending' } }, 'confirm', false, '테스트컷 확인', { before: '다음은 ', strong: '프로필 이미지 확정', after: '' }],
  ['re-registration overrides revoked history', { ownedModel: { ...verified, status: 'reverification_required' }, enrollment: { status: 'photos_pending', photos: [] }, license: { ...activeLicense, status: 'revoked' } }, 'progress', false, '등록 진행 중', { before: '다음은 ', strong: '사진 등록', after: '' }],
  ['test-cut confirmation overrides revoked history', { ownedModel: { ...verified, status: 'awaiting_confirm' }, enrollment: { status: 'confirm_pending' }, license: { ...activeLicense, status: 'revoked' } }, 'confirm', false, '테스트컷 확인', { before: '다음은 ', strong: '프로필 이미지 확정', after: '' }],
  ...[
    ['identity_pending', '본인확인'], ['photos_pending', '사진 등록'], ['liveness_pending', '본인확인 마무리'],
    ['processing', '모델 이미지 준비'], ['asset_building', '모델 이미지 준비'],
    ['license_pending', '사용 조건 정하기'], ['vc_pending', '사용 조건 정하기'],
  ].map(([status, step]) => [status, { enrollment: { status, photos: [] }, application: { status: 'approved' } },
    'progress', false, '등록 진행 중', { before: '다음은 ', strong: step, after: '' }]),
  ['unknown enrollment step', { enrollment: { status: 'review_pending' } }, 'progress', false, '등록 진행 중', null],
  ['pending model', { ownedModel: { status: 'pending' } }, 'progress', false, '등록 진행 중', { before: '다음은 ', strong: '모델 이미지 준비', after: '' }],
  ['reverification model', { ownedModel: { status: 'reverification_required' }, enrollment: { status: 'failed' } }, 'progress', false, '등록 진행 중', { before: '다음은 ', strong: '모델 이미지 준비', after: '' }],
  ['enrollment before pending model', { ownedModel: { status: 'pending' }, enrollment: { status: 'photos_pending' } }, 'progress', false, '등록 진행 중', { before: '다음은 ', strong: '사진 등록', after: '' }],
  ['Seoul receipt rolls to next day', { application: { status: 'under_review', createdAt: '2026-09-11T15:05:00Z' } }, 'review', true, '검토 중', { before: '', strong: '9월 12일', after: ' 접수' }],
  ['Seoul receipt before midnight', { application: { status: 'under_review', createdAt: '2026-09-11T14:59:00Z' } }, 'review', true, '검토 중', { before: '', strong: '9월 11일', after: ' 접수' }],
  ['invalid receipt', { application: { status: 'under_review', createdAt: 'invalid' } }, 'review', true, '검토 중', null],
  ['missing receipt', { application: { status: 'under_review' } }, 'review', true, '검토 중', null],
  ['approved', { application: { status: 'approved' } }, 'approved', false, '승인됐어요', { before: '다음은 ', strong: '본인확인', after: '' }],
  ['rejected', { application: { status: 'rejected' } }, 'rejected', false, '이번엔 어려워요', null],
];
for (const [name, input, tone, pulse, title, detail] of cases) {
  test(`landing status: ${name}`, () => {
    const pill = landingStatusPill(input);
    assert.deepEqual({ tone: pill.tone, pulse: pill.pulse, title: pill.title, detail: pill.detail }, { tone, pulse, title, detail });
    assert.deepEqual(pill.cta, registerCta(input.ownedModel, input.enrollment, {
      application: input.application, license: input.license, scope: 'landing',
    }));
  });
}
for (const input of [{}, { application: { status: 'cancelled' } }, ...['cancelled', 'failed', 'passed'].map(status => ({ enrollment: { status } }))]) {
  test(`landing status: no pill for ${JSON.stringify(input)}`, () => assert.equal(landingStatusPill(input), null));
}

test('Hero replaces the earlybird button, ring and caption with one clickable status pill', async () => {
  const harness = await modelComponentHarness({ entry: '/src/features/facemarket-landing/sections/HeroSection.jsx', exportName: 'HeroSection', initialStates: [], api: {} });
  try {
    let clicks = 0;
    for (const [, input] of cases) {
      const statusPill = landingStatusPill(input);
      const tree = harness.render({ statusPill, primaryLabel: APPLY_LABEL, onPrimary: () => { clicks += 1; } });
      const pill = byClass(tree, 'statusPill');
      assert.ok(pill);
      assert.ok(text(pill).includes(statusPill.title));
      assert.ok(text(pill).includes(statusPill.cta.label));
      if (statusPill.detail) assert.ok(text(pill).includes(Object.values(statusPill.detail).join('')));
      for (const name of ['heroCta', 'heroCtaRing', 'heroCaption']) assert.equal(byClass(tree, name), null);
      assert.equal(pill.type, 'button');
      pill.props.onClick();
    }
    assert.equal(clicks, cases.length);
    const ordinary = harness.render({ primaryLabel: APPLY_LABEL, onPrimary: () => {} });
    for (const name of ['heroCta', 'heroCtaRing', 'heroCaption']) assert.ok(byClass(ordinary, name));
    const hidden = harness.render({ primaryLabel: null, statusPill: null });
    assert.equal(byClass(hidden, 'statusPill'), null);
    assert.equal(byClass(hidden, 'heroCta'), null);
  } finally { await harness.close(); }
});

async function shellHarness(api = {}) {
  const savedWindow = globalThis.window;
  const savedDocument = globalThis.document;
  globalThis.window = { scrollTo() {}, location: { hostname: 'localhost', search: '?facemarket=1' } };
  const meta = { getAttribute: () => '', setAttribute() {} };
  globalThis.document = { title: '', querySelector: () => meta };
  let harness;
  try {
    harness = await modelComponentHarness({ entry: '/src/features/facemarket-landing/LandingShell.jsx', exportName: 'LandingShell', initialStates: [], honorHookDependencies: true,
      api: { listMyModels: async () => [], listLicenses: async () => [], getCurrentEnrollment: async () => null, getCurrentApplication: async () => null, ...api } });
  } catch (error) { globalThis.window = savedWindow; globalThis.document = savedDocument; throw error; }
  harness.runtime.session = { user: { id: 'u1' } };
  harness.runtime.location = { pathname: '/' };
  let props;
  return {
    ...harness,
    view: () => props,
    commit() {
      const tree = harness.render({ title: 'FaceMarket', description: 'test', children: value => { props = value; return null; } });
      harness.runtime.effects.forEach(effect => effect());
      return tree;
    },
    async close() { await harness.close(); globalThis.window = savedWindow; globalThis.document = savedDocument; },
  };
}

for (const fails of [false, true]) {
  test(`LandingShell loads summary only after verified records and keeps pill on ${fails ? 'failure' : 'success'}`, async () => {
    let settle;
    const summary = new Promise((resolve, reject) => { settle = fails ? reject : resolve; });
    const order = [];
    const harness = await shellHarness({
      listMyModels: async () => { order.push('models'); return [verified]; },
      listLicenses: async () => { order.push('licenses'); return [activeLicense]; },
      getCurrentEnrollment: async () => { order.push('enrollment'); return null; },
      getCurrentApplication: async () => { order.push('application'); return null; },
      getSettlementSummary: () => { order.push('summary'); return summary; },
    });
    try {
      harness.commit();
      await eventually(() => { harness.commit(); return !!harness.view().statusPill; }, 'status must render while summary is pending');
      assert.deepEqual(order, ['models', 'licenses', 'enrollment', 'application', 'summary']);
      assert.equal(harness.view().statusPill.detail, null);
      settle(fails ? new Error('offline') : { monthCount: 3 });
      await new Promise(resolve => setImmediate(resolve));
      const tree = harness.commit();
      assert.equal(harness.view().statusPill.title, '공개 중');
      assert.equal(harness.view().statusPill.detail?.strong ?? null, fails ? null : '3건');
      const header = findTree(tree, node => node.type?.name === 'LandingHeader');
      assert.equal(header.props.primaryLabel, '마이페이지');
      assert.equal(header.props.statusPill, undefined);
      const destinations = [];
      harness.runtime.navigate = to => destinations.push(to);
      harness.commit();
      harness.view().onPrimary();
      assert.deepEqual(destinations, ['/status']);
      harness.runtime.location.pathname = '/status';
      harness.commit();
      assert.equal(harness.view().statusPill, null);
      assert.equal(harness.view().ctaLabel, null);
      assert.equal(harness.view().onPrimary, undefined);
    } finally { await harness.close(); }
  });
}

test('LandingShell skips summary for ordinary records and anonymous visits', async () => {
  let calls = 0;
  const harness = await shellHarness({ getCurrentApplication: async () => ({ status: 'approved' }), getSettlementSummary: async () => { calls += 1; return { monthCount: 0 }; } });
  try {
    harness.commit();
    await eventually(() => { harness.commit(); return !!harness.view().statusPill; }, 'approved pill');
    assert.equal(harness.view().statusPill.title, '승인됐어요');
    assert.equal(calls, 0);
    harness.runtime.session = null;
    harness.commit();
    assert.equal(harness.view().statusPill, null, 'previous account data is hidden immediately');
    harness.commit();
    assert.equal(harness.view().ctaLabel, APPLY_LABEL);
    assert.equal(harness.view().statusPill, null);
  } finally { await harness.close(); }
});

test('LandingShell prefers an active re-registration over revoked license history', async () => {
  const harness = await shellHarness({
    listMyModels: async () => [{ id: 'm1', status: 'reverification_required' }],
    listLicenses: async () => [{ ...activeLicense, status: 'revoked' }],
    getCurrentEnrollment: async () => ({ id: 'e2', modelId: 'm1', status: 'photos_pending', photos: [] }),
  });
  try {
    harness.commit();
    await eventually(() => { harness.commit(); return !!harness.view().statusPill; }, 're-registration pill');
    assert.equal(harness.view().statusPill.title, '등록 진행 중');
    assert.equal(harness.view().statusPill.detail.strong, '사진 등록');
    assert.equal(harness.view().ctaLabel, '모델 등록하기');
  } finally { await harness.close(); }
});

test('landing home renders the shell status in its real hero', async () => {
  const harness = await modelComponentHarness({ entry: '/src/features/facemarket-landing/FacemarketLanding.jsx', exportName: 'FacemarketLanding', initialStates: [], api: {} });
  try {
    const statusPill = landingStatusPill({ application: { status: 'rejected' } });
    const shell = harness.render();
    const contents = shell.props.children({ ctaLabel: statusPill.cta.label, onPrimary: () => {}, statusPill });
    const hero = findTree(contents, node => node.type?.name === 'HeroSection');
    const rendered = hero.type(hero.props);
    assert.ok(text(byClass(rendered, 'statusPill')).includes('이번엔 어려워요'));
    assert.equal(byClass(rendered, 'heroCta'), null);
  } finally { await harness.close(); }
});

for (const status of [null, 'cancelled']) {
  test(`LandingShell retains earlybird CTA when application is ${status}`, async () => {
    let summaryCalls = 0;
    const harness = await shellHarness({
      getCurrentApplication: async () => status ? { status } : null,
      getSettlementSummary: async () => { summaryCalls += 1; return { monthCount: 3 }; },
    });
    try {
      harness.commit();
      await eventually(() => { harness.commit(); return harness.view().ctaLabel === APPLY_LABEL; }, 'earlybird CTA');
      assert.equal(harness.view().statusPill, null);
      assert.equal(summaryCalls, 0);
      const destinations = [];
      harness.runtime.navigate = to => destinations.push(to);
      harness.commit();
      harness.view().onPrimary();
      assert.deepEqual(destinations, [status ? '/model/apply' : '/apply']);
    } finally { await harness.close(); }
  });
}

test('LandingShell never treats a failed records lookup as no records', async () => {
  let complete;
  const pending = new Promise(resolve => { complete = resolve; });
  let summaryCalls = 0;
  const harness = await shellHarness({
    listMyModels: async () => { await pending; throw new Error('offline'); },
    getSettlementSummary: async () => { summaryCalls += 1; return { monthCount: 3 }; },
  });
  try {
    harness.commit();
    assert.equal(harness.view().ctaLabel, null);
    assert.equal(harness.view().statusPill, null);
    complete();
    await new Promise(resolve => setImmediate(resolve));
    harness.commit();
    assert.equal(harness.view().ctaLabel, null);
    assert.equal(harness.view().statusPill, null);
    assert.equal(summaryCalls, 0);
  } finally { await harness.close(); }
});
