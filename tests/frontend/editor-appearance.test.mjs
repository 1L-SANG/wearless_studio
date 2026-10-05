import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import * as editorAppearance from '../../src/features/editor/editorAppearance.js';

const {
  DEFAULT_EDITOR_COLOR_PRESETS,
  imageResizeRect,
  lineHitStrokeWidth,
  resizePolicyForElement,
  shouldShowRotationHandle,
  speechBubblePath,
  stripPhotoBlockTextElements,
} = editorAppearance;

const editorPanelsSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url)), 'utf8');
const editorStylesSource = readFileSync(fileURLToPath(new URL('../../src/styles/features.css', import.meta.url)), 'utf8');
const moveableStylesSource = readFileSync(fileURLToPath(new URL('../../src/styles/moveable.css', import.meta.url)), 'utf8');
const editorSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/Editor.jsx', import.meta.url)), 'utf8');
const infoPresetsSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/presets/infoPresets.js', import.meta.url)), 'utf8');

test('editor colors expose a practical preset palette made only of HEX values', () => {
  assert.deepEqual(DEFAULT_EDITOR_COLOR_PRESETS, [
    '#000000', '#3C3C3C', '#5B5B5B', '#8E8E8E', '#C5C5C5', '#EBEBEB', '#F1F1F1', '#FFFFFF',
    '#F20011', '#FD0083', '#FF00E8', '#0F00E7', '#00EFFE', '#00F035', '#7FFA38', '#EDFF3B',
    '#F4C5C5', '#FDE7D3', '#FFF0C8', '#D3E7CE', '#C9DCDF', '#C8DEF0', '#D4CCE4', '#E8CAD7',
    '#E98D8F', '#FAC495', '#FFE194', '#ACD2A1', '#96BDC1', '#93BEE2', '#AB9CCE', '#D19BB4',
    '#DF595C', '#F7A866', '#FFD466', '#86BD76', '#699BA5', '#619ED4', '#8370B8', '#BC6E94',
    '#BB000D', '#E5853A', '#F1BA3D', '#5C9F4C', '#397682', '#317ABB', '#5D439A', '#9E426C',
    '#87000A', '#AD5318', '#B9851F', '#2E6B23', '#0F4651', '#024986', '#2F1967', '#6B173D',
    '#570606', '#6E3710', '#755514', '#214518', '#0C2E35', '#063056', '#1E1242', '#44112A',
  ]);
  assert.ok(DEFAULT_EDITOR_COLOR_PRESETS.every((color) => /^#[0-9A-F]{6}$/.test(color)));
});

test('editor color controls use app-owned presets and a HEX-only text entry', () => {
  assert.doesNotMatch(editorPanelsSource, /type=["']color["']/);
  assert.match(editorPanelsSource, /aria-label="HEX 색상"/);
  assert.match(editorPanelsSource, /className="sf-preset-grid"/);
});

test('editor color popover shows both default presets and a functional custom palette', () => {
  assert.match(editorPanelsSource, /className="sf-color-palette"/);
  assert.match(editorPanelsSource, /aria-label="색조"/);
  assert.equal(typeof editorAppearance.hexToHsv, 'function');
  assert.equal(typeof editorAppearance.hsvToHex, 'function');
  assert.deepEqual(editorAppearance.hexToHsv('#FF0000'), { h: 0, s: 100, v: 100 });
  assert.equal(editorAppearance.hsvToHex({ h: 210, s: 100, v: 100 }), '#0080FF');
});

test('element selection does not paint the parent block as selected', () => {
  assert.match(editorSource, /className=\{`canvas-block\$\{blockSelected \? ' on' : ''\}/);
  assert.doesNotMatch(editorSource, /className=\{`canvas-block\$\{blockActive \? ' on' : ''\}/);
});

test('multi-selection keeps every member and its Moveable bounds visibly in sync while dragging', () => {
  assert.match(editorSource, /selectionCount > 1 \? ' multi-selected' : ''/);
  assert.match(editorSource, /syncPointerGroupSelectionBounds\(\);/);
  assert.match(editorStylesSource, /\.el\.on\.multi-selected \{[^}]*outline:/);
});

test('template catalog shows readable completed references instead of checkerboards', () => {
  assert.match(editorStylesSource, /\.frame-layout-prev\.template \{[^}]*aspect-ratio:\s*3\s*\/\s*4/s);
  assert.match(editorStylesSource, /\.frame-layout-prev\.template > img \{[^}]*object-fit:\s*contain[^}]*background:\s*#fff/s);
  assert.doesNotMatch(editorStylesSource, /\.frame-layout-prev\.template \{[^}]*linear-gradient/s);
});

test('preset colors keep eight columns with practical pointer targets', () => {
  assert.match(editorStylesSource, /\.sf-color-popover\s*\{[^}]*width:\s*244px/s);
  assert.match(editorStylesSource, /\.sf-preset-grid\s*\{[^}]*grid-template-columns:\s*repeat\(8,\s*24px\)[^}]*gap:\s*4px/s);
  assert.match(editorStylesSource, /\.sf-preset\s*\{[^}]*width:\s*24px[^}]*height:\s*24px[^}]*border-radius:\s*4px/s);
  assert.match(editorStylesSource, /\.sf-preset::after\s*\{[^}]*inset:\s*-2px/s);
});

test('compact Moveable controls expose a larger invisible hit surface', () => {
  assert.match(moveableStylesSource, /\.moveable-control::after\s*\{[^}]*inset:\s*-7px/s);
});

test('auto-height text keeps side controls attached to the selection border', () => {
  assert.match(editorSource, /className=\{autoHeightTextTarget \? 'moveable-auto-text' : undefined\}/);
  assert.doesNotMatch(moveableStylesSource, /\.moveable-auto-text \.moveable-[we]\s*\{[^}]*margin-left:/s);
  assert.match(moveableStylesSource, /\.moveable-control\s*\{[^}]*margin-left:\s*-6px/s);
  assert.match(moveableStylesSource, /\.moveable-auto-text \.moveable-control::after\s*\{[^}]*inset:\s*-2px/s);
});

test('crop image clipping does not clip the outside half of resize hit targets', () => {
  assert.match(editorSource, /className="crop-frame-image"/);
  assert.match(editorStylesSource, /\.crop-layer\s*\{[^}]*z-index:\s*7[^}]*pointer-events:\s*none/s);
  assert.match(editorStylesSource, /\.crop-frame\s*\{[^}]*overflow:\s*visible/s);
  assert.match(editorStylesSource, /\.crop-frame-image\s*\{[^}]*overflow:\s*hidden[^}]*pointer-events:\s*none/s);
  assert.match(editorStylesSource, /\.crop-h::after\s*\{[^}]*inset:\s*-6px/s);
  assert.match(editorStylesSource, /\.crop-bar\s*\{[^}]*pointer-events:\s*none/s);
  assert.match(editorStylesSource, /\.crop-bar button\s*\{[^}]*pointer-events:\s*auto/s);
});

test('speech bubbles hide the neutral editor outline outside the actual bubble border', () => {
  assert.match(editorStylesSource, /\.el-speech-bubble:not\(\.on\)\s*\{[^}]*outline:\s*none/);
});

test('photo content blocks discard only source-linked legacy AI copy inside the photo', () => {
  const legacyHeadline = {
    id: 'legacy-headline', type: 'text', text: '사진 위 카피', sourceBlockId: 'shot-1', copyRole: 'headline',
    x: 120, y: 110, w: 600, h: 80,
  };
  const legacyBody = {
    id: 'legacy-body', type: 'text', text: '사진 아래쪽 카피', sourceBlockId: 'shot-1', copyRole: 'body',
    x: 120, y: 560, w: 760, h: 40,
  };
  const photo = {
    id: 'photo', type: 'image', src: '/cut.png', sourceBlockId: 'shot-1', x: 60, y: 50, w: 880, h: 560,
  };
  const faqText = { id: 'faq', type: 'text', text: 'FAQ' };
  const preserved = [
    { id: 'seller', type: 'text', text: '셀러가 쓴 말풍선', x: 120, y: 160, w: 200, h: 40 },
    { id: 'badge', type: 'text', text: 'SALE', x: 720, y: 90, w: 120, h: 34 },
    { id: 'hook-title', type: 'text', text: '제품명', sourceBlockId: 'shot-1', copyRole: 'hookTitle', x: 60, y: 275, w: 880, h: 110 },
    { id: 'caption', type: 'text', text: '사진 밖 캡션', sourceBlockId: 'shot-1', copyRole: 'headline', x: 60, y: 642, w: 880, h: 34 },
    { id: 'label', type: 'text', text: 'POINT 01', sourceBlockId: 'shot-1', copyRole: 'label', x: 60, y: 44, w: 400, h: 24 },
  ];
  const input = [
    { id: 'shot', contentRole: 'hero', elements: [photo, legacyHeadline, legacyBody, ...preserved] },
    { id: 'faq-block', kind: 'faq', elements: [faqText] },
  ];

  const output = stripPhotoBlockTextElements(input);

  assert.deepEqual(output[0].elements, [photo, ...preserved]);
  assert.deepEqual(output[1].elements, [faqText]);
  assert.equal(output[1], input[1], 'unrelated blocks keep their object identity');
});

test('row photo blocks remove linked overlay copy but preserve seller text and row labels', () => {
  const output = stripPhotoBlockTextElements([{
    id: 'row', kind: 'twocol', elements: [
      { id: 'image', type: 'image', src: '/row.png', sourceBlockId: 'shot-2', x: 60, y: 50, w: 430, h: 500 },
      { id: 'old-copy', type: 'text', text: '옛 행 카피', sourceBlockId: 'shot-2', copyRole: 'body', x: 80, y: 500, w: 360, h: 34 },
      { id: 'row-label', type: 'text', text: 'FRONT', sourceBlockId: 'shot-2', copyRole: 'label', x: 60, y: 566, w: 430, h: 26 },
      { id: 'seller', type: 'text', text: '셀러 배지', x: 100, y: 100, w: 120, h: 30 },
    ],
  }]);

  assert.deepEqual(output[0].elements.map((element) => element.id), ['image', 'row-label', 'seller']);
});

test('빈 캡션 placeholder는 편집 화면에만 점선 안내로 보이고 preview에서는 렌더하지 않는다', () => {
  const textBranch = editorSource.slice(
    editorSource.indexOf("if (el.type === 'text')"),
    editorSource.indexOf("if (el.type === 'license-verify')"),
  );
  assert.match(textBranch, /const emptyText = isEmptyTextElement\(el\)/);
  assert.match(textBranch, /if \(preview && emptyText\) return null/);
  assert.match(textBranch, /emptyText \? el\.placeholder : display/);
  assert.match(textBranch, /el-placeholder/);
  assert.match(textBranch, /el-empty-text/, '말풍선을 포함한 모든 빈 텍스트에 export 제거 표식이 필요하다');
  assert.match(editorStylesSource, /\.el-placeholder:not\(\.editing\)\s*\{[^}]*color:\s*var\(--fg-3\)[^}]*font-style:\s*italic[^}]*outline:\s*1px dashed var\(--fg-3\)/s);
});

test('ordinary text resizes horizontally only — height is content-derived (auto render)', () => {
  // 세로 핸들을 주면 드래그 순간만 커졌다가 h 동기화가 실측값으로 되돌리는 죽은 컨트롤이 된다.
  assert.deepEqual(resizePolicyForElement({ type: 'text' }, true), {
    keepRatio: false,
    directions: ['w', 'e'],
  });
});

test('speech bubbles retain free resize handles while thin rules keep only their endpoints', () => {
  assert.deepEqual(resizePolicyForElement({ type: 'text', shape: 'bubble' }, true), {
    keepRatio: false,
    directions: ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'],
  });
  assert.deepEqual(resizePolicyForElement({ type: 'line' }, true), {
    keepRatio: false,
    directions: ['w', 'e'],
  });
});

test('thin rules keep a minimum twelve-pixel pointer target at every editor zoom', () => {
  assert.equal(lineHitStrokeWidth(2, 1), 12);
  assert.equal(lineHitStrokeWidth(2, 0.4), 30);
  assert.equal(lineHitStrokeWidth(16, 1), 16);
  assert.match(editorSource, /stroke="transparent" strokeWidth=\{hitWidth\}/);
});

test('text restores direct rotation while thin rules keep the numeric control', () => {
  assert.equal(shouldShowRotationHandle({ type: 'text' }), true);
  assert.equal(shouldShowRotationHandle({ type: 'line' }), false);
  assert.equal(shouldShowRotationHandle({ type: 'text', shape: 'bubble' }), true);
  assert.equal(shouldShowRotationHandle({ type: 'image' }), true);
  assert.match(editorPanelsSource, /labelText="회전" value=\{el\.rotate \|\| 0\}/);
  assert.match(editorSource, /rotatable=\{showRotationHandle\}/);
});

test('auto-height text expands its pointer target without covering adjacent table rules', () => {
  assert.match(editorStylesSource, /\.el-text:not\(\.editing\)::before/);
  assert.match(editorStylesSource, /top: calc\(-3px \* var\(--canvas-inv, 1\)\)/);
  assert.match(editorStylesSource, /bottom: calc\(-3px \* var\(--canvas-inv, 1\)\)/);
});

test('canvas routes every movable element through one pointer drag without native text ranges', () => {
  const elementPick = editorSource.slice(
    editorSource.indexOf('const pick = (e) => {'),
    editorSource.indexOf('const finishClick = (e) => {'),
  );
  const beforeTextDrag = elementPick.slice(0, elementPick.indexOf('if (isolateText)'));
  const textDragPick = elementPick.slice(
    elementPick.indexOf('if (isolateText)'),
    elementPick.indexOf('const preserveSelection'),
  );

  assert.doesNotMatch(beforeTextDrag, /e\.preventDefault\(\)/);
  assert.match(textDragPick, /e\.preventDefault\(\)/);
  assert.match(elementPick, /window\.getSelection\?\.\(\)\?\.removeAllRanges\(\)/);
  assert.match(elementPick, /onElementDragStart\?\.\(e, el, \{ isolateText, preserveSelection \}/);
  assert.doesNotMatch(editorSource, /onMultiDragStart|onTextDragStart|onObjectGroupDragStart/);
  assert.match(editorSource, /draggable=\{false\}/);
  assert.match(editorSource, /data-editor-snap-guide="vertical"/);
  assert.match(editorSource, /data-editor-snap-guide="horizontal"/);
  assert.match(editorStylesSource, /\.editor-snap-guide\.vertical/);
  assert.match(editorStylesSource, /\.editor-snap-guide\.horizontal/);
  assert.match(editorStylesSource, /\.editor-snap-guide\.vertical\s*\{[^}]*width:\s*calc\(1px \* var\(--inv, 1\)\)/s);
  assert.match(editorStylesSource, /\.editor-snap-guide\.horizontal\s*\{[^}]*height:\s*calc\(1px \* var\(--inv, 1\)\)/s);
  assert.match(editorStylesSource, /\.ed-canvas\s*\{[^}]*user-select:\s*none[^}]*-webkit-user-select:\s*none/s);
  assert.match(editorStylesSource, /\.el-text:not\(\.editing\)\s*\{[^}]*user-select:\s*none[^}]*touch-action:\s*none/s);
  assert.match(editorStylesSource, /\.el-text\.editing\s*\{[^}]*user-select:\s*text[^}]*touch-action:\s*auto/s);
  assert.match(editorSource, /onDoubleClick=\{preview \? undefined : \(e\) => \{\s*e\.stopPropagation\(\); pendingBubbleFit\.current = null; onEdit\(el\.id\)/s);
  assert.match(editorSource, /onDoubleClick=\{\(e\) => \{ e\.stopPropagation\(\); pendingTextSize\.current = null; onEdit\(el\.id\)/);
});

test('텍스트 높이 동기화 기준선은 StrictMode 재마운트에서도 유지된다', () => {
  // dev 는 setup→cleanup→setup 으로 두 번 돈다. ref 는 cleanup 으로 안 돌아가므로 마운트
  // 전용 cleanup 이 없으면 두 번째 setup 이 '이미 무장됨'이 돼, 문서를 여는 것만으로 전
  // 텍스트의 h 가 재기록되고 자동저장이 나간다(2026-08-16 리뷰).
  assert.match(editorSource, /useLayoutEffect\(\(\) => \(\) => \{ hSyncArmed\.current = false; \}, \[\]\);/);
});

test('entering text edit places the caret at the end for normal text and speech bubbles', () => {
  // 방금 만든 텍스트만 예외 — 기본 문구가 통째로 선택돼 그냥 타이핑하면 갈아 끼워진다(오너 8/16).
  assert.match(editorSource, /range\.selectNodeContents\(node\);\s*if \(!selectAll\) range\.collapse\(false\);/s);
  assert.match(editorSource, /focusEditableAtEnd\(isSpeechBubbleElement\(el\) \? textRef\.current : ref\.current, fresh\)/);
  // 편집 시작 때 표식을 떼면 "손 안 댄 안내 문구"인지 끝에 가서 알 수 없다 — 엿보기만 한다.
  assert.match(editorSource, /const fresh = FRESH_TEXT_IDS\.has\(el\.id\);/);
  assert.match(editorSource, /if \(String\(value \?\? ''\)\.trim\(\) !== DEFAULT_TEXT_BODY\) FRESH_TEXT_IDS\.delete\(elementId\);/);
  assert.doesNotMatch(editorSource, /setTimeout\(\(\) => (?:textRef|ref)\.current/);
});

test('new ordinary text starts as an immediately editable Figma-style point text box', () => {
  const addTextSource = editorSource.slice(
    editorSource.indexOf('const addText ='),
    editorSource.indexOf('/* ---- 정보 블록', editorSource.indexOf('const addText =')),
  );

  // 요소 생성은 textPresets.js 로 이동 — 빈 텍스트+auto 계약은 text-presets.test.mjs 가
  // 동작 수준으로 고정한다. 여기서는 addText 가 그 빌더를 쓰고 즉시 편집에 들어가는지만 본다.
  assert.match(addTextSource, /buildTextPresetElement\(preset\)/);
  assert.match(addTextSource, /setEditEl\(el\.id\)/);
  assert.match(editorSource, /const previewAutoTextSize = useCallback/);
  assert.match(editorSource, /naturalTextWidth\(node, value\)/);
  assert.match(editorSource, /h:\s*Math\.max\(1, Math\.ceil\(node\.scrollHeight\)\)/);
  assert.match(editorSource, /onInput=\{\(e\) => \{ if \(editing\) previewAutoTextSize\(e\.currentTarget\); \}\}/);
  // 텍스트 커밋은 항상 onTextCommit(commitText)으로, 빈 텍스트 정리는 편집 종료 감시가 맡는다 —
  // blur를 안 타는 종료 경로(패널 클릭 등)에서도 유령 요소가 남지 않아야 한다. 단 copyRole/
  // sourceBlockId가 달린 카피 자리는 생성 완료 때 AI 카피가 착지할 슬롯이라 지우면 안 된다.
  assert.match(editorSource, /if \(onTextCommit\) onTextCommit\(blockId, el\.id, value, nextSize\)/);
  assert.match(editorSource, /function pruneEmptyTextEl\(elId\)/);
  assert.match(editorSource, /target\.copyRole \|\| target\.sourceBlockId/);
  assert.match(editorSource, /if \(prev && prev !== editEl\) pruneEmptyTextEl\(prev\)/);
});

test('manually resizing point text converts it into a fixed text box', () => {
  // 일반 텍스트는 라이브 드래그에서 px 높이를 인라인으로 박지 않고(height:auto 유지),
  // 커밋에도 h를 넣지 않는다 — h 동기화 효과가 실측값으로 잰다.
  assert.match(editorSource, /const plainText = elNow\?\.type === 'text' && elNow\.shape !== 'bubble'/);
  assert.match(editorSource, /if \(plainText\) target\.style\.height = 'auto'/);
  assert.match(editorSource, /\.\.\.\(plainText \? \{\} : \{ h: Math\.round\(rect\.h\) \}\)/);
  assert.match(editorSource, /\.\.\.\(plainText && elNow\.textSizing === 'auto' \? \{ textSizing: 'fixed' \} : \{\}\)/);
  // 렌더 높이는 항상 auto — 고정폭 텍스트도 크기를 키우면 상자가 따라 자라고,
  // 저장되는 el.h는 h 동기화 효과(offsetHeight)가 맞춘다. 고정되는 건 폭뿐이다.
  assert.match(editorSource, /style=\{\{ \.\.\.base, height: 'auto',/);
  assert.match(editorSource, /const h = Math\.max\(1, Math\.ceil\(node\.offsetHeight\)\)/);
  assert.match(editorPanelsSource, /onChange\(\{ w, \.\.\.\(!isBubble && el\.textSizing === 'auto' \? \{ textSizing: 'fixed' \} : \{\}\) \}\)/);
});

test('도형·선에는 사각 회색 외곽선을 긋지 않는다 — 원·사선 둘레의 상자가 모양을 왜곡한다(오너 8/15)', () => {
  // 알 수 없는 요소 타입은 기존 .el 외곽선을 유지해야 화면에서 찾을 수 있다 — 도형·선만 게이트.
  assert.match(editorSource, /className=\{cls\(el\.type === 'line' \? 'el-line' : el\.type === 'shape' \? 'el-shape' : ''\)\}/);
  assert.match(editorStylesSource, /\.el\.el-shape, \.el\.el-line, \.el\.el-shape:hover, \.el\.el-line:hover \{ outline: none; \}/);
});

test('새 텍스트는 자동 관리 블록(정보·사이즈·세탁·AI 고지)을 피해 가까운 일반 블록에 붙는다', () => {
  // 자동 관리 블록의 요소는 폼 재적용·생성 병합 때 통째로 재생성돼 셀러 텍스트가 사라진다.
  // 술어는 infoPresets가 한 벌로 소유한다. 콘텐츠가 블록 바닥까지 차 있으면(프레임 템플릿)
  // 아래 빈 띠 대신 위쪽(80)에 둔다.
  assert.match(editorSource, /isAutoManagedBlock\(bs0\[idx\]\)/);
  assert.match(editorSource, /const bs0 = latestBlocks\.current \|\| blocks/);
  assert.match(editorSource, /const roomBelow = \(block\.h \|\| 220\) - contentBottom/);
  assert.match(editorSource, /contentBottom > 0 && roomBelow >= 20 \? contentBottom \+ 32 : 80/);
  assert.match(infoPresetsSource, /export function isAutoManagedBlock/);
});

test('block quick actions stay visible while the top-level block is selected', () => {
  assert.match(editorStylesSource, /\.canvas-block:hover \.quick, \.canvas-block\.on \.quick\s*\{[^}]*opacity:\s*1[^}]*visibility:\s*visible[^}]*pointer-events:\s*auto/s);
});

test('shared pointer drag covers frame controls, movable group members, rotation bounds, and cleanup', () => {
  const slotButtonStart = editorSource.indexOf('<button className={`slot-add');
  const slotButton = editorSource.slice(slotButtonStart, editorSource.indexOf('</button>', slotButtonStart));
  const pointerDrag = editorSource.slice(
    editorSource.indexOf('const startPointerSelectionDrag ='),
    editorSource.indexOf('const startElementDrag ='),
  );

  assert.match(slotButton, /onPointerDown=\{\(e\) => e\.stopPropagation\(\)\}/);
  assert.doesNotMatch(slotButton, /if \(draggedPointer\.current\) return/);
  assert.match(pointerDrag, /!candidate\.hidden/);
  assert.match(pointerDrag, /!candidate\.locked/);
  assert.match(pointerDrag, /nodeById\[candidate\.id\]/);
  assert.match(pointerDrag, /getBoundingClientRect\(\)/);
  assert.match(pointerDrag, /activePointerDragCleanup\.current = cleanup/);
  assert.match(editorSource, /activePointerDragCleanup\.current\?\.\(\)/);
});

test('editor copy and paste shortcuts duplicate canvas selections without stealing typing shortcuts', () => {
  assert.match(editorSource, /mod && !typing && !kb\.current\.croppingOn && copyKey && kb\.current\.copy\?\.\(\)/);
  assert.match(editorSource, /mod && !typing && !kb\.current\.croppingOn && pasteKey && kb\.current\.paste\?\.\(\)/);
  assert.match(editorSource, /copy:\s*copySelectedElements, paste:\s*pasteCopiedElements/);
  assert.match(editorSource, /setSelEls\(pasted\.selectedIds\)/);
});

test('text numeric controls allow an empty editing draft and commit the finished number', () => {
  assert.equal(typeof editorAppearance.commitNumberDraft, 'function');
  assert.equal(editorAppearance.commitNumberDraft('', { min: 1, max: 10000, fallback: 24 }), 24);
  assert.equal(editorAppearance.commitNumberDraft('7', { min: 1, max: 10000, fallback: 24 }), 7);
  assert.equal(editorAppearance.commitNumberDraft('125.5', { min: 1, max: 10000, fallback: 24 }), 125.5);
  assert.match(editorPanelsSource, /function DraftNumberInput/);
  assert.match(editorPanelsSource, /onChange=\{\(event\) => setDraft\(event\.target\.value\)\}/);
  assert.match(editorPanelsSource, /iconText="가로"[^>]*min=\{1\}[^>]*max=\{10000\}/);
});

test('image resize keeps the existing lock preference', () => {
  assert.deepEqual(resizePolicyForElement({ type: 'image' }, true), {
    keepRatio: true,
    directions: ['nw', 'ne', 'sw', 'se'],
  });
  assert.equal(resizePolicyForElement({ type: 'image' }, false).keepRatio, false);
});

test('unlocked images accept independent vertical resize handles', () => {
  assert.deepEqual(imageResizeRect({
    element: { type: 'image', crop: undefined },
    start: { x: 120, y: 200, w: 398, h: 517 },
    width: 398,
    height: 620,
    beforeTranslate: [0, 0],
    naturalWidth: 398,
    naturalHeight: 517,
    lockRatio: false,
  }), { x: 120, y: 200, w: 398, h: 620 });

  assert.deepEqual(imageResizeRect({
    element: { type: 'image', crop: undefined },
    start: { x: 120, y: 200, w: 398, h: 517 },
    width: 398,
    height: 620,
    beforeTranslate: [0, -103],
    naturalWidth: 398,
    naturalHeight: 517,
    lockRatio: false,
  }), { x: 120, y: 97, w: 398, h: 620 });
});

test('locked frame images keep the frame ratio instead of snapping to the source image ratio', () => {
  assert.deepEqual(imageResizeRect({
    element: { type: 'image', frameSlot: true, crop: undefined },
    start: { x: 60, y: 50, w: 880, h: 644 },
    width: 880,
    height: 644,
    beforeTranslate: [0, 0],
    naturalWidth: 880,
    naturalHeight: 1144,
    lockRatio: true,
  }), { x: 60, y: 50, w: 880, h: 644 });
});

test('speech bubble path applies the editable pixel corner radius', () => {
  const rounded = speechBubblePath({ width: 400, height: 200, radius: 45 });
  const square = speechBubblePath({ width: 400, height: 200, radius: 0 });
  assert.match(rounded, /^M 45 0/);
  assert.notEqual(rounded, square);
});

test('speech bubble path keeps a clearly visible tail at compact sizes', () => {
  const path = speechBubblePath({ width: 196, height: 87, radius: 28 });
  const tailTip = path.match(/L ([\d.]+) 87 L/);

  assert.ok(tailTip, 'the tail reaches the bottom edge of the bubble box');
  assert.ok(Number(tailTip[1]) < 32, 'the tail tip extends clearly beyond its left base');
});

test('상세페이지 템플릿 프레임(templateId)은 자동채움으로 sourceBlockId 붙어도 텍스트 보존', () => {
  const templateBlock = {
    id: 'b', templateId: 'kiwi-t5-01-cover', kind: 'styling', contentRole: 'custom',
    elements: [
      { id: 'img', type: 'image', frameSlot: true, src: 'cut.jpg', sourceBlockId: 'sb-1' }, // 자동채움된 슬롯
      { id: 'txt', type: 'text', text: 'Fashion Cover' },
    ],
  };
  const out = stripPhotoBlockTextElements([templateBlock]);
  assert.equal(out[0].elements.length, 2, '템플릿 텍스트가 걷히지 않는다');
  assert.ok(out[0].elements.some((el) => el.type === 'text'));
});

test('프레임 슬롯 채움(frameSlot+sourceBlockId)은 templateId 없어도 텍스트 보존', () => {
  const frameBlock = {
    id: 'b', kind: 'styling', contentRole: 'custom',
    elements: [
      { id: 'img', type: 'image', frameSlot: true, src: 'cut.jpg', sourceBlockId: 'sb-1' },
      { id: 'txt', type: 'text', text: '라벨' },
    ],
  };
  const out = stripPhotoBlockTextElements([frameBlock]);
  assert.ok(out[0].elements.some((el) => el.type === 'text'), 'frameSlot 채움은 생성 카피 블록이 아니다');
});

test('생성 컷 블록이어도 copyRole 없는 셀러 텍스트는 보존한다', () => {
  const cutBlock = {
    id: 'b', kind: 'styling', contentRole: 'hero',
    elements: [
      { id: 'img', type: 'image', src: 'cut.jpg', sourceBlockId: 'sb-1', x: 60, y: 50, w: 880, h: 560 },
      { id: 'copy', type: 'text', text: '셀러가 직접 쓴 글자', x: 120, y: 110, w: 300, h: 40 },
    ],
  };
  const out = stripPhotoBlockTextElements([cutBlock]);
  assert.ok(out[0].elements.some((el) => el.type === 'text'), 'sourceBlockId 없는 셀러 글자는 보존한다');
});

test('선 색: 템플릿 선(stroke)도 패널에서 바꾼 색이 그대로 보인다', async () => {
  const { lineColor, lineColorPatch, DEFAULT_LINE_COLOR } = await import('../../src/features/editor/editorAppearance.js');
  // 템플릿·프리셋 선은 stroke 로 색을 들고 온다 — 예전엔 패널이 fill 만 바꿔서 색이 안 바뀌었다.
  const templateLine = { type: 'line', stroke: '#e5e5e3', fill: '#0e0d14' };
  assert.equal(lineColor(templateLine), '#e5e5e3');
  assert.equal(lineColor({ ...templateLine, ...lineColorPatch('#ff0000') }), '#ff0000');
  // 예전에 그린 선(fill 만)과 빈 선도 같은 규칙으로 읽힌다.
  assert.equal(lineColor({ type: 'line', fill: '#123456' }), '#123456');
  assert.equal(lineColor({ type: 'line', stroke: 'none', fill: '#123456' }), '#123456');
  assert.equal(lineColor({ type: 'line' }), DEFAULT_LINE_COLOR);
});

test('선 색 패널은 lineColor 로 읽고 lineColorPatch 로 쓴다(fill 만 바꾸던 회귀 방지)', () => {
  const panels = readFileSync(fileURLToPath(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url)), 'utf8');
  assert.match(panels, /isLine \? \(\s*<SwatchField value=\{lineColor\(el\)\}[^>]*onColor=\{\(c\) => onChange\(lineColorPatch\(c\)\)\}/);
  const editor = readFileSync(fileURLToPath(new URL('../../src/features/editor/Editor.jsx', import.meta.url)), 'utf8');
  assert.match(editor, /const lc = lineColor\(el\)/);
});
