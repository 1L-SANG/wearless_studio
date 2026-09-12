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
    initialStates: ['ready', completeForm, { staged: true, previewUrl: 'blob:profile' }, {
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
    initialStates: ['ready', { ...completeForm, heightCm: '99', weightKg: '201' }, { staged: true }, {}, 2, null, false, ''],
    api: {},
  });
  try {
    const tree = harness.render();
    const height = findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.label === '키');
    const weight = findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.label === '몸무게');
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
    api: { stageApplicationPhoto: async () => ({ staged: true }), deleteStagedApplicationPhoto: async () => null },
  });
  try {
    let tree = harness.render();
    await findTree(tree, (node) => node.type?.name === 'ImageUpload').props.onSelect({ name: 'first.jpg', type: 'image/jpeg' });
    tree = harness.render();
    assert.equal(findTree(tree, (node) => node.type?.name === 'ImageUpload').props.previewUrl, 'blob:application-1');

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
  const originalPhoto = { staged: true, previewUrl: 'blob:profile', fileName: 'profile.jpg' };
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: ['ready', completeForm, originalPhoto, {}, 2, null, false, ''],
    api: { deleteStagedApplicationPhoto: async (kind) => { calls.push(kind); await pendingDelete; } },
  });
  try {
    const remove = findTree(harness.render(), (node) => node.type?.name === 'ImageUpload').props.onRemove;
    const deleting = remove();
    assert.deepEqual(harness.runtime.states[2], originalPhoto);
    assert.deepEqual(calls, ['profile']);
    finishDelete();
    await deleting;
    assert.equal(harness.runtime.states[2], null);
  } finally { await harness.close(); }
});

test('서버 임시 사진 삭제가 실패하면 미리보기와 다음 재시도 기회를 유지한다', async () => {
  const originalPhoto = { staged: true, previewUrl: 'blob:profile', fileName: 'profile.jpg' };
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: ['ready', completeForm, originalPhoto, {}, 2, null, false, ''],
    api: { deleteStagedApplicationPhoto: async () => { throw new Error('임시 사진을 지우지 못했어요.'); } },
  });
  try {
    await findTree(harness.render(), (node) => node.type?.name === 'ImageUpload').props.onRemove();
    assert.deepEqual(harness.runtime.states[2], originalPhoto);
    assert.match(harness.runtime.states[7], /지우지 못했어요/);
  } finally { await harness.close(); }
});
