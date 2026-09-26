// 정산 체인 대조 · 월말 정산 실행 · 실제 이체 기록 (2026-09-26).
// 핵심 계약: "일치"는 서버가 eth_call 로 읽은 판정일 때만, 지급 완료는 실제 이체 기록이 있을 때만.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  chainCheckError, chainCheckedLabel, chainFieldValue, chainVerdict, shortHash,
} from '../../src/lib/settlementChain.js';
import {
  monthOpensOn, runOutcomeLabel, transferRecordBody, currentSeoulMonth,
} from '../../src/features/admin/adminPayoutStatements.js';
import { payoutDisplayRows, payoutStatementStatusLabel } from '../../src/features/model/mypage/payoutStatements.js';
import { loadEarningsHarness, findTree } from './helpers/mypageHarness.mjs';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const text = node => node == null || node === false ? '' : Array.isArray(node) ? node.map(text).join('')
  : typeof node === 'object' ? text(node.props?.children) : String(node);

const MATCH = {
  verdict: 'match', match: true, checkedAt: '2026-09-26T05:03:22Z', method: 'eth_call getSettlement',
  fields: [
    { key: 'total', label: '총액', db: 14900, chain: 14900, match: true },
    { key: 'model', label: '모델 정산 70%', db: 10430, chain: 10430, match: true },
    { key: 'block', label: '기록 블록', db: 4242, chain: 4242, match: true },
    { key: 'modelRef', label: '모델 참조(해시)', db: `0x${'ab'.repeat(32)}`, chain: `0x${'ab'.repeat(32)}`, match: true },
  ],
};

// ── 순수 규칙 ────────────────────────────────────────────────────────────────

test('짧은 해시는 앞 6·뒤 4자리, 빈 값은 대시', () => {
  assert.equal(shortHash(`0x${'1234567890abcdef'.repeat(4)}`), '0x123456…cdef');
  assert.equal(shortHash(''), '-');
  assert.equal(shortHash(null), '-');
});

test('판정 배지는 서버 verdict 에서만 — 모르는 값·없는 값은 판정하지 않는다', () => {
  assert.equal(chainVerdict(MATCH).label, '일치');
  assert.equal(chainVerdict({ verdict: 'mismatch' }).label, '불일치');
  assert.equal(chainVerdict({ verdict: 'not_found' }).label, '체인에 기록 없음');
  assert.equal(chainVerdict({ match: true }), null, 'match 필드만으로는 일치라고 하지 않는다');
  assert.equal(chainVerdict(undefined), null);
  assert.equal(chainVerdict({ verdict: 'ok' }), null);
});

test('조회 실패 문구는 성공처럼 들리지 않는다', () => {
  for (const error of [{ status: 502, code: 'chain_rpc_failed' }, { status: 503 }, { status: 500 }, new Error('x')]) {
    const message = chainCheckError(error);
    assert.ok(message.length > 0);
    assert.doesNotMatch(message, /일치|완료|확인됐/);
  }
  assert.match(chainCheckError({ status: 502, code: 'chain_rpc_failed' }), /응답을 받지 못했어요/);
});

test('비교 칸 값 표기 — 금액·블록·해시', () => {
  assert.equal(chainFieldValue(MATCH.fields[0], 'chain'), '14,900원');
  assert.equal(chainFieldValue(MATCH.fields[2], 'db'), '#4,242');
  assert.equal(chainFieldValue(MATCH.fields[3], 'chain'), '0xababab…abab');
  assert.equal(chainFieldValue({ key: 'total', chain: null }, 'chain'), '-');
  assert.match(chainCheckedLabel(MATCH.checkedAt), /^14:03:22 체인에서 읽음$/);
  assert.equal(chainCheckedLabel('garbage'), '');
});

test('마감 전 달은 실행 시각을, 마감된 달은 null 을', () => {
  const september = new Date('2026-09-26T03:00:00Z');
  assert.equal(currentSeoulMonth(september), '2026-09');
  assert.equal(monthOpensOn('2026-09', september), '2026-10-01');
  assert.equal(monthOpensOn('2026-12', september), '2027-01-01');
  assert.equal(monthOpensOn('2026-08', september), null);
  // KST 자정 경계 — 9/30 15:00Z 는 서울로 10/1 이라 9월은 마감됐다.
  assert.equal(monthOpensOn('2026-09', new Date('2026-09-30T15:00:00Z')), null);
});

test('월말 정산 결과 문구는 돈이 움직였다고 말하지 않는다', () => {
  const outcomes = ['prepared', 'in_progress', 'nothing_due', 'held', 'paid', 'unconfirmed', 'no_account'];
  for (const outcome of outcomes) {
    const label = runOutcomeLabel({ outcome, amount: 16000, count: 2, unconfirmedCount: 1 });
    assert.doesNotMatch(label, /지급 완료|입금(됐|되었|완료)|송금(됐|완료)/, outcome);
  }
  assert.match(runOutcomeLabel({ outcome: 'prepared', amount: 16000, count: 2 }), /참조번호를 기록/);
});

test('이체 기록 폼은 참조번호·금액·이체일이 다 있어야 본문이 된다', () => {
  assert.deepEqual(transferRecordBody({ reference: ' 신한 0042 ', amount: '16,000', date: '2026-10-02' }),
    { transferReference: '신한 0042', amount: 16000, transferredOn: '2026-10-02' });
  assert.equal(transferRecordBody({ reference: '12', amount: '16000', date: '2026-10-02' }), null);
  assert.equal(transferRecordBody({ reference: '신한 0042', amount: '', date: '2026-10-02' }), null);
  assert.equal(transferRecordBody({ reference: '신한 0042', amount: '16000', date: '' }), null);
});

test('스텁이 paid 로 만든 건은 모델 화면에서 지급 완료가 아니다', () => {
  const [row] = payoutDisplayRows({ periodMonth: '2026-08', status: 'paid', unpaidCount: 0, confirmations: [
    { id: 'c1', status: 'paid', simulated: true, paidAt: '2026-09-12T00:00:00Z', transferReferenceMasked: '••••abcd', amount: 7000, count: 1 },
  ] });
  assert.equal(row.status, 'simulated');
  assert.equal(payoutStatementStatusLabel(row.status), '시뮬레이션 · 실제 이체 없음');
  assert.equal(row.displayDate, '-');
  assert.equal(row.reference, null);
});

test('실제 이체로 지급된 건은 이체일과 가린 참조번호를 보인다', () => {
  const [row] = payoutDisplayRows({ periodMonth: '2026-08', status: 'paid', unpaidCount: 0, confirmations: [
    { id: 'c1', status: 'paid', simulated: false, paidAt: '2026-10-02T09:00:00Z', transferredOn: '2026-10-01',
      transferReferenceMasked: '••••0042', amount: 16000, count: 2 },
  ] });
  assert.equal(row.status, 'paid');
  assert.equal(payoutStatementStatusLabel(row.status), '지급 완료');
  assert.equal(row.displayDate, '2026-10-01');
  assert.equal(row.reference, '••••0042');
});

// ── 모델 정산 내역: [체인 확인] ─────────────────────────────────────────────────

test('정산 한 줄은 날짜·상품·금액과 짧은 해시 + [체인 확인]을 보인다', async () => {
  const h = await loadEarningsHarness();
  try {
    const data = { loading: false, statements: { items: [] }, rows: [{
      id: 's1', createdAt: '2026-09-20T03:00:00Z', productName: '린넨 셔츠', modelAmount: 10430,
      txHash: `0x${'cd'.repeat(32)}`,
    }] };
    const tree = h.module.MyPageEarnings({ data, month: '2026-09', onMonthChange() {} });
    const line = findTree(tree, node => node.type === h.module.ChainCheckLine);
    assert.ok(line, '체인 칸이 정산 줄마다 있어야 해요');
    assert.equal(line.props.row.id, 's1');
    assert.match(text(tree), /09\.20/);
    assert.match(text(tree), /린넨 셔츠/);
    assert.match(text(tree), /10,430/);
  } finally { await h.close(); }
});

test('[체인 확인] — 서버가 일치라고 할 때만 일치, 실패는 실패로', async () => {
  const calls = [];
  const h = await loadEarningsHarness({ checkModelSettlementOnChain: async id => { calls.push(id); return MATCH; } });
  try {
    const row = { id: 's1', txHash: `0x${'cd'.repeat(32)}` };
    let tree = h.render(h.module.ChainCheckLine, { row });
    assert.match(text(tree), /0xcdcdcd…cdcd/);
    const button = findTree(tree, node => node.type === 'button');
    assert.equal(text(button), '체인 확인');
    await button.props.onClick();
    tree = h.render(h.module.ChainCheckLine, { row });
    assert.deepEqual(calls, ['s1']);
    assert.match(text(tree), /체인 일치 · 블록 #4,242 · 14:03:22 체인에서 읽음/);
  } finally { await h.close(); }

  const failing = await loadEarningsHarness({
    checkModelSettlementOnChain: async () => { throw Object.assign(new Error('x'), { status: 502, code: 'chain_rpc_failed' }); },
  });
  try {
    const row = { id: 's1', txHash: `0x${'cd'.repeat(32)}` };
    const tree = failing.render(failing.module.ChainCheckLine, { row });
    await findTree(tree, node => node.type === 'button').props.onClick();
    const after = failing.render(failing.module.ChainCheckLine, { row });
    assert.match(text(after), /응답을 받지 못했어요/);
    assert.doesNotMatch(text(after), /일치/);
  } finally { await failing.close(); }
});

test('체인 기록이 아직 없는 줄은 조회 버튼 없이 대기라고만', async () => {
  const h = await loadEarningsHarness();
  try {
    const tree = h.render(h.module.ChainCheckLine, { row: { id: 's2', txHash: null } });
    assert.match(text(tree), /체인 기록 대기/);
    assert.equal(findTree(tree, node => node.type === 'button'), null);
  } finally { await h.close(); }
});

// ── 배선(소스 계약) ─────────────────────────────────────────────────────────────

test('API 는 소유 범위별 체인 조회·월말 정산·이체 기록 엔드포인트를 부른다', () => {
  const source = read('src/lib/api/facemarket.js');
  assert.match(source, /function checkSettlementOnChain\(paymentId\)[\s\S]*?\/v1\/facemarket\/settlements\/\$\{encodeURIComponent\(paymentId\)\}\/chain-check/);
  assert.match(source, /function checkModelSettlementOnChain\(settlementId\)[\s\S]*?\/v1\/facemarket\/model\/settlements\/\$\{encodeURIComponent\(settlementId\)\}\/chain-check/);
  assert.match(source, /function adminCheckSettlementOnChain\(settlementId\)[\s\S]*?\/v1\/facemarket\/admin\/settlements\/\$\{encodeURIComponent\(settlementId\)\}\/chain-check/);
  assert.match(source, /function adminListSettlements\([\s\S]*?\/v1\/facemarket\/admin\/settlements\?limit=/);
  assert.match(source, /function adminRunMonthlyPayout\(periodMonth\)[\s\S]*?payout-statements\/\$\{encodeURIComponent\(periodMonth\)\}\/run`, \{ method: 'POST' \}/);
  assert.match(source, /function adminAdvancePayoutConfirmation\(confirmationId, action, transfer\)[\s\S]*?body: transfer/);
});

test('영수증 모달은 트랜잭션·체인 ID·블록과 [체인에서 확인]을 싣는다', () => {
  const editor = read('src/features/editor/Editor.jsx');
  assert.match(editor, /<SettlementReceiptChain receipt=\{genReceipt\} \/>/);
  const chain = read('src/features/editor/SettlementReceiptChain.jsx');
  assert.match(chain, /checkSettlementOnChain\(receipt\.paymentId\)/);
  assert.match(chain, /'체인에서 확인'/);
  assert.match(chain, /체인 ID/);
  assert.match(chain, /기록 블록/);
  assert.match(chain, /navigator\.clipboard\.writeText\(receipt\.txHash\)/);
  // 배지는 서버 판정에서만 — 조회 실패 분기는 배지를 그리지 않는다.
  assert.match(chain, /check\.status === 'error' && <p className="fm-chain-error"/);
});

test('관리자 콘솔에 체인 검증 화면이 있고 월말 정산·이체 기록이 배선돼 있다', () => {
  const app = read('src/apps/admin/App.jsx');
  assert.match(app, /<Route path="settlements-chain" element=\{<AdminSettlementChain \/>\} \/>/);
  assert.match(read('src/features/admin/AdminShell.jsx'), /to: '\/settlements-chain', label: '체인 검증'/);
  assert.match(read('src/apps/admin/adminReturnTarget.js'), /'\/settlements-chain'/);
  const chain = read('src/features/admin/AdminSettlementChain.jsx');
  assert.match(chain, /adminCheckSettlementOnChain\(item\.id\)/);
  assert.match(chain, /contract\.ruleLines/);
  const payout = read('src/features/admin/AdminPayoutStatements.jsx');
  assert.match(payout, /adminRunMonthlyPayout\(month\)/);
  assert.match(payout, /'월말 정산 실행'/);
  assert.match(payout, /adminAdvancePayoutConfirmation\(confirmation\.id, 'paid', body\)/);
  // 스텁 표시는 그대로 — 지우면 안 된다(services/payout_provider.py 머리말).
  assert.match(payout, /시뮬레이션 · 실제 이체 없음/);
});

test('모델 마이페이지는 자동이체라고 말하지 않는다 — 지급은 운영자 확인 후 이체', () => {
  const dashboard = read('src/features/model/mypage/MyPageDashboard.jsx');
  assert.doesNotMatch(dashboard, /자동이체/);
  assert.match(dashboard, /운영자 확인 후 이체/);
});
