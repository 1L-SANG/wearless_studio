import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { findTree, modelComponentHarness, eventually } from './helpers/facemarketHarness.mjs';
import { SLOTS, CONSENT_VERSION, PHOTO_REVIEW_SUB, restoreRegisterScreen } from '../../src/features/model/registerSlots.js';
const text = node => Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text(node.props?.children) : String(node ?? '');
const enrollment = { id: 'e1', modelId: 'm1', status: 'photos_pending', photos: [], consentDocumentVersion: CONSENT_VERSION, termsConsentVersion: CONSENT_VERSION };

test('R1 consent details reorder unchanged paragraphs inside a focusable measured region', async () => {
  const h = await modelComponentHarness({ initialStates: ['1'], api: {} });
  try {
    const tree = h.render();
    const details = findTree(tree, n => n.type?.name === 'ConsentDetails');
    assert.ok(details);
    const paragraphs = details.props.children;
    assert.deepEqual(paragraphs.map(n => text(n).split(':')[0]), ['목적', '학습', '수집', '보유', '거부', '확인', '사용 시점']);
    assert.equal(text(paragraphs[1]), '학습: 18장 중 12장으로 FaceMarket이 직접 학습해요. 학습을 외부 AI 회사에 맡기지 않아요.');
    assert.equal(findTree(details, n => n.props.to === '/biometric-consent'), null);
    assert.ok(findTree(tree, n => n.props.to === '/biometric-consent'));
  } finally { await h.close(); }
  const source = readFileSync('src/features/model/RegisterScreens.jsx', 'utf8');
  assert.match(source, /tabIndex=\{0\}[^]*aria-label="얼굴 정보 처리 세부 내용"/);
  assert.match(source, /setProperty\('--consent-fit'/);
});

test('P1 P3 P4 P5 photos use first-page checklist, upload cards and no slot hints', async () => {
  const h = await modelComponentHarness({ initialStates: ['2', enrollment, 1], api: {} });
  try {
    let tree = h.render();
    assert.match(text(tree), /온라인 모델 생성을 위해 필요한 이미지들을 업로드해요/);
    assert.ok(findTree(tree, n => n.type?.name === 'ShootChecklist'));
    assert.equal(findTree(tree, n => n.props.className === 'photoStages'), null);
    assert.doesNotMatch(text(tree), /촬영 준비 보기|사진 찍기·선택/);
    assert.match(text(tree), /예시처럼 사진 올리기/);
    for (const slot of SLOTS) assert.ok(!text(tree).includes(slot.hint));
    h.runtime.states[2] = 2;
    tree = h.render();
    assert.equal(findTree(tree, n => n.type?.name === 'ShootChecklist'), null);
    assert.ok(findTree(tree, n => n.props.to === '/photo-guide'));
  } finally { await h.close(); }
});

for (const failure of [Object.assign(new Error('얼굴이 작아요. 한 걸음 다가가서 다시 찍어 주세요.'), { status: 422 }), new TypeError('Failed to fetch')]) {
  test(`P2 upload failure stays in its card: ${failure.message}`, async () => {
    let reject;
    const h = await modelComponentHarness({ initialStates: ['2', enrollment, 1], api: { uploadEnrollmentPhoto: () => new Promise((_, r) => { reject = r; }) } });
    try {
      const input = findTree(h.render(), n => n.props.id === 'photo-input-sh_front');
      input.props.onChange({ target: { files: [new Blob(['photo'])], value: 'file' } });
      await eventually(() => !!reject, 'upload starts');
      assert.ok(findTree(h.render(), n => n.props.role === 'status' && text(n) === '올리는 중이에요'));
      reject(failure);
      await eventually(() => !h.runtime.states[4], 'upload ends');
      const tree = h.render();
      const alert = findTree(tree, n => n.props.id === 'photo-error-sh_front');
      assert.equal(alert.props.role, 'alert');
      assert.equal(text(alert), failure instanceof TypeError ? '인터넷 연결이 불안정해 사진을 올리지 못했어요. 다시 시도해 주세요.' : failure.message);
      assert.equal(findTree(tree, n => n.props.className === 'error'), null);
      let scrolled = false;
      h.runtime.refs[4].current = { scrollIntoView: () => { scrolled = true; } };
      h.runtime.effects[5]();
      assert.equal(scrolled, false);
    } finally { await h.close(); }
  });
}

test('P2 picker reload restores its position only on matching step and sub within five minutes', async () => {
  const oldStorage = globalThis.sessionStorage, oldWindow = globalThis.window;
  const data = new Map(), scrolls = [];
  globalThis.sessionStorage = { getItem: key => data.get(key) || null, setItem: (key, val) => data.set(key, val), removeItem: key => data.delete(key) };
  globalThis.window = { scrollY: 920, scrollTo: options => scrolls.push(options) };
  let h;
  try {
    h = await modelComponentHarness({ initialStates: ['2', enrollment, 1], api: {} });
    findTree(h.render(), n => n.props.id === 'photo-input-sh_front').props.onClick();
    const key = 'fm.registration.photoPos.e1';
    assert.equal(JSON.parse(data.get(key)).scrollY, 920);
    await h.close();
    h = await modelComponentHarness({ initialStates: ['2', enrollment, 1], api: {} });
    h.render(); h.runtime.effects[2]();
    assert.equal(scrolls.at(-1).top, 920);
    assert.equal(data.has(key), false);
    assert.match(text(h.render()), /사진을 받지 못했어요. 다시 골라 주세요./);
    for (const value of [{ sub: 2, at: Date.now() }, { sub: 1, at: Date.now() - 300001 }]) {
      data.set(key, JSON.stringify({ slot: 'sh_front', scrollY: 920, ...value }));
      await h.close();
      h = await modelComponentHarness({ initialStates: ['2', enrollment, 1], api: {} });
      h.render(); h.runtime.effects[2]();
      assert.equal(scrolls.at(-1).top, 0);
      assert.equal(data.has(key), false);
    }
  } finally { await h?.close(); globalThis.sessionStorage = oldStorage; globalThis.window = oldWindow; }
});

test('flow restoration goes straight to conditions and hides pending certificates', () => {
  for (const status of ['processing', 'asset_building']) assert.equal(restoreRegisterScreen({ status }).step, '3');
  assert.equal(restoreRegisterScreen({ status: 'review_pending' }).step, 'processing');
  assert.equal(restoreRegisterScreen({ status: 'vc_pending' }).step, 'done');
});

test('C3 conditions remain editable while assets build and only issuance is disabled', async () => {
  const h = await modelComponentHarness({ initialStates: ['3', { ...enrollment, status: 'asset_building' }], api: {} });
  try {
    const tree = h.render();
    assert.doesNotMatch(text(tree), /몸의 두께/);
    assert.match(text(tree), /예시: 상의, 하의, 아우터, 원피스/);
    assert.match(text(tree), /모델 몫 70%/);
    assert.equal(findTree(tree, n => n.props.role === 'switch').props.disabled, false);
    assert.equal(findTree(tree, n => n.type === 'button' && text(n) === '라이선스 증서 발급하기').props.disabled, true);
    assert.match(text(tree), /사진을 정리하고 있어요. 잠시 뒤 발급할 수 있어요./);
  } finally { await h.close(); }
});

test('C4 completion uses the three owner sentences and has no certificate link or number', async () => {
  const h = await modelComponentHarness({ initialStates: ['done', enrollment], api: {} });
  try {
    const tree = h.render();
    for (const copy of ['발급한 라이선스 증서는 모델님이 철회하기 전까지 유효해요.', '모델님의 얼굴을 활용한 테스트컷은 2일 이내 보내드릴게요.', '모델님이 테스트컷을 보고 최종 확정해주시면 라이선스 증서 발급과 함께 등록이 최종 완료됩니다.']) assert.ok(text(tree).includes(copy));
    assert.doesNotMatch(text(tree), /증서 번호|증서 보기/);
    assert.ok(findTree(tree, n => n.props.to === '/status'));
  } finally { await h.close(); }
});

const issueButton = tree => findTree(tree, n => n.type === 'button' && text(n) === '라이선스 증서 발급하기');
function readyConditions(h) {
  h.render(); h.runtime.states[12] = true; h.runtime.states[17] = 'm1';
}
for (const status of ['pending', 'active']) {
  test(`flow accepts a single ${status} license response as completion`, async () => {
    let calls = 0;
    const h = await modelComponentHarness({ initialStates: ['3', { ...enrollment, status: 'license_pending' }], api: {
      createLicense: async () => { calls++; return { id: 'l1', status }; },
    } });
    try {
      readyConditions(h);
      await issueButton(h.render()).props.onClick();
      assert.equal(calls, 1); assert.equal(h.runtime.states[0], 'done');
      assert.doesNotMatch(text(h.render()), /증서 번호|증서 보기/);
    } finally { await h.close(); }
  });
}
for (const failure of [{ code: 'vc_issue_delayed' }, { status: 503 }, { name: 'AbortError' }]) {
  test(`flow reconciles ${JSON.stringify(failure)} once without retrying issuance`, async () => {
    let issued = 0, lookedUp = 0;
    const h = await modelComponentHarness({ initialStates: ['3', { ...enrollment, status: 'license_pending' }], api: {
      createLicense: async () => { issued++; throw Object.assign(new Error('늦어지고 있어요'), failure); },
      getEnrollment: async () => { lookedUp++; return { ...enrollment, status: 'vc_pending' }; },
    } });
    try {
      readyConditions(h);
      await issueButton(h.render()).props.onClick();
      assert.equal(issued, 1); assert.equal(lookedUp, 1); assert.equal(h.runtime.states[0], 'done');
    } finally { await h.close(); }
  });
}

test('flow keeps the selected conditions after an unconfirmed delayed response', async () => {
  const h = await modelComponentHarness({ initialStates: ['3', { ...enrollment, status: 'license_pending' }], api: {
    createLicense: async () => { throw Object.assign(new Error('잠시 뒤 다시 시도해 주세요.'), { code: 'vc_issue_delayed' }); },
    getEnrollment: async () => ({ ...enrollment, status: 'license_pending' }),
  } });
  try {
    readyConditions(h); h.runtime.states[6] = { allowedUse: ['액티브웨어'] };
    await issueButton(h.render()).props.onClick();
    assert.equal(h.runtime.states[0], '3');
    assert.deepEqual(h.runtime.states[6], { allowedUse: ['액티브웨어'] });
    assert.equal(issueButton(h.render()).props.disabled, false);
    assert.match(text(h.render()), /잠시 뒤 다시 시도해 주세요./);
  } finally { await h.close(); }
});

test('flow asset polling updates readiness without resetting conditions or moving the step', async () => {
  const h = await modelComponentHarness({ initialStates: ['3', { ...enrollment, status: 'asset_building' }], api: {
    getEnrollment: async () => ({ ...enrollment, status: 'license_pending' }),
  } });
  let cleanup;
  try {
    readyConditions(h); h.runtime.states[6] = { allowedUse: ['액티브웨어'] };
    h.render(); cleanup = h.runtime.effects[4]();
    await eventually(() => h.runtime.states[1].status === 'license_pending', 'asset preparation ends');
    assert.equal(h.runtime.states[0], '3');
    assert.deepEqual(h.runtime.states[6], { allowedUse: ['액티브웨어'] });
    assert.equal(issueButton(h.render()).props.disabled, false);
  } finally { cleanup?.(); await h.close(); }
});

test('P2 picker completion and cancellation clear recovery markers', async () => {
  const old = globalThis.sessionStorage;
  const entries = new Map();
  globalThis.sessionStorage = { getItem: key => entries.get(key), setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) };
  const h = await modelComponentHarness({ initialStates: ['2', enrollment, 1], api: { uploadEnrollmentPhoto: async () => { throw new Error('사진 확인이 필요해요'); } } });
  try {
    const input = findTree(h.render(), n => n.props.id === 'photo-input-sh_front');
    input.props.onClick(); assert.equal(entries.size, 1);
    input.props.onCancel(); assert.equal(entries.size, 0);
    input.props.onClick(); assert.equal(entries.size, 1);
    input.props.onChange({ target: { files: [new Blob(['photo'])], value: 'picked' } });
    await eventually(() => !h.runtime.states[4], 'upload ends');
    assert.equal(entries.size, 0);
  } finally { globalThis.sessionStorage = old; await h.close(); }
});

test('R1 consent measurement fits purpose and learning with a peek and cleans up listeners', async () => {
  const oldWindow = globalThis.window, oldDocument = globalThis.document, oldStyle = globalThis.getComputedStyle;
  const calls = [], listeners = new Map(); let fontsReady;
  globalThis.window = { addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key) };
  globalThis.document = { fonts: { ready: new Promise(resolve => { fontsReady = resolve; }) } };
  globalThis.getComputedStyle = () => ({ lineHeight: '20px' });
  const h = await modelComponentHarness({ initialStates: [], api: {}, entry: '/src/features/model/RegisterScreens.jsx', exportName: 'ConsentDetails' });
  try {
    const tree = h.render({ children: 'details' });
    assert.equal(tree.props.tabIndex, 0); assert.equal(tree.props.role, 'region');
    h.runtime.refs[0].current = { querySelector: () => ({ offsetTop: 50, offsetHeight: 40 }), style: { setProperty: (...args) => calls.push(args) } };
    const cleanup = h.runtime.effects[0]();
    assert.deepEqual(calls.at(-1), ['--consent-fit', '102px']);
    listeners.get('resize')(); assert.equal(calls.length, 2);
    cleanup(); fontsReady(); await Promise.resolve();
    assert.equal(listeners.size, 0); assert.equal(calls.length, 2);
  } finally { await h.close(); globalThis.window = oldWindow; globalThis.document = oldDocument; globalThis.getComputedStyle = oldStyle; }
});

const allPhotos = SLOTS.map(slot => ({ slot: slot.key }));
const button = (tree, label) => findTree(tree, n => n.type === 'button' && text(n) === label);
function backToPhotoGroup(h) {
  button(h.render(), '이전').props.onClick();
  button(h.render(), '고치기').props.onClick();
  return h.render();
}

for (const status of ['processing', 'asset_building']) {
  test(`ROUND2 P2 ${status} 중 동의 화면에서 다시 시작해도 요청받은 사진을 올려요`, async () => {
    const pending = { ...enrollment, status, photos: allPhotos };
    const requested = { ...pending, photoReviewStatus: 'reshoot_requested', reshootSlots: [{ slot: 'sh_front', reason: '얼굴이 선명하게 나오도록 다시 찍어 주세요.' }] };
    const refreshed = { ...pending, photoReviewStatus: 'pending', reshootSlots: [] };
    const calls = [], uploads = [];
    const h = await modelComponentHarness({ initialStates: ['3', pending, PHOTO_REVIEW_SUB], api: {
      createEnrollment: async () => { calls.push('create'); return requested; },
      reopenEnrollmentPhotos: async () => { calls.push('reopen'); return pending; },
      uploadEnrollmentPhoto: async args => { calls.push('upload'); uploads.push(args); return { slot: args.slot, qcStatus: 'passed' }; },
      getEnrollment: async id => { calls.push(['refresh', id]); return refreshed; },
    } });
    try {
      backToPhotoGroup(h);
      button(h.render(), '이전').props.onClick();
      assert.equal(h.render().props['data-step'], '1');
      findTree(h.render(), n => n.props.id === 'register-consent-all').props.onChange({ target: { checked: true } });
      await button(h.render(), '동의하고 신분증 인증하기').props.onClick();
      const tree = h.render();
      assert.equal(tree.props['data-step'], 'reshoot');
      assert.match(text(tree), /얼굴이 선명하게 나오도록 다시 찍어 주세요/);
      assert.equal(findTree(tree, n => n.props.id === 'photo-input-sh_smile'), null);
      const input = findTree(tree, n => n.props.id === 'photo-input-sh_front');
      assert.equal(input.props.disabled, false);
      const file = new File(['retaken photo'], 'retaken.jpg', { type: 'image/jpeg' });
      input.props.onChange({ target: { files: [file], value: 'picked' } });
      await eventually(() => !h.runtime.states[4], '재촬영 업로드가 끝나요');
      assert.equal(uploads.length, 1, '자산 처리 중에도 요청받은 사진을 한 번 올려요');
      assert.deepEqual(uploads[0], { enrollmentId: 'e1', slot: 'sh_front', fileBlob: file, filename: 'retaken.jpg' });
      assert.deepEqual(calls, ['create', 'upload', ['refresh', 'e1']]);
      assert.equal(h.runtime.states[1].status, status);
      assert.deepEqual(h.runtime.states[1].reshootSlots, []);
      assert.equal(button(h.render(), '확인 요청 보내기').props.disabled, false);
      assert.equal(findTree(h.render(), n => n.props.role === 'alert'), null);
    } finally {
      Object.values(h.runtime.states[11] || {}).forEach(URL.revokeObjectURL);
      await h.close();
    }
  });
}

for (const status of ['processing', 'asset_building']) {
  test(`FIX2 ${status} 중 돌아온 사진 화면은 교체와 삭제를 막고 이유를 보여요`, async () => {
    const h = await modelComponentHarness({ initialStates: ['3', { ...enrollment, status, photos: allPhotos }, PHOTO_REVIEW_SUB], api: {
      uploadEnrollmentPhoto: () => assert.fail('준비 중 업로드 금지'),
      deleteEnrollmentPhoto: () => assert.fail('준비 중 삭제 금지'),
    } });
    try {
      const tree = backToPhotoGroup(h);
      const input = findTree(tree, n => n.props.id === 'photo-input-sh_front');
      const remove = findTree(tree, n => n.props['aria-label'] === '1번 사진 삭제');
      assert.equal(input.props.disabled, true);
      assert.equal(remove.props.disabled, true);
      assert.equal(findTree(tree, n => n.props.className === 'slotAction').props['aria-disabled'], true);
      assert.match(text(tree), /준비가 끝나면 사진을 바꾸거나 지울 수 있어요/);
      input.props.onChange({ target: { files: [new File(['photo'], 'photo.jpg')], value: 'picked' } });
      await eventually(() => !h.runtime.states[4], '늦은 파일 선택 결과도 무시해요');
      await remove.props.onClick();
      assert.equal(h.runtime.states[3], '');
    } finally { await h.close(); }
  });

  for (const action of ['replace', 'delete']) {
    test(`FIX2 ${status} 사진 화면 폴링 완료 후 ${action}는 등록을 다시 열어요`, async () => {
      const calls = []; let ready;
      const pending = { ...enrollment, status, photos: allPhotos };
      const h = await modelComponentHarness({ initialStates: ['3', pending, PHOTO_REVIEW_SUB], api: {
        getEnrollment: id => { calls.push(['poll', id]); return new Promise(resolve => { ready = resolve; }); },
        reopenEnrollmentPhotos: async id => { calls.push(['reopen', id]); return { ...pending, status: 'photos_pending' }; },
        uploadEnrollmentPhoto: async ({ enrollmentId, slot }) => { calls.push(['replace', enrollmentId, slot]); return { slot }; },
        deleteEnrollmentPhoto: async (id, slot) => { calls.push(['delete', id, slot]); },
      } });
      let cleanup;
      try {
        backToPhotoGroup(h);
        cleanup = h.runtime.effects[4]();
        assert.deepEqual(calls, [['poll', 'e1']]);
        ready({ ...pending, status: 'license_pending' });
        await eventually(() => h.runtime.states[1].status === 'license_pending', '사진 화면에서도 준비 완료를 받아요');
        assert.equal(h.runtime.states[0], '2');
        assert.equal(h.runtime.states[2], 1);
        const tree = h.render();
        const input = findTree(tree, n => n.props.id === 'photo-input-sh_front');
        const remove = findTree(tree, n => n.props['aria-label'] === '1번 사진 삭제');
        assert.equal(input.props.disabled, false);
        assert.equal(remove.props.disabled, false);
        if (action === 'replace') input.props.onChange({ target: { files: [new File(['photo'], 'photo.jpg')], value: 'picked' } });
        else await remove.props.onClick();
        await eventually(() => !h.runtime.states[4], '사진 수정을 마쳐요');
        assert.deepEqual(calls, [['poll', 'e1'], ['reopen', 'e1'], [action, 'e1', 'sh_front']]);
        assert.equal(h.runtime.states[1].status, 'photos_pending');
        assert.equal(h.runtime.states[3], '');
      } finally { cleanup?.(); await h.close(); }
    });
  }
}

for (const step of ['2', '3']) {
  for (const failure of ['requests', 'deadline']) {
    test(`FIX3 ${step}단계 ${failure} 준비 오류와 재시도는 입력, 스크롤, 초점을 유지해요`, async () => {
      let recovered = false;
      const h = await modelComponentHarness({ initialStates: [step, { ...enrollment, status: 'asset_building', photos: allPhotos }, 1], honorHookDependencies: true, api: {
        getEnrollment: async (_id, { signal }) => {
          if (recovered) return { ...enrollment, status: 'license_pending', photos: allPhotos };
          if (failure === 'requests') throw new Error('사진 준비 상태를 불러오지 못했어요.');
          return new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('aborted'))));
        },
      } });
      const originalTimeout = globalThis.setTimeout, originalWindow = globalThis.window, originalDocument = globalThis.document;
      const delays = [], movements = [], activeElement = {};
      let deadline, cleanup;
      globalThis.setTimeout = (callback, ms, ...args) => {
        if (ms === 300000) { deadline = callback; return 0; }
        if (ms === 2500) { delays.push(callback); return 0; }
        return originalTimeout(callback, ms, ...args);
      };
      globalThis.window = { scrollY: 920, scrollTo: () => movements.push('scroll') };
      globalThis.document = { activeElement, querySelector: () => ({ focus: () => movements.push('heading focus') }) };
      try {
        h.render();
        h.runtime.states[6] = { allowedUse: ['액티브웨어'] };
        h.runtime.states[12] = true; h.runtime.states[17] = 'm1';
        h.runtime.refs[4].current = { scrollIntoView: () => movements.push('error scroll'), focus: () => movements.push('error focus') };
        cleanup = h.runtime.effects[4]();
        if (failure === 'deadline') { assert.ok(deadline); deadline(); }
        else for (let i = 0; i < 3; i++) { await eventually(() => delays.length, '다음 조회를 기다려요'); delays.shift()(); }
        const message = failure === 'deadline' ? '사진 정리가 늦어지고 있어요. 잠시 뒤 다시 확인해 주세요.' : '사진 준비 상태를 불러오지 못했어요.';
        await eventually(() => h.runtime.states.includes(message), '준비 오류가 표시돼요');
        const tree = h.render();
        for (const effect of h.runtime.effects) effect();
        assert.equal(h.runtime.states[3], '');
        assert.deepEqual(movements, []);
        assert.equal(globalThis.document.activeElement, activeElement);
        assert.equal(globalThis.window.scrollY, 920);
        assert.equal(h.runtime.states[0], step);
        assert.equal(h.runtime.states[2], 1);
        const footer = findTree(tree, n => n.type === 'footer');
        assert.match(text(findTree(footer, n => n.props.role === 'alert')), new RegExp(message));
        const retry = button(footer, '사진 준비 상태 다시 확인하기');
        assert.ok(retry);
        if (step === '3') {
          assert.equal(findTree(tree, n => n.props.role === 'switch').props.disabled, false);
          assert.equal(issueButton(tree).props.disabled, true);
        }
        recovered = true; cleanup(); cleanup = null;
        retry.props.onClick();
        h.render();
        assert.equal(h.runtime.effects.length, 1, '재시도는 폴링만 다시 시작해요');
        cleanup = h.runtime.effects[0]();
        await eventually(() => h.runtime.states[1].status === 'license_pending', '재시도 후 준비를 마쳐요');
        const readyTree = h.render();
        assert.equal(findTree(readyTree, n => n.props.role === 'alert'), null);
        assert.equal(h.runtime.states[0], step);
        assert.equal(h.runtime.states[2], 1);
        assert.deepEqual(h.runtime.states[6], { allowedUse: ['액티브웨어'] });
        assert.equal(h.runtime.states[12], true);
        assert.deepEqual(movements, []);
        if (step === '3') assert.equal(issueButton(readyTree).props.disabled, false);
      } finally { cleanup?.(); globalThis.setTimeout = originalTimeout; globalThis.window = originalWindow; globalThis.document = originalDocument; await h.close(); }
    });
  }
}
