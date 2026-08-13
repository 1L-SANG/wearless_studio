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

test('N6 matching chip precedes caption values whenever a match is selected', () => {
  const caption = storyboardSource.slice(
    storyboardSource.indexOf('function StoryboardCaption'),
    storyboardSource.indexOf('function StoryboardCardActions'),
  );
  const chipIndex = caption.indexOf('className="sb-match-chip"');
  const valuesIndex = caption.indexOf('className="sb-caption-values"');

  assert.ok(chipIndex >= 0, 'selected matching clothing must render its chip');
  assert.ok(valuesIndex >= 0, 'caption values must remain rendered without a match');
  assert.ok(chipIndex < valuesIndex, 'the conditional chip must be the leftmost caption child');
});

test('N6 matching chip uses a white background with black border and text', () => {
  assert.match(
    featureStyles,
    /\.sb-match-chip \{[^}]*background: #fff;[^}]*border: 1px solid #111;[^}]*color: #111;/,
  );
  assert.doesNotMatch(featureStyles, /\.sb-match-chip \{[^}]*#f6f2ea/);
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
  const expression = inspector.match(/onChange\(\{ matchIds: ([^}]+) \}\)/)?.[1];
  assert.ok(expression, 'matching selection must update the matchIds array');
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
  assert.match(featureStyles, /\.sb-match-chip \{[^}]*font-size: 12px/);
  assert.match(
    featureStyles,
    /\.sb-canvas-caption \{[^}]*width: 100%;[^}]*max-width: 100%;[^}]*overflow: hidden;[^}]*white-space: nowrap;[^}]*box-sizing: border-box/,
  );
  assert.match(
    featureStyles,
    /\.sb-caption-values \{[^}]*min-width: 0;[^}]*overflow: hidden;[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap/,
    '사이드 · 미디움샷 · 라이트그레이와 matching chip 조합은 줄바꿈 대신 말줄임되어야 한다',
  );
});
