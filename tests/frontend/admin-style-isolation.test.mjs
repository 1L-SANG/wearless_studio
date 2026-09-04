/* Tailwind 는 admin 진입 번들에만 들어간다.

   셀러·facemarket 문서에 Tailwind preflight 가 실리면 전역 리셋이 기존 화면을 통째로
   바꾼다. 반대로 admin 이 스튜디오 CSS 를 JS 로 import 하면 그 규칙들이 레이어 밖(unlayered)
   에 놓여, 레이어 안에 있는 Tailwind 유틸리티를 **명시도와 무관하게** 이긴다.
   그래서 admin 은 CSS 한 파일에서 레이어 순서를 직접 정한다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('admin 부트스트랩은 스튜디오 CSS 를 JS 로 import 하지 않는다', () => {
  const source = read('src/apps/admin/mountAdminApp.jsx');
  assert.ok(!source.includes("@/styles/app.css"), 'app.css 를 JS 로 물면 레이어 밖에 놓인다');
  assert.ok(source.includes("./admin.css"), 'admin.css 를 물어야 한다');
});

test('admin.css 는 preflight → 스튜디오 → 유틸리티 순으로 레이어를 정한다', () => {
  const css = read('src/apps/admin/admin.css');
  const order = css.match(/@layer\s+([^;]+);/);
  assert.ok(order, '@layer 선언이 없다');
  const layers = order[1].split(',').map((s) => s.trim());
  assert.deepEqual(layers, ['theme', 'base', 'studio', 'components', 'utilities']);
});

test('스튜디오 진입은 Tailwind 를 물지 않는다', () => {
  for (const entry of ['src/apps/mountApp.jsx', 'src/apps/seller/App.jsx', 'src/apps/facemarket/App.jsx']) {
    const source = read(entry);
    assert.ok(!source.includes('admin.css'), `${entry} 가 admin.css 를 문다`);
    assert.ok(!source.includes('tailwindcss'), `${entry} 가 tailwind 를 문다`);
  }
});

test('mountApp 은 스튜디오 스타일을 계속 물고 프로바이더는 공유한다', () => {
  const source = read('src/apps/mountApp.jsx');
  assert.ok(source.includes("@/styles/app.css"));
  assert.ok(source.includes("AppProviders.jsx"));
});
