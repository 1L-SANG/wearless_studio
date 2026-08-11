import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { fitHotspotsFor } from '../../src/features/mannequin/fitHotspots.js';

test('every guided fit axis resolves to a visible mannequin adjustment hotspot', () => {
  const expected = {
    top: ['fit', 'length'],
    outer: ['fit', 'length'],
    pants: ['cut', 'length'],
    skirt: ['silhouette', 'length'],
    dress: ['silhouette', 'length'],
  };

  Object.entries(expected).forEach(([category, axes]) => {
    axes.forEach((axis) => {
      const hotspots = fitHotspotsFor(category, axis);
      assert.ok(hotspots.length > 0, `${category}.${axis}`);
      assert.ok(
        hotspots.every(({ id, label }) => id && label),
        `${category}.${axis} labels`,
      );
    });
  });
});

test('unsupported category and axis pairs do not expose misleading hotspots', () => {
  assert.deepEqual(fitHotspotsFor('pants', 'fit'), []);
  assert.deepEqual(fitHotspotsFor('top', 'silhouette'), []);
  assert.deepEqual(fitHotspotsFor(null, 'length'), []);
});

test('adjustment hotspots are immediately available without the old question card', () => {
  const source = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url),
    'utf8',
  );
  const styles = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.css', import.meta.url),
    'utf8',
  );

  assert.match(source, /const adjustmentHotspots = steps\.flatMap/);
  assert.match(source, /onAdjustmentSelect=\{openAdjustmentExamples\}/);
  assert.match(
    source,
    /continueLabel=\{needsRegen \? '수정 반영' : '이대로 진행'\}/,
  );
  assert.match(
    source,
    /<Button\s+variant="primary"\s+size="lg"\s+block\s+iconRight="arrowRight"\s+className="fit-continue btn-glowring"/,
  );
  assert.ok(
      source.indexOf('className="fit-continue btn-glowring"')
      > source.indexOf('{(cuts.length > 1 || waitTile) && ('),
    '진행 버튼은 이미지 오버레이가 아닌 아래 영역에 있어야 한다',
  );
  assert.doesNotMatch(source, /className="fit-ask"/);
  assert.match(
    styles,
    /\.fit-hotspot::before \{[^}]*width: 16px;[^}]*height: 16px;[^}]*border: 2px solid/s,
  );
  assert.match(styles, /\.fit-hotspot-top-fit \{ left: 60%; top: 24%; \}/);
  assert.match(styles, /\.fit-hotspot-top-hem \{ left: 52%; top: 41%; \}/);
  assert.match(styles, /\.fit-hotspot-pants-cut \{ left: 56%; top: 55%; \}/);
  assert.match(styles, /\.fit-continue \{ margin-top: var\(--sp-24\); \}/);
});
