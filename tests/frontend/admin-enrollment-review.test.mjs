/* 등록 심사 콘솔 계약 — 점수는 정보일 뿐 승인/거절을 막지 않고, 마스킹 체크만 승인을
   막는다. 감지 실패(skipped)와 기준 미달(belowThreshold)은 서로 다른 상태라 배지가
   같으면 안 된다. 결정 직후 카드를 닫고 큐를 새로고침한다(신분증 파기 → 재조회 404). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { scoreRow } from '../../src/features/admin/enrollmentReviewMath.js';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');
const source = read('src/features/admin/AdminEnrollmentReview.jsx');

// ── (a) scoreRow: 백분율 + 기준선 + 배수를 모두 낸다 ────────────────────────────

test('scoreRow: 기준의 2배 이상은 통과 배지와 백분율·기준선·배수를 모두 낸다', () => {
  const row = scoreRow('front', 0.31, 0.15);
  assert.equal(row.percent, '31%', '백분율이 반올림 안 맞다');
  assert.equal(row.baseline, '기준 15%', '기준선이 안 나온다');
  assert.equal(row.multiple, '기준의 2.1배', '배수가 안 나온다');
  assert.equal(row.tone, 'ok');
  assert.equal(row.badge, '✓ 통과');
});

test('scoreRow: 기준의 1~2배는 아슬 배지(warn)', () => {
  const row = scoreRow('side', 0.11, 0.10);
  assert.equal(row.tone, 'warn');
  assert.equal(row.badge, '△ 아슬');
  assert.equal(row.multiple, '기준의 1.1배');
  assert.equal(row.percent, '11%');
  assert.equal(row.baseline, '기준 10%');
});

test('scoreRow: 기준 미달(danger)도 백분율은 그대로 보여준다 — 배지만 다르다', () => {
  const row = scoreRow('front', 0.05, 0.15);
  assert.equal(row.tone, 'danger');
  assert.equal(row.badge, '✗ 미달');
  assert.equal(row.percent, '5%', '미달이어도 원점수를 감추면 관리자가 판단 근거를 잃는다');
  assert.equal(row.baseline, '기준 15%');
});

// ── (c) skipped(감지 안 됨)는 belowThreshold(기준 미달)와 다른 상태로 나온다 ───────

test('scoreRow: 점수가 없으면(그 각도에서 얼굴을 못 찾음) muted 톤 — danger 와 같은 배지를 쓰면 안 된다', () => {
  const row = scoreRow('angle45', null, 0.15);
  assert.equal(row.tone, 'muted');
  assert.equal(row.label, '– 대조 안 됨');
  assert.notEqual(row.tone, 'danger', 'skipped 가 danger 와 같은 톤이면 위조 의심과 구분이 안 된다');
  assert.equal(row.percent, undefined, '감지 실패인데 백분율이 나오면 마치 낮은 점수처럼 보인다');
  assert.equal(row.badge, undefined);
});

test('심사 카드는 muted(감지 안 됨) 상태를 danger 와 다른 배지 마크업으로 그린다', () => {
  // ScoreLine 이 row.tone === 'muted' 를 별도로 분기해 회색 안내 배지(label)를 쓰고,
  // ok/warn/danger 는 percent+baseline+ScoreBadge 로 그린다 — 두 분기가 실제로 다른
  // JSX 를 쓰는지 소스에서 확인한다(같은 컴포넌트로 뭉치면 색만 다르고 정보량이 같아져
  // "감지 실패"와 "낮은 점수"를 눈으로 구분할 수 없다).
  const lineIdx = source.indexOf('function ScoreLine');
  assert.ok(lineIdx !== -1, 'ScoreLine 컴포넌트를 못 찾았다');
  const lineBlock = source.slice(lineIdx, source.indexOf('\n}', lineIdx));
  assert.ok(/row\.tone\s*===\s*['"]muted['"]/.test(lineBlock), 'muted 상태를 따로 분기하지 않는다');
  assert.ok(/row\.label/.test(lineBlock), 'muted 분기가 안내 문구(label)를 안 쓴다');
  assert.ok(/row\.percent/.test(lineBlock) && /ScoreBadge/.test(lineBlock), 'muted 가 아닌 분기가 점수·배지를 안 쓴다');
});

// ── (b) 배지는 승인 버튼을 막지 않는다 — 마스킹 체크박스만 막는다 ────────────────────

test('승인 버튼은 마스킹 체크박스로만 막힌다 — 점수 배지/톤으로 막으면 안 된다', () => {
  const approveIdx = source.indexOf('onClick={approve}');
  assert.ok(approveIdx !== -1, '승인 버튼(onClick={approve})을 못 찾았다');
  const blockStart = source.lastIndexOf('<Button', approveIdx);
  const blockEnd = source.indexOf('>', approveIdx);
  const buttonBlock = source.slice(blockStart, blockEnd);
  assert.ok(/disabled=\{[^}]*maskOk[^}]*\}/.test(buttonBlock), '승인 버튼이 maskOk 로 안 막힌다');
  assert.ok(
    !/disabled=\{[^}]*(tone|badge|scoreRow|matchScore)/i.test(buttonBlock),
    `승인 버튼이 점수 배지/톤으로 막힌다(위조 신분증도 점수가 높을 수 있어 배지는 정보일 뿐이어야 한다): ${buttonBlock}`,
  );
});

test('거절 확정 버튼도 사유로만 막힌다 — 점수 배지로 막으면 안 된다', () => {
  const rejectIdx = source.indexOf('onClick={reject}');
  assert.ok(rejectIdx !== -1, '거절 확정 버튼(onClick={reject})을 못 찾았다');
  const blockStart = source.lastIndexOf('<Button', rejectIdx);
  const blockEnd = source.indexOf('>', rejectIdx);
  const buttonBlock = source.slice(blockStart, blockEnd);
  assert.ok(/disabled=\{[^}]*finalReason[^}]*\}/.test(buttonBlock), '거절 버튼이 사유(finalReason)로 안 막힌다');
  assert.ok(
    !/disabled=\{[^}]*(tone|badge|scoreRow|matchScore)/i.test(buttonBlock),
    `거절 버튼이 점수 배지/톤으로 막힌다: ${buttonBlock}`,
  );
});

test('마스킹 확인 문구가 실제로 화면에 있다', () => {
  assert.ok(source.includes('주민등록번호 뒷자리가 가려져 있어요'), '마스킹 확인 체크박스 문구가 없다');
  assert.ok(/type="checkbox"[\s\S]{0,80}checked=\{maskOk\}/.test(source), '체크박스가 maskOk state 를 안 쓴다');
});

// ── 거절 사유: 프리셋 + 자유 입력, 빈 사유 금지 ───────────────────────────────────

test('거절 사유 프리셋 5종(신분증-사진 불일치/판독 불가/마스킹 미이행/위조 의심/기타)이 있다', () => {
  for (const label of ['신분증-사진 불일치', '신분증 판독 불가', '마스킹 미이행', '위조 의심', '기타']) {
    assert.ok(source.includes(label), `거절 사유 프리셋 누락: ${label}`);
  }
});

test('기타 사유는 자유 입력(Textarea)을 받고, 빈 사유로는 거절할 수 없다', () => {
  assert.ok(source.includes('@/components/admin-ui/textarea.jsx'), '자유 입력에 Textarea 를 안 쓴다');
  assert.ok(/disabled=\{busy \|\| !finalReason\}/.test(source), '빈 사유로 거절 확정이 눌린다');
});

// ── 결정 직후: 카드를 닫고 큐를 새로고침 ───────────────────────────────────────

test('결정(승인/거절) 직후 카드를 닫고 큐를 새로고침한다 — 신분증이 파기돼 재조회는 404 다', () => {
  const idx = source.indexOf('const handleDecided');
  assert.ok(idx !== -1, 'handleDecided 콜백을 못 찾았다');
  const block = source.slice(idx, idx + 200);
  assert.ok(/setSelectedId\(null\)/.test(block), '결정 후 카드를 안 닫는다');
  assert.ok(/load\(\)/.test(block), '결정 후 큐를 안 새로고침한다');
  assert.ok(source.includes('onDecided()'), 'approve/reject 성공 경로가 onDecided 를 안 부른다');
});

test('승인 응답의 assetBuildError 를 성공과 다르게 알린다', () => {
  const idx = source.indexOf('const approve = async');
  assert.ok(idx !== -1, 'approve 핸들러를 못 찾았다');
  const block = source.slice(idx, source.indexOf('const reject = async', idx));
  assert.ok(block.includes('assetBuildError'), 'assetBuildError 를 안 읽는다');
  assert.ok(/if\s*\(result\?\.assetBuildError\)/.test(block), 'assetBuildError 분기가 없다 — 실패가 성공처럼 보인다');
});

// ── API 클라이언트: 기존 4종 + 신규 이미지 fetch 1종 ─────────────────────────────

test('API 클라이언트 함수를 그대로 호출한다(신규 요청 코드를 새로 안 쓴다)', () => {
  for (const fn of [
    'adminListEnrollments', 'adminEnrollmentCard', 'adminApproveEnrollment',
    'adminRejectEnrollment', 'adminFetchEnrollmentImageUrl',
  ]) {
    assert.ok(source.includes(fn), `호출이 없다: ${fn}`);
  }
  const api = read('src/lib/api/facemarket.js');
  assert.ok(api.includes('export async function adminFetchEnrollmentImageUrl'), '이미지 fetch 클라이언트 함수가 없다');
});

test('이미지 4종(신분증/정면/45도/측면)을 다 그리고, objectURL 을 해제한다', () => {
  for (const kind of ['id_document', 'front', 'angle45', 'side']) {
    assert.ok(source.includes(`'${kind}'`), `이미지 종류 누락: ${kind}`);
  }
  assert.ok(source.includes('URL.revokeObjectURL'), 'objectURL 을 해제하지 않는다 — 생체 이미지가 새어 남는다');
});

test('카드를 바꾸면 EnrollmentDetail 이 key 로 완전히 새로 마운트된다 — 마스킹 체크가 새어 들어가면 안 된다', () => {
  assert.ok(
    /<EnrollmentDetail\s+key=\{selectedId\}/.test(source),
    'EnrollmentDetail 에 key={selectedId} 가 없다 — 이전 카드의 maskOk 가 다음 카드로 넘어갈 수 있다',
  );
});

// ── 내비게이션·라우트 ───────────────────────────────────────────────────────────

test('AdminShell 내비게이션에 등록 심사가 있고 App.jsx 가 /review 라우트로 연결한다', () => {
  const shell = read('src/features/admin/AdminShell.jsx');
  assert.ok(shell.includes('등록 심사'), '내비 라벨이 없다');
  assert.ok(shell.includes("to: '/review'"), '/review 내비 경로가 없다');
  const app = read('src/apps/admin/App.jsx');
  assert.ok(app.includes('AdminEnrollmentReview'), 'App.jsx 가 새 화면을 안 쓴다');
  assert.ok(app.includes('path="review"'), 'App.jsx 에 review 라우트가 없다');
});
