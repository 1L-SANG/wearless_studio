/* 모델 화면 계약 — 정지에 사유를 강제하는지, 상세가 네 블록을 다 내는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 모델 함수 네 개가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  for (const fn of ['adminListModels', 'adminModelDetail', 'adminSuspendModel', 'adminUnsuspendModel']) {
    assert.ok(api.includes(`export function ${fn}`), `누락: ${fn}`);
  }
});

test('사유가 비면 정지 버튼이 비활성이다', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  assert.ok(/disabled=\{[^}]*!reason\.trim\(\)/.test(source), '빈 사유로 정지가 눌린다');
});

test('상세는 라이선스·정산·생체등록을 모두 보여준다', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  for (const label of ['라이선스', '정산', '생체등록']) {
    assert.ok(source.includes(label), `상세 블록 누락: ${label}`);
  }
});
