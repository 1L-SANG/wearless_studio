import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

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

test('현재 서버와 Phase B의 processing은 라이선스 존재 여부로 구분해 타임라인이 후퇴하지 않는다', async () => {
  const { hasCurrentEnrollmentLicense, resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  assert.equal(hasCurrentEnrollmentLicense([{ status: 'reverification_required' }]), false);
  assert.equal(hasCurrentEnrollmentLicense([{ status: 'pending' }]), true);
  assert.equal(hasCurrentEnrollmentLicense([{ status: 'active' }]), true);
  for (const status of ['processing', 'asset_building']) {
    const currentServer = resolveHubJourney({ enrollment: { id: 'e1', status }, hasLicense: false });
    assert.equal(currentServer.currentIndex, 2, status);
    assert.equal(currentServer.action.label, '등록 상태 새로고침', status);

    const phaseB = resolveHubJourney({ enrollment: { id: 'e1', status }, hasLicense: true });
    assert.equal(phaseB.currentIndex, 4, status);
    assert.equal(phaseB.action.label, '생성 상태 새로고침', status);
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

test('테스트컷 전송 뒤에는 확인 화면으로 바로 이어진다', async () => {
  const { resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  const journey = resolveHubJourney({
    ownedModel: { id: 'm1', status: 'awaiting_confirm', redoCount: 0 },
    enrollment: { id: 'e1', status: 'passed' },
    hasLicense: true,
  });

  assert.equal(journey.currentIndex, 5);
  assert.deepEqual(journey.action, {
    label: '테스트컷 확인하기',
    kind: 'route',
    to: '/model/confirm',
  });
});

test('재생성 요청 뒤에는 등록 화면으로 되돌리지 않고 생성 중으로 표시한다', async () => {
  const { resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  const journey = resolveHubJourney({
    ownedModel: { id: 'm1', status: 'pending', redoCount: 1 },
    enrollment: { id: 'e1', status: 'passed' },
    hasLicense: true,
  });

  assert.equal(journey.currentIndex, 4);
  assert.deepEqual(journey.action, {
    label: '생성 상태 새로고침',
    kind: 'reload',
  });
});

test('첫 VC 발급 뒤에도 현재 등록 조회가 끝났다고 등록 단계로 후퇴하지 않는다', async () => {
  const { resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  const journey = resolveHubJourney({
    ownedModel: { id: 'm1', status: 'pending', redoCount: 0 },
    enrollment: null,
    hasLicense: true,
  });

  assert.equal(journey.currentIndex, 4);
  assert.deepEqual(journey.action, {
    label: '생성 상태 새로고침',
    kind: 'reload',
  });
});

test('1280px 활동 중 허브는 트윈·규칙·이번 달 요약을 같은 행에 둔다', () => {
  const css = readFileSync(
    new URL('../../src/features/model/ModelPersonalization.module.css', import.meta.url),
    'utf8',
  );
  const start = css.indexOf('@media (min-width: 64rem)');
  assert.notEqual(start, -1, '데스크톱 활동 중 레이아웃 구간이 필요합니다');
  const nextMedia = css.indexOf('@media', start + 1);
  const desktop = css.slice(start, nextMedia === -1 ? css.length : nextMedia);
  assert.match(desktop, /\.hubActiveGrid\s*\{[\s\S]*grid-template-columns:\s*repeat\(3,/);
  assert.match(desktop, /\.hubActiveCardWide\s*\{\s*grid-column:\s*auto;/);
});
