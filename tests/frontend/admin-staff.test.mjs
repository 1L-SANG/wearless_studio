/* 관리자 관리 화면 — 서버 가드를 UI 가 안내로 미리 보여주는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 staff·audit 함수가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  for (const fn of ['adminListStaff', 'adminSetRole', 'adminListAudit']) {
    assert.ok(api.includes(`export function ${fn}`), `누락: ${fn}`);
  }
});

test('자기 자신·마지막 관리자는 회수 버튼이 비활성이다', () => {
  const source = read('src/features/admin/AdminStaff.jsx');
  assert.ok(source.includes('isSelf'), '자기 자신 판정이 없다');
  assert.ok(source.includes('admins.length <= 1') || source.includes('lastAdmin'), '최후 관리자 판정이 없다');
});

test('최근 감사 기록을 보여준다', () => {
  const source = read('src/features/admin/AdminStaff.jsx');
  assert.ok(source.includes('adminListAudit'));
});

test('관리자 목록·감사 기록 조회 실패는 화면에 남는 에러 상태고, 화면 전체가 하나로 게이팅되지 않는다', () => {
  // 예전엔 관리자 목록 조회 실패가 토스트(몇 초 뒤 사라짐)로만 처리됐고, data 는 계속
  // null 이라 `if (!data) return <Skeleton />` 가 화면 전체를(검색 카드까지) 영원히
  // 가뒀다 — 유일한 복구가 전체 새로고침이었다. 감사 기록 실패는 빈 배열로 위장됐다.
  const source = read('src/features/admin/AdminStaff.jsx');

  assert.ok(source.includes('dataError'), '관리자 목록 조회 실패를 담을 에러 상태가 없다');
  assert.ok(source.includes('auditError'), '감사 기록 조회 실패를 담을 에러 상태가 없다');
  assert.ok(!/\.catch\(\(\)\s*=>\s*setAudit\(\[\]\)\)/.test(source), '감사 기록 조회 실패가 빈 배열로 위장된다');
  assert.ok(
    !/if\s*\(!data\)\s*return\s*<Skeleton/.test(source),
    '화면 전체가 여전히 data 하나로 통째로 게이팅된다 — 실패하면 검색 카드까지 영원히 안 보인다',
  );
});
