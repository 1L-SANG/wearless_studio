import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const storyboardSource = readFileSync(
  new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
  'utf8',
);
const featureStyles = readFileSync(
  new URL('../../src/styles/features.css', import.meta.url),
  'utf8',
);

test('N6 matching badge overlays the image only when the seller changed it in the inspector', () => {
  // 2026-08-14 오너 확정: 캡션 줄 상시 칩 폐기 → 이미지 우측 하단 오버레이,
  // 셀러가 인스펙터에서 직접 바꾼 컷(matchIdsOrigin 'user')에만 뜬다.
  const media = storyboardSource.slice(
    storyboardSource.indexOf('function StoryboardMedia'),
    storyboardSource.indexOf('function CardDragSurface'),
  );
  assert.match(media, /block\.matchIdsOrigin === 'user'/, 'the overlay must gate on the user-change marker');
  assert.match(media, /className="sb-match-overlay"/, 'the badge renders on the image, not in the caption');

  const caption = storyboardSource.slice(
    storyboardSource.indexOf('function StoryboardCaption'),
    storyboardSource.indexOf('function StoryboardCardActions'),
  );
  assert.doesNotMatch(caption, /sb-match-chip/, 'the always-on caption chip is retired');
  assert.ok(caption.includes('className="sb-caption-values"'), 'caption values must remain rendered');
});

test('N6 matching badge sits at the image bottom-right corner', () => {
  assert.match(
    featureStyles,
    /\.sb-match-overlay \{[^}]*position: absolute;[^}]*right: 7px;[^}]*bottom: 7px;/,
  );
  assert.doesNotMatch(featureStyles, /\.sb-match-chip/);
});

test('N7 swatch labels win, then trimmed names, then numbered fallbacks for mock and HTTP shapes', () => {
  const entry = storyboardSource.slice(
    storyboardSource.indexOf('function prepareStoryboardEntry'),
    storyboardSource.indexOf('function ComposeModeSummary'),
  );
  const expression = entry.match(/label:\s*([^,]+),\s*hex: hexFor\(color\)/)?.[1];
  assert.ok(expression, 'the allColorOpts label expression must stay next to hexFor');
  const deriveLabel = new Function('hydratedCatalogs', 'color', 'index', `return ${expression}`);
  const catalogs = {
    swatchColors: [
      { id: 'pink', label: '핑크' },
      { id: 'light-gray', label: '라이트그레이' },
    ],
  };
  const productsByMode = {
    mock: [
      { id: 'mock-1', swatchId: 'pink', name: '블랙' },
      { id: 'mock-2', swatchId: 'unknown', name: '  민트  ' },
      { id: 'mock-3', swatchId: '', name: '' },
    ],
    http: [
      { id: 'http-1', swatchId: 'light-gray', name: 'Black' },
      { id: 'http-2', swatchId: 'missing', name: '  Navy  ' },
      { id: 'http-3', swatchId: null, name: null },
    ],
  };

  assert.deepEqual(
    productsByMode.mock.map((color, index) => deriveLabel(catalogs, color, index)),
    ['핑크', '민트', '색상 3'],
  );
  assert.deepEqual(
    productsByMode.http.map((color, index) => deriveLabel(catalogs, color, index)),
    ['라이트그레이', 'Navy', '색상 3'],
  );
  assert.match(entry, /hex: hexFor\(color\)/, 'existing hex derivation must remain unchanged');
});

test('N8 selecting A then B stores only B, and selecting B again clears the array', () => {
  const inspector = storyboardSource.slice(
    storyboardSource.indexOf('function Inspector'),
    storyboardSource.indexOf('function prepareStoryboardEntry'),
  );
  // 셀러 변경은 matchIdsOrigin 'user' 로 함께 기록된다(카드 오버레이 표시 조건).
  const expression = inspector.match(/onChange\(\{ matchIds: (.+?), matchIdsOrigin: 'user' \}\)/)?.[1];
  assert.ok(expression, 'matching selection must update the matchIds array and stamp the user origin');
  const nextMatchIds = new Function('on', 'm', `return ${expression}`);

  let matchIds = [];
  const click = (id) => {
    const m = { id };
    const on = matchIds.includes(id);
    matchIds = nextMatchIds(on, m);
    assert.ok(Array.isArray(matchIds), 'the blocks contract keeps matchIds as an array');
    assert.ok(matchIds.length <= 1, 'the UI must never save multiple matching clothes');
  };

  click('A');
  assert.deepEqual(matchIds, ['A']);
  click('B');
  assert.deepEqual(matchIds, ['B']);
  click('B');
  assert.deepEqual(matchIds, []);
  assert.doesNotMatch(inspector, /new Set\(block\.matchIds/);
  assert.match(inspector, /aria-pressed=\{on\}/, 'native buttons expose their selected state to keyboards and assistive tech');
});

test('legacy storyboard blocks keep only their first matching garment on entry', () => {
  const entry = storyboardSource.slice(
    storyboardSource.indexOf('function prepareStoryboardEntry'),
    storyboardSource.indexOf('function ComposeModeSummary'),
  );
  assert.match(entry, /block\.matchIds\.length > 1/);
  assert.match(entry, /matchIds: normalizeMatchIds\(block\.matchIds\)/);
});

test('set members hide the cut-type tabs and matching editor entirely', () => {
  // 2026-08-15 오너: 세트 멤버 인스펙터에는 잠금 표시 대신 컷 종류·매칭 편집을 아예 숨긴다.
  assert.doesNotMatch(storyboardSource, /묶음을 푼 뒤 바꿀 수 있어요/);
  assert.doesNotMatch(storyboardSource, /장소 세트로 묶인 동안 고정돼요/);
  assert.match(storyboardSource, /\{!spaceContext && \(\s*<div className="insp-sec">\s*<div className="sb-cut-label-row">/);
  assert.match(storyboardSource, /WORN_CUT_TYPES\.has\(block\.cutType\) && !block\.spaceGroupId/);
});

test('N8 matching list scrolls horizontally inside the independently vertical inspector', () => {
  assert.match(
    featureStyles,
    /\.sb-match-inline \{[^}]*overflow-x: auto;[^}]*overflow-y: hidden;[^}]*overscroll-behavior-inline: contain/,
  );
  assert.match(featureStyles, /\.sb-match-inline \.match-grid \{[^}]*display: flex;[^}]*width: max-content/);
  assert.match(featureStyles, /\.sb-match-inline \.match-cell \{[^}]*flex: 0 0 92px/);
  assert.match(featureStyles, /\.sb-scroll-l, \.insp-col \{[^}]*overflow-y: auto/);
  assert.doesNotMatch(featureStyles, /\.sb-match-inline \{[^}]*overflow-y: auto/);
});

test('N11 caption typography is 13px and long matched captions stay inside one line', () => {
  assert.match(featureStyles, /\.sb-canvas-caption \{[^}]*font-size: 13px/);
  assert.match(featureStyles, /\.sb-caption-color \{[^}]*max-width: 64px;[^}]*font-size: 13px/);
  assert.match(featureStyles, /\.sb-caption-dot \{[^}]*width: 11px;[^}]*height: 11px/);
  assert.match(
    featureStyles,
    /\.sb-canvas-caption \{[^}]*width: 100%;[^}]*max-width: 100%;[^}]*overflow: hidden;[^}]*white-space: nowrap;[^}]*box-sizing: border-box/,
  );
  assert.match(
    featureStyles,
    /\.sb-caption-values \{[^}]*min-width: 0;[^}]*overflow: hidden;[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap/,
    '사이드 · 미디움샷 · 라이트그레이 조합은 줄바꿈 대신 말줄임되어야 한다',
  );
});
