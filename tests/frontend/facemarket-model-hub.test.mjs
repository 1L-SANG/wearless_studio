import test from 'node:test';
import assert from 'node:assert/strict';
import * as hub from '../../src/features/model/modelHubState.js';
import * as terms from '../../src/features/facemarket-landing/facemarketTerms.js';

const now = new Date('2026-09-11T00:00:00Z');
const model = { id: 'm1', status: 'verified' };
const active = { id: 'l1', modelId: 'm1', status: 'active', licenseValidUntil: '2027-09-01T00:00:00Z' };

test('진행표는 지원 접수부터 프로필 확정까지 다섯 단계를 표시해요', () => {
  assert.deepEqual(hub.HUB_STEPS.map(row => row.label), ['지원 접수', '내부 검토', '모델 등록', '최종 검토', '프로필 이미지 확정']);
});

const cases = [
  ['비로그인', { authenticated: false }, 'guest', undefined, undefined, 'none'],
  ['지원 검토', { application: { status: 'under_review' } }, 'onboarding', 0, undefined, 'none'],
  ['승인', { application: { status: 'approved' } }, 'onboarding', 1, undefined, 'none'],
  ['중간 저장', { enrollment: { id: 'e1', status: 'photos_pending' } }, 'onboarding', 2, undefined, 'none'],
  ['반려', { application: { status: 'rejected', rejectReason: '사진 확인' } }, 'onboarding', 3, undefined, 'none'],
  ['사진 검수', { enrollment: { status: 'review_pending' } }, 'review', undefined, 'review', 'none'],
  ['처리 중', { enrollment: { status: 'processing' } }, 'review', undefined, 'assets', 'none'],
  ['자산 생성', { enrollment: { status: 'asset_building' } }, 'review', undefined, 'assets', 'none'],
  ['사진 확정 대기', { ownedModel: { ...model, status: 'awaiting_confirm' } }, 'review', undefined, 'confirm', 'none'],
  ['활동', { ownedModel: model, license: active }, 'active', undefined, undefined, 'none'],
  ['철회', { ownedModel: model, license: { ...active, status: 'revoked' } }, 'active', undefined, undefined, 'revoked'],
  ['본인 중단', { ownedModel: { ...model, status: 'suspended', suspensionSource: 'owner' }, license: active }, 'active', undefined, undefined, 'paused'],
  ['만료 임박', { ownedModel: model, license: { ...active, licenseValidUntil: '2026-10-01T00:00:00Z' } }, 'active', undefined, undefined, 'expiring'],
  ['영구', { ownedModel: model, license: { ...active, licenseValidUntil: null } }, 'active', undefined, undefined, 'none'],
  ['라이선스 누락', { ownedModel: model }, 'onboarding', 2, undefined, 'none'],
  ['발급 대기', { ownedModel: model, license: { ...active, status: 'pending' }, enrollment: { id: 'e1', status: 'vc_pending' } }, 'onboarding', 2, undefined, 'none'],
];
for (const [name, input, mode, step, sub, flag] of cases) {
  test(`마이페이지 판정: ${name}`, () => {
    const journey = hub.resolveHubJourney({ ...input, now });
    assert.deepEqual([journey.mode, journey.step, journey.sub, journey.flag], [mode, step, sub, flag]);
    assert.equal(journey.steps.length, 5);
    assert.ok(journey.steps.filter(row => ['progress', 'todo'].includes(row.state)).length <= 1);
  });
}

test('현재 모델의 최신 라이선스를 골라 과거 철회나 타인 모델과 섞지 않아요', () => {
  assert.equal(typeof hub.currentModelLicense, 'function');
  const rows = [
    { id: 'other', modelId: 'other', status: 'active', createdAt: '2026-09-11' },
    { ...active, id: 'old', status: 'revoked', createdAt: '2026-09-01' },
    { ...active, id: 'new', createdAt: '2026-09-10' },
  ];
  assert.equal(hub.currentModelLicense(rows, model).id, 'new');
  assert.equal(hub.currentModelLicense(rows, { id: 'missing' }), null);
});

test('진행표 시각은 서버 필드를 사용하고 없으면 생략해요', () => {
  const journey = hub.resolveHubJourney({
    application: { status: 'approved', createdAt: '2026-09-01T01:00:00Z', reviewedAt: '2026-09-01T02:00:00Z' },
    enrollment: { status: 'review_pending', completedAt: '2026-09-02T01:00:00Z' }, now,
  });
  assert.deepEqual(journey.steps.map(row => row.timestamp), ['2026-09-01T01:00:00Z', '2026-09-01T02:00:00Z', '2026-09-02T01:00:00Z', undefined, undefined]);
  assert.equal(hub.resolveHubJourney({ application: { status: 'approved' }, now }).steps[0].timestamp, undefined);
});

test('발급 재시도와 테스트컷 확정의 실제 경로를 유지해요', () => {
  assert.equal(hub.resolveHubJourney({ enrollment: { id: 'retry-vc', status: 'vc_pending' }, now }).action.to, '/model/register');
  assert.equal(hub.resolveHubJourney({ ownedModel: { status: 'awaiting_confirm' }, now }).action.to, '/model/confirm');
  assert.equal(hub.resolveHubJourney({ ownedModel: { status: 'pending', redoCount: 1 }, hasLicense: true, now }).sub, 'assets');
});

test('표준 요금과 모델 몫을 같은 기준으로 계산해요', () => {
  assert.equal(terms.STANDARD_UNIT_PRICE_KRW * terms.MODEL_SHARE, 10430);
  assert.equal(terms.MONTHLY_PASS_PRICE_KRW * terms.MODEL_SHARE, 34930);
  assert.equal(terms.MONTHLY_PASS_CUTS, 10);
  assert.equal(terms.MONTHLY_OVERAGE_KRW * terms.MODEL_SHARE, 5530);
  assert.deepEqual(terms.VALIDITY_OPTIONS, [365, 730, null]);
});
