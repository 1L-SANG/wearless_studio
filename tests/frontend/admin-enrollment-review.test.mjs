/* 등록 심사 콘솔 계약 — 점수는 정보일 뿐 승인/거절을 막지 않고, 마스킹 체크만 승인을
   막는다. 감지 실패(skipped)와 기준 미달(belowThreshold)은 서로 다른 상태라 배지가
   같으면 안 된다. 결정 직후 카드를 닫고 큐를 새로고침한다(신분증 파기 → 재조회 404). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  finalRejectReason, imageFailureLabel, scoreRow, reviewActions,
} from '../../src/features/admin/enrollmentReviewMath.js';
import { PHOTO_GROUPS, SLOTS } from '../../src/features/model/registerSlots.js';
import { seoulDateTime } from '../../src/lib/datetime.js';
import { findTree } from './helpers/facemarketHarness.mjs';

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

test('승인 버튼의 disabled 조건은 오직 identityOk·busy 로만 이루어진다(긍정 형태 검사)', () => {
  const approveIdx = source.indexOf('onClick={approve}');
  assert.ok(approveIdx !== -1, '승인 버튼(onClick={approve})을 못 찾았다');
  const buttonBlock = source.slice(source.lastIndexOf('<Button', approveIdx), source.indexOf('>', approveIdx));
  const expr = disabledExprOf(buttonBlock);
  assert.ok(expr, '승인 버튼에 disabled 조건이 없다');
  const idents = identifiersIn(expr);
  assert.ok(idents.length > 0, 'disabled 조건에서 식별자를 못 찾았다');
  for (const id of idents) {
    assert.ok(
      id === 'identityOk' || id === 'busy' || id === 'canApproveIdentity',
      `승인 버튼의 disabled 조건에 허용되지 않은 식별자가 있다(위조 신분증도 점수가 높을 수 있어 배지는 정보일 뿐이어야 한다): ${id} — 전체: ${expr}`,
    );
  }
  assert.ok(idents.includes('identityOk'), '승인 버튼이 identityOk 를 안 쓴다');
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

test('동일인 확인 문구가 실제로 화면에 있다', () => {
  assert.ok(source.includes('신분증 사진과 등록 사진이 같은 사람이에요'), '마스킹 확인 체크박스 문구가 없다');
  assert.ok(/type="checkbox"[\s\S]{0,80}checked=\{identityOk\}/.test(source), '체크박스가 identityOk state 를 안 쓴다');
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
    'EnrollmentDetail 에 key={selectedId} 가 없다 — 이전 카드의 identityOk 가 다음 카드로 넘어갈 수 있다',
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
    // 경로는 '@/lib/adminDevice.js' 든 '../adminDevice.js' 든 상관없다 — 가져오는지가 요점이다
    // (configFile:false 하네스 때문에 실제 코드는 상대 경로를 쓴다).
    /import \{[^}]*DEVICE_HEADER[^}]*\} from '[^']*adminDevice\.js'/.test(api),
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

// ── Task9: mask_mode 배지 + 인증된 신원을 카드 사진 옆에 ─────────────────────────

test('수동 마스킹 건에 배지가 붙는다', () => {
  assert.match(source, /maskMode/, '기하 검증을 못 거친 건을 구분해야 한다');
  assert.match(source, /수동 마스킹/);
});

test('인증된 신원이 카드 사진 옆에 온다', () => {
  assert.match(source, /idDocumentWithIdentity|identityBeside/, '대조가 한눈에 되게 붙여 놔야 한다');
});

test('배지는 maskMode 가 auto 가 아닐 때만 뜬다 — auto 는 서버가 이미 확인했다는 뜻이다', () => {
  // fix: null(검사 자체가 안 돎)도 auto 가 아니므로 배지가 떠야 한다 — "확인 안 됨" 을
  // "확인해 통과함" 처럼 조용히 넘기면 안 된다는 브리핑의 요구를 긍정 형태로 잠근다.
  assert.match(source, /card\.maskMode\s*!==\s*['"]auto['"]/, "배지 조건이 maskMode !== 'auto' 가 아니다");
});

test('신원 블록이 인증된 이름·출생연도를 둘 다 낸다(지원서 자기신고가 아니라 캐리어 증명 값)', () => {
  const idx = source.indexOf('idDocumentWithIdentity = (');
  assert.ok(idx !== -1, 'idDocumentWithIdentity 블록을 못 찾았다');
  const block = source.slice(idx, source.indexOf('return (', idx));
  assert.ok(/card\.identityNameMasked/.test(block), '인증된 이름(identityNameMasked)을 안 쓴다');
  assert.ok(/card\.identityBirthYear/.test(block), '인증된 출생연도(identityBirthYear)를 안 쓴다');
});


test('신원 승인은 조건 제출 뒤 열리고 옛 심사 대기도 계속 처리한다', () => {
  for (const status of ['asset_building', 'license_pending']) {
    const state = reviewActions({ status, reviewStatus: 'pending' });
    assert.equal(state.canApproveIdentity, false);
    assert.equal(state.approvalHint, '등록자가 사용 조건을 마치면 승인할 수 있어요.');
    assert.equal(state.identityCleared, false);
  }
  const ready = reviewActions({ status: 'vc_pending', reviewStatus: 'pending' });
  assert.equal(ready.canApproveIdentity, true);
  assert.equal(ready.approvalLabel, '승인하고 증서 발급');
  assert.equal(reviewActions({ status: 'review_pending', reviewStatus: 'pending' }).approvalLabel, '승인');
  assert.equal(reviewActions({ status: 'review_pending', reviewStatus: 'pending' }).canApproveIdentity, true);
  assert.equal(reviewActions({ status: 'failed', reviewStatus: 'pending' }).canApproveIdentity, false);
});

test('사진 확인은 신원 승인 뒤에만 열리고 이유를 버튼 옆에 표시한다', () => {
  for (const reviewStatus of [null, 'approved']) assert.equal(reviewActions({ reviewStatus }).identityCleared, true);
  for (const reviewStatus of ['pending', 'rejected', 'unknown']) assert.equal(reviewActions({ reviewStatus }).identityCleared, false);
  const index = source.indexOf('onClick={approvePhotos}');
  const button = source.slice(source.lastIndexOf('<Button', index), source.indexOf('>', index));
  assert.match(button, /disabled=\{busy \|\| !identityCleared\}/);
  assert.ok(source.includes('신원 확인을 먼저 마쳐 주세요.'));
  assert.ok(source.includes('신원 확인을 마쳤어요. 증서는 몇 분 안에 자동으로 발급돼요.'));
  assert.ok(source.includes('ENROLLMENT_STATUS_LABEL[row.status]'));
});

// 실제 화면과 상세 카드를 메모리에서 실행해요. 이미지 컴포넌트는 실행하지 않아요.
async function enrollmentReviewHarness(api) {
  const { transformWithEsbuild } = await import('vite');
  const { code } = await transformWithEsbuild(source
    .replace(/^import[\s\S]*?from '[^']+';\n/gm, '')
    .replace(/^export default .+;\n?/gm, '')
    .replace(/^export /gm, ''), 'AdminEnrollmentReview.jsx', { jsx: 'transform' });
  let active;
  let cursor;
  let effects = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const bindings = {
    React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
    useState(initial) {
      const frame = active;
      const index = cursor++;
      if (!(index in frame)) frame[index] = typeof initial === 'function' ? initial() : initial;
      return [frame[index], value => { frame[index] = typeof value === 'function' ? value(frame[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in active)) active[index] = { current: initial };
      return active[index];
    },
    useCallback(value, deps) {
      const index = cursor++;
      if (!same(active[index]?.deps, deps)) active[index] = { value, deps };
      return active[index].value;
    },
    useEffect(effect, deps) {
      const frame = active;
      const index = cursor++;
      if (same(frame[index]?.deps, deps)) return;
      const previous = frame[index];
      frame[index] = { deps };
      effects.push(() => {
        previous?.cleanup?.();
        frame[index].cleanup = effect();
      });
    },
    useToast: () => ({ push() {} }),
    useSearchParams: () => [new URLSearchParams(), () => {}],
    styles: {}, PHOTO_GROUPS, SLOTS, seoulDateTime,
    finalRejectReason, imageFailureLabel, scoreRow, reviewActions,
    ...Object.fromEntries([
      'Badge', 'Button', 'Card', 'CardContent', 'CardDescription', 'CardHeader', 'CardTitle',
      'Skeleton', 'Textarea', 'Table', 'TableBody', 'TableCell', 'TableHead', 'TableHeader', 'TableRow',
    ].map(name => [name, name])),
    ...api,
  };
  const { AdminEnrollmentReview, EnrollmentDetail } = new Function(...Object.keys(bindings),
    `${code}; return { AdminEnrollmentReview, EnrollmentDetail };`)(...Object.values(bindings));
  const pageFrame = [];
  let detailFrame = [];
  let detailKey;
  function render() {
    active = pageFrame;
    cursor = 0;
    const page = AdminEnrollmentReview();
    const selected = findTree(page, node => node.type === EnrollmentDetail);
    if (selected?.props.key !== detailKey) {
      for (const hook of detailFrame) hook?.cleanup?.();
      detailFrame = [];
      detailKey = selected?.props.key;
    }
    active = detailFrame;
    cursor = 0;
    const detail = selected ? EnrollmentDetail(selected.props) : null;
    const pendingEffects = effects;
    effects = [];
    for (const effect of pendingEffects) effect();
    return { page, detail };
  }
  return {
    render,
    async flush() {
      for (let i = 0; i < 4; i += 1) {
        render();
        await new Promise(resolve => setImmediate(resolve));
      }
      return render();
    },
  };
}

const nodeText = node => Array.isArray(node)
  ? node.map(nodeText).join('')
  : node && typeof node === 'object' ? nodeText(node.props?.children) : String(node ?? '');
const buttonNamed = (tree, label) => findTree(tree, node => node.type === 'Button' && nodeText(node) === label);
const identityCheckbox = tree => findTree(tree, node => node.type === 'input' && node.props.type === 'checkbox');
const queueRow = tree => findTree(tree, node => node.type === 'TableRow' && node.props.role === 'button');
const enrollmentFixture = (overrides = {}) => ({
  id: 'enrollment-1', identityMethod: 'simple_auth', reviewStatus: 'pending', status: 'license_pending',
  createdAt: '2026-09-25T00:00:00Z', photoReviewStatus: 'pending', fullPhotosVisible: false,
  maskMode: 'auto', images: {}, application: null, applicationId: null, matchScores: null,
  identityNameMasked: '김*나', identityBirthYear: '1995', reviewedAt: null, reviewReason: null,
  ...overrides,
});

for (const [query, expectedTab, expectedRequest] of [
  ['tab=photos', '사진 확인', 'photos:awaiting'],
  ['', '대기', 'identity:pending'],
  ['tab=unknown', '대기', 'identity:pending'],
]) {
  test(`관리자 등록 심사 링크 ${query || '(기본)'}는 ${expectedTab} 탭을 열어요`, async () => {
    const requests = [];
    const harness = await enrollmentReviewHarness({
      useSearchParams: () => [new URLSearchParams(query), () => {}],
      adminListPhotoReview: async (status) => { requests.push(`photos:${status}`); return []; },
      adminListEnrollments: async (status) => { requests.push(`identity:${status}`); return []; },
    });
    const { page } = await harness.flush();
    assert.equal(buttonNamed(page, expectedTab).props.variant, 'default');
    assert.deepEqual(requests, [expectedRequest]);
  });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const photoReviewSection = tree => findTree(tree, node => node.type?.name === 'PhotoReviewSection');

// 같은 경로의 타입과 key가 유지돼야 React가 입력과 사진 영역을 다시 마운트하지 않아요.
function elementLocation(tree, target, path = []) {
  if (!tree || typeof tree !== 'object') return null;
  const here = [...path, Array.isArray(tree) ? 'array' : [tree.type, tree.props?.key]];
  if (tree === target) return here;
  const children = Array.isArray(tree) ? tree : tree.props?.children || [];
  for (let index = 0; index < children.length; index += 1) {
    const found = elementLocation(children[index], target, [...here, index]);
    if (found) return found;
  }
  return null;
}

for (const trigger of ['새로고침', '사진 확인 뒤 재조회']) {
  for (const staleResult of ['성공', '실패']) {
    test(`상세 요청 역전: ${trigger}의 늦은 ${staleResult} 응답이 최신 승인 상태를 덮지 않아요`, async () => {
      let enrollment = enrollmentFixture({ fullPhotosVisible: true });
      const stale = deferred();
      const latest = deferred();
      const responses = [Promise.resolve(enrollment), stale.promise, latest.promise];
      const harness = await enrollmentReviewHarness({
        adminListEnrollments: async () => [enrollment],
        adminEnrollmentCard: () => responses.shift(),
      });
      let { page } = await harness.flush();
      queueRow(page).props.onClick();
      let { detail } = await harness.flush();
      identityCheckbox(detail).props.onChange({ target: { checked: true } });
      ({ page, detail } = harness.render());
      const reload = () => trigger === '새로고침'
        ? buttonNamed(page, '새로고침').props.onClick()
        : photoReviewSection(detail).props.onChanged();
      reload();
      ({ page, detail } = await harness.flush());
      const oldEnrollment = enrollment;
      enrollment = { ...enrollment, status: 'vc_pending' };
      // 두 종류의 재조회가 같은 요청 순서 보호를 사용해야 해요.
      buttonNamed(page, '새로고침').props.onClick();
      ({ page, detail } = await harness.flush());
      latest.resolve(enrollment);
      ({ page, detail } = await harness.flush());
      assert.equal(buttonNamed(detail, '승인하고 증서 발급').props.disabled, false);
      if (staleResult === '성공') stale.resolve(oldEnrollment);
      else stale.reject(new Error('이전 요청이 실패했어요.'));
      ({ page, detail } = await harness.flush());
      assert.equal(identityCheckbox(detail)?.props.checked, true);
      assert.equal(buttonNamed(detail, '승인하고 증서 발급')?.props.disabled, false);
      assert.equal(nodeText(detail).includes('이전 요청이 실패했어요.'), false);
      assert.equal(nodeText(queueRow(page)).includes('신원 확인 대기'), true);
    });
  }
}

test('상세 재조회 실패와 재시도 중에도 거절 입력과 사진 영역을 같은 위치에 유지해요', async () => {
  const enrollment = enrollmentFixture({ fullPhotosVisible: true });
  const refresh = deferred();
  const retry = deferred();
  const responses = [Promise.resolve(enrollment), refresh.promise, retry.promise];
  const harness = await enrollmentReviewHarness({
    adminListEnrollments: async () => [enrollment],
    adminEnrollmentCard: () => responses.shift(),
  });
  let { page } = await harness.flush();
  queueRow(page).props.onClick();
  let { detail } = await harness.flush();
  identityCheckbox(detail).props.onChange({ target: { checked: true } });
  ({ page } = harness.render());
  buttonNamed(page, '새로고침').props.onClick();
  ({ detail } = await harness.flush());
  buttonNamed(detail, '거절').props.onClick();
  ({ detail } = harness.render());
  findTree(detail, node => node.type === 'input' && node.props.value === 'other').props.onChange();
  ({ detail } = harness.render());
  findTree(detail, node => node.type === 'Textarea').props.onChange({ target: { value: '사진을 다시 확인해 주세요.' } });
  ({ detail } = harness.render());
  const inputLocation = elementLocation(detail, findTree(detail, node => node.type === 'Textarea'));
  const photosLocation = elementLocation(detail, photoReviewSection(detail));
  const assertKept = tree => {
    const textarea = findTree(tree, node => node.type === 'Textarea');
    assert.ok(textarea, '재조회 실패가 거절 입력을 제거하면 안 돼요.');
    assert.equal(textarea.props.value, '사진을 다시 확인해 주세요.');
    assert.deepEqual(elementLocation(tree, textarea), inputLocation);
    assert.ok(photoReviewSection(tree), '재조회 실패가 열어 둔 사진 영역을 제거하면 안 돼요.');
    assert.deepEqual(elementLocation(tree, photoReviewSection(tree)), photosLocation);
  };
  refresh.reject(new Error('상세 정보를 다시 불러오지 못했어요.'));
  ({ detail } = await harness.flush());
  assertKept(detail);
  const alert = findTree(detail, node => node.props?.role === 'alert');
  assert.ok(nodeText(alert).includes('상세 정보를 다시 불러오지 못했어요.'));
  buttonNamed(detail, '다시 시도').props.onClick();
  ({ detail } = await harness.flush());
  assertKept(detail);
  retry.resolve({ ...enrollment, status: 'vc_pending' });
  ({ detail } = await harness.flush());
  assertKept(detail);
  assert.equal(nodeText(detail).includes('상세 정보를 다시 불러오지 못했어요.'), false);
  buttonNamed(detail, '취소').props.onClick();
  ({ detail } = harness.render());
  assert.equal(identityCheckbox(detail).props.checked, true);
  assert.equal(buttonNamed(detail, '승인하고 증서 발급').props.disabled, false);
});

test('최신 상세 조회가 실패하면 뒤늦은 성공도 기존 카드와 최신 오류를 바꾸지 않아요', async () => {
  const enrollment = enrollmentFixture({ status: 'vc_pending', fullPhotosVisible: true });
  const stale = deferred();
  const latest = deferred();
  const responses = [Promise.resolve(enrollment), stale.promise, latest.promise];
  const harness = await enrollmentReviewHarness({
    adminListEnrollments: async () => [enrollment],
    adminEnrollmentCard: () => responses.shift(),
  });
  let { page } = await harness.flush();
  queueRow(page).props.onClick();
  let { detail } = await harness.flush();
  identityCheckbox(detail).props.onChange({ target: { checked: true } });
  ({ page } = harness.render());
  buttonNamed(page, '새로고침').props.onClick();
  ({ detail } = await harness.flush());
  photoReviewSection(detail).props.onChanged();
  await harness.flush();
  latest.reject(new Error('최신 요청이 실패했어요.'));
  ({ detail } = await harness.flush());
  stale.resolve({ ...enrollment, status: 'license_pending' });
  ({ detail } = await harness.flush());
  assert.equal(buttonNamed(detail, '승인하고 증서 발급')?.props.disabled, false);
  assert.ok(nodeText(findTree(detail, node => node.props?.role === 'alert')).includes('최신 요청이 실패했어요.'));
});

test('최초 상세 조회 실패에는 오류 카드와 재시도를 표시해요', async () => {
  const enrollment = enrollmentFixture();
  let requests = 0;
  const harness = await enrollmentReviewHarness({
    adminListEnrollments: async () => [enrollment],
    adminEnrollmentCard: async () => {
      if (++requests === 1) throw new Error('최초 조회에 실패했어요.');
      return enrollment;
    },
  });
  let { page } = await harness.flush();
  queueRow(page).props.onClick();
  let { detail } = await harness.flush();
  assert.ok(nodeText(detail).includes('최초 조회에 실패했어요.'));
  assert.equal(identityCheckbox(detail), null);
  assert.equal(findTree(detail, node => node.type === 'Skeleton'), null);
  buttonNamed(detail, '다시 시도').props.onClick();
  ({ detail } = await harness.flush());
  assert.ok(identityCheckbox(detail));
  assert.equal(nodeText(detail).includes('최초 조회에 실패했어요.'), false);
});

test('새로고침하면 선택한 등록의 최신 조건 제출 상태로 승인 버튼을 갱신해요', async () => {
  let enrollment = enrollmentFixture();
  let resolveRefresh;
  let cardRequests = 0;
  const approvedIds = [];
  const harness = await enrollmentReviewHarness({
    adminListEnrollments: async () => [enrollment],
    adminEnrollmentCard: async id => {
      assert.equal(id, 'enrollment-1');
      cardRequests += 1;
      return cardRequests === 1 ? enrollment : new Promise(resolve => { resolveRefresh = resolve; });
    },
    adminApproveEnrollment: async id => { approvedIds.push(id); return { status: 'vc_pending' }; },
  });
  let { page } = await harness.flush();
  queueRow(page).props.onClick();
  let { detail } = await harness.flush();
  identityCheckbox(detail).props.onChange({ target: { checked: true } });
  ({ page, detail } = harness.render());
  assert.equal(buttonNamed(detail, '승인하고 증서 발급').props.disabled, true);

  enrollment = { ...enrollment, status: 'vc_pending' };
  buttonNamed(page, '새로고침').props.onClick();
  ({ page, detail } = await harness.flush());
  assert.equal(nodeText(queueRow(page)).includes('신원 확인 대기'), true);
  assert.equal(cardRequests, 2, '목록과 함께 선택한 상세 카드도 다시 조회해야 해요.');
  assert.ok(identityCheckbox(detail), '재조회 중에도 기존 상세 카드가 보여야 해요.');
  assert.equal(identityCheckbox(detail).props.checked, true);
  assert.equal(findTree(detail, node => node.type === 'Skeleton'), null);

  resolveRefresh(enrollment);
  ({ detail } = await harness.flush());
  assert.equal(buttonNamed(detail, '승인하고 증서 발급').props.disabled, false);
  assert.equal(nodeText(detail).includes('등록자가 사용 조건을 마치면 승인할 수 있어요.'), false);
  await buttonNamed(detail, '승인하고 증서 발급').props.onClick();
  assert.deepEqual(approvedIds, ['enrollment-1']);
});

test('새로고침 응답을 기다리는 동안 목록과 상세 카드를 유지해요', async () => {
  const enrollment = enrollmentFixture();
  let refreshing = false;
  let finishRefresh;
  const refresh = new Promise(resolve => { finishRefresh = resolve; });
  const harness = await enrollmentReviewHarness({
    adminListEnrollments: async () => { if (refreshing) await refresh; return [enrollment]; },
    adminEnrollmentCard: async () => { if (refreshing) await refresh; return enrollment; },
  });
  let { page } = await harness.flush();
  queueRow(page).props.onClick();
  let { detail } = await harness.flush();
  identityCheckbox(detail).props.onChange({ target: { checked: true } });
  ({ page } = harness.render());
  refreshing = true;
  buttonNamed(page, '새로고침').props.onClick();
  try {
    ({ page, detail } = await harness.flush());
    assert.ok(queueRow(page), '상세 카드 위의 목록이 사라지면 스크롤 위치가 달라져요.');
    assert.equal(identityCheckbox(detail)?.props.checked, true);
    assert.equal(findTree(page, node => node.type === 'Skeleton'), null);
    assert.equal(findTree(detail, node => node.type === 'Skeleton'), null);
  } finally {
    finishRefresh();
    await harness.flush();
  }
});

test('신원 승인 직후 승인 탭의 vc_pending 배지는 증서 발급 대기로 표시해요', async () => {
  let enrollment = enrollmentFixture({ status: 'vc_pending' });
  const harness = await enrollmentReviewHarness({
    adminListEnrollments: async review => enrollment.reviewStatus === review ? [enrollment] : [],
    adminEnrollmentCard: async () => enrollment,
    adminApproveEnrollment: async () => {
      enrollment = { ...enrollment, reviewStatus: 'approved' };
      return { status: 'vc_pending', reviewStatus: 'approved' };
    },
  });
  let { page } = await harness.flush();
  assert.equal(nodeText(findTree(queueRow(page), node => node.type === 'Badge')), '신원 확인 대기');
  queueRow(page).props.onClick();
  let { detail } = await harness.flush();
  identityCheckbox(detail).props.onChange({ target: { checked: true } });
  ({ detail } = harness.render());
  await buttonNamed(detail, '승인하고 증서 발급').props.onClick();
  ({ page } = await harness.flush());
  buttonNamed(page, '승인').props.onClick();
  ({ page } = await harness.flush());
  assert.equal(nodeText(findTree(queueRow(page), node => node.type === 'Badge')), '증서 발급 대기');
});
