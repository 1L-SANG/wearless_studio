import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { TEXT_PRESET_DRAG_PREFIX, textPresetKeyFromDragTypes } from '../../src/features/editor/editorImageDrop.js';
import {
  CANVAS_WIDTH, DEFAULT_TEXT_BODY, DEFAULT_TEXT_PRESET, TEXT_PRESETS, activeTextPreset,
  buildTextPresetElement, quickStylePatch, textPresetBox, textPresetDropPlacement, textPresetOf,
} from '../../src/features/editor/presets/textPresets.js';

/* 값의 출처: docs/superpowers/specs/2026-08-04-editor-text-slots-design.md
   (셀러 텍스트 568건 실측 기반 타이포 위계). 이 테스트는 프리셋이 스펙 값에서
   조용히 어긋나는 것을 막는 계약 고정이다. */
const SPEC = {
  headline: { size: 40, weight: 600, color: '#0e0d14' },
  subtitle: { size: 26, weight: 600, color: '#0e0d14' },
  body: { size: 17, weight: 400, color: '#6b6b73', lineHeight: 26 },
};

test('프리셋 3종이 스펙 타이포 값과 일치한다(꼬리표는 오너 8/16 제거)', () => {
  assert.equal(TEXT_PRESETS.length, 3);
  for (const [key, expected] of Object.entries(SPEC)) {
    const p = TEXT_PRESETS.find((x) => x.key === key);
    assert.ok(p, `${key} 프리셋이 있어야 한다`);
    for (const [prop, value] of Object.entries(expected)) {
      assert.equal(p.style[prop], value, `${key}.style.${prop}`);
    }
  }
});

test('패널 축소판 크기는 실제 크기 순서를 보존한다 — 목록이 위계를 거꾸로 보여주면 안 된다', () => {
  const bySize = [...TEXT_PRESETS].sort((a, b) => b.style.size - a.style.size);
  const byPreview = [...TEXT_PRESETS].sort((a, b) => b.previewSize - a.previewSize);
  assert.deepEqual(byPreview.map((p) => p.key), bySize.map((p) => p.key));
});

test('기본 프리셋은 소제목 — 시장 최다 텍스트(65.5%)', () => {
  assert.equal(DEFAULT_TEXT_PRESET, 'subtitle');
});

test('요소 생성 — 기본 문구+자동 폭, 이미지 기둥(x=60), 문구가 들어갈 만한 씨앗 폭', () => {
  for (const p of TEXT_PRESETS) {
    const el = buildTextPresetElement(p.key);
    assert.equal(el.type, 'text');
    // 오너 2026-08-16: 빈 상자로 시작하면 어디에 생겼는지·얼마나 큰지 안 보인다.
    assert.equal(el.text, DEFAULT_TEXT_BODY, `${p.key}는 기본 문구로 시작한다`);
    assert.equal(el.textSizing, 'auto', `${p.key}는 자동 폭`);
    // 씨앗 폭은 공식(크기 × 8.8)을 못 박는다 — 부등식으로 두면 20배가 돼도 통과해
    // 드래그 미리보기 상자와 드롭 가둠이 조용히 어긋난다(2026-08-16 리뷰).
    assert.equal(el.w, Math.round(p.style.size * 8.8), `${p.key} w — 기본 문구 폭 씨앗값`);
    assert.equal(el.h, p.style.lineHeight || Math.round(p.style.size * 1.4), `${p.key} h`);
    assert.equal(el.x, 60, `${p.key} x`);
    assert.ok(el.id && el.id !== buildTextPresetElement(p.key).id, 'id는 매번 달라야 한다');
    assert.deepEqual(el.style, { font: 'Pretendard', ...p.style });
  }
});

test("'텍스트 추가' 버튼은 네 번째 고정 스타일을 만들지 않는다", () => {
  // 오너 2026-08-16: 크기를 하나 더 못박은 '텍스트 상자' 항목은 물렸다 — 버튼은 기본 프리셋을 쓴다.
  assert.equal(TEXT_PRESETS.length, 3);
  assert.equal(textPresetOf('plain').key, DEFAULT_TEXT_PRESET, '모르는 키는 전부 기본 프리셋으로');
  assert.deepEqual(buildTextPresetElement(undefined).style, buildTextPresetElement(DEFAULT_TEXT_PRESET).style);
});

test('모르는 키·미지정은 기본 프리셋으로 — 요소와 라벨이 같은 폴백을 공유한다', () => {
  assert.equal(buildTextPresetElement('nope').style.size, SPEC.subtitle.size);
  assert.equal(textPresetOf(undefined).label, '소제목', 'T 단축키의 토스트 라벨도 소제목이어야 한다');
  assert.equal(textPresetOf('nope').key, DEFAULT_TEXT_PRESET);
});

test('빠른 스타일 전환 — 위계 속성만 바꾸고 셀러의 폰트·정렬은 남긴다', () => {
  const patch = quickStylePatch('headline');
  assert.equal(patch.size, SPEC.headline.size);
  assert.equal(patch.weight, SPEC.headline.weight);
  assert.equal(patch.color, SPEC.headline.color);
  assert.ok(!('font' in patch), '폰트는 건드리지 않는다');
  assert.ok(!('align' in patch), '정렬은 건드리지 않는다');
});

test('빠른 스타일 전환 — 행간·자간은 프리셋에 없으면 undefined로 리셋된다', () => {
  // 설명글(행간 26) → 큰 제목으로 바꿀 때 행간 26이 남으면 40px 글줄이 겹친다.
  // 0이 아니라 undefined인 이유: 명시적 0과 미설정이 저장 문서에서 구분돼야 하고,
  // 스프레드 병합에서 undefined가 기존 값을 덮어 리셋 효과는 동일하다.
  const toHeadline = quickStylePatch('headline');
  assert.ok('lineHeight' in toHeadline && toHeadline.lineHeight === undefined, '행간 리셋(undefined = 자동 1.4배)');
  assert.ok('tracking' in toHeadline && toHeadline.tracking === undefined, '자간 리셋');
  assert.equal(quickStylePatch('body').lineHeight, SPEC.body.lineHeight);
});

test('왕복 — 칩으로 입힌 스타일은 그 칩이 켜진 상태여야 한다', () => {
  for (const p of TEXT_PRESETS) {
    assert.equal(activeTextPreset(quickStylePatch(p.key)), p.key);
  }
});

test('활성 프리셋 판별 — 실효값이 다 맞을 때만, 아니면 null', () => {
  assert.equal(activeTextPreset({ ...SPEC.subtitle }), 'subtitle');
  assert.equal(activeTextPreset({ ...SPEC.subtitle, align: 'center' }), 'subtitle',
    '위계 밖 속성(정렬 등)은 판별에 영향 없다');
  assert.equal(activeTextPreset({ ...SPEC.subtitle, size: 27 }), null);
  assert.equal(activeTextPreset({ ...SPEC.body }), 'body');
  assert.equal(activeTextPreset({ ...SPEC.body, lineHeight: 30 }), null,
    '행간을 실제로 바꿨으면 더는 그 프리셋이 아니다');
  assert.equal(activeTextPreset(undefined), null);
});

test('활성 프리셋 판별 — 화면이 같으면 같은 상태로 본다(실효값 정규화)', () => {
  // 색상 UI(normalizeHexColor·hsvToHex)는 대문자로 저장한다 — 같은 색을 다시 골라도 칩이 꺼지면 안 된다.
  assert.equal(activeTextPreset({ ...SPEC.subtitle, color: '#0E0D14' }), 'subtitle');
  // 행간 자동(0/없음)과 명시된 size×1.4는 같은 화면이다 — 행간 칸을 스쳐도 칩이 꺼지면 안 된다.
  assert.equal(activeTextPreset({ ...SPEC.headline, lineHeight: 56 }), 'headline');
  // 반대로 렌더 기본값과 다른 실제 변경은 구분한다: weight 없음 = 400 렌더 ≠ 소제목 600.
  assert.equal(activeTextPreset({ size: 26, color: '#0e0d14' }), null);
});


/* ---------- 끌어다 놓기(오너 2026-08-16: 세 종류 다 블록 위 원하는 자리에) ---------- */

test('놓은 자리에 캐럿이 온다 — 세로는 글줄 한가운데', () => {
  const el = buildTextPresetElement('subtitle');
  const place = textPresetDropPlacement({ x: 300, y: 400, w: el.w, h: el.h, blockW: 1000, blockH: 1200 });
  assert.equal(place.x, 300);
  assert.equal(place.y, Math.round(400 - el.h / 2), '포인터가 글줄 한가운데');
});

test('블록 밖으로는 못 나간다 — 안 보이는 글자를 만들지 않는다', () => {
  const inside = textPresetDropPlacement({ x: 990, y: 1190, w: 12, h: 36, blockW: 1000, blockH: 1200 });
  assert.equal(inside.x, 988, '오른쪽 끝에서도 상자 전체가 블록 안');
  assert.equal(inside.y, 1164);
  const negative = textPresetDropPlacement({ x: -50, y: -50, w: 12, h: 36, blockW: 1000, blockH: 1200 });
  assert.deepEqual(negative, { x: 0, y: 0 });
});

test('블록 폭을 안 넘겨도 캔버스 폭으로 가둔다 — 실제 호출부에는 block.w 가 없다', () => {
  // 2026-08-16 리뷰: 호출부가 존재하지 않는 block.w 를 넘겨 가둠이 통째로 죽어 있었다.
  // 기본값이 캔버스 폭이라야 "인자를 안 넘긴 실제 경로"에서도 상자가 블록 밖으로 안 나간다.
  assert.equal(CANVAS_WIDTH, 1000);
  const wide = textPresetDropPlacement({ x: 900, y: 500, w: 352, h: 56, blockH: 1200 });
  assert.equal(wide.x + 352, CANVAS_WIDTH, '오른끝이 정확히 블록 끝');
  // 높이를 모르면(0) 세로만 가두지 않는다 — 가로는 항상 가둔다.
  assert.deepEqual(textPresetDropPlacement({ x: 700, y: 900, w: 12, h: 36 }), { x: 700, y: 882 });
});

test('상자가 블록보다 넓으면 0으로 붙인다 — 음수 한계에서 가둠이 풀리면 안 된다', () => {
  assert.deepEqual(textPresetDropPlacement({ x: 900, y: 50, w: 1200, h: 56, blockW: 1000, blockH: 400 }), { x: 0, y: 22 });
});

test('패널 계약 — 프리셋 버튼이 드래그 가능하고, 커서 그림은 비어 있다(글자 겹침 방지)', () => {
  const panel = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
  const item = panel.slice(panel.indexOf('className="text-preset-item"'), panel.indexOf('tp-sample'));
  assert.match(item, /draggable/, '버튼이 draggable 이어야 브라우저가 드래그를 시작한다');
  assert.match(item, /startTextPresetDrag\(e, p\.key\)/);
  const addBtn = panel.slice(panel.indexOf('className="add-text-btn"'), panel.indexOf('name="type"'));
  assert.match(addBtn, /draggable/, "'텍스트 추가'도 같은 방식으로 끌 수 있다(네 항목이 같은 조작)");
  assert.match(addBtn, /startTextPresetDrag\(e, DEFAULT_TEXT_PRESET\)/);
  assert.doesNotMatch(panel, /text-preset-plain|PLAIN_TEXT_PRESET/, '물린 방식의 흔적이 남으면 안 된다');
  const start = panel.slice(panel.indexOf('function startTextPresetDrag'), panel.indexOf('export function TextPanel'));
  assert.match(start, /setData\('text\/object', `text:\$\{presetKey\}`\)/,
    "블록 드롭 핸들러가 이미 아는 'text/object' 형식이라야 하이라이트·드롭이 그대로 동작한다");
  assert.match(start, /setData\(`\$\{TEXT_PRESET_DRAG_PREFIX\}\$\{presetKey\}`/,
    '드래그 도중 getData 가 막히므로 종류는 타입 이름으로 실어 보낸다');
  // 커서 그림에 같은 문구를 그리면 블록 위에서 글자가 둘로 겹쳐 보인다(오너 2026-08-16).
  // 놓일 자리는 블록 안 미리보기 하나만 말한다.
  assert.doesNotMatch(start, /textContent = box\.text/, '커서 그림에 문구를 넣지 않는다');
  assert.match(start, /setDragImage\(blank, 0, 0\)/);
  const css = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');
  const ghostStart = css.indexOf('.text-drag-ghost {');
  const ghostCss = css.slice(ghostStart, css.indexOf('}', ghostStart));
  assert.match(ghostCss, /opacity: 0/, '커서 그림은 눈에 안 보여야 한다');
});

test('드래그 미리보기 상자와 실제로 만들어지는 요소는 같은 값을 본다', () => {
  for (const key of [...TEXT_PRESETS.map((p) => p.key), undefined]) {
    const box = textPresetBox(key);
    const el = buildTextPresetElement(key);
    assert.deepEqual({ w: el.w, h: el.h, text: el.text, style: el.style }, box, `${key}`);
    // 스프레드 항등식만 보면 두 경로가 갈라져도 통과한다 — 스펙 값으로도 못 박는다.
    const spec = textPresetOf(key);
    assert.equal(box.style.size, spec.style.size);
    assert.equal(box.h, spec.style.lineHeight || Math.round(spec.style.size * 1.4));
    assert.equal(box.text, DEFAULT_TEXT_BODY);
  }
});

test('에디터 계약 — 드롭된 텍스트는 도형이 아니라 텍스트 경로로 간다', () => {
  const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  assert.match(editor, /type === 'text' \? dropText\(id, bid, ev\) : addShape\(/);
  // 자동 관리 블록(정보·사이즈·세탁·AI 고지)은 클릭 추가와 똑같이 막는다 — 재생성 때 글이 사라진다.
  assert.match(editor.slice(editor.indexOf('const dropText =')), /isAutoManagedBlock\(block\)/);
});


test('드래그 도중 종류 알아내기 — 타입 목록에서 프리셋 키를 뽑는다', () => {
  const types = ['text/object', `${TEXT_PRESET_DRAG_PREFIX}body`];
  assert.equal(textPresetKeyFromDragTypes(types), 'body');
  assert.equal(textPresetKeyFromDragTypes([`${TEXT_PRESET_DRAG_PREFIX}plain`]), 'plain');
  // 오브젝트·이미지 드래그는 텍스트 미리보기를 켜지 않는다.
  assert.equal(textPresetKeyFromDragTypes(['text/object', 'Files']), null);
  assert.equal(textPresetKeyFromDragTypes(undefined), null);
});

test('에디터 계약 — 블록 위에서 놓일 자리를 실제 상자로 미리 그린다', () => {
  const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  assert.match(editor, /const presetKey = textPresetKeyFromDragTypes\(types\);/);
  assert.match(editor, /className="text-drop-ghost"/);
  // 미리보기 자리와 실제 놓이는 자리는 같은 함수를 써야 "본 자리"와 "놓인 자리"가 같다.
  const overlayStart = editor.indexOf('const presetKey = textPresetKeyFromDragTypes');
  const overlay = editor.slice(overlayStart, editor.indexOf('onDragLeave', overlayStart));
  assert.match(overlay, /textPresetDropPlacement\(/);
  assert.match(overlay, /textPresetBox\(presetKey\)/);
});


test('에디터 계약 — 미리보기와 실제 드롭이 같은 기준으로 가둔다(존재하지 않는 block.w 금지)', () => {
  const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  assert.doesNotMatch(editor, /textPresetDropPlacement\([^)]*blockW: block\.w/,
    '블록에는 w 필드가 없다 — 넘기면 가둠이 통째로 죽는다');
  assert.match(editor, /textPresetDropPlacement\(\{ \.\.\.point, w: base\.w, h: base\.h, blockH: getBlockRenderHeight\(block\) \}\)/,
    '드롭도 미리보기와 같은 렌더 높이를 쓴다');
});

test('에디터 계약 — 대기 화면이 쓰는 함수는 early-return 위에서 선언된다(TDZ 흰화면 방지)', () => {
  const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const declared = editor.indexOf('const leaveToLibrary =');
  const firstEarlyReturn = editor.indexOf('\n  if (loadError) return (');
  assert.ok(declared > 0 && firstEarlyReturn > 0);
  assert.ok(declared < firstEarlyReturn,
    'const 는 선언 전 참조 시 ReferenceError — early-return 의 onClick 이 먼저 평가된다');
});


test('에디터 계약 — 손 안 댄 기본 문구는 편집이 끝날 때 요소째 사라진다', () => {
  const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const prune = editor.slice(editor.indexOf('function pruneEmptyTextEl'), editor.indexOf('const changeBg'));
  // '내용을 입력하세요.' 가 그대로 남으면 상업 상세페이지에 안내 문구가 발행된다(2026-08-16 리뷰).
  assert.match(prune, /FRESH_TEXT_IDS\.has\(elId\)/);
  assert.match(prune, /=== DEFAULT_TEXT_BODY/);
  // 카피 슬롯·말풍선·자동 관리 블록은 여전히 예외로 남긴다.
  assert.match(prune, /target\.copyRole \|\| target\.sourceBlockId/);
  assert.match(prune, /target\.shape === 'bubble'/);
  assert.match(prune, /isAutoManagedBlock\(block\)/);
});
