import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { allowedCutTypeOptionsForSection } from '../../src/lib/storyboardTaxonomy.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const storyboardSource = read('../../src/features/storyboard/Storyboard.jsx');
const chromeSource = read('../../src/features/shell/ChromeLayout.jsx');
const uiSource = read('../../src/components/ui.jsx');
const appStyles = read('../../src/styles/app.css');

test('N12 addBlock accepts cut types that older section-specific gates rejected', () => {
  const addBlockStart = storyboardSource.indexOf('const addBlock = async');
  const addBlockEnd = storyboardSource.indexOf('// drag-to-reorder blocks', addBlockStart);
  const addBlockSource = storyboardSource.slice(addBlockStart, addBlockEnd);
  assert.match(addBlockSource, /allowedCutTypeOptionsForSection\(sectionRole\)\.some/);

  const acceptsDrop = (sectionRole, cutType) => allowedCutTypeOptionsForSection(sectionRole)
    .some((option) => option.value === cutType);
  assert.equal(acceptsDrop('hooking', 'product'), true);
  assert.equal(acceptsDrop('studio', 'mirror'), true);
  assert.equal(acceptsDrop('product', 'styling'), true);
  assert.match(addBlockSource, /!reservation && droppedCutType === 'mirror' && sectionRole !== SECTION_ROLES\.STYLING/);
  assert.match(addBlockSource, /거울컷은 스타일링 섹션에만 추가할 수 있어요/);
});

test('N12 product conversion still clears worn-only matching fields', () => {
  const commitStart = storyboardSource.indexOf('const commitPendingRecipe = async');
  const commitEnd = storyboardSource.indexOf('const generationExamplePatch', commitStart);
  const commitSource = storyboardSource.slice(commitStart, commitEnd);
  assert.match(
    commitSource,
    /recipePatch\.cutType === 'product' \? \{ matchIds: \[\], faceExposure: null \} : \{\}/,
  );
  assert.match(
    commitSource,
    /WORN_CUT_TYPES\.has\(recipePatch\.cutType\)[\s\S]*?: null/,
  );
});

test('N13 completion toast renders a card image before its message', () => {
  const toastStart = uiSource.indexOf('<div\n              className={`toast');
  const toastEnd = uiSource.indexOf('</div>', toastStart);
  const toastDom = uiSource.slice(toastStart, toastEnd);
  assert.ok(toastStart >= 0, 'toast DOM must be present');
  assert.ok(toastDom.indexOf('<img className="toast-thumb"') < toastDom.indexOf('<span className="toast-message">'));
  assert.match(toastDom, /onError=\{\(event\) => \{ event\.currentTarget\.hidden = true; \}\}/);

  assert.match(chromeSource, /thumbUrl\(firstImage, 400\)/);
  assert.match(chromeSource, /pushToast\('마네킹컷이 만들어졌어요',[\s\S]*?duration: 5000,[\s\S]*?variant: 'mannequinCompletion'/);
  assert.match(appStyles, /\.toast\.mannequin-completion \{[^}]*width: var\(--sb-card-w, 184px\)/);
  assert.match(appStyles, /\.toast\.mannequin-completion \.toast-thumb \{[^}]*aspect-ratio: 3 \/ 4/);
});

test('N13 completion toast keeps text-only fallback and exits accessibly', () => {
  assert.match(uiSource, /event\.currentTarget\.hidden = true/);
  assert.match(uiSource, /variant === 'mannequinCompletion' && toastPrefersReducedMotion\(\)[\s\S]*?filter\(\(t\) => t\.id !== id\)[\s\S]*?return/);
  assert.match(uiSource, /\{ \.\.\.t, exiting: true \}/);
  assert.match(uiSource, /t\.exiting \? ' exiting' : ''/);
  assert.match(appStyles, /\.toast\.exiting \{[^}]*animation: toastOut \.4s/);
  assert.match(appStyles, /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\.toast\.mannequin-completion, \.toast\.mannequin-completion\.exiting \{ animation: none; \}/);
});
