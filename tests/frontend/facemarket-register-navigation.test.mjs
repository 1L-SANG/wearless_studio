import test from 'node:test';
import assert from 'node:assert/strict';
import { modelComponentHarness, findTree, eventually } from './helpers/facemarketHarness.mjs';
import { CONSENT_VERSION, SLOTS } from '../../src/features/model/registerSlots.js';

const record = { id: 'e1', status: 'identity_pending', photos: [], consentDocumentVersion: CONSENT_VERSION, termsConsentVersion: CONSENT_VERSION };
const commit = h => { const tree = h.render(); h.runtime.effects.forEach(effect => effect()); return tree; };
const button = (tree, label) => findTree(tree, node => node.type === 'button' && node.props.children === label);

async function navigationHarness(enrollment, api = {}) {
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    getCurrentEnrollment: async () => enrollment, ...api,
  } });
  const saved = { window: globalThis.window, document: globalThis.document };
  const scrolls = [];
  const focus = [];
  globalThis.window = { scrollTo: options => scrolls.push(options) };
  globalThis.document = { querySelector: () => ({ focus: options => focus.push(options) }) };
  return {
    h, scrolls, focus,
    async mount() {
      commit(h);
      await eventually(() => !['loading', 'error'].includes(h.runtime.states[0]), 'registration restored');
      return commit(h);
    },
    async close() { Object.assign(globalThis, saved); await h.close(); },
  };
}

test('인증 취소로 같은 화면에 돌아와도 맨 위로 이동하고 동의 체크만 할 때는 스크롤을 유지해요', async () => {
  let attempts = 0;
  const fixture = await navigationHarness(record, {
    runIdentityWidget: async () => { if (++attempts === 1) throw new Error('인증이 중단됐어요.'); return 'token'; },
    createIdentity: async () => ({ ...record, status: 'photos_pending' }),
  });
  const { h, scrolls, focus } = fixture;
  try {
    let tree = await fixture.mount();
    assert.deepEqual(scrolls.at(-1), { top: 0, left: 0, behavior: 'instant' });
    const initial = scrolls.length;
    findTree(tree, node => node.props?.id === 'register-consent-all').props.onChange({ target: { checked: false } });
    tree = commit(h);
    findTree(tree, node => node.props?.id === 'register-consent-all').props.onChange({ target: { checked: true } });
    tree = commit(h);
    assert.equal(scrolls.length, initial);
    await button(tree, '신분증 인증하기').props.onClick();
    tree = commit(h);
    assert.equal(h.runtime.states[0], '1');
    assert.equal(scrolls.length, initial + 1, '취소 복귀는 step 값이 같아도 상단부터 보여야 해요');
    await button(tree, '다시 인증하기').props.onClick();
    commit(h);
    assert.equal(h.runtime.states[0], '2');
    assert.equal(scrolls.length, initial + 2);
    assert.deepEqual(focus.at(-1), { preventScroll: true });
  } finally { await fixture.close(); }
});

test('사진 묶음에서 이전과 다음을 누를 때마다 상단부터 보여요', async () => {
  const fixture = await navigationHarness({ ...record, status: 'photos_pending', photos: SLOTS.slice(0, 9).map(({ key }) => ({ slot: key })) });
  const { h, scrolls } = fixture;
  try {
    let tree = await fixture.mount();
    assert.equal(h.runtime.states[2], 2);
    const initial = scrolls.length;
    button(tree, '이전').props.onClick();
    tree = commit(h);
    assert.equal(h.runtime.states[2], 1);
    assert.equal(scrolls.length, initial + 1);
    await button(tree, '다음').props.onClick();
    commit(h);
    assert.equal(h.runtime.states[2], 2);
    assert.equal(scrolls.length, initial + 2);
    assert.deepEqual(scrolls.at(-1), { top: 0, left: 0, behavior: 'instant' });
  } finally { await fixture.close(); }
});
