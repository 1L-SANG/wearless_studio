// 중간 정산 (2026-09-27) — 마감 전인 이번 달을 누른 시각까지 먼저 지급 확인서로.
// 핵심 계약: 지급 완료는 실제 이체 기록이 있을 때만, 스텁(simulated)은 지급 완료라고 말하지 않는다.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  confirmationKindLabel, cutoffLabel, interimRangeLabel, runOutcomeLabel,
} from '../../src/features/admin/adminPayoutStatements.js';
import { interimPayoutLine, payoutDisplayRows, payoutStatementStatusLabel } from '../../src/features/model/mypage/payoutStatements.js';
import { loadEarningsHarness } from './helpers/mypageHarness.mjs';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const text = node => node == null || node === false ? '' : Array.isArray(node) ? node.map(text).join('')
  : typeof node === 'object' ? text(node.props?.children) : String(node);

const interimPaid = {
  id: 'c-interim', periodMonth: '2026-09', kind: 'interim', cutoffAt: '2026-09-27T05:05:00Z', coveredThrough: '2026-09-27',
  status: 'paid', simulated: false, amount: 16000, count: 2, paidAt: '2026-09-30T02:00:00Z', transferredOn: '2026-09-30',
  transferReferenceMasked: '••••0042',
};
const september = {
  periodMonth: '2026-09', open: true, status: 'scheduled', amount: 21000, count: 3, scheduledFor: '2026-10-10',
  unpaidAmount: 5000, unpaidCount: 1, paidAmount: 16000, paidCount: 2, confirmations: [interimPaid],
};

test('중간 정산 기간·기준 시각 표시는 KST 기준이다', () => {
  assert.equal(interimRangeLabel('2026-09', '2026-09-27'), '9/1–9/27분');
  assert.equal(interimRangeLabel('2026-09', null), '');
  assert.equal(cutoffLabel('2026-09-27T05:05:00Z'), '9/27 14:05');
  assert.equal(cutoffLabel(null), '-');
  assert.equal(confirmationKindLabel(interimPaid), '중간 정산 · 9/1–9/27분');
  assert.equal(confirmationKindLabel({ ...interimPaid, kind: 'monthly' }), '');
});

test('실행 결과는 담지 않은 체인 미확정 건수를 함께 말하고 돈이 움직였다고 하지 않는다', () => {
  const line = runOutcomeLabel({ outcome: 'prepared', amount: 16000, count: 2, unconfirmedCount: 1 });
  assert.match(line, /지급 확인서 준비 16,000원 · 2건/);
  assert.match(line, /체인 미확정 1건은 담지 않았어요/);
  assert.doesNotMatch(line, /입금됐|송금 완료|지급 완료/);
  assert.equal(runOutcomeLabel({ outcome: 'nothing_due', unconfirmedCount: 0 }), '새로 지급할 정산이 없어요');
});

test('모델 화면: 실제 이체된 중간 정산은 "중간 정산 · 9/1–9/27분 · 지급 완료(이체일)"', () => {
  const rows = payoutDisplayRows(september);
  assert.equal(rows.length, 2);
  const [interim, remainder] = rows;
  assert.equal(interim.interimLabel, '중간 정산 · 9/1–9/27분');
  assert.equal(interimPayoutLine(interim), '중간 정산 · 9/1–9/27분 · 지급 완료(9월 30일 이체)');
  assert.equal(interim.displayDate, '2026-09-30');
  assert.equal(interim.reference, '••••0042');
  // 남은 몫은 "추가 미지급"이 아니라 달이 끝나면 월말 정산이 모으는 남은 몫이다.
  assert.equal(remainder.remainder, true);
  assert.equal(remainder.amount, 5000);
  assert.equal(payoutStatementStatusLabel(remainder.status), '예정');
});

test('스텁(simulated) 중간 정산은 지급 완료라고 말하지 않는다', () => {
  const [row] = payoutDisplayRows({ ...september, unpaidCount: 0, confirmations: [{ ...interimPaid, simulated: true, transferredOn: null }] });
  assert.equal(row.status, 'simulated');
  assert.equal(interimPayoutLine(row), '중간 정산 · 9/1–9/27분 · 시뮬레이션 · 실제 이체 없음');
  assert.doesNotMatch(interimPayoutLine(row), /지급 완료/);
});

test('진행 중인 중간 정산은 처리 중으로만 보인다', () => {
  const [row] = payoutDisplayRows({ ...september, unpaidCount: 0, confirmations: [{ ...interimPaid, status: 'transfer_started', transferredOn: null, paidAt: null }] });
  assert.equal(interimPayoutLine(row), '중간 정산 · 9/1–9/27분 · 송금 진행 중');
});

test('월말 정산 확인서는 중간 정산 표시가 없다', () => {
  const [row] = payoutDisplayRows({ periodMonth: '2026-08', status: 'paid', unpaidCount: 0, confirmations: [{ ...interimPaid, kind: 'monthly', coveredThrough: null }] });
  assert.equal(row.interimLabel, '');
  assert.equal(interimPayoutLine(row), '');
});

test('정산 탭 요약: 중간 정산 줄 + 지금까지 지급 · 남은 몫', async () => {
  const h = await loadEarningsHarness();
  try {
    const tree = h.module.InterimSummary({ items: [september, { periodMonth: '2026-08', confirmations: [] }] });
    const shown = text(tree);
    assert.match(shown, /중간 정산 · 9\/1–9\/27분 · 지급 완료\(9월 30일 이체\)/);
    assert.match(shown, /2026년 9월 지금까지 지급 16,000원 · 남은 몫 5,000원 \(10월 10일 지급 예정 · 운영자 확인 후 이체\)/);
    assert.doesNotMatch(shown, /2026년 8월/);
    assert.equal(h.module.InterimSummary({ items: [{ ...september, confirmations: [] }] }), null);
  } finally { await h.close(); }
});

test('API 는 이번 달 중간 정산을 ?interim=true 로 부른다', () => {
  const source = read('src/lib/api/facemarket.js');
  assert.match(source, /function adminRunInterimPayout\(periodMonth\)[\s\S]*?payout-statements\/\$\{encodeURIComponent\(periodMonth\)\}\/run\?interim=true`, \{ method: 'POST' \}/);
});
