import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const termsUrl = new URL('../../src/features/facemarket-landing/facemarketTerms.js', import.meta.url);
const journeyUrl = new URL('../../src/features/model/modelHubState.js', import.meta.url);

async function loadRequired(url, label) {
  assert.ok(existsSync(url), `${label} 단일 출처 파일이 필요합니다`);
  return import(url);
}

test('조건표는 계약과 정산 기준을 한 곳에서 제공한다', async () => {
  const terms = await loadRequired(termsUrl, 'FaceMarket 조건표');

  assert.equal(terms.MODEL_SHARE, 0.7);
  assert.equal(terms.PLATFORM_SHARE, 0.2);
  assert.equal(terms.OPS_SHARE, 0.1);
  assert.equal(terms.SETTLEMENT_DAY, 10);
  assert.equal(terms.MIN_PAYOUT_KRW, 10_000);
  assert.equal(terms.MONTHLY_PERIOD_DAYS, 30);
  assert.equal(terms.APPROVAL_MODE, 'auto');
  assert.deepEqual(terms.VALIDITY_OPTIONS, [365, 730, null]);

  assert.equal(terms.formatKrw(25_000), '25,000원');
  assert.equal(terms.validityLabel(365), '365일');
  assert.equal(terms.validityLabel(730), '730일');
  assert.equal(terms.validityLabel(null), '철회 시까지');
});

test('Digital DNA 여정은 다섯 단계와 현재 단계 설명을 제공한다', async () => {
  const { HUB_STEPS } = await loadRequired(journeyUrl, '허브 상태');
  assert.deepEqual(HUB_STEPS, [
    { key: 'application', label: '지원 접수', description: null },
    { key: 'review', label: '내부 검토', description: '지원서를 검토중이에요. 24시간 이내 결과를 전달해드릴게요.' },
    { key: 'registration', label: '모델 등록', description: '얼굴 구현을 위한 이미지들과, 라이선스 증서에 대한 설정이 필요해요.' },
    { key: 'final_review', label: '최종 검토', description: '등록서를 최종 검토중이에요. 24시간 이내 결과를 전달해드릴게요.' },
    { key: 'confirm', label: '프로필 이미지 확정', description: '저희가 만들어드리는 프로필 이미지 중 사용될 이미지를 선택해주시면 모델 등록이 끝나요.' },
  ]);
});

test('지원 상태는 현재 칸 하나와 행동 하나로 이어진다', async () => {
  const { resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  const cases = [
    {
      input: { applicationRequired: true },
      wantIndex: 0,
      wantAction: { label: '얼리버드 지원하기', kind: 'route', to: '/apply' },
    },
    {
      input: { applicationRequired: true, application: { id: 'a1', status: 'under_review' } },
      wantIndex: 1,
      wantAction: { label: '지원 취소', kind: 'cancel' },
    },
    {
      input: { applicationRequired: true, application: { id: 'a2', status: 'approved' } },
      wantIndex: 2,
      wantAction: { label: '등록 시작하기', kind: 'route', to: '/model/register' },
    },
    {
      input: { applicationRequired: true, application: { id: 'a3', status: 'rejected' } },
      wantIndex: 1,
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

test('등록 상태는 등록, 최종 검토, 프로필 확정과 도달 가능한 행동으로 매핑된다', async () => {
  const { resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  const cases = [
    ['identity_pending', 2, '등록 이어가기', '/model/register'],
    ['photos_pending', 2, '등록 이어가기', '/model/register'],
    ['terms_pending', 2, '조건·증서 이어가기', '/model/license?step=terms&enrollment=e1'],
    ['vc_pending', 2, '조건·증서 이어가기', '/model/license?step=terms&enrollment=e1'],
    ['review_pending', 3, '검토 상태 새로고침', undefined],
    ['processing', 3, '검토 상태 새로고침', undefined],
    ['asset_building', 3, '검토 상태 새로고침', undefined],
    ['confirm_pending', 4, '이미지 선택하기', '/model/confirm'],
  ];

  for (const [status, wantIndex, label, to] of cases) {
    const journey = resolveHubJourney({ enrollment: { id: 'e1', status } });
    assert.equal(journey.currentIndex, wantIndex, status);
    assert.equal(journey.action.label, label, status);
    assert.equal(journey.action.to, to, status);
    assert.equal(journey.steps.filter((step) => step.state === 'current').length, 1, status);
  }
});

test('processing은 라이선스 유무와 관계없이 최종 검토 단계다', async () => {
  const { hasCurrentEnrollmentLicense, resolveHubJourney } = await loadRequired(journeyUrl, '허브 상태');
  assert.equal(hasCurrentEnrollmentLicense([{ status: 'reverification_required' }]), false);
  assert.equal(hasCurrentEnrollmentLicense([{ status: 'pending' }]), true);
  assert.equal(hasCurrentEnrollmentLicense([{ status: 'active' }]), true);
  for (const status of ['processing', 'asset_building']) {
    const currentServer = resolveHubJourney({ enrollment: { id: 'e1', status }, hasLicense: false });
    assert.equal(currentServer.currentIndex, 3, status);
    assert.equal(currentServer.action.label, '검토 상태 새로고침', status);

    const phaseB = resolveHubJourney({ enrollment: { id: 'e1', status }, hasLicense: true });
    assert.equal(phaseB.currentIndex, 3, status);
    assert.equal(phaseB.action.label, '검토 상태 새로고침', status);
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
  assert.equal(journey.currentIndex, 4);
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

  assert.equal(journey.currentIndex, 4);
  assert.deepEqual(journey.action, {
    label: '이미지 선택하기',
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

  assert.equal(journey.currentIndex, 3);
  assert.deepEqual(journey.action, {
    label: '검토 상태 새로고침',
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

  assert.equal(journey.currentIndex, 3);
  assert.deepEqual(journey.action, {
    label: '검토 상태 새로고침',
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


test('활동 허브는 서버 전체 월 합계를 조회하고 최근 내역을 합산하지 않는다', () => {
  const source = readFileSync(new URL('../../src/features/model/ModelHub.jsx', import.meta.url), 'utf8');
  assert.match(source, /getSettlementSummary\(\)/);
  assert.doesNotMatch(source, /listSettlements|summarizeSettlements/);
  assert.match(source, /settlementSummary=\{settlementSummary\}/);
});


test('a pending VC still offers issuance retry with a pending license row', async () => {
  const { resolveHubJourney, hasCurrentEnrollmentLicense } = await loadRequired(journeyUrl, '허브 상태');
  const result = resolveHubJourney({
    ownedModel: { status: 'pending' },
    enrollment: { id: 'retry-vc', status: 'vc_pending' },
    hasLicense: hasCurrentEnrollmentLicense([{ status: 'pending' }]),
  });
  assert.equal(result.currentIndex, 2);
  assert.equal(result.action.to, '/model/license?step=terms&enrollment=retry-vc');
});
