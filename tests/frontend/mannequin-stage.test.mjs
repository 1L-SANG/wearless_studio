import test from 'node:test';
import assert from 'node:assert/strict';
import { isMannequinStageEnabled } from '../../src/lib/mannequinStage.js';
import { WIZARD_STEPS, STEP_INDEX, wizardStepsForMannequinStage } from '../../src/lib/wizardSteps.js';
import { mannequinGenerationCreditShortfall, detailPageGenerationCreditShortfall } from '../../src/lib/creditPreflight.js';
import { flowScreenName } from '../../src/lib/flowAbandon.js';
import { storyboardTransitionState } from '../../src/features/product-input/storyboardTransition.js';
import { storyboardContinueLabel, storyboardDetailPageCredits, loadStoryboardCreditQuote, storyboardCreditResume,
  continueStoryboardToDetailPage } from '../../src/features/storyboard/storyboardDetailPage.js';

test('마네킹 스위치는 정확히 off, 0, false만 끈다', () => {
  for (const value of [undefined, null, '', 'on', 'true', '1', 'OFF', false, 0]) assert.equal(isMannequinStageEnabled(value), true, String(value));
  for (const value of ['off', '0', 'false']) assert.equal(isMannequinStageEnabled(value), false);
});

test('끔의 세 점은 모든 제작 주소를 포함하고 켬은 기존 상수를 쓴다', () => {
  const on = wizardStepsForMannequinStage(true), off = wizardStepsForMannequinStage(false);
  assert.equal(on.steps, WIZARD_STEPS);
  assert.equal(on.index, STEP_INDEX);
  assert.deepEqual(off.steps.map(step => step.key), ['input', 'storyboard', 'editor']);
  assert.deepEqual(off.index, { input: 0, analysis: 0, storyboard: 1, mannequin: 1, generating: 2, editor: 2 });
  for (const index of Object.values(off.index)) assert.ok(index >= 0 && index < off.steps.length);
});

test('분석 확정은 끔에서 잔액 0도 통과하고 켬에서는 기존 비용을 검사한다', () => {
  assert.equal(mannequinGenerationCreditShortfall({ credits: 0 }, 64, false), null);
  assert.equal(mannequinGenerationCreditShortfall({ credits: 0 }, 64, true).requiredCredits, 64);
  assert.equal(mannequinGenerationCreditShortfall({ credits: 45 }, 45, true), null);
});

test('콘티 버튼과 이탈 이름과 도착 표시는 스위치를 따른다', () => {
  assert.equal(storyboardContinueLabel(false), '상세페이지 만들기');
  assert.equal(storyboardContinueLabel(true), '다음: 마네킹컷 선택');
  assert.equal(flowScreenName('/create/mannequin', false), '콘티 화면');
  assert.equal(flowScreenName('/create/mannequin', true), '마네킹 화면');
  assert.deepEqual(storyboardTransitionState({}, true), { showMannequinTransition: true, customMatchPromotionStarted: false });
  const state = storyboardTransitionState({ customMatch: { uploads: [{}] } }, false);
  assert.equal(state.showMannequinTransition, undefined);
  assert.equal(state.customMatchPromotionStarted, true);
});

test('콘티 견적은 컷 최소값과 서버 모델 요금을 합치고 0컷과 모르는 컷 수는 보존한다', () => {
  const options = { quote: { detailPageGenerate: { extensionModelFee: 10 } }, plan: 'free', selectedModelId: 'mF' };
  assert.equal(storyboardDetailPageCredits(1, options), 105);
  assert.equal(storyboardDetailPageCredits(10, options), 200);
  assert.equal(storyboardDetailPageCredits(0, options), 0);
  assert.equal(storyboardDetailPageCredits(null, options), null);
  assert.equal(storyboardDetailPageCredits(5, { ...options, quote: { detailPageGenerate: { extensionModelFee: 0 } } }), 95);
  assert.equal(detailPageGenerationCreditShortfall({ credits: 100 }, 5, 19).requiredCredits, 114);
  assert.equal(detailPageGenerationCreditShortfall({ credits: 0 }, 0, 19), null);
});

test('옛 서버와 견적 실패는 요금제와 고른 모델의 로컬 금액으로 대신한다', async () => {
  for (const [plan, selectedModelId, required] of [['free', 'mF', 114], ['seller', 'mF', 105], ['pro', 'mF', 95], ['free', 'mA', 95], ['free', 'real-model', 95]]) {
    for (const quote of [null, {}, { detailPageGenerate: {} }]) assert.equal(storyboardDetailPageCredits(5, { quote, plan, selectedModelId }), required);
  }
  let requests = 0;
  const getCreditQuote = async id => { requests++; assert.equal(id, 'p'); return { detailPageGenerate: { extensionModelFee: 19 } }; };
  assert.equal(await loadStoryboardCreditQuote({ mannequinStageEnabled: true, projectId: 'p', getCreditQuote }), null);
  assert.equal(requests, 0);
  assert.deepEqual(await loadStoryboardCreditQuote({ mannequinStageEnabled: false, projectId: 'p', getCreditQuote }), { detailPageGenerate: { extensionModelFee: 19 } });
  assert.equal(requests, 1);
  assert.equal(await loadStoryboardCreditQuote({ mannequinStageEnabled: false, projectId: 'p', getCreditQuote: async () => { throw Error('offline'); } }), null);
});

test('콘티 재개는 끔의 detail-page만 받는다', () => {
  const resume = { action: 'detail-page', requiredCredits: 114 };
  assert.equal(storyboardCreditResume({ creditResume: resume }, false), resume);
  assert.equal(storyboardCreditResume({ creditResume: resume }, true), null);
  assert.equal(storyboardCreditResume({ creditResume: { action: 'storyboard' } }, false), null);
  assert.equal(storyboardCreditResume(null, false), null);
});

function continuation(options = {}) {
  const events = [], submission = { current: false };
  const run = overrides => continueStoryboardToDetailPage({ submission,
    flush: async () => { events.push('save'); }, afterFlush: () => events.push('measure'),
    account: { credits: 114 }, aiCutCount: 5, plan: 'free', selectedModelId: 'mF',
    onFailure: message => events.push(['failure', message]),
    onShortfall: shortfall => events.push(['shortfall', shortfall.requiredCredits]),
    navigate: path => events.push(path), ...options, ...overrides });
  return { run, events, submission };
}

test('저장 실패는 색 재기와 이동을 막고 재시도할 수 있다', async () => {
  const { run, events, submission } = continuation({ flush: async () => { throw Error('save offline'); } });
  assert.equal(await run(), false);
  assert.deepEqual(events, [['failure', 'save offline']]);
  assert.equal(submission.current, false);
});

test('저장 뒤 모델 요금까지 부족하면 안내하고 이동하지 않는다', async () => {
  const { run, events, submission } = continuation({ account: { credits: 100 } });
  assert.equal(await run(), false);
  assert.deepEqual(events, ['save', 'measure', ['shortfall', 114]]);
  assert.equal(submission.current, false);
  assert.equal(await run({ account: { credits: 114 } }), true);
  assert.equal(events.at(-1), '/create/generating');
});

test('충분하면 저장과 색 재기 뒤 생성으로 한번 이동하며 연타를 막는다', async () => {
  let release;
  const { run, events } = continuation({ flush: () => new Promise(resolve => { release = resolve; events.push('save'); }) });
  const first = run();
  assert.equal(await run(), false);
  release();
  assert.equal(await first, true);
  assert.equal(await run(), false);
  assert.deepEqual(events, ['save', 'measure', '/create/generating']);
});

test('계정과 금액을 모르면 통과하고 색 재기 예외도 이동을 막지 않는다', async () => {
  for (const options of [{ account: null }, { account: { credits: NaN } }, { aiCutCount: null }]) {
    const { run, events } = continuation({ ...options, afterFlush: () => { throw Error('measure'); } });
    assert.equal(await run(), true);
    assert.equal(events.at(-1), '/create/generating');
  }
});

test('저장 중 화면이나 프로젝트가 바뀌면 색 재기와 부족 안내와 생성 이동을 모두 멈춘다', async () => {
  for (const failure of [true, false]) {
    let active = true, release;
    const { run, events } = continuation({
      isCurrent: () => active,
      flush: () => new Promise((resolve, reject) => { release = () => failure ? reject(Error('old save')) : resolve(); }),
    });
    const pending = run();
    active = false;
    release();
    assert.equal(await pending, false);
    assert.deepEqual(events, []);
  }
});

test('다른 모델의 견적은 현재 선택한 모델의 로컬 요금으로 대신한다', () => {
  assert.equal(storyboardDetailPageCredits(5, { plan: 'free', selectedModelId: 'mF',
    quote: { detailPageGenerate: { selectedModelId: 'mA', extensionModelFee: 0 } } }), 114);
});
