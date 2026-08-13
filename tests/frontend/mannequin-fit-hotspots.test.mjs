import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { axesFor } from '../../src/lib/fitAxes.js';
import { matchingFitDefinition } from '../../src/lib/matchingFit.js';
import { fitHotspotsFor } from '../../src/features/mannequin/fitHotspots.js';

test('every guided fit axis resolves to a visible mannequin adjustment hotspot', () => {
  const expected = {
    top: ['fit', 'length', 'sleeve'],
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

test('소매 기장은 자체 핫존을 갖고, 몸통 핏 라벨은 소매를 더는 주장하지 않는다', () => {
  assert.deepEqual(fitHotspotsFor('top', 'sleeve'), [{ id: 'top-sleeve', label: '소매 기장' }]);
  assert.deepEqual(fitHotspotsFor('top', 'fit'), [{ id: 'top-fit', label: '몸통 핏' }]);
  // 아우터는 소매 축이 없어 기존 라벨을 유지한다
  assert.deepEqual(fitHotspotsFor('outer', 'fit'), [{ id: 'outer-fit', label: '몸통·소매 핏' }]);
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
  assert.match(source, /continueLabel=\{continueLabel\}/);
  assert.match(source, /`수정 반영 · \$\{CREDIT_COSTS\.mannequinGenerate\} 크레딧`/);
  assert.match(source, /listModels\(\)\.catch\(\(\) => \[\]\)/);
  assert.match(source, /realModelFeeLabel\(analysis\?\.selectedModelId, realModels\)/);
  assert.match(
    source,
    /`이대로 진행 · \$\{aiCutCount == null \? '—' : aiCutCount \* CREDIT_COSTS\.storyboardPerCut\} 크레딧\$\{realModelFee\}`/,
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
  assert.match(styles, /\.fit-hotspot-top-fit \{ left: 40%; top: 25%; \}/);
  assert.match(styles, /\.fit-hotspot-top-sleeve \{ left: 61%; top: 24%; \}/);
  assert.match(styles, /\.fit-hotspot-top-hem \{ left: 52%; top: 41%; \}/);
  assert.match(styles, /\.fit-hotspot-pants-cut \{ left: 60%; top: 62%; \}/);
  assert.match(styles, /\.fit-continue \{ margin-top: var\(--sp-24\); \}/);
});

test('넓은 화면의 예시 카드는 잘리지 않고 홀수 마지막 카드만 가운데 정렬된다', () => {
  const styles = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.css', import.meta.url),
    'utf8',
  );

  assert.match(
    styles,
    /\.fit-ex-col \{[^}]*width: 332px; max-height: none;[^}]*padding: 14px;/s,
  );
  assert.match(
    styles,
    /\.fit-ex-track \{[^}]*flex: none;[^}]*min-height: auto;[^}]*display: grid;[^}]*overflow: visible;[^}]*scroll-snap-type: none;/s,
  );
  assert.match(
    styles,
    /\.fit-ex-track \.fit-tile:last-child:nth-child\(odd\) \{[^}]*grid-column: 1 \/ -1;[^}]*justify-self: center;[^}]*width: calc\(\(100% - 10px\) \/ 2\);/s,
  );
});

test('picked adjustments stay visible, can be cleared, and lock while proceeding', () => {
  const source = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url),
    'utf8',
  );
  const styles = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.css', import.meta.url),
    'utf8',
  );

  assert.match(source, /pickLb: stepState\[step\.key\]\?\.pickLb/);
  assert.match(source, /aria-selected=\{selected\}/);
  assert.match(source, /className=\{`fit-tile\$\{img \? '' : ' text'\}\$\{selected \? ' is-selected' : ''\}`\}/);
  assert.match(source, /selectedValue=\{stepState\[changingStep\.key\]\?\.pick\}/);
  assert.match(source, /선택 취소/);
  assert.match(source, /setStep\(key, \{ mode: 'changing', pick: null, pickLb: null \}\)/);
  assert.match(source, /adjustmentDisabled=\{busy\}/);
  assert.match(source, /disabled=\{disabled\}/);

  const continueStart = source.indexOf('const onCta = async () =>');
  const navigateStart = source.indexOf("navigate('/create/generating')", continueStart);
  const continueSource = source.slice(continueStart, navigateStart);
  assert.ok(
    continueSource.indexOf('submittingRef.current = true')
      < continueSource.indexOf('await api.saveAnalysis'),
    '저장 시작 전에 조정 UI의 동기 가드를 잠가야 한다',
  );
  assert.doesNotMatch(continueSource, /finally \{\s*setBusy\(false\)/);
  assert.match(styles, /\.fit-tile\.is-selected/);
  assert.match(
    styles,
    /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\.fit-hotspot::before, \.fit-hotspot span \{ transition: none; \}/,
  );
});

// .fit-mine-img 는 예시 패널이 열린 comparing 상태에서 가장 좁다 (Mannequin.css: width 300px, aspect-ratio 3/4).
const FRAME_W = 300;
const FRAME_H = 400;
// .fit-hotspot 히트박스가 44×44px 이라, 중심이 이보다 가까우면 두 버튼이 겹쳐 뒤엣것이 클릭을 가로챈다.
const MIN_GAP_PX = 48;

function hotspotCoords() {
  const css = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.css', import.meta.url),
    'utf8',
  );
  const coords = new Map();
  const rule = /\.fit-hotspot-([a-z-]+)\s*\{\s*left:\s*([\d.]+)%;\s*top:\s*([\d.]+)%;\s*\}/g;
  let match = rule.exec(css);
  while (match !== null) {
    coords.set(match[1], { left: Number(match[2]), top: Number(match[3]) });
    match = rule.exec(css);
  }
  return coords;
}

// 한 화면에 동시에 뜨는 점 = 주상품 축들 + 메인 매칭 의류 축 1개.
// 매칭 가능한 조합은 server/app/services/matching.py 의 보완타입 규칙 미러다:
// top·outer → 하의(pants|skirt), pants·skirt → 상의(top), dress → 매칭 없음.
const SCREEN_COMBOS = [
  ['top', ['pants', 'skirt', null]],
  ['outer', ['pants', 'skirt', null]],
  ['pants', ['top', null]],
  ['skirt', ['top', null]],
  ['dress', [null]],
];

function hotspotIdsOnScreen(category, gender, matchCategory) {
  const ids = Object.keys(axesFor(category, gender))
    .flatMap((axis) => fitHotspotsFor(category, axis).map(({ id }) => id));
  if (matchCategory) {
    const def = matchingFitDefinition({ id: 'match-1', fitCategory: matchCategory }, gender);
    if (def) ids.push(...fitHotspotsFor(def.fitCategory, def.axisKey).map(({ id }) => id));
  }
  return ids;
}

test('한 화면에 함께 뜨는 핫존은 서로 겹치지 않는다', () => {
  const coords = hotspotCoords();
  const failures = [];

  for (const [category, matchCategories] of SCREEN_COMBOS) {
    for (const gender of ['women', 'men']) {
      for (const matchCategory of matchCategories) {
        const label = `${category}/${gender}${matchCategory ? `+${matchCategory}` : ''}`;
        const ids = hotspotIdsOnScreen(category, gender, matchCategory);
        assert.deepEqual([...new Set(ids)], ids, `${label}: 같은 핫존이 두 번 뜬다`);
        ids.forEach((id) => assert.ok(coords.has(id), `${label}: 좌표 없음 .fit-hotspot-${id}`));

        for (let i = 0; i < ids.length; i += 1) {
          for (let j = i + 1; j < ids.length; j += 1) {
            const a = coords.get(ids[i]);
            const b = coords.get(ids[j]);
            const dx = ((a.left - b.left) / 100) * FRAME_W;
            const dy = ((a.top - b.top) / 100) * FRAME_H;
            const gap = Math.hypot(dx, dy);
            if (gap < MIN_GAP_PX) {
              failures.push(`${label}: ${ids[i]} ↔ ${ids[j]} = ${gap.toFixed(1)}px`);
            }
          }
        }
      }
    }
  }

  assert.deepEqual(failures, [], `핫존 간격 ${MIN_GAP_PX}px 미만:\n${failures.join('\n')}`);
});
