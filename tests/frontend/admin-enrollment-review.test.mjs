/* 등록 심사 콘솔 계약 — 점수는 정보일 뿐 승인/거절을 막지 않고, 마스킹 체크만 승인을
   막는다. 감지 실패(skipped)와 기준 미달(belowThreshold)은 서로 다른 상태라 배지가
   같으면 안 된다. 결정 직후 카드를 닫고 큐를 새로고침한다(신분증 파기 → 재조회 404). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  finalRejectReason, imageFailureLabel, scoreRow,
} from '../../src/features/admin/enrollmentReviewMath.js';

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

test('scoreRow: 기준값이 없으면(방어적 — 오늘 백엔드는 항상 세 각도를 채운다) NaN 대신 muted 로 낮춘다', () => {
  // fix round 1, minor: thresholds 맵에 그 각도가 없으면 ratio = score/undefined = NaN 이
  // 배지·배수 문구에 그대로 새어 나갈 뻔했다 — score 는 있는데 threshold 만 없는 경우도
  // score 자체가 없는 경우(skipped)와 같은 안전한 muted 로 접는다.
  const row = scoreRow('front', 0.31, null);
  assert.equal(row.tone, 'muted');
  assert.ok(!JSON.stringify(row).includes('NaN'), 'NaN 이 그대로 심사자 화면에 새어 나간다');
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
//
// fix round 1: 이전 버전은 disabled 조건에 tone/badge/scoreRow/matchScore 라는 이름이
// "없는지"만 봤다(부정 목록) — `const anyDanger = ...` 처럼 이름을 바꿔 배지 판정을
// 숨기면 그대로 통과했다. 여기서는 disabled 표현식에 쓰인 식별자를 전부 뽑아 허용
// 목록에만 있는지(긍정 형태) 확인한다 — 이름을 뭐라 지어도 허용 목록 밖이면 잡힌다.

function disabledExprOf(buttonBlock) {
  const m = /disabled=\{([^}]*)\}/.exec(buttonBlock);
  return m ? m[1] : null;
}

function identifiersIn(expr) {
  // `card.matchScores` 같은 멤버 접근도 하나의 단위로 잡는다 — `card` 만 허용해 두고
  // `.matchScores` 접근을 못 보는 구멍을 막는다.
  return Array.from(expr.matchAll(/[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*/g)).map((m) => m[0]);
}

test('승인 버튼의 disabled 조건은 오직 maskOk·busy 로만 이루어진다(긍정 형태 검사)', () => {
  const approveIdx = source.indexOf('onClick={approve}');
  assert.ok(approveIdx !== -1, '승인 버튼(onClick={approve})을 못 찾았다');
  const buttonBlock = source.slice(source.lastIndexOf('<Button', approveIdx), source.indexOf('>', approveIdx));
  const expr = disabledExprOf(buttonBlock);
  assert.ok(expr, '승인 버튼에 disabled 조건이 없다');
  const idents = identifiersIn(expr);
  assert.ok(idents.length > 0, 'disabled 조건에서 식별자를 못 찾았다');
  for (const id of idents) {
    assert.ok(
      id === 'maskOk' || id === 'busy',
      `승인 버튼의 disabled 조건에 허용되지 않은 식별자가 있다(위조 신분증도 점수가 높을 수 있어 배지는 정보일 뿐이어야 한다): ${id} — 전체: ${expr}`,
    );
  }
  assert.ok(idents.includes('maskOk'), '승인 버튼이 maskOk 를 안 쓴다');
});

test('거절 확정 버튼의 disabled 조건은 오직 finalReason·busy 로만 이루어진다(긍정 형태 검사)', () => {
  const rejectIdx = source.indexOf('onClick={reject}');
  assert.ok(rejectIdx !== -1, '거절 확정 버튼(onClick={reject})을 못 찾았다');
  const buttonBlock = source.slice(source.lastIndexOf('<Button', rejectIdx), source.indexOf('>', rejectIdx));
  const expr = disabledExprOf(buttonBlock);
  assert.ok(expr, '거절 버튼에 disabled 조건이 없다');
  const idents = identifiersIn(expr);
  assert.ok(idents.length > 0, 'disabled 조건에서 식별자를 못 찾았다');
  for (const id of idents) {
    assert.ok(
      id === 'finalReason' || id === 'busy',
      `거절 버튼의 disabled 조건에 허용되지 않은 식별자가 있다: ${id} — 전체: ${expr}`,
    );
  }
  assert.ok(idents.includes('finalReason'), '거절 버튼이 finalReason 을 안 쓴다');
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

test('finalRejectReason: 기타 사유는 trim 한다 — 공백만 입력하면 빈 사유로 취급해야 한다', () => {
  // fix round 1, IMPORTANT: !finalReason 가드는 소스 정규식이 아니라 이 함수의 실제
  // 반환값으로 잠근다. trim() 이 빠지면 "   "(공백 3개)는 truthy 라 가드를 통과해,
  // 빈 것이나 다름없는 사유가 그대로 누군가의 거절 기록에 남는다.
  assert.equal(finalRejectReason('other', '   ', '기타'), '', '공백만 입력한 자유 사유가 trim 안 된 채로 통과한다');
  assert.equal(finalRejectReason('other', '  진짜 사유  ', '기타'), '진짜 사유');
  assert.equal(finalRejectReason('id_mismatch', '', '신분증-사진 불일치'), '신분증-사진 불일치');
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
    'adminRejectEnrollment', 'adminFetchGatedImageUrl', 'adminFetchApplicationPhotoUrl',
  ]) {
    assert.ok(source.includes(fn), `호출이 없다: ${fn}`);
  }
  const api = read('src/lib/api/facemarket.js');
  assert.ok(api.includes('export async function adminFetchGatedImageUrl'), '이미지 fetch 클라이언트 함수가 없다');
});

test('이미지 4종(신분증/정면/45도/측면)을 다 그리고, objectURL 을 해제한다', () => {
  for (const kind of ['id_document', 'front', 'angle45', 'side']) {
    assert.ok(source.includes(`'${kind}'`), `이미지 종류 누락: ${kind}`);
  }
  assert.ok(source.includes('URL.revokeObjectURL'), 'objectURL 을 해제하지 않는다 — 생체 이미지가 새어 남는다');
});

test('이미지는 카드 응답의 images 맵을 그대로 쓴다 — enrollmentId+kind 를 다시 조립하지 않는다', () => {
  // fix round 1, minor: 서버가 이미 만들어 준 경로(card.images[kind])를 버리고 프런트가
  // `/v1/facemarket/admin/enrollments/{id}/images/{kind}` 를 다시 조립하면, 서버가 그
  // 프리픽스를 바꿀 때 두 곳을 나란히 고쳐야 하는 드리프트 위험이 생긴다.
  assert.ok(/imagePath=\{card\.images\?\.\[kind\]\}/.test(source), 'EnrollmentImage 가 card.images 를 안 쓴다');
  assert.ok(
    !/adminFetchGatedImageUrl\([^)]*enrollmentId/.test(source),
    '이미지 fetch 가 여전히 enrollmentId 로 URL 을 재조립한다',
  );
});

test('지원서 프로필 사진은 새 이미지 라우트를 만들지 않고 기존 관리자 지원서 사진 라우트를 재사용한다', () => {
  // fix round 1, SPEC GAP 2: 프로필 사진은 지원~등록 사이 인물 스왑을 잡는 제3의
  // 독립 얼굴 사진이다 — application_id 가 있을 때만 시도한다(지원서 없이 등록될 수
  // 있다).
  assert.ok(source.includes('function ApplicationProfilePhoto'), 'ApplicationProfilePhoto 컴포넌트가 없다');
  assert.ok(
    /adminFetchApplicationPhotoUrl\(applicationId,\s*'profile'\)/.test(source),
    "kind='profile' 로 기존 지원서 사진 라우트를 안 부른다",
  );
  // ApplicantPhoto(AdminApplications.jsx)와 같은 관례: 슬롯 자체는 지원서가 있으면
  // (applicationId) 늘 그리고, hasPhoto prop 이 fetch 를 걸지 말지만 결정한다 — 사진
  // 없는 지원서마다 헛된 요청+404 를 걸지 않는다.
  assert.ok(
    /<ApplicationProfilePhoto\s+applicationId=\{card\.applicationId\}\s+hasPhoto=\{!!app\?\.hasProfileImage\}/.test(source),
    'ApplicationProfilePhoto 가 hasPhoto={!!app?.hasProfileImage} 를 안 받는다',
  );
  const photoIdx = source.indexOf('function ApplicationProfilePhoto');
  const photoBlock = source.slice(photoIdx, source.indexOf('\n}', photoIdx));
  assert.ok(
    /if\s*\(!hasPhoto\)\s*return/.test(photoBlock),
    'ApplicationProfilePhoto 가 hasPhoto 를 안 보고 무턱대고 fetch 를 건다 — 사진 없는 지원서마다 헛된 요청+404 가 난다',
  );
});

test('identityMismatchCount 를 카드에 낸다 — mid 전용이 아니라는 걸 잊지 않는다', () => {
  // fix round 1, SPEC GAP 1(correction): 게이트는 identity_method 가 아니라
  // fm_application_required + application_id 뿐이라 simple_auth 등록도 이 카운터가
  // 오른다. 0 회는 강조할 신호가 아니라서 >0 일 때만 보여줘야 한다.
  assert.ok(source.includes('identityMismatchCount'), 'identityMismatchCount 를 안 쓴다');
  assert.ok(/\{app\.identityMismatchCount > 0 &&/.test(source), '0회여도 항상 보이면 신호가 무뎌진다 — >0 가드가 없다');
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

// ── 기기 게이트(C4) · 실패 상태(I10) ────────────────────────────────────────────

test('403(기기 미승인)을 "파기됨"으로 그리지 않는다 — 존재하는 증거를 없다고 믿게 만든다', () => {
  assert.equal(imageFailureLabel(403, 'id_document'), '권한 없음 (기기 미승인)');
  assert.equal(imageFailureLabel(404, 'id_document'), '볼 수 없음 (파기됨)');
  assert.equal(imageFailureLabel(404, 'front'), '볼 수 없음 (없음)');
  // 상태를 모르는 실패(네트워크 등)를 "파기"로 단정하면 안 된다.
  assert.equal(imageFailureLabel(0, 'id_document'), '불러오지 못했어요');
  assert.equal(imageFailureLabel(500, 'id_document'), '불러오지 못했어요');
});

test('EnrollmentImage 는 status 를 들고 실패 문구를 고른다(failed 불리언으로 뭉개지 않는다)', () => {
  assert.ok(source.includes('imageFailureLabel(failedStatus, kind)'), '실패 문구가 status 를 안 본다');
  assert.ok(!source.includes("' (파기됨)' : ''"), '파기됨을 kind 만으로 단정하던 옛 분기가 남아 있다');
});

test('ApplicationProfilePhoto 는 실패를 삼키지 않는다 — 스켈레톤이 영원히 돌면 안 된다', () => {
  // 최종리뷰 I10: `.catch(() => {})` 는 실패 플래그를 안 세워 Skeleton 이 무한히 돈다.
  // 기기 게이트가 enforce 인 프로덕션에서는 그게 기본 상태였다.
  const start = source.indexOf('function ApplicationProfilePhoto(');
  assert.ok(start > 0, 'ApplicationProfilePhoto 정의를 못 찾았다');
  const body = source.slice(start, source.indexOf('function EnrollmentDetail(', start));
  assert.ok(!/\.catch\(\(\) => \{\}\)/.test(body), '실패를 빈 catch 로 삼킨다');
  assert.ok(/setFailedStatus\(e\?\.status \|\| 0\)/.test(body), '실패 상태를 기록하지 않는다');
  assert.ok(body.includes('imageFailureLabel('), '실패 화면을 안 그린다');
});

test('_authFetch 가 X-Admin-Device 헤더를 싣는다 — 없으면 심사 화면의 모든 이미지가 403', () => {
  const api = read('src/lib/api/facemarket.js');
  assert.ok(
    /import \{[^}]*DEVICE_HEADER[^}]*\} from '@\/lib\/adminDevice\.js'/.test(api),
    'adminDevice 에서 DEVICE_HEADER 를 안 가져온다',
  );
  const start = api.indexOf('async function _authFetch(');
  assert.ok(start > 0, '_authFetch 정의를 못 찾았다');
  const body = api.slice(start, api.indexOf('async function _gatedImageUrl(', start));
  assert.ok(/readDeviceToken\(\)/.test(body), '_authFetch 가 기기 토큰을 읽지 않는다');
  assert.ok(/\[DEVICE_HEADER\]: deviceToken/.test(body), '_authFetch 가 기기 헤더를 안 싣는다');
});

test('게이트 이미지 fetch 는 status·code 를 에러에 싣고 device_* 403 이면 복구 이벤트를 쏜다', () => {
  const api = read('src/lib/api/facemarket.js');
  const start = api.indexOf('async function _gatedImageUrl(');
  assert.ok(start > 0, '_gatedImageUrl 정의를 못 찾았다');
  const body = api.slice(start, api.indexOf('async function checkedJson(', start));
  assert.ok(/error\.status = res\.status;/.test(body), '호출부가 403/404 를 구분할 수 없다');
  assert.ok(/DEVICE_REJECTED_EVENT/.test(body), '기기 거절 이벤트를 안 쏜다 — RequireDevice 가 복구를 못 한다');
  // 두 이미지 라우트가 같은 헬퍼를 쓴다(지원서 사진도 프로덕션에서 같은 이유로 깨져 있었다).
  assert.ok(/adminFetchApplicationPhotoUrl[\s\S]{0,200}_gatedImageUrl\(/.test(api));
  assert.ok(/adminFetchGatedImageUrl[\s\S]{0,200}_gatedImageUrl\(/.test(api));
});
