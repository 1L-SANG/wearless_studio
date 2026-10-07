import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createFacemarketMock } from '../../src/mock/facemarket.js';
import { CONSENT_VERSION } from '../../src/features/model/registerSlots.js';
import { eventually, findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';

const NOTICE = '셀러가 내 이미지를 셀러 자신의 SNS 속 피드에 게시물로 올릴 수가 있어요. 셀러 브랜드 피드에 노출되고 싶다면 허용해 주세요. 허용한 모델들은 모델 선택창에서 강조되어, 셀러들에게 더 많은 선택을 받을 수 있어요.';
const textOf = node => node == null || typeof node === 'boolean' ? '' : typeof node !== 'object' ? String(node) : Array.isArray(node) ? node.map(textOf).join('') : textOf(node.props?.children);
const button = (tree, label) => findTree(tree, node => node.type === 'button' && textOf(node) === label);
const commit = (h, props) => { const tree = h.render(props); h.runtime.effects.forEach(effect => effect()); return tree; };
const secondaryUseSettings = tree => findTree(tree, node => node.type?.name === 'SecondaryUseSettings');
const collectTree = (node, predicate, found = []) => {
  if (!node || typeof node !== 'object') return found;
  if (Array.isArray(node)) {
    node.forEach(child => collectTree(child, predicate, found));
    return found;
  }
  if (predicate(node)) found.push(node);
  collectTree(node.props?.children, predicate, found);
  return found;
};

test('협찬을 끈 뒤 2차 활용을 바꿔도 협찬과 삭제한 개인정보가 복원되지 않아요', async () => {
  let model = { id: 'm1', secondaryUseEnabled: false, sponsorshipEnabled: true,
    instagramHandle: 'initial', instagramFollowers: 100, sizeTop: 'M',
    sizeBottomWaist: 28, sponsorshipProfileConsentAt: '2026-09-28' };
  const writes = [];
  const api = {
    getSponsorshipCredential: async () => ({ featureEnabled: false }),
    updateModelSecondaryUse: async (_, enabled) => ({ secondaryUseEnabled: enabled }),
    updateModelSponsorship: async (_, payload) => {
      writes.push(payload);
      return payload.sponsorshipEnabled ? { ...payload, sponsorshipProfileConsentAt: '2026-09-28' }
        : { sponsorshipEnabled: false, instagramHandle: null, instagramFollowers: null,
          sizeTop: null, sizeBottomWaist: null, sponsorshipProfileConsentAt: null };
    },
  };
  const options = { entry: '/src/features/model/SponsorshipSettings.jsx', initialStates: [], honorHookDependencies: true, api };
  const secondary = await modelComponentHarness({ ...options, exportName: 'ModelSecondaryUseSettings' });
  const sponsorship = await modelComponentHarness({ ...options, exportName: 'ModelSponsorshipSettings' });
  const props = () => ({ model, onModelChange: next => { model = next; } });
  const renderBoth = () => { commit(secondary, props()); commit(sponsorship, props()); };
  const fields = () => findTree(sponsorship.render(props()), node => node.type?.name === 'SponsorshipFields');
  try {
    renderBoth();
    fields().props.onChange({ ...fields().props.value, sponsorshipEnabled: false });
    await eventually(() => { renderBoth(); return model.sponsorshipEnabled === false; }, '협찬 해제를 저장해요');
    await secondaryUseSettings(secondary.render(props())).props.onChange(true);
    renderBoth();
    assert.equal(fields().props.value.sponsorshipEnabled, false);
    assert.equal(model.instagramHandle, null);
    assert.equal(model.sponsorshipProfileConsentAt, null);
    await button(sponsorship.render(props()), '협찬 설정 저장').props.onClick();
    assert.deepEqual(writes, [{ sponsorshipEnabled: false }, { sponsorshipEnabled: false }]);
  } finally { await Promise.all([secondary.close(), sponsorship.close()]); }
});

test('모델 설정 조회가 실패하면 증서는 권한 확인 불가를 표시하고 재시도로 복구해요', async () => {
  let fail = true;
  const h = await modelComponentHarness({
    entry: '/src/features/model/ModelLicense.jsx', exportName: 'ModelLicense',
    initialStates: [], honorHookDependencies: true,
    api: {
      listLicenses: async () => [{ id: 'l1', modelId: 'm1', status: 'active' }],
      listMyModels: async () => { if (fail) throw new Error('temporary 503'); return [{ id: 'm1', secondaryUseEnabled: true }]; },
    },
  });
  h.runtime.push = () => {};
  const card = () => findTree(h.render(), node => node.type?.name === 'VcCard');
  try {
    await eventually(() => { commit(h); return card(); }, '조회에 실패해도 증서는 남아요');
    assert.equal(card().props.secondaryUseEnabled, null);
    assert.match(textOf(h.render()), /사용 범위를 확인하지 못했어요/);
    fail = false;
    await button(h.render(), '사용 범위 다시 확인').props.onClick();
    await eventually(() => { commit(h); return card()?.props.secondaryUseEnabled === true; }, '권한 확인이 복구돼요');
    assert.doesNotMatch(textOf(h.render()), /사용 범위를 확인하지 못했어요/);
  } finally { await h.close(); }
});

test('권한을 모르는 증서는 불허로 단정하지 않아요', async () => {
  const h = await modelComponentHarness({ entry: '/src/features/model/ModelLicense.jsx', exportName: 'VcCard', initialStates: [], api: {} });
  const previousWindow = globalThis.window;
  globalThis.window = { location: { origin: 'https://facemarket.example' } };
  try {
    const tree = h.render({ license: { id: 'l1', status: 'active', allowedUse: [], unitPrice: 14900 }, secondaryUseEnabled: null });
    assert.match(textOf(tree), /사용 범위확인 필요/);
    assert.doesNotMatch(textOf(tree), /사용 범위상세페이지 착용컷/);
  } finally { globalThis.window = previousWindow; await h.close(); }
});

test('2차 활용 토글은 정본 문구와 실제 제어 상태를 보여줘요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/model/SponsorshipSettings.jsx',
    exportName: 'SecondaryUseSettings',
    initialStates: [],
    api: {},
  });
  let enabled = false;
  const render = () => h.render({ enabled, onChange: next => { enabled = next; } });
  try {
    const off = render();
    assert.match(textOf(off), /2차 활용 허용/);
    assert.ok(textOf(off).includes(NOTICE));
    assert.equal(findTree(off, node => node.props?.role === 'switch').props['aria-checked'], false);
    findTree(off, node => node.props?.role === 'switch').props.onClick();
    assert.equal(enabled, true);
    assert.equal(findTree(render(), node => node.props?.role === 'switch').props['aria-checked'], true);
  } finally { await h.close(); }
});

test('마이페이지 토글은 즉시 저장하고 실패하면 마지막 서버 값으로 되돌려요', async () => {
  const calls = [];
  let fail = true;
  let model = { id: 'm1', secondaryUseEnabled: false };
  const h = await modelComponentHarness({
    entry: '/src/features/model/SponsorshipSettings.jsx',
    exportName: 'ModelSecondaryUseSettings',
    initialStates: [],
    honorHookDependencies: true,
    api: {
      updateModelSecondaryUse: async (modelId, enabled, screen) => {
        calls.push({ modelId, enabled, screen });
        if (fail) throw new Error('서버 내부 오류');
        return { secondaryUseEnabled: enabled };
      },
    },
  });
  const props = () => ({ model, onModelChange: next => { model = next; } });
  try {
    commit(h, props());
    await secondaryUseSettings(h.render(props())).props.onChange(true);
    assert.equal(secondaryUseSettings(h.render(props())).props.enabled, false);
    assert.match(textOf(h.render(props())), /2차 활용 설정을 저장하지 못했어요\. 다시 시도해 주세요\./);
    assert.ok(findTree(h.render(props()), node => node.props?.role === 'alert'));

    fail = false;
    await secondaryUseSettings(h.render(props())).props.onChange(true);
    assert.equal(model.secondaryUseEnabled, true);
    assert.equal(secondaryUseSettings(h.render(props())).props.enabled, true);
    commit(h, props());
    assert.match(textOf(h.render(props())), /2차 활용 설정을 저장했어요\./);
    assert.deepEqual(calls, [
      { modelId: 'm1', enabled: true, screen: 'model_mypage' },
      { modelId: 'm1', enabled: true, screen: 'model_mypage' },
    ]);
  } finally { await h.close(); }
});

test('등록 조건 제출은 2차 활용 값이 바뀐 경우에만 라이선스보다 먼저 저장해요', async () => {
  const calls = [];
  const model = { id: 'm1', sponsorshipEnabled: false, secondaryUseEnabled: false };
  const enrollment = { id: 'e1', modelId: 'm1', status: 'license_pending', consentDocumentVersion: CONSENT_VERSION, termsConsentVersion: CONSENT_VERSION };
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    listMyModels: async () => [model],
    getCurrentEnrollment: async () => enrollment,
    updateModelSecondaryUse: async (modelId, enabled, screen) => {
      calls.push({ type: 'secondary', modelId, enabled, screen });
      return { secondaryUseEnabled: enabled };
    },
    createLicense: async () => { calls.push({ type: 'license' }); return { id: 'l1' }; },
  } });
  try {
    await eventually(() => {
      commit(h);
      return secondaryUseSettings(h.render())?.props.disabled === false;
    }, '저장된 모델 조건을 불러와요');
    secondaryUseSettings(h.render()).props.onChange(true);
    findTree(h.render(), node => node.props?.id === 'price-agreed').props.onChange({ target: { checked: true } });
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    assert.deepEqual(calls, [
      { type: 'secondary', modelId: 'm1', enabled: true, screen: 'model_register' },
      { type: 'license' },
    ]);
  } finally { await h.close(); }
});

test('등록 조건의 2차 활용 값이 그대로면 별도 저장 없이 라이선스를 발급해요', async () => {
  const calls = [];
  const enrollment = { id: 'e1', modelId: 'm1', status: 'license_pending', consentDocumentVersion: CONSENT_VERSION, termsConsentVersion: CONSENT_VERSION };
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    listMyModels: async () => [{ id: 'm1', sponsorshipEnabled: false, secondaryUseEnabled: false }],
    getCurrentEnrollment: async () => enrollment,
    updateModelSecondaryUse: async () => { calls.push('secondary'); },
    createLicense: async () => { calls.push('license'); return { id: 'l1' }; },
  } });
  try {
    await eventually(() => { commit(h); return secondaryUseSettings(h.render())?.props.disabled === false; }, '저장된 모델 조건을 불러와요');
    findTree(h.render(), node => node.props?.id === 'price-agreed').props.onChange({ target: { checked: true } });
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    assert.deepEqual(calls, ['license']);
  } finally { await h.close(); }
});

test('등록의 2차 활용 저장 실패는 증서 발급을 막고 같은 화면에 오류를 보여줘요', async () => {
  const enrollment = { id: 'e1', modelId: 'm1', status: 'license_pending', consentDocumentVersion: CONSENT_VERSION, termsConsentVersion: CONSENT_VERSION };
  let issued = false;
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    listMyModels: async () => [{ id: 'm1', sponsorshipEnabled: false, secondaryUseEnabled: false }],
    getCurrentEnrollment: async () => enrollment,
    updateModelSecondaryUse: async () => { throw new Error('2차 활용 설정을 저장하지 못했어요. 다시 시도해 주세요.'); },
    createLicense: async () => { issued = true; return { id: 'l1' }; },
  } });
  try {
    await eventually(() => { commit(h); return secondaryUseSettings(h.render())?.props.disabled === false; }, '저장된 모델 조건을 불러와요');
    secondaryUseSettings(h.render()).props.onChange(true);
    findTree(h.render(), node => node.props?.id === 'price-agreed').props.onChange({ target: { checked: true } });
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    assert.equal(issued, false);
    assert.match(textOf(h.render()), /2차 활용 설정을 저장하지 못했어요\. 다시 시도해 주세요\./);
  } finally { await h.close(); }
});

test('목 어댑터도 2차 활용 설정을 저장하고 실제 응답 모양을 돌려줘요', async () => {
  const mock = createFacemarketMock();
  assert.deepEqual(await mock.updateModelSecondaryUse('m1', true, 'model_mypage'), { secondaryUseEnabled: true });
  assert.deepEqual(await mock.updateModelSecondaryUse('m1', false, 'model_register'), { secondaryUseEnabled: false });
});

test('증서는 2차 활용 허용 여부에 맞는 사용 범위를 표시해요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/model/ModelLicense.jsx',
    exportName: 'VcCard',
    initialStates: [],
    api: {},
  });
  const previousWindow = globalThis.window;
  globalThis.window = { location: { origin: 'https://facemarket.example' } };
  const license = { id: 'l1', status: 'active', allowedUse: ['일반 의류'], unitPrice: 14900, faceImageUri: '' };
  try {
    assert.match(textOf(h.render({ license, secondaryUseEnabled: true })), /사용 범위상세페이지 착용컷 · 공식 SNS 게시물/);
    assert.match(textOf(h.render({ license, secondaryUseEnabled: false })), /사용 범위상세페이지 착용컷/);
    assert.doesNotMatch(textOf(h.render({ license, secondaryUseEnabled: false })), /공식 SNS 게시물/);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
    await h.close();
  }
});

test('증서는 포토 월렛 패스 구조와 본인 실명, 네 가지 허락 항목을 표시해요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/model/ModelLicense.jsx',
    exportName: 'VcCard',
    initialStates: [null, { model: { nameMasked: '김*우' } }, 'data:image/png;base64,qr', false, false],
    api: {},
  });
  const previousWindow = globalThis.window;
  globalThis.window = { location: { origin: 'https://facemarket.example' } };
  const vcId = 'vc:fm:1234567890abcdefghijklmnopqrstuvwxyz';
  const license = {
    id: 'l1', modelId: 'm1', status: 'active', holderName: '김선우',
    allowedUse: ['일반 의류', '액티브웨어'], unitPrice: 14900,
    faceImageUri: '', coverImageUrl: '/model.webp', vcId, licenseValidUntil: null,
  };
  try {
    const tree = h.render({ license, secondaryUseEnabled: true });
    assert.equal(tree.props.className, 'vcWrap');
    const card = findTree(tree, node => node.type === 'article');
    assert.equal(card.props.className, 'vc');
    assert.ok(findTree(card, node => node.type === 'img' && node.props?.src === '/assets/brand/facemarket-logo.svg' && node.props?.alt === 'FaceMarket'));
    assert.ok(findTree(card, node => node.type === 'img' && node.props?.src === '/model.webp'));
    assert.match(textOf(card), /유효/);
    assert.match(textOf(card), /김선우/);
    assert.doesNotMatch(textOf(card), /김\*우/);
    assert.match(textOf(card), /초상 라이선스 · OpenDID 발급/);
    assert.deepEqual(
      collectTree(card, node => node.type === 'dt').map(textOf),
      ['사용 범위', '허용 품목', '단가', '유효기간'],
    );
    assert.match(textOf(card), /상세페이지 착용컷 · 공식 SNS 게시물/);
    assert.match(textOf(card), /일반 의류 · 액티브웨어/);
    assert.match(textOf(card), /₩14,900 \/ 건/);
    assert.match(textOf(card), /철회 시까지/);
    assert.match(textOf(card), /QR로 누구나 이 허락을 확인할 수 있어요/);
    const vcCode = findTree(card, node => node.type === 'code' && node.props?.title === vcId);
    assert.ok(vcCode);
    assert.match(textOf(vcCode), /…/);
    let copied = null;
    vcCode.props.onCopy({
      preventDefault() {},
      clipboardData: { setData: (type, value) => { copied = { type, value }; } },
    });
    assert.deepEqual(copied, { type: 'text/plain', value: vcId });
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
    await h.close();
  }
});

test('증서는 실명이 없으면 공개 검증의 가려진 이름을 사용해요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/model/ModelLicense.jsx',
    exportName: 'VcCard',
    initialStates: [null, { model: { nameMasked: '김*우' } }, null, false, false],
    api: {},
  });
  const previousWindow = globalThis.window;
  globalThis.window = { location: { origin: 'https://facemarket.example' } };
  try {
    const tree = h.render({ license: { id: 'l1', status: 'active', allowedUse: [], unitPrice: 14900, faceImageUri: '' } });
    assert.match(textOf(tree), /김\*우/);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
    await h.close();
  }
});

test('앞면의 작은 QR은 큰 검증 QR로 전환하고 카드 밖에는 해지 버튼만 둬요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/model/ModelLicense.jsx',
    exportName: 'VcCard',
    initialStates: [null, null, 'data:image/png;base64,qr', false, false],
    api: {},
  });
  const previousWindow = globalThis.window;
  globalThis.window = { location: { origin: 'https://facemarket.example' }, confirm: () => false };
  const license = { id: 'l1', status: 'active', allowedUse: [], unitPrice: 14900, faceImageUri: '' };
  try {
    const front = h.render({ license });
    const smallQr = findTree(front, node => node.type === 'button' && node.props?.['aria-label'] === 'QR 크게 보기');
    assert.ok(smallQr);
    assert.ok(findTree(smallQr, node => node.type === 'img' && node.props?.className === 'vcQrThumb'));
    const outsideActions = Array.isArray(front.props.children) ? front.props.children[1] : null;
    assert.equal(textOf(outsideActions), '해지');
    assert.equal(collectTree(outsideActions, node => node.type === 'button').length, 1);

    smallQr.props.onClick();
    const back = h.render({ license });
    assert.ok(findTree(back, node => node.type === 'button' && node.props?.['aria-label'] === '증서로 돌아가기'));
    assert.match(textOf(back), /스캔하면 이 라이선스가 유효한지 로그인 없이 확인할 수 있어요/);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
    await h.close();
  }
});

test('해지된 증서는 중립 상태 pill을 쓰고 해지 버튼을 숨겨요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/model/ModelLicense.jsx',
    exportName: 'VcCard',
    initialStates: [null, { status: 'revoked', model: { nameMasked: '김*우' } }, null, false, false],
    api: {},
  });
  const previousWindow = globalThis.window;
  globalThis.window = { location: { origin: 'https://facemarket.example' } };
  try {
    const tree = h.render({ license: { id: 'l1', status: 'revoked', allowedUse: [], unitPrice: 14900, faceImageUri: '' } });
    const pill = findTree(tree, node => typeof node.props?.className === 'string' && node.props.className.includes('vcStatusOff'));
    assert.equal(textOf(pill), '해지됨');
    assert.equal(button(tree, '해지'), null);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
    await h.close();
  }
});

test('라이선스 화면은 모델 조회의 2차 활용 값을 해당 증서 카드에 전달해요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/model/ModelLicense.jsx',
    exportName: 'ModelLicense',
    initialStates: [],
    honorHookDependencies: true,
    api: {
      listLicenses: async () => [{ id: 'l1', modelId: 'm1', status: 'active' }],
      listMyModels: async () => [{ id: 'm1', secondaryUseEnabled: true }],
    },
  });
  h.runtime.push = () => {};
  try {
    await eventually(() => {
      commit(h);
      return findTree(h.render(), node => node.type?.name === 'VcCard')?.props.secondaryUseEnabled === true;
    }, '모델의 2차 활용 설정을 증서에 전달해요');
  } finally { await h.close(); }
});

test('공용 배지는 허용 상태에서만 아이콘과 2차 활용 가능 문구를 읽을 수 있게 보여줘요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/model/SecondaryUseBadge.jsx',
    exportName: 'SecondaryUseBadge',
    initialStates: [],
    api: {},
  });
  try {
    assert.equal(h.render({ enabled: false }), null);
    const badge = h.render({ enabled: true });
    assert.equal(textOf(badge), '2차 활용 가능');
    assert.equal(badge.props['aria-label'], '2차 활용 가능');
    assert.equal(Object.hasOwn(badge.props, 'aria-hidden'), false);
    const icon = findTree(badge, node => node !== badge && node.props?.size === 13);
    assert.ok(icon, '13px ShieldCheck 아이콘을 표시해요');
  } finally { await h.close(); }
});

test('셀러 목록과 상세는 허용한 실제 모델에만 공용 배지를 보여줘요', async () => {
  const base = {
    id: 'model-1', kind: 'real', name: '모델', alt: '모델 대표 이미지', closeup: '/model.webp',
    gender: '여성', ageBand: '20대', license: { uses: ['일반 의류'] }, verified: true,
  };
  const enabled = { ...base, secondaryUseEnabled: true };
  const disabled = { ...base, id: 'model-2', secondaryUseEnabled: false };
  const list = await modelComponentHarness({
    entry: '/src/features/facemarket-landing/sections/BrowseSection.jsx',
    exportName: 'BrowseSection',
    initialStates: [null, false, 0, [enabled, disabled], 'all'],
    api: {},
  });
  const detail = await modelComponentHarness({
    entry: '/src/features/facemarket-landing/sections/ModelDetailDialog.jsx',
    exportName: 'ModelDetailDialog',
    initialStates: [],
    api: {},
  });
  list.runtime.session = { access_token: 'token' };
  detail.runtime.session = { user: { id: 'seller-1' } };
  try {
    const isBadge = node => node.type?.name === 'SecondaryUseBadge';
    assert.equal(collectTree(list.render(), isBadge).length, 1);
    assert.equal(collectTree(detail.render({ model: enabled, onClose: () => {} }), isBadge).length, 1);
    assert.equal(collectTree(detail.render({ model: disabled, onClose: () => {} }), isBadge).length, 0);
  } finally { await Promise.all([list.close(), detail.close()]); }
});
