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
