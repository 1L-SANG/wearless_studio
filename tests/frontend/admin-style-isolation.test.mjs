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

// 위의 '@layer 선언 순서' 테스트는 딱 그 한 줄(`@layer theme, base, ...;`)만 본다 —
// admin.css 본문에 레이어 밖(unlayered) 규칙이 실제로 없는지는 아무도 안 본다.
// 그런데 이 파일 맨 위 주석이 스스로 경고하는 게 바로 그거다: 레이어 밖 규칙은
// 명시도와 무관하게 레이어 안 규칙을 이긴다. @import 줄 아래에 벌거벗은 셀렉터
// 블록 하나만 추가돼도(예: `.foo { color: red }`) 설계 전체가 조용히 무력화되는데
// 기존 네 테스트는 전부 그대로 통과한다. 그 구멍을 여기서 막는다.
test('admin.css 는 최상위 규칙을 전부 @layer 블록이나 @import ... layer(...) 안에만 둔다', () => {
  const css = read('src/apps/admin/admin.css');
  // 블록 주석 제거 — 주석 안의 `{`/`}` 가 깊이 계산을 흐트러뜨리지 않게.
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');

  // Tailwind v4 가 컴파일 타임에 소비해 theme 레이어로 접어 넣는 두 구조만 예외로 둔다:
  //   1) 순정 `:root { --… }` 커스텀 프로퍼티 블록 — Tailwind 의 @theme inline 이
  //      var(--…) 로 참조하는 원천이라 여기 있어야 토큰이 갱신된다.
  //   2) `@theme inline { … }` — Tailwind 문법상 @layer 로 감쌀 수 없는 자체 at-rule.
  // 이 둘 말고 최상위에 나타나는 모든 규칙은 @layer 블록이거나 @import 문이어야 한다.
  const ALLOWED_UNLAYERED_HEADS = [':root', '@theme inline'];

  let depth = 0;
  let head = '';
  const violations = [];

  for (const ch of stripped) {
    if (ch === '{') {
      if (depth === 0) {
        const trimmedHead = head.trim();
        const isAllowed = trimmedHead.startsWith('@layer')
          || trimmedHead.startsWith('@import')
          || ALLOWED_UNLAYERED_HEADS.some((h) => trimmedHead === h || trimmedHead.startsWith(`${h} `));
        if (!isAllowed) violations.push(trimmedHead || '(빈 셀렉터)');
      }
      depth += 1;
      head = '';
    } else if (ch === '}') {
      depth = Math.max(0, depth - 1);
      head = '';
    } else if (ch === ';' && depth === 0) {
      head = ''; // @layer 선언 줄·@import 문 등 세미콜론으로 끝나는 최상위 문장
    } else {
      head += ch;
    }
  }

  assert.deepEqual(violations, [], `레이어 밖(unlayered) 규칙 발견 — 명시도와 무관하게 레이어 안 유틸리티를 이긴다: ${violations.join(', ')}`);
});
