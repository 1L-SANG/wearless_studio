import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const adminReturn = await import('../../src/apps/admin/adminReturnTarget.js').catch(() => ({}));

async function loginPromptDestination(pathname, search, { admin = true } = {}) {
  const source = read('src/apps/guards.jsx').replace(/^import[\s\S]*?from '[^']+';\n/gm, '').replace(/^export /gm, '');
  const { code } = await transformWithEsbuild(source, 'guards.jsx', { jsx: 'transform' });
  const destinations = [];
  const effects = [];
  const bindings = {
    React: { createElement: () => null },
    useRef: (value) => ({ current: value }),
    useEffect: (effect) => effects.push(effect),
    useAuth: () => ({ openLogin: (path) => destinations.push(path) }),
    useLocation: () => ({ pathname, search }),
    IS_ADMIN: admin,
    IS_FACEMARKET: !admin,
    Button: () => null,
    Link: () => null,
    s: { prompt: 'prompt', actions: 'actions' },
  };
  const prompt = new Function(...Object.keys(bindings), `${code}; return FacemarketLoginPrompt;`)(...Object.values(bindings));
  prompt();
  for (const effect of effects) effect();
  return destinations;
}

test('로그아웃된 관리자 알림 링크는 사진 확인 탭 주소를 로그인 복귀 목표로 남겨요', async () => {
  assert.deepEqual(await loginPromptDestination('/review', '?tab=photos'), ['/review?tab=photos']);
  assert.deepEqual(await loginPromptDestination('/review', ''), ['/review']);
  assert.deepEqual(await loginPromptDestination('/model/register', '', { admin: false }), ['/model/register']);
});

test('관리자 루트는 로그인 복귀 목표를 관리자 화면으로만 제한해요', () => {
  assert.equal(adminReturn.adminReturnTarget?.('/review?tab=photos'), '/review?tab=photos');
  assert.equal(adminReturn.adminReturnTarget?.('/review'), '/review');
  assert.equal(adminReturn.adminReturnTarget?.('/applications?status=under_review'), '/applications?status=under_review');
  for (const path of ['/', '/model/register', '/review-evil', '//evil.example', 'https://evil.example']) {
    assert.equal(adminReturn.adminReturnTarget?.(path), null, path);
  }
});

test('관리자 앱의 로그인 복귀 지점은 로그인 가드 안의 루트 화면에 연결돼요', () => {
  const app = read('src/apps/admin/App.jsx');
  assert.match(app, /<Route index element=\{<AdminPostLogin \/>\}/);
});
