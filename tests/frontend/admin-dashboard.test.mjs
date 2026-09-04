/* 대시보드 계약 — 큐 숫자가 목록으로 이어지는지, 기간 토글이 서버 허용값만 쓰는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 adminOverview 가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  assert.ok(api.includes('export function adminOverview'));
  assert.ok(api.includes('/v1/facemarket/admin/overview'));
});

test('기간 토글은 서버 허용값(7·30·90)만 낸다', () => {
  const source = read('src/features/admin/AdminDashboard.jsx');
  const periods = source.match(/PERIODS\s*=\s*\[([^\]]+)\]/);
  assert.ok(periods, 'PERIODS 상수가 없다');
  assert.deepEqual(
    periods[1].match(/\d+/g).map(Number).sort((a, b) => a - b),
    [7, 30, 90],
  );
});

test('큐 카드는 처리 화면으로 이어진다', () => {
  const source = read('src/features/admin/AdminDashboard.jsx');
  assert.ok(source.includes('/applications?status=under_review'), '검토 대기가 목록으로 안 이어진다');
});
