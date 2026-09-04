/* 관리자 콘솔 셸과 라우트 계약.

   라우트가 늘어난 뒤에도 로그인 가드(RequireAuth) 밖으로 새는 화면이 없어야 한다 —
   콘솔은 전부 보호 대상이다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('admin 라우트 4개가 셸 아래, 로그인 가드 안에 있다', () => {
  const app = read('src/apps/admin/App.jsx');
  assert.ok(app.includes('<RequireAuth />'), '로그인 가드가 없다');
  assert.ok(app.includes('AdminShell'), '셸이 없다');
  for (const path of ['applications', 'models', 'staff']) {
    assert.ok(app.includes(`path="${path}"`), `라우트 누락: ${path}`);
  }
  // 셸 밖(가드 밖)에 화면 라우트를 두면 안 된다 — catch-all 리다이렉트만 허용.
  const outside = app.split('</Route>')[1] || '';
  assert.ok(!outside.includes('element={<Admin'), '가드 밖에 관리자 화면이 있다');
});

test('셸은 네 갈래 내비게이션을 가진다', () => {
  const shell = read('src/features/admin/AdminShell.jsx');
  for (const label of ['대시보드', '지원서', '모델', '관리자']) {
    assert.ok(shell.includes(label), `내비 항목 누락: ${label}`);
  }
});

test('admin-ui 컴포넌트는 공용 ui.jsx 를 물지 않는다', () => {
  for (const name of ['button', 'card', 'badge', 'input', 'table', 'skeleton']) {
    const source = read(`src/components/admin-ui/${name}.jsx`);
    assert.ok(!source.includes('@/components/ui.jsx'), `${name} 이 공용 ui.jsx 를 문다`);
    assert.ok(source.includes('@/lib/adminCn.js'), `${name} 이 cn() 을 안 쓴다`);
  }
});
