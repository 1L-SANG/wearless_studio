import test from 'node:test';
import assert from 'node:assert/strict';

import {
  defaultUsageMonth,
  monthLabel,
  rowsForMonth,
  settlementLineParts,
  usageDate,
  usageMonths,
} from '../../src/features/model/mypage/usageMonths.js';
import {
  PAYOUT_ACCOUNT_API_READY,
  PAYOUT_BANKS,
  maskAccountNumber,
  normalizePayoutAccount,
  payoutAccountLabel,
  validatePayoutAccount,
} from '../../src/features/model/mypage/payoutAccount.js';
import {
  activityOptions,
  isRegistrationJourney,
  registrationCard,
  tabFromHash,
} from '../../src/features/model/mypage/mypageState.js';
import { nextPayoutLabel, payoutStatementStatusLabel } from '../../src/features/model/mypage/payoutStatements.js';

const seoulSeptember = new Date('2026-08-31T15:01:00Z');

test('사용 월은 유효한 사용일을 서울 월로 묶고 이번 달을 포함해 최신순으로 정렬해요', () => {
  const rows = [
    { id: 'aug', createdAt: '2026-08-31T14:59:00Z' },
    { id: 'sep-1', createdAt: '2026-08-31T15:00:00Z' },
    { id: 'sep-2', createdAt: '2026-09-12T00:00:00Z' },
    { id: 'nov', createdAt: '2026-10-31T15:00:00Z' },
    { id: 'invalid', createdAt: 'not-a-date' },
  ];

  assert.deepEqual(usageMonths(rows, seoulSeptember), ['2026-11', '2026-09', '2026-08']);
  assert.equal(defaultUsageMonth(rows, seoulSeptember), '2026-09');
});

test('선택한 서울 월의 유효한 사용 기록만 최신순으로 돌려줘요', () => {
  const rows = [
    { id: 'older', createdAt: '2026-09-01T00:00:00Z' },
    { id: 'other-month', createdAt: '2026-08-31T14:59:59Z' },
    { id: 'newer', createdAt: '2026-09-30T14:59:59Z' },
    { id: 'invalid', createdAt: 'not-a-date' },
  ];

  assert.deepEqual(rowsForMonth(rows, '2026-09').map(row => row.id), ['newer', 'older']);
  assert.deepEqual(rows.map(row => row.id), ['older', 'other-month', 'newer', 'invalid']);
});

test('사용일과 월 이름은 지정된 표시 형식으로 바꾸고 잘못된 값은 대시로 표시해요', () => {
  assert.equal(usageDate('2026-08-31T15:00:00Z'), '2026.09.01');
  assert.equal(usageDate('not-a-date'), '-');
  assert.equal(monthLabel('2026-09'), '2026년 9월');
});

test('이번 달 정산 문구는 숫자만 강조하고 없는 합계에 0을 만들지 않아요', () => {
  assert.deepEqual(settlementLineParts({ monthAmount: 1_407_000, monthCount: 201 }), [
    { text: '이번 달 정산 금액: ', strong: false },
    { text: '1,407,000', strong: true },
    { text: '원 / ', strong: false },
    { text: '201', strong: true },
    { text: '건', strong: false },
  ]);
  assert.deepEqual(settlementLineParts(null).map(part => part.text), [
    '이번 달 정산 금액: ', '-', '원 / ', '-', '건',
  ]);
});

test('입금 계좌 입력은 공백과 하이픈을 정리한 뒤 은행, 숫자 길이, 예금주를 검증해요', () => {
  const fullAccount = ['110', '123', '456', '789'].join('');
  const input = { bankCode: 'shinhan ', accountNumber: ` ${fullAccount} `, holderName: ' 김서연 ' };
  assert.deepEqual(normalizePayoutAccount(input), {
    bankCode: 'shinhan', accountNumber: fullAccount, holderName: '김서연',
  });
  assert.equal(validatePayoutAccount(input), true);
  assert.equal(validatePayoutAccount({ ...input, bankCode: '999' }), false);
  assert.equal(validatePayoutAccount({ ...input, accountNumber: '123-45ab-678' }), false);
  assert.equal(validatePayoutAccount({ ...input, accountNumber: '1234567' }), false);
  assert.equal(validatePayoutAccount({ ...input, holderName: '   ' }), false);
  assert.equal(validatePayoutAccount({ ...input, holderName: '가'.repeat(41) }), false);
});

test('계좌번호는 앞 세 자리와 뒤 네 자리만 남기고 잘못된 번호는 표시하지 않아요', () => {
  const fullAccount = ['110', '123', '456', '789'].join('');
  const eightDigits = ['1234', '5678'].join('');
  assert.equal(maskAccountNumber(fullAccount), '***-****-6789');
  assert.equal(maskAccountNumber(eightDigits), '***-****-5678');
  assert.equal(maskAccountNumber('1234'), '');
  assert.equal(maskAccountNumber('110-12A-456789'), '');
});

test('입금 계좌 라벨은 서버 마스킹 값을 우선하고 지정된 은행 이름을 사용해요', () => {
  assert.deepEqual(PAYOUT_BANKS, [
    { code: 'shinhan', name: '신한은행' },
    { code: 'kb', name: '국민은행' },
    { code: 'woori', name: '우리은행' },
    { code: 'hana', name: '하나은행' },
    { code: 'nh', name: 'NH농협은행' },
    { code: 'ibk', name: 'IBK기업은행' },
    { code: 'kakao', name: '카카오뱅크' },
    { code: 'toss', name: '토스뱅크' },
  ]);
  assert.equal(payoutAccountLabel({
    bankCode: 'shinhan',
    bankName: '신한은행',
    accountNumber: '110-000-000000',
    accountMasked: '***-****-9999',
    holderName: '김서연',
  }), '신한은행 · ***-****-9999 · 김서연');
});

test('입금 계좌 API를 활성화하고 서버 계약의 은행 코드를 보내요', () => {
  const fullAccount = ['110', '123', '456', '789'].join('');
  assert.equal(PAYOUT_ACCOUNT_API_READY, true);
  assert.equal(validatePayoutAccount({ bankCode: 'shinhan', accountNumber: fullAccount, holderName: '김서연' }), true);
  assert.equal(validatePayoutAccount({ bankCode: '088', accountNumber: fullAccount, holderName: '김서연' }), false);
});

test('다음 지급과 월별 상태를 사람이 읽는 문구로 표시해요', () => {
  assert.equal(nextPayoutLabel({ scheduledFor: '2026-10-10', periodMonth: '2026-09', amount: 1407000 }),
    '다음 지급 10월 10일 · 2026년 9월분 1,407,000원');
  assert.equal(nextPayoutLabel(null), '');
  assert.equal(payoutStatementStatusLabel('scheduled'), '예정');
  assert.equal(payoutStatementStatusLabel('paid'), '지급 완료');
  assert.equal(payoutStatementStatusLabel('held'), '보류');
});

test('등록 진행 화면은 등록 2단계와 검토 모드에만 적용해요', () => {
  assert.equal(isRegistrationJourney({ mode: 'onboarding', step: 2 }), true);
  assert.equal(isRegistrationJourney({ mode: 'onboarding', step: 2, needsApplication: true }), false);
  assert.equal(isRegistrationJourney({ mode: 'review', sub: 'review' }), true);
  assert.equal(isRegistrationJourney({ mode: 'active' }), false);
});

test('등록 진행 카드는 저장, 증서 발급, 검수, 프로필 확정 상태를 화면 문구와 단계에 매핑해요', () => {
  assert.deepEqual(registrationCard({ mode: 'onboarding', step: 2 }, { status: 'photos_pending' }), {
    title: '등록을 이어서 마쳐 주세요.',
    description: '얼굴 사진과 사용 조건을 확인하면 등록이 끝나요.',
    label: '이어서 등록하기',
    to: '/model/register',
    currentStep: 2,
  });
  assert.deepEqual(registrationCard({ mode: 'onboarding', step: 2 }, { status: 'vc_pending' }), {
    title: '라이선스 증서를 발급하고 있어요.',
    description: '발급이 완료되면 내 증서 카드에서 확인할 수 있어요.',
    label: '진행 상황 확인',
    to: '/model/register',
    currentStep: 3,
  });
  assert.deepEqual(registrationCard({ mode: 'review', sub: 'assets' }, { status: 'asset_building' }), {
    title: '사진을 검수하고 있어요.',
    description: '검수가 끝나면 다음 단계를 알려드릴게요.',
    label: '등록 내용 확인',
    to: '/model/register',
    currentStep: 4,
  });
  assert.deepEqual(registrationCard({ mode: 'review', sub: 'confirm' }, null), {
    title: '공개할 테스트컷을 골라 주세요.',
    description: '마음에 드는 컷을 확인하고 공개할 프로필을 정해요.',
    label: '테스트컷 확인하기',
    to: '/model/confirm',
    currentStep: 5,
  });
  assert.equal(registrationCard({ mode: 'onboarding', step: 1 }, null), null);
});

test('활동 관리 옵션은 라이선스와 중단 주체에 따라 정해진 순서로 나와요', () => {
  assert.deepEqual(activityOptions({ mode: 'active', flag: 'none' }, {}), ['pause', 'revoke', 'data']);
  assert.deepEqual(activityOptions({ mode: 'active', flag: 'paused' }, { suspensionSource: 'owner' }), ['resume', 'revoke', 'data']);
  assert.deepEqual(activityOptions({ mode: 'active', flag: 'paused' }, { suspensionSource: 'admin' }), ['contact', 'revoke', 'data']);
  assert.deepEqual(activityOptions({ mode: 'active', flag: 'paused' }, {}), ['contact', 'revoke', 'data']);
  assert.deepEqual(activityOptions({ mode: 'active', flag: 'revoked' }, {}), ['contact', 'data']);
  assert.deepEqual(activityOptions({ mode: 'review', sub: 'review' }, {}), ['contact', 'data']);
});

test('마이페이지 해시는 세 탭과 이전 해시 별칭을 정규화해요', () => {
  assert.equal(tabFromHash('#usage'), 'usage');
  assert.equal(tabFromHash('payout'), 'payout');
  assert.equal(tabFromHash('#license'), 'license');
  assert.equal(tabFromHash('#earnings'), 'payout');
  assert.equal(tabFromHash('#conditions'), 'license');
  assert.equal(tabFromHash('#unknown'), 'usage');
  assert.equal(tabFromHash(''), 'usage');
});
