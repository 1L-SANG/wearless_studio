import test from 'node:test';
import assert from 'node:assert/strict';
import { findTree, modelComponentHarness, eventually } from './helpers/facemarketHarness.mjs';
import { sponsorshipDraft } from '../../src/features/model/sponsorshipOptions.js';
import { CONSENT_VERSION } from '../../src/features/model/registerSlots.js';

const model = { id: 'm1', sponsorshipEnabled: true, instagramHandle: 'daily', instagramFollowers: 1200, sizeTop: 'M', sizeBottomWaist: 28, sponsorshipProfileConsentAt: '2026-09-22T00:00:00Z' };
// 서버는 끄면 프로필 정보와 동의 기록을 지운 행을 돌려줘요.
const purged = { ...model, sponsorshipEnabled: false, instagramHandle: null, instagramFollowers: null, sizeTop: null, sizeBottomWaist: null, sponsorshipProfileConsentAt: null };
const textOf = node => node == null || typeof node === 'boolean' ? '' : typeof node !== 'object' ? String(node) : Array.isArray(node) ? node.map(textOf).join('') : textOf(node.props?.children);
const button = (tree, label) => findTree(tree, node => node.type === 'button' && textOf(node) === label);
const fields = tree => findTree(tree, node => node.type?.name === 'SponsorshipFields');
const commit = (h, props) => { const tree = h.render(props); h.runtime.effects.forEach(effect => effect()); return tree; };
const settingsHarness = api => modelComponentHarness({ entry: '/src/features/model/SponsorshipSettings.jsx', exportName: 'ModelSponsorshipSettings', initialStates: [], honorHookDependencies: true, api });

test('협찬 기본값은 꺼짐이고 켜면 네 입력을 보이며 끄면 값을 보존해요', async () => {
  const h = await modelComponentHarness({ entry: '/src/features/model/SponsorshipSettings.jsx', exportName: 'SponsorshipFields', initialStates: [], api: {} });
  let value = sponsorshipDraft();
  const render = () => h.render({ value, onChange: next => { value = next; } });
  try {
    assert.equal(findTree(render(), node => node.props?.role === 'switch').props['aria-checked'], false);
    assert.equal(findTree(render(), node => node.type === 'input'), null);
    value = sponsorshipDraft(model);
    assert.equal(findTree(render(), node => node.type === 'input').props.value, 'daily');
    findTree(render(), node => node.props?.role === 'switch').props.onClick();
    assert.equal(findTree(render(), node => node.type === 'input'), null);
    findTree(render(), node => node.props?.role === 'switch').props.onClick();
    assert.deepEqual(value, sponsorshipDraft(model));
  } finally { await h.close(); }
});

test('마이페이지에서 끄기 저장이 실패하면 실제 켜진 상태와 재시도를 보여줘요', async () => {
  const calls = [];
  let shouldFail = true;
  const h = await settingsHarness({ updateModelSponsorship: async (id, body) => {
    calls.push({ id, body });
    if (shouldFail) throw new Error('잠시 연결할 수 없어요.');
    return purged;
  } });
  let currentModel = model;
  const props = () => ({ model: currentModel, onModelChange: next => { currentModel = next; } });
  try {
    commit(h, props());
    fields(h.render(props())).props.onChange({ ...sponsorshipDraft(model), sponsorshipEnabled: false });
    await eventually(() => findTree(h.render(props()), node => node.props?.role === 'alert'), 'save error appears');
    assert.equal(fields(h.render(props())).props.value.sponsorshipEnabled, true);
    assert.match(textOf(h.render(props())), /마지막으로 확인한 설정은 켜짐/);
    shouldFail = false;
    await button(h.render(props()), '다시 저장하기').props.onClick();
    assert.equal(currentModel.sponsorshipEnabled, false);
    assert.equal(currentModel.instagramHandle, null);
    assert.deepEqual(calls, [
      { id: 'm1', body: { sponsorshipEnabled: false } },
      { id: 'm1', body: { sponsorshipEnabled: false } },
    ]);
  } finally { await h.close(); }
});

test('사이즈 격자는 FREE 없이 선택 상태를 바꾸고 같은 칸을 다시 눌러도 유지해요', async () => {
  const h = await modelComponentHarness({ entry: '/src/features/model/SponsorshipSettings.jsx', exportName: 'SponsorshipFields', initialStates: [], api: {} });
  let value = sponsorshipDraft({ ...model, sizeTop: 'FREE' });
  const render = (disabled = false) => h.render({ value, disabled, onChange: next => { value = next; } });
  const group = (tree, title) => {
    const label = findTree(tree, node => textOf(node) === title && node.props?.id);
    assert.ok(label, title);
    return findTree(tree, node => node.props?.role === 'radiogroup' && node.props['aria-labelledby'] === label.props.id);
  };
  const radios = tree => {
    const result = [];
    findTree(tree, node => { if (node.props?.role === 'radio') result.push(node); return false; });
    return result;
  };
  try {
    const top = group(render(), '상의 사이즈');
    assert.ok(top);
    assert.deepEqual(radios(top).map(textOf), ['XS', 'S', 'M', 'L', 'XL']);
    assert.ok(radios(top).every(node => node.props['aria-checked'] === false));
    assert.equal(findTree(render(), node => node.type === 'select'), null);
    for (const size of ['S', 'XL', 'XL']) {
      button(group(render(), '상의 사이즈'), size).props.onClick();
      assert.equal(value.sizeTop, size);
      assert.deepEqual(radios(group(render(), '상의 사이즈')).filter(node => node.props['aria-checked']).map(textOf), [size]);
    }
    const bottomTitle = '하의 사이즈 (허리, 인치)';
    assert.deepEqual(radios(group(render(), bottomTitle)).map(textOf), ['24', '25', '26', '27', '28', '29', '30', '31', '32', '33', '34']);
    for (const size of ['24', '34', '34']) {
      button(group(render(), bottomTitle), size).props.onClick();
      assert.equal(value.sizeBottomWaist, size);
      assert.deepEqual(radios(group(render(), bottomTitle)).filter(node => node.props['aria-checked']).map(textOf), [size]);
    }
    assert.equal(radios(render(true)).length, 16);
    assert.ok(radios(render(true)).every(node => node.props.disabled === true));
    for (const [title, key, from, expected] of [
      ['상의 사이즈', 'ArrowRight', 'XL', 'XS'],
      ['상의 사이즈', 'ArrowLeft', 'XS', 'XL'],
      [bottomTitle, 'ArrowDown', '34', '24'],
      [bottomTitle, 'ArrowUp', '24', '34'],
    ]) {
      const grid = group(render(), title);
      let focused, prevented = false;
      const buttons = radios(grid).map(node => ({ value: node.props.value, focus() { focused = this.value; } }));
      grid.props.onKeyDown({ key, target: buttons.find(node => node.value === from), currentTarget: { querySelectorAll: () => buttons }, preventDefault() { prevented = true; } });
      assert.equal(focused, expected);
      assert.equal(prevented, true);
      assert.deepEqual(radios(group(render(), title)).filter(node => node.props['aria-checked']).map(textOf), [expected]);
      assert.deepEqual(radios(group(render(), title)).filter(node => node.props.tabIndex === 0).map(textOf), [expected]);
    }
  } finally { await h.close(); }
});

test('마이페이지의 기존 FREE 값은 새 상의 사이즈를 고른 뒤에만 저장돼요', async () => {
  const calls = [];
  const h = await settingsHarness({ updateModelSponsorship: async (id, body) => { calls.push(body); return { ...model, ...body }; } });
  const legacy = { ...model, sizeTop: 'FREE' };
  try {
    await button(h.render({ model: legacy }), '협찬 설정 저장').props.onClick();
    assert.deepEqual(calls, []);
    assert.match(textOf(h.render({ model: legacy })), /상의 사이즈를 골라 주세요\./);
    fields(h.render({ model: legacy })).props.onChange({ ...sponsorshipDraft(legacy), sizeTop: 'L' });
    await button(h.render({ model: legacy }), '협찬 설정 저장').props.onClick();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].sizeTop, 'L');
  } finally { await h.close(); }
});

test('마이페이지의 무관한 모델 갱신은 입력 중인 협찬 계정을 덮지 않아요', async () => {
  const h = await settingsHarness({});
  try {
    commit(h, { model });
    fields(h.render({ model })).props.onChange({ ...sponsorshipDraft(model), instagramHandle: 'new.account' });
    commit(h, { model: { ...model, name: '새 표시 이름' } });
    assert.equal(fields(h.render({ model })).props.value.instagramHandle, 'new.account');
  } finally { await h.close(); }
});

test('협찬을 끄면 서버가 지운 대로 입력과 동의가 비워지고, 다시 켜면 새로 적어요', async () => {
  const h = await settingsHarness({ updateModelSponsorship: async () => purged });
  let savedModel = model;
  const props = () => ({ model: savedModel, onModelChange: next => { savedModel = next; } });
  try {
    commit(h, props());
    fields(h.render(props())).props.onChange({ ...sponsorshipDraft(model), instagramHandle: 'new.account' });
    fields(h.render(props())).props.onChange({ ...fields(h.render(props())).props.value, sponsorshipEnabled: false });
    await eventually(() => savedModel.sponsorshipEnabled === false, 'off setting saves immediately');
    commit(h, props());
    assert.match(textOf(h.render(props())), /계정과 사이즈 정보는 지웠어요/);
    fields(h.render(props())).props.onChange({ ...fields(h.render(props())).props.value, sponsorshipEnabled: true });
    assert.equal(fields(h.render(props())).props.value.instagramHandle, '');
    assert.equal(fields(h.render(props())).props.value.profileConsent, false);
  } finally { await h.close(); }
});

test('켤 때 프로필 정보 수집 체크가 없으면 저장하지 않고 안내해요', async () => {
  const calls = [];
  const h = await settingsHarness({ updateModelSponsorship: async (id, body) => { calls.push(body); return { ...model }; } });
  const off = { ...purged };
  try {
    commit(h, { model: off });
    const draft = { ...sponsorshipDraft(off), sponsorshipEnabled: true, instagramHandle: 'daily', instagramFollowers: '1200', sizeTop: 'M', sizeBottomWaist: '28', profileConsent: false };
    fields(h.render({ model: off })).props.onChange(draft);
    assert.equal(fields(h.render({ model: off })).props.value.profileConsent, false);
    await button(h.render({ model: off }), '협찬 설정 저장').props.onClick();
    assert.deepEqual(calls, []);
    assert.match(textOf(h.render({ model: off })), /프로필 정보 수집에 동의해 주세요/);
    fields(h.render({ model: off })).props.onChange({ ...draft, profileConsent: true });
    await button(h.render({ model: off }), '협찬 설정 저장').props.onClick();
    assert.deepEqual(calls, [{ sponsorshipEnabled: true, instagramHandle: 'daily', instagramFollowers: 1200, sizeTop: 'M', sizeBottomWaist: 28, profileConsent: true }]);
  } finally { await h.close(); }
});

for (const nextEnabled of [true, false]) {
test(`기존 협찬을 ${nextEnabled ? '켜 둔' : '끄는'} 설정 저장에 실패하면 증서를 발급하지 않고 재시도해요`, async () => {
  const calls = [];
  let failSave = true;
  const record = { id: 'e1', modelId: 'm1', status: 'license_pending', consentDocumentVersion: CONSENT_VERSION, termsConsentVersion: CONSENT_VERSION };
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    listMyModels: async () => [model],
    getCurrentEnrollment: async () => record,
    updateModelSponsorship: async (_id, payload) => {
      assert.equal(payload.sponsorshipEnabled, nextEnabled);
      calls.push('save'); if (failSave) throw new Error('협찬 설정 저장 실패'); return { ...model, sponsorshipEnabled: nextEnabled };
    },
    createLicense: async () => { calls.push('issue'); return { id: 'l1' }; },
  } });
  try {
    commit(h);
    await eventually(() => { commit(h); return fields(h.render()) && !fields(h.render()).props.disabled; }, 'stored sponsorship loads');
    fields(h.render()).props.onChange({ ...sponsorshipDraft(model), sponsorshipEnabled: nextEnabled });
    findTree(h.render(), node => node.props?.id === 'price-agreed').props.onChange({ target: { checked: true } });
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    assert.deepEqual(calls, ['save']);
    assert.match(textOf(h.render()), /협찬 설정 저장 실패/);
    failSave = false;
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    assert.deepEqual(calls, ['save', 'save', 'issue']);
  } finally { await h.close(); }
});
}

test('등록에서 협찬 조회가 실패하면 발급을 잠그고 다시 불러온 저장값을 보여줘요', async () => {
  let lookups = 0;
  const record = { id: 'e1', modelId: 'm1', status: 'license_pending', consentDocumentVersion: CONSENT_VERSION, termsConsentVersion: CONSENT_VERSION };
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    listMyModels: async () => { if (++lookups === 2) throw new Error('협찬 조회 실패'); return [model]; },
    getCurrentEnrollment: async () => record,
    createLicense: () => assert.fail('설정을 읽기 전에 발급하면 안 돼요'),
  } });
  try {
    commit(h);
    await eventually(() => { commit(h); return textOf(h.render()).includes('협찬 조회 실패'); }, 'load failure appears');
    findTree(h.render(), node => node.props?.id === 'price-agreed').props.onChange({ target: { checked: true } });
    assert.equal(button(h.render(), '라이선스 증서 발급하기').props.disabled, true);
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    button(h.render(), '설정 다시 불러오기').props.onClick();
    commit(h);
    await eventually(() => { commit(h); return !fields(h.render()).props.disabled; }, 'retry loads saved values');
    assert.deepEqual(fields(h.render()).props.value, sponsorshipDraft(model));
    assert.equal(button(h.render(), '라이선스 증서 발급하기').props.disabled, false);
  } finally { await h.close(); }
});

test('기존 협찬이 꺼져 있고 참여하지 않으면 불필요한 저장 없이 증서를 발급해요', async () => {
  let issued = false;
  const record = { id: 'e1', modelId: 'm1', status: 'license_pending', consentDocumentVersion: CONSENT_VERSION, termsConsentVersion: CONSENT_VERSION };
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    listMyModels: async () => [{ ...model, sponsorshipEnabled: false }],
    getCurrentEnrollment: async () => record,
    updateModelSponsorship: () => assert.fail('참여 설정이 그대로 꺼져 있어 저장할 변경이 없어요'),
    createLicense: async () => { issued = true; return { id: 'l1' }; },
  } });
  try {
    await eventually(() => { commit(h); return fields(h.render()) && !fields(h.render()).props.disabled; }, 'stored off setting loads');
    findTree(h.render(), node => node.props?.id === 'price-agreed').props.onChange({ target: { checked: true } });
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    assert.equal(issued, true);
  } finally { await h.close(); }
});

test('마이페이지를 다시 열면 저장된 꺼짐과 비워진 입력으로 시작해요', async () => {
  const h = await settingsHarness({});
  const savedModel = purged;
  try {
    const tree = h.render({ model: savedModel });
    assert.deepEqual(fields(tree).props.value, sponsorshipDraft(savedModel));
  } finally { await h.close(); }
});

test('마이페이지 라이선스 탭은 협찬 저장 결과를 상위 모델 상태에 전달해요', async () => {
  const h = await modelComponentHarness({ entry: '/src/features/model/mypage/MyPageConditions.jsx', exportName: 'MyPageConditions', initialStates: [], api: {} });
  const onModelChange = () => {};
  try {
    const tree = h.render({ model, license: { id: 'l1', status: 'active' }, onModelChange });
    const settings = findTree(tree, node => node.type?.name === 'ModelSponsorshipSettings');
    assert.equal(settings.props.model, model);
    assert.equal(settings.props.onModelChange, onModelChange);
  } finally { await h.close(); }
});

test('협찬 선택 화면은 게시 7일과 유지 90일을 포함한 오너 문구를 보여줘요', async () => {
  const h = await modelComponentHarness({ entry: '/src/features/model/SponsorshipSettings.jsx', exportName: 'SponsorshipFields', initialStates: [], api: {} });
  try {
    const text = textOf(h.render({ value: sponsorshipDraft(), onChange() {} }));
    for (const sentence of ['켜 두면 셀러에게 협찬 요청까지 받을 수 있어요.',
      '옷을 받은 뒤 기본적으로 7일 이내, SNS 피드에 착용컷을 올리면 돼요.',
      '게시물은 90일간만 유지하면 돼요.', '협찬 옷도 위에서 정한 허용 품목 안에서만 와요.']) assert.ok(text.includes(sentence));
  } finally { await h.close(); }
});
