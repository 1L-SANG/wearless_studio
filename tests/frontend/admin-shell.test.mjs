/* 관리자 콘솔 셸과 라우트 계약.

   라우트가 늘어난 뒤에도 로그인 가드(RequireAuth) 밖으로 새는 화면이 없어야 한다 —
   콘솔은 전부 보호 대상이다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

// App.jsx 는 <Route element={<RequireAuth />}> 안에 <Route element={<AdminShell />}> 를
// 중첩한다 — 즉 </Route> 가 텍스트에 두 번 나온다(먼저 셸 라우트가 닫히고, 그다음 가드
// 라우트가 닫힌다). `text.split('</Route>')[1]` 은 그 둘 **사이**(공백뿐인 구간)를 가리켜서,
// 가드가 진짜로 닫힌 *뒤* 구간([2] 이후)은 아무도 안 본다 — 화면 라우트를 가드 밖으로
// 옮겨도 이 조건은 통과했다. 여기서는 태그를 실제로 짝지어 가드 Route 블록이 어디서
// 끝나는지 찾는다(self-closing 리프 라우트는 건너뛰고, 중첩된 open/close 만 깊이로 센다).
function findTagEnd(text, tagStart) {
  // tagStart 는 '<Route' 의 '<' 위치. 그 태그 자신의 '>' 를 찾되, 어트리뷰트 값 안의
  // JSX(예: element={<AdminShell />})에 있는 '<'/'>' 는 세지 않는다 — {} 깊이로 가린다.
  let i = tagStart;
  let braceDepth = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '{') braceDepth += 1;
    else if (ch === '}') braceDepth -= 1;
    else if (ch === '>' && braceDepth === 0) {
      let j = i - 1;
      while (j > tagStart && /\s/.test(text[j])) j -= 1;
      return { selfClosing: text[j] === '/', tagEnd: i + 1 };
    }
    i += 1;
  }
  throw new Error('<Route 태그의 닫는 > 를 못 찾았다 — App.jsx 마크업이 안 잘렸는지 확인');
}

// openTagStart 가 가리키는 <Route> 블록이 실제로 끝나는 인덱스(그 블록의 </Route> 바로
// 다음)를 돌려준다. self-closing(<Route .../>)이면 그 태그 자신이 곧 끝.
function findRouteBlockEnd(text, openTagStart) {
  const { selfClosing, tagEnd } = findTagEnd(text, openTagStart);
  if (selfClosing) return tagEnd;
  let depth = 1;
  let i = tagEnd;
  while (i < text.length) {
    if (text.startsWith('</Route>', i)) {
      depth -= 1;
      i += '</Route>'.length;
      if (depth === 0) return i;
      continue;
    }
    if (text.startsWith('<Route', i)) {
      const inner = findTagEnd(text, i);
      if (!inner.selfClosing) depth += 1;
      i = inner.tagEnd;
      continue;
    }
    i += 1;
  }
  throw new Error('가드 Route 를 짝지어 닫는 </Route> 를 못 찾았다 — 태그 균형이 안 맞는다');
}

test('admin 라우트 4개가 셸 아래, 로그인 가드 안에 있다', () => {
  const app = read('src/apps/admin/App.jsx');
  assert.ok(app.includes('<RequireAuth />'), '로그인 가드가 없다');
  assert.ok(app.includes('AdminShell'), '셸이 없다');
  for (const path of ['applications', 'models', 'staff']) {
    assert.ok(app.includes(`path="${path}"`), `라우트 누락: ${path}`);
  }
  // 셸 밖(가드 밖)에 화면 라우트를 두면 안 된다 — catch-all 리다이렉트만 허용.
  // 가드를 감싼 <Route element={<RequireAuth />}> 태그를 찾아, 그 블록이 실제로 닫히는
  // 지점을 태그 짝짓기로 계산한다(문자열 split 매직 인덱스 금지 — 중첩이 하나 더 늘면
  // 또 어긋난다).
  const requireAuthIdx = app.indexOf('<RequireAuth');
  assert.ok(requireAuthIdx !== -1, 'RequireAuth 가드를 못 찾았다');
  const guardTagStart = app.lastIndexOf('<Route', requireAuthIdx);
  assert.ok(guardTagStart !== -1, '가드를 감싼 Route 를 못 찾았다');
  const guardBlockEnd = findRouteBlockEnd(app, guardTagStart);
  const outside = app.slice(guardBlockEnd);
  assert.ok(!/element=\{<Admin/.test(outside), `가드 밖에 관리자 화면이 있다: ${outside.slice(0, 200)}`);
});

test('셸은 네 갈래 내비게이션을 가진다', () => {
  const shell = read('src/features/admin/AdminShell.jsx');
  for (const label of ['대시보드', '지원서', '모델', '관리자']) {
    assert.ok(shell.includes(label), `내비 항목 누락: ${label}`);
  }
});

test('admin-ui 컴포넌트는 공용 ui.jsx 를 물지 않는다', () => {
  for (const name of ['button', 'card', 'badge', 'input', 'textarea', 'table', 'skeleton']) {
    const source = read(`src/components/admin-ui/${name}.jsx`);
    assert.ok(!source.includes('@/components/ui.jsx'), `${name} 이 공용 ui.jsx 를 문다`);
    assert.ok(source.includes('@/lib/adminCn.js'), `${name} 이 cn() 을 안 쓴다`);
  }
});
