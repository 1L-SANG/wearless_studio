import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

const termsUrl = new URL('../../src/features/facemarket-landing/facemarketTerms.js', import.meta.url);
const journeyUrl = new URL('../../src/features/model/modelHubState.js', import.meta.url);

async function loadRequired(url, label) {
  assert.ok(existsSync(url), `${label} 단일 출처 파일이 필요합니다`);
  return import(url);
}

test('조건표는 월정액과 정산 기준을 한 곳에서 계산한다', async () => {
  const terms = await loadRequired(termsUrl, 'FaceMarket 조건표');

  assert.equal(terms.MODEL_SHARE, 0.7);
  assert.equal(terms.PLATFORM_SHARE, 0.2);
  assert.equal(terms.OPS_SHARE, 0.1);
  assert.equal(terms.SETTLEMENT_DAY, 10);
  assert.equal(terms.MIN_PAYOUT_KRW, 10_000);
  assert.equal(terms.MONTHLY_MULTIPLIER, 2.5);
  assert.equal(terms.MONTHLY_PERIOD_DAYS, 30);
  assert.equal(terms.APPROVAL_MODE, 'auto');
  assert.deepEqual(terms.VALIDITY_OPTIONS, [365, 730, null]);

  assert.equal(terms.monthlyPriceFor(10_000), 25_000);
  assert.equal(terms.monthlyPriceFor(5_150), 12_900);
  assert.equal(terms.monthlyPriceFor(undefined), 25_000);
  assert.equal(terms.formatKrw(25_000), '25,000원');
  assert.equal(terms.validityLabel(365), '365일');
  assert.equal(terms.validityLabel(730), '730일');
  assert.equal(terms.validityLabel(null), '영구');
});

test('지원부터 활동까지 일곱 단계의 라벨이 고정된다', async () => {
  const { HUB_STEPS } = await loadRequired(journeyUrl, '허브 상태');
  assert.deepEqual(HUB_STEPS.map((step) => step.label), [
    '지원 접수',
    '등록 링크',
    '본인확인·사진·조건·증서',
    '우리 검수',
    '테스트 컷 생성',
    '확정',
    '활동 중',
  ]);
});

test('지원 상태는 현재 칸 하나와 행동 하나로 이어진다', async () => {
  const { resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  const cases = [
    {
      input: { applicationRequired: true },
      wantIndex: 0,
      wantAction: { label: '얼리버드 지원하기', kind: 'route', to: '/model/apply' },
    },
    {
      input: { applicationRequired: true, application: { id: 'a1', status: 'under_review' } },
      wantIndex: 0,
      wantAction: { label: '지원 취소', kind: 'cancel' },
    },
    {
      input: { applicationRequired: true, application: { id: 'a2', status: 'approved' } },
      wantIndex: 1,
      wantAction: { label: '등록 시작하기', kind: 'route', to: '/model/register' },
    },
    {
      input: { applicationRequired: true, application: { id: 'a3', status: 'rejected' } },
      wantIndex: 0,
      wantAction: { label: '다시 지원하기', kind: 'route', to: '/model/apply' },
    },
  ];

  for (const { input, wantIndex, wantAction } of cases) {
    const journey = resolveHubJourney(input);
    assert.equal(journey.mode, 'onboarding');
    assert.equal(journey.currentIndex, wantIndex);
    assert.deepEqual(journey.action, wantAction);
    assert.equal(journey.steps.filter((step) => step.state === 'current').length, 1);
  }
});

test('등록 상태는 검수·생성·확정 단계와 도달 가능한 행동으로 매핑된다', async () => {
  const { resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  const cases = [
    ['identity_pending', 2, '등록 이어가기', '/model/register'],
    ['photos_pending', 2, '등록 이어가기', '/model/register'],
    ['terms_pending', 2, '조건·증서 이어가기', '/model/license?step=terms&enrollment=e1'],
    ['vc_pending', 2, '조건·증서 이어가기', '/model/license?step=terms&enrollment=e1'],
    ['review_pending', 3, '검수 상태 새로고침', undefined],
    ['processing', 4, '생성 상태 새로고침', undefined],
    ['asset_building', 4, '생성 상태 새로고침', undefined],
    ['confirm_pending', 5, '테스트 컷 확인하기', '/model/confirm'],
  ];

  for (const [status, wantIndex, label, to] of cases) {
    const journey = resolveHubJourney({ enrollment: { id: 'e1', status } });
    assert.equal(journey.currentIndex, wantIndex, status);
    assert.equal(journey.action.label, label, status);
    assert.equal(journey.action.to, to, status);
    assert.equal(journey.steps.filter((step) => step.state === 'current').length, 1, status);
  }
});

test('verified 모델은 거래가 없어도 활동 중 화면이다', async () => {
  const { resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  const journey = resolveHubJourney({
    ownedModel: { id: 'm1', status: 'verified' },
    enrollment: null,
    settlements: [],
  });
  assert.equal(journey.mode, 'active');
  assert.equal(journey.currentIndex, 6);
  assert.equal(journey.steps.every((step) => step.state === 'done'), true);
  assert.equal(journey.action, null);
});
