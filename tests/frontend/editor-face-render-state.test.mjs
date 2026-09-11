/* 얼굴 렌더 준비 표시(faceRender)가 쓰는 이름이 전부 선언돼 있는지.

   2026-09-11 deaad9dc 가 Editor.jsx 에 `setFaceRender(...)`·`warmFaceRender(...)`·
   `getFaceRenderStatus(...)` 호출과 `faceRender` 렌더를 넣으면서 useState 선언과 import 를
   빠뜨렸다. Vite 는 미선언 전역을 에러로 보지 않고 이 레포엔 ESLint 가 없어서 그대로
   prod 까지 갔고, 비-REAL 분기도 `setFaceRender(null)` 을 부르니 **모든** 프로젝트에서
   에디터가 마운트 즉시 ReferenceError 로 죽었다.

   렌더 테스트(jsdom) 없이 잡을 수 있는 가장 원인에 가까운 자리는 소스다 — 호출이 있으면
   같은 파일에 선언·import 도 있어야 한다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

/* 주석은 뺀다 — 이 레포는 주석에 식별자 이름을 자주 적는다. */
function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

const editor = stripComments(read('src/features/editor/Editor.jsx'));

test('Editor.jsx: setFaceRender 를 쓰면 faceRender useState 선언이 있어야 한다', () => {
  if (!/\bsetFaceRender\s*\(/.test(editor)) return; // 기능이 빠지면 이 테스트도 할 일이 없다
  assert.match(
    editor,
    /const\s+\[\s*faceRender\s*,\s*setFaceRender\s*\]\s*=\s*useState\(/,
    'setFaceRender 호출은 있는데 `const [faceRender, setFaceRender] = useState(...)` 가 없다',
  );
});

test('Editor.jsx: 호출하는 facemarket API 는 import 돼 있어야 한다', () => {
  const importBlock = [...editor.matchAll(/^import\s+\{([^}]*)\}\s+from\s+'@\/lib\/api\/facemarket\.js';/gm)]
    .map((m) => m[1]).join(',');
  const imported = new Set(importBlock.split(',').map((s) => s.trim().split(/\s+as\s+/).pop()).filter(Boolean));
  for (const name of ['warmFaceRender', 'getFaceRenderStatus']) {
    if (!new RegExp(`\\b${name}\\s*\\(`).test(editor)) continue;
    assert.ok(imported.has(name), `${name}(...) 를 부르는데 '@/lib/api/facemarket.js' 에서 import 하지 않았다`);
  }
});
