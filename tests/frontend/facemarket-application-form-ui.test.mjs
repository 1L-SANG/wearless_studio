import test from 'node:test';
import assert from 'node:assert/strict';

import { findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';

function findAll(node, predicate, found = []) {
  if (!node || typeof node !== 'object') return found;
  if (Array.isArray(node)) {
    node.forEach((child) => findAll(child, predicate, found));
    return found;
  }
  if (predicate(node)) found.push(node);
  const children = Array.isArray(node.props?.children) ? node.props.children : [node.props?.children];
  children.forEach((child) => findAll(child, predicate, found));
  return found;
}

function textOf(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(' ');
  return textOf(node.props?.children);
}

const completeForm = {
  contactEmail: 'model@example.com', applicantName: '김하나', phone: '010-1234-5678',
  birthdate: '2000-01-01', gender: 'female', heightCm: '170', weightKg: '55',
  experienceLevel: 'beginner', agencyContracted: false, portfolioUrl: '', snsUrl: '',
};

const allAttestations = {
  adultAndTruthful: true,
  photosAreMine: true,
  noAgencyContract: true,
  reviewOnlyUse: true,
  privacyPolicy: true,
};
const PROFILE_STAGE_ID = 'a'.repeat(64);

test('새 지원서의 생년월일은 예시 숫자나 브라우저 자동완성 없이 빈칸으로 시작한다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'BirthdateInput',
    initialStates: [],
    api: {},
  });
  try {
    const tree = harness.render({ value: '', onChange: () => {} });
    const inputs = findAll(tree, (node) => node.type === 'input');
    assert.equal(inputs.length, 3);
    for (const input of inputs) {
      assert.equal(input.props.value, '');
      assert.equal(input.props.autoComplete, 'off');
      assert.equal((input.props.placeholder || '').trim(), '');
    }
  } finally { await harness.close(); }
});

test('지원 확인의 전체 동의는 다섯 필수 항목을 한 번에 선택하고 해제한다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: ['ready', completeForm, { staged: true, stageId: PROFILE_STAGE_ID, previewUrl: 'blob:profile' }, {
      ...allAttestations,
      privacyPolicy: false,
    }, 3, null, false, ''],
    api: {},
  });
  try {
    let tree = harness.render();
    let all = findTree(tree, (node) => node.props?.id === 'attest-all');
    assert.ok(all);
    assert.equal(all.props.checked, false);
    assert.equal(all.props['aria-checked'], 'mixed');

    all.props.onChange({ target: { checked: true } });
    assert.deepEqual(harness.runtime.states[3], allAttestations);
    tree = harness.render();
    all = findTree(tree, (node) => node.props?.id === 'attest-all');
    assert.equal(all.props.checked, true);
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '지원 완료').props.disabled, false);

    all.props.onChange({ target: { checked: false } });
    assert.deepEqual(Object.values(harness.runtime.states[3]), [false, false, false, false, false]);
  } finally { await harness.close(); }
});

test('키와 몸무게 입력은 현재 숫자 범위와 선택 여부를 바로 알려 준다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: ['ready', { ...completeForm, heightCm: '99', weightKg: '201' }, { staged: true, stageId: PROFILE_STAGE_ID }, {}, 2, null, false, ''],
    api: {},
  });
  try {
    let tree = harness.render();
    let height = findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.label === '키');
    let weight = findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.label === '몸무게');
    height.props.onBlur();
    weight.props.onBlur();
    tree = harness.render();
    height = findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.label === '키');
    weight = findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.label === '몸무게');
    assert.match(height.props.error, /100~250cm/);
    assert.match(weight.props.error, /30~200kg/);
  } finally { await harness.close(); }
});

test('지원서 텍스트 입력은 공통 제목 입력을 쓴다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: ['ready', completeForm, null, {}, 1, null, false, ''],
    api: {},
  });
  try {
    let tree = harness.render();
    assert.deepEqual(
      findAll(tree, (node) => node.type?.name === 'FormInput').map((node) => node.props.label),
      ['이름', '전화번호', '이메일'],
    );
    findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.onClick();
    tree = harness.render();
    assert.deepEqual(
      findAll(tree, (node) => node.type?.name === 'FormInput').map((node) => node.props.label),
      ['키', '몸무게', '포트폴리오 링크', 'SNS 링크'],
    );
  } finally { await harness.close(); }
});

test('공통 이미지 입력은 클릭과 끌어놓기를 받고 미리보기에서 교체·삭제할 수 있다', async () => {
  const selected = [];
  const rejected = [];
  let removed = 0;
  const harness = await modelComponentHarness({
    entry: '/src/components/ui/ImageUpload.jsx',
    exportName: 'ImageUpload',
    initialStates: [],
    api: {},
  });
  try {
    const props = {
      previewUrl: null,
      fileName: null,
      accept: 'image/png,image/jpeg,image/webp',
      onSelect: (file) => selected.push(file),
      onReject: (file) => rejected.push(file),
      onRemove: () => { removed += 1; },
    };
    let tree = harness.render(props);
    const input = findTree(tree, (node) => node.type === 'input' && node.props.type === 'file');
    const first = { name: 'first.jpg', type: 'image/jpeg' };
    input.props.onChange({ target: { files: [first], value: 'first.jpg' } });
    assert.equal(selected[0], first);

    const dropZone = findTree(tree, (node) => node.props?.['data-image-dropzone'] === true);
    const second = { name: 'second.webp', type: 'image/webp' };
    dropZone.props.onDrop({ preventDefault() {}, stopPropagation() {}, dataTransfer: { files: [second] } });
    assert.equal(selected[1], second);

    const extensionOnly = { name: 'third.webp', type: '' };
    input.props.onChange({ target: { files: [extensionOnly], value: 'third.webp' } });
    assert.equal(selected[2], extensionOnly);

    const textFile = { name: 'notes.txt', type: 'text/plain' };
    dropZone.props.onDrop({ preventDefault() {}, stopPropagation() {}, dataTransfer: { files: [textFile] } });
    assert.equal(selected.length, 3);
    assert.equal(rejected[0], textFile);

    tree = harness.render({ ...props, previewUrl: 'blob:second', fileName: second.name });
    assert.ok(findTree(tree, (node) => node.type === 'img' && node.props.src === 'blob:second'));
    assert.ok(textOf(tree).includes('second.webp'));
    findTree(tree, (node) => node.type === 'button' && node.props['aria-label'] === '사진 삭제').props.onClick();
    assert.equal(removed, 1);
    assert.ok(findTree(tree, (node) => node.type === 'button' && node.props['aria-label'] === '다른 사진 선택'));
  } finally { await harness.close(); }
});

test('지원 사진을 바꾸거나 지우면 더는 쓰지 않는 미리보기 주소를 회수한다', async () => {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const revoked = [];
  let sequence = 0;
  URL.createObjectURL = () => `blob:application-${++sequence}`;
  URL.revokeObjectURL = (url) => revoked.push(url);
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: ['ready', completeForm, null, {}, 2, null, false, ''],
    api: { stageApplicationPhoto: async () => ({ staged: true, stageId: PROFILE_STAGE_ID }), deleteStagedApplicationPhoto: async () => null },
  });
  try {
    let tree = harness.render();
    await findTree(tree, (node) => node.type?.name === 'ImageUpload').props.onSelect({ name: 'first.jpg', type: 'image/jpeg' });
    tree = harness.render();
    assert.equal(findTree(tree, (node) => node.type?.name === 'ImageUpload').props.previewUrl, 'blob:application-1');
    assert.equal(harness.runtime.states[2].stageId, PROFILE_STAGE_ID);

    await findTree(tree, (node) => node.type?.name === 'ImageUpload').props.onSelect({ name: 'second.jpg', type: 'image/jpeg' });
    tree = harness.render();
    assert.deepEqual(revoked, ['blob:application-1']);
    assert.equal(findTree(tree, (node) => node.type?.name === 'ImageUpload').props.previewUrl, 'blob:application-2');

    await findTree(tree, (node) => node.type?.name === 'ImageUpload').props.onRemove();
    assert.deepEqual(revoked, ['blob:application-1', 'blob:application-2']);
    assert.equal(harness.runtime.states[2], null);
  } finally {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    await harness.close();
  }
});

test('지원 사진 삭제는 서버 임시 사진 삭제가 끝난 뒤에만 화면을 비운다', async () => {
  let finishDelete;
  const calls = [];
  const pendingDelete = new Promise((resolve) => { finishDelete = resolve; });
  const originalPhoto = { staged: true, stageId: PROFILE_STAGE_ID, previewUrl: 'blob:profile', fileName: 'profile.jpg' };
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: ['ready', completeForm, originalPhoto, {}, 2, null, false, ''],
    api: { deleteStagedApplicationPhoto: async (...args) => { calls.push(args); await pendingDelete; } },
  });
  try {
    const remove = findTree(harness.render(), (node) => node.type?.name === 'ImageUpload').props.onRemove;
    const deleting = remove();
    assert.deepEqual(harness.runtime.states[2], { ...originalPhoto, staged: false });
    assert.deepEqual(calls, [['profile', PROFILE_STAGE_ID]]);
    finishDelete();
    await deleting;
    assert.equal(harness.runtime.states[2], null);
  } finally { await harness.close(); }
});

test('서버 임시 사진 삭제 결과를 모르면 미리보기는 유지하되 제출을 잠근다', async () => {
  const calls = [];
  const originalPhoto = { staged: true, stageId: PROFILE_STAGE_ID, previewUrl: 'blob:profile', fileName: 'profile.jpg' };
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: ['ready', completeForm, originalPhoto, {}, 2, null, false, ''],
    api: { deleteStagedApplicationPhoto: async (...args) => { calls.push(args); throw new Error('Failed to fetch'); } },
  });
  try {
    await findTree(harness.render(), (node) => node.type?.name === 'ImageUpload').props.onRemove();
    assert.deepEqual(calls, [['profile', PROFILE_STAGE_ID]]);
    assert.deepEqual(harness.runtime.states[2], { ...originalPhoto, staged: false });
    const tree = harness.render();
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.disabled, true);
    assert.match(harness.runtime.states[7], /다시 올려/);
  } finally { await harness.close(); }
});

test('제출 직전 다른 탭이 사진을 바꾸면 프로필 단계로 돌아가 재업로드를 요구한다', async () => {
  const photo = { staged: true, stageId: PROFILE_STAGE_ID, previewUrl: 'blob:profile', fileName: 'profile.jpg' };
  const error = Object.assign(new Error('프로필 사진이 다른 탭에서 바뀌었어요. 사진을 다시 올려 주세요.'), {
    status: 409,
    code: 'profile_photo_changed',
  });
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: ['ready', completeForm, photo, allAttestations, 3, null, false, ''],
    api: { submitApplication: async () => { throw error; } },
  });
  try {
    await findTree(harness.render(), (node) => node.type === 'button' && node.props.children === '지원 완료').props.onClick();
    assert.equal(harness.runtime.states[4], 2);
    assert.equal(harness.runtime.states[2], null);
    const tree = harness.render();
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.disabled, true);
    assert.match(harness.runtime.states[7], /다시 올려/);
  } finally { await harness.close(); }
});

async function applyHistoryHarness(initialStates, api = {}) {
  const navigations = [];
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates,
    api: { getCurrentApplication: () => new Promise(() => {}), ...api },
  });
  harness.runtime.navigate = (...args) => navigations.push(args);
  // 브라우저 기록 항목 하나에 도착한 것처럼 그리고 effect를 돌린다.
  const visit = (location) => {
    harness.runtime.location = { pathname: '/model/apply', search: '', ...location };
    const tree = harness.render();
    harness.runtime.effects.forEach((effect) => effect());
    return tree;
  };
  return { harness, navigations, visit };
}

const reviewPhoto = { staged: true, stageId: PROFILE_STAGE_ID, previewUrl: 'blob:profile', fileName: 'profile.jpg' };

test('지원 완료 뒤 뒤로가기로 옛 단계 기록에 오면 지원 상태 화면으로 보낸다', async () => {
  const { harness, navigations, visit } = await applyHistoryHarness(
    ['ready', completeForm, reviewPhoto, allAttestations, 3, null, false, ''],
    { submitApplication: async () => ({ status: 'under_review' }) },
  );
  try {
    const review = visit({ state: { applyStep: 3, applyEditing: false }, key: 'k3' });
    await findTree(review, (node) => node.type === 'button' && node.props.children === '지원 완료').props.onClick();
    assert.equal(harness.runtime.states[0], 'complete');
    // 라우터가 기록 변경을 늦게 반영해 완료 화면이 떠난 항목으로 먼저 그려져도 상태 화면으로 보내지 않는다.
    visit({ state: { applyStep: 3, applyEditing: false }, key: 'k3' });
    visit({ state: { applyComplete: true }, key: 'kc' });
    assert.deepEqual(navigations, [['/model/apply', { replace: true, state: { applyComplete: true } }]]);
    visit({ state: { applyStep: 2, applyEditing: false }, key: 'k2' });
    assert.deepEqual(navigations.at(-1), ['/status', { replace: true }]);
  } finally { await harness.close(); }
});

test('갈 수 없는 단계 기록에 오면 그 항목을 낮춘 단계로 바꿔 적고 이전은 제자리로 튕기지 않는다', async () => {
  const { harness, navigations, visit } = await applyHistoryHarness(
    ['ready', completeForm, null, allAttestations, 3, null, false, ''],
  );
  try {
    // 사진이 없어 확인 단계로 갈 수 없으니 프로필 단계를 보여 주고 기록 항목도 바꿔 적는다.
    visit({ state: { applyStep: 3, applyEditing: false }, key: 'k3' });
    assert.equal(harness.runtime.states[4], 2);
    assert.equal(harness.runtime.states[5], null);
    assert.deepEqual(navigations, [['/model/apply', { replace: true, state: { applyStep: 2, applyEditing: false, applyLowered: true } }]]);
    const lowered = visit({ state: { applyStep: 2, applyEditing: false, applyLowered: true }, key: 'k2l' });
    assert.equal(navigations.length, 1);
    findTree(lowered, (node) => node.type === 'button' && node.props.children === '이전').props.onClick();
    assert.equal(harness.runtime.states[4], 1);
    assert.deepEqual(navigations.at(-1), ['/model/apply', { replace: true, state: { applyStep: 1, applyEditing: false } }]);
  } finally { await harness.close(); }
});

test('고치던 칸을 비우고 이전을 누르면 확인 단계를 그리지 않고 그 단계로 낮춘다', async () => {
  const { harness, navigations, visit } = await applyHistoryHarness(
    ['ready', completeForm, reviewPhoto, allAttestations, 3, null, false, ''],
  );
  try {
    const review = visit({ state: { applyStep: 3, applyEditing: false }, key: 'k3' });
    findTree(review, (node) => node.type === 'button' && node.props['aria-label'] === '기본 정보 고치기').props.onClick();
    assert.deepEqual(navigations.at(-1), ['/model/apply', { replace: false, state: { applyStep: 1, applyEditing: true } }]);
    let tree = visit({ state: { applyStep: 1, applyEditing: true }, key: 'k1e' });
    assert.equal(harness.runtime.states[5], 1);
    findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.label === '이름').props.onChange({ target: { value: '' } });
    tree = visit({ state: { applyStep: 1, applyEditing: true }, key: 'k1e' });
    findTree(tree, (node) => node.type === 'button' && node.props.children === '이전').props.onClick();
    assert.deepEqual(navigations.at(-1), [-1]);
    assert.equal(harness.runtime.states[4], 1);
    visit({ state: { applyStep: 3, applyEditing: false }, key: 'k3' });
    assert.equal(harness.runtime.states[4], 1);
    assert.equal(harness.runtime.states[5], null);
    assert.deepEqual(navigations.at(-1), ['/model/apply', { replace: true, state: { applyStep: 1, applyEditing: false, applyLowered: true } }]);
  } finally { await harness.close(); }
});

function draftStorage(t, draft) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  const memory = new Map(draft ? [['wl_fmApplyDraft:u1', JSON.stringify(draft)]] : []);
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: key => memory.get(key) ?? null,
    setItem: (key, value) => memory.set(key, value),
    removeItem: key => memory.delete(key),
  } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'sessionStorage', previous);
    else delete globalThis.sessionStorage;
  });
  return memory;
}

for (const previous of [null, { status: 'rejected', applicantName: '예전 이름', heightCm: 180 }]) {
  test(`지원 초안은 ${previous ? '재지원 기본값' : '빈 지원서'} 위에 허용된 입력만 복원해요`, async t => {
    const draft = { ...completeForm, applicantName: '복원한 이름', heightCm: 172, weightKg: null,
      portfolioUrl: {}, snsUrl: [], stageId: 'do-not-restore', step: 3, attestations: allAttestations };
    const memory = draftStorage(t, draft);
    const h = await modelComponentHarness({ entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
      initialStates: ['loading'], honorHookDependencies: true,
      api: { getCurrentApplication: async () => {
        if (previous) return previous;
        throw Object.assign(new Error('not found'), { status: 404 });
      } } });
    try {
      h.runtime.session = { user: { id: 'u1', email: 'account@example.com' } };
      h.runtime.push = () => assert.fail('초안 복원 알림은 표시하지 않아요');
      const navigations = [];
      h.runtime.navigate = (...args) => navigations.push(args);
      h.runtime.location = { pathname: '/model/apply', key: 'reload', state: { applyStep: 2 } };
      h.render();
      h.runtime.effects.forEach(effect => effect());
      assert.equal(memory.get('wl_fmApplyDraft:u1'), JSON.stringify(draft), '로딩 중에는 초안을 덮어쓰지 않아요');
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(h.runtime.states[0], 'ready');
      assert.deepEqual(h.runtime.states[1], { ...completeForm, applicantName: '복원한 이름', heightCm: 172, weightKg: null });
      assert.equal(h.runtime.states[2], null);
      assert.deepEqual(Object.values(h.runtime.states[3]), [false, false, false, false, false]);
      h.render(); h.runtime.effects.forEach(effect => effect());
      assert.equal(h.runtime.states[4], 2);
      assert.deepEqual(navigations, []);
      assert.deepEqual(JSON.parse(memory.get('wl_fmApplyDraft:u1')), h.runtime.states[1]);
      const name = findTree(h.render(), node => node.type?.name === 'FormInput' && node.props.label === '키');
      name.props.onChange({ target: { value: '175' } });
      h.render(); h.runtime.effects.forEach(effect => effect());
      assert.equal(JSON.parse(memory.get('wl_fmApplyDraft:u1')).heightCm, '175');
    } finally { await h.close(); }
  });
}

for (const status of ['under_review', 'approved']) test(`${status} 지원서는 초안을 지우고 상태 화면으로 이동해요`, async t => {
  const memory = draftStorage(t, completeForm);
  const { harness, navigations, visit } = await applyHistoryHarness(['loading'], { getCurrentApplication: async () => ({ status }) });
  try {
    harness.runtime.session = { user: { id: 'u1', email: 'model@example.com' } };
    visit({ key: 'load' });
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(navigations, [['/status', { replace: true }]]);
    assert.equal(memory.has('wl_fmApplyDraft:u1'), false);
  } finally { await harness.close(); }
});

test('지원 성공은 화면을 떠났어도 초안을 지워요', async t => {
  const memory = draftStorage(t, completeForm);
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const h = await modelComponentHarness({ entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['ready', completeForm, reviewPhoto, allAttestations, 3],
    api: { submitApplication: () => pending, getCurrentApplication: () => new Promise(() => {}) } });
  try {
    h.runtime.session = { user: { id: 'u1' } };
    const tree = h.render();
    const cleanup = h.runtime.effects[0]();
    const submitting = findTree(tree, node => node.type === 'button' && node.props.children === '지원 완료').props.onClick();
    cleanup(); finish({ status: 'under_review' }); await submitting;
    assert.equal(memory.has('wl_fmApplyDraft:u1'), false);
  } finally { await h.close(); }
});

test('사진 안내와 확인 문구는 오너 문구와 실제 필수 공백을 표시해요', async () => {
  const h = await modelComponentHarness({ entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['ready', completeForm, reviewPhoto, allAttestations, 2], api: {} });
  const exactText = node => node == null || typeof node === 'boolean' ? '' : Array.isArray(node) ? node.map(exactText).join('')
    : typeof node === 'object' ? exactText(node.props?.children) : String(node);
  try {
    let tree = h.render();
    for (const text of ['얼굴이 잘 보이게 정면에서 가까이 찍은 사진을 올려 주세요.',
      '보정이나 필터 없이 찍은 원본 사진이어야 해요. 안경과 모자는 벗어 주세요.',
      '지원서 확인용으로만 쓰고, 공개 프로필이나 AI 학습에는 쓰지 않아요.']) assert.ok(exactText(tree).includes(text));
    assert.equal(findTree(tree, node => node.type?.name === 'ImageUpload').props.alt, '내 이미지');
    h.runtime.states[4] = 3; tree = h.render();
    assert.equal(findTree(tree, node => node.type === 'img').props.alt, '내 이미지');
    assert.ok(exactText(tree).includes('(필수) 현재 어떤 모델 에이전시와도 계약하거나 소속되어 있지 않음을 확인합니다.'));
    assert.ok(exactText(tree).includes('기존 계약이 있으면 FaceMarket 모델 리스트에서 제외될 수 있습니다.'));
    assert.ok(exactText(tree).includes('(필수) 제출한 내용이 FaceMarket 등록 심사에만 쓰이고, 다른 목적으로는 쓰이지 않는다는 점을 확인합니다.'));
    assert.ok(exactText(tree).includes('(필수) 개인정보 처리 약관에 대해 동의합니다.'));
  } finally { await h.close(); }
});

test('허용된 초안 원시값이 텍스트 칸에 남아 있어도 화면이 중단되지 않아요', async t => {
  const memory = draftStorage(t, { contactEmail: null, applicantName: false, phone: 1012345678,
    birthdate: null, heightCm: 170, weightKg: null, portfolioUrl: null, snsUrl: true });
  const h = await modelComponentHarness({ entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['loading'], api: {} });
  try {
    h.runtime.session = { user: { id: 'u1', email: 'model@example.com' } };
    h.render(); h.runtime.effects.forEach(effect => effect());
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.runtime.states[1].contactEmail, null);
    const tree = h.render();
    assert.equal(findTree(tree, node => node.type === 'button' && node.props.children === '다음').props.disabled, true);
    const birthday = findTree(tree, node => node.type?.name === 'BirthdateInput');
    assert.doesNotThrow(() => birthday.type(birthday.props));
    assert.equal(memory.has('wl_fmApplyDraft:u1'), true);
  } finally { await h.close(); }
});

test('계정이 바뀌어 새 지원서를 읽는 동안 이전 계정 입력을 새 초안에 저장하지 않아요', async t => {
  const memory = draftStorage(t, completeForm);
  memory.set('wl_fmApplyDraft:u2', JSON.stringify({ applicantName: '다른 계정' }));
  let resolveSecond;
  let calls = 0;
  const h = await modelComponentHarness({ entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['loading'], honorHookDependencies: true, api: { getCurrentApplication: () => {
      calls += 1;
      return calls === 1 ? Promise.resolve(null) : new Promise(resolve => { resolveSecond = resolve; });
    } } });
  try {
    h.runtime.push = () => {};
    h.runtime.session = { user: { id: 'u1', email: 'first@example.com' } };
    h.render(); h.runtime.effects.forEach(effect => effect());
    await new Promise(resolve => setImmediate(resolve));
    h.render(); h.runtime.effects.forEach(effect => effect());
    h.runtime.session = { user: { id: 'u2', email: 'second@example.com' } };
    h.render(); h.runtime.effects.forEach(effect => effect());
    assert.deepEqual(JSON.parse(memory.get('wl_fmApplyDraft:u2')), { applicantName: '다른 계정' });
    resolveSecond(null); await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.runtime.states[1].applicantName, '다른 계정');
    assert.equal(h.runtime.states[1].contactEmail, 'second@example.com');
    assert.equal(JSON.parse(memory.get('wl_fmApplyDraft:u1')).applicantName, '김하나');
  } finally { await h.close(); }
});

test('초안의 무한 숫자는 저장된 지원서 기본값을 덮지 않아요', async t => {
  const memory = draftStorage(t);
  memory.set('wl_fmApplyDraft:u1', '{"heightCm":1e999,"weightKg":-1e999}');
  const h = await modelComponentHarness({ entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['loading'], api: { getCurrentApplication: async () => ({ status: 'rejected', heightCm: 170, weightKg: 55 }) } });
  try {
    h.runtime.session = { user: { id: 'u1' } };
    h.render(); h.runtime.effects.forEach(effect => effect());
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.runtime.states[1].heightCm, 170);
    assert.equal(h.runtime.states[1].weightKg, 55);
  } finally { await h.close(); }
});
