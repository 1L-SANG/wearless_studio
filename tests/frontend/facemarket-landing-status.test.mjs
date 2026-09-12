import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { APPLY_LABEL } from '../../src/features/facemarket-landing/registerCta.js';
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
const byClass = (tree, name) => findTree(tree, node => typeof node.props?.className === 'string' && node.props.className.split(' ').includes(name));
const verified = { id: 'm1', status: 'verified' };
const activeLicense = { id: 'l1', modelId: 'm1', status: 'active', licenseValidUntil: null };

// These cases catch incorrect precedence and keep every status on the My Page route.
const cases = [
  ['verified', { ownedModel: verified, license: activeLicense }, 'live', true, '공개 중'],
  ['verified legacy future license', { ownedModel: verified, license: { ...activeLicense, licenseValidUntil: '2099-01-01T00:00:00Z' } }, 'live', true, '공개 중'],
  ['verified takes precedence', { ownedModel: verified, license: activeLicense, enrollment: { status: 'photos_pending' }, application: { status: 'rejected' } }, 'live', true, '공개 중'],
  ['revoked license', { ownedModel: verified, license: { ...activeLicense, status: 'revoked' } }, 'rejected', false, '라이선스 종료'],
  ['expired legacy license', { ownedModel: verified, license: { ...activeLicense, licenseValidUntil: '2020-01-01T00:00:00Z' } }, 'rejected', false, '라이선스 종료'],
  ['verified without license', { ownedModel: verified }, 'progress', false, '공개 준비 중'],
  ['owner paused', { ownedModel: { ...verified, status: 'suspended', suspensionSource: 'owner' }, license: activeLicense }, 'paused', false, '활동 일시 중지'],
  ['admin paused', { ownedModel: { ...verified, status: 'suspended', suspensionSource: 'admin' }, license: activeLicense }, 'paused', false, '활동 일시 중지'],
  ['awaiting test cuts', { ownedModel: { ...verified, status: 'awaiting_confirm' }, license: activeLicense }, 'confirm', false, '테스트컷 확인'],
  ['confirm pending enrollment', { ownedModel: { ...verified, status: 'pending' }, enrollment: { status: 'confirm_pending' } }, 'confirm', false, '테스트컷 확인'],
  ['re-registration overrides revoked history', { ownedModel: { ...verified, status: 'reverification_required' }, enrollment: { status: 'photos_pending', photos: [] }, license: { ...activeLicense, status: 'revoked' } }, 'progress', false, '등록 진행 중'],
  ['test-cut confirmation overrides revoked history', { ownedModel: { ...verified, status: 'awaiting_confirm' }, enrollment: { status: 'confirm_pending' }, license: { ...activeLicense, status: 'revoked' } }, 'confirm', false, '테스트컷 확인'],
  ...[
    'identity_pending', 'photos_pending', 'liveness_pending', 'processing',
    'asset_building', 'license_pending', 'vc_pending',
  ].map(status => [status, { enrollment: { status, photos: [] }, application: { status: 'approved' } },
    'progress', false, '등록 진행 중']),
  ['unknown enrollment step', { enrollment: { status: 'review_pending' } }, 'progress', false, '등록 진행 중'],
  ['pending model', { ownedModel: { status: 'pending' } }, 'progress', false, '등록 진행 중'],
  ['reverification model', { ownedModel: { status: 'reverification_required' }, enrollment: { status: 'failed' } }, 'progress', false, '등록 진행 중'],
  ['enrollment before pending model', { ownedModel: { status: 'pending' }, enrollment: { status: 'photos_pending' } }, 'progress', false, '등록 진행 중'],
  ['under review', { application: { status: 'under_review' } }, 'review', true, '검토 중'],
  ['approved', { application: { status: 'approved' } }, 'approved', false, '승인됐어요'],
  ['rejected', { application: { status: 'rejected' } }, 'rejected', false, '이번엔 어려워요'],
];
for (const [name, input, tone, pulse, title] of cases) {
  test(`landing status: ${name}`, () => {
    const pill = landingStatusPill(input);
    assert.deepEqual(pill, { tone, pulse, title, cta: { label: '마이페이지', to: '/status' } });
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
      assert.equal(text(pill), `${statusPill.title}마이페이지`);
      assert.equal(byClass(pill, 'statusPillDetail'), null);
      assert.ok(byClass(pill, 'statusPillSeparator'));
      assert.equal(Boolean(byClass(pill, 'statusPillPulse')), statusPill.pulse);
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

for (const [name, input, , , title] of cases.filter(([name]) => ['verified', 'photos_pending', 'under review', 'approved', 'rejected'].includes(name))) {
  test(`LandingShell ${name} skips summary, hides the header CTA and navigates to My Page`, async () => {
    let summaryCalls = 0;
    const harness = await shellHarness({
      listMyModels: async () => input.ownedModel ? [input.ownedModel] : [],
      listLicenses: async () => input.license ? [input.license] : [],
      getCurrentEnrollment: async () => input.enrollment ?? null,
      getCurrentApplication: async () => input.application ?? null,
      getSettlementSummary: async () => { summaryCalls += 1; return { monthCount: 3 }; },
    });
    try {
      harness.commit();
      await eventually(() => { harness.commit(); return !!harness.view().statusPill; }, 'status pill');
      const tree = harness.commit();
      assert.equal(summaryCalls, 0);
      assert.equal(harness.view().statusPill.title, title);
      assert.equal(harness.view().ctaLabel, '마이페이지');
      const header = findTree(tree, node => node.type?.name === 'LandingHeader');
      assert.equal(header.props.primaryLabel, null);
      assert.equal(header.props.onPrimary, undefined);
      assert.equal(header.props.statusPill, undefined);
      const headerTree = header.type(header.props);
      assert.equal(byClass(headerTree, 'headerCta'), null);
      assert.ok(findTree(headerTree, node => node.props?.to === '/status'));
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

test('LandingShell restores the earlybird header CTA after sign-out without loading summary', async () => {
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
    assert.equal(calls, 0);
    const header = findTree(harness.commit(), node => node.type?.name === 'LandingHeader');
    assert.equal(header.props.primaryLabel, APPLY_LABEL);
    const button = byClass(header.type(header.props), 'headerCta');
    assert.ok(button);
    const destinations = [];
    harness.runtime.navigate = to => destinations.push(to);
    const currentHeader = findTree(harness.commit(), node => node.type?.name === 'LandingHeader');
    byClass(currentHeader.type(currentHeader.props), 'headerCta').props.onClick();
    assert.deepEqual(destinations, ['/apply']);
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
    assert.deepEqual(harness.view().statusPill.cta, { label: '마이페이지', to: '/status' });
    assert.equal(harness.view().ctaLabel, '마이페이지');
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
      const header = findTree(harness.commit(), node => node.type?.name === 'LandingHeader');
      assert.equal(header.props.primaryLabel, APPLY_LABEL);
      assert.ok(byClass(header.type(header.props), 'headerCta'));
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
