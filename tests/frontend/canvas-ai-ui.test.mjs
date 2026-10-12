import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import test from 'node:test';

const read = (path) => existsSync(new URL(path, import.meta.url))
  ? readFileSync(new URL(path, import.meta.url), 'utf8') : '';
const layer = read('../../src/features/editor/CanvasAiLayer.jsx');
const hook = read('../../src/features/editor/useCanvasAi.js');
const editor = read('../../src/features/editor/Editor.jsx');
const css = read('../../src/styles/features.css');

test('오브는 메뉴 버튼이며 방향키와 Esc가 초점을 돌려준다', () => {
  assert.match(layer, /aria-label="AI로 새 컷"/);
  assert.match(layer, /aria-haspopup="menu"/);
  assert.match(layer, /aria-expanded=\{open\}/);
  assert.match(layer, /role="menu"/);
  assert.match(layer, /ArrowDown/);
  assert.match(layer, /ArrowUp/);
  assert.match(layer, /Escape/);
  assert.match(layer, /orbRef\.current\?\.focus\(\)/);
});

test('선택이 바뀌면 메뉴를 닫고 몸짓과 편집 중에는 숨긴다', () => {
  assert.match(layer, /\[element\.id, element\.src\]/);
  assert.match(editor, /const \[canvasGesture, setCanvasGesture\] = useState\(false\)/);
  assert.match(editor, /!canvasGesture && !cropping && !marquee && !spaceDown && !editEl && !preview/);
  assert.match(editor, /gesturing\.current = true; setCanvasGesture\(true\)/);
  assert.match(editor, /gesturing\.current = false; setCanvasGesture\(false\)/);
});

test('생성 결과는 의류에 남고 현재 탭을 바꾸지 않는다', () => {
  assert.match(hook, /api\.getEditorAssetInfo/);
  assert.match(hook, /api\.fillEmptySlot/);
  assert.match(hook, /api\.generateImage/);
  assert.match(hook, /onResult/);
  assert.doesNotMatch(hook, /setTab/);
  assert.match(layer, /현재 이미지와 교체/);
  assert.match(layer, /새 블록에 추가/);
  assert.match(layer, /크레딧은 나가지 않았어요/);
});

test('새 UI는 CSS 변수로 배치하고 내보내기에서 제외한다', () => {
  assert.doesNotMatch(layer, /style=\{/);
  assert.match(layer, /style\.setProperty/);
  const exportSource = read('../../src/features/editor/editorExport.js');
  assert.match(exportSource, /'\.canvas-ai-layer'/);
  assert.match(exportSource, /'\.canvas-ai-slot-progress'/);
  assert.match(css, /\.ed-body\s*\{[^}]*position: relative/);
});

test('오브와 로고 채움은 확정한 크기와 속도이며 동작 줄이기를 존중한다', () => {
  assert.match(css, /\.canvas-ai-orb\s*\{[^}]*--d: 30px/);
  assert.match(css, /canvasAiGlassA 12s/);
  assert.match(css, /canvasAiGlassB 17s/);
  assert.match(css, /canvasAiFill 2\.4s ease-in-out/);
  assert.match(css, /canvasAiDrain 2s linear/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.match(css, /\.canvas-ai-pill\.btn-glowring[^}]*color: var\(--bg-1\)/);
  assert.doesNotMatch(layer, /className="spin"/);
});

test('갈래 너비는 확정 시안처럼 실제 글자를 재고 선택지는 같은 폭으로 늘린다', () => {
  assert.match(layer, /canvas-ai-measure/);
  assert.match(layer, /--canvas-ai-branch-width/);
  assert.match(layer, /getBoundingClientRect\(\)\.width/);
  assert.match(css, /\.canvas-ai-options\.is-closure[^}]*display: grid/);
  assert.doesNotMatch(css.slice(css.indexOf('/* ADR-0020:')), /min-width: 132px|width: 152px/);
});

test('낮은 가로 사진의 취소 버튼도 로고 옆에 보여 준다', () => {
  assert.match(layer, /classList\.toggle\('is-compact'/);
  assert.match(css, /\.canvas-ai-progress\.is-compact[^}]*flex-direction: row/);
});
