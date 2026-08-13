import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  normalizeMatchClothingSelection,
  normalizeMatchIds,
  reconcileMatchCompatibility,
  toMatchItem,
} from '../../src/lib/api/matchingItems.js';
import { matchingFitDefinition } from '../../src/lib/matchingFit.js';
import {
  addCustomMatchToAnalysis,
  fitCategoryFromMatchingMetadata,
  recommendLegacyMatchClothing,
  removeCustomMatchFromAnalysis,
} from '../../src/mock/matchingRecommendation.js';
import {
  genderForClothingType,
  normalizeTargetGendersForClothingType,
} from '../../src/lib/productGender.js';
import { seedMatchingItems } from '../../src/mock/seedMatchingItems.js';

const custom = {
  id: 'custom-1', name: '내 스커트', gender: 'unisex',
  thumb: 'https://img.test/source-1.png',
  imageUrl: 'https://img.test/grid.jpg',
  thumbnailUrl: 'https://img.test/source-1.png',
  clothingType: 'bottom', category: '스커트', fit: 'regular', length: 'midi',
  fitCategory: 'skirt', isCustom: true, isCompatible: true,
};

test('http match mapper preserves custom and compatibility fields', () => {
  assert.deepEqual(toMatchItem(custom, null), {
    ...custom,
    selected: false,
  });
  assert.equal(toMatchItem({ ...custom, isCompatible: false }, 1).isCompatible, false);
  assert.equal(toMatchItem({ ...custom, isCustom: false }, 1).isCustom, false);
});

test('mock add and remove return full analysis shape and normalize selection order', () => {
  const base = {
    projectId: 'p1',
    suggestedName: '상품',
    matchClothing: [
      { id: 'curated-main', selected: true, selOrder: 1 },
      { id: 'curated-sub', selected: true, selOrder: 2 },
    ],
    fitProfile: {
      category: 'top',
      matchingFit: { clothingId: custom.id, fitCategory: 'skirt', axes: {} },
    },
  };
  const added = addCustomMatchToAnalysis(base, custom);
  assert.deepEqual(Object.keys(added).sort(), ['analysis', 'item']);
  assert.equal(added.item.selected, false);
  assert.equal(added.analysis.suggestedName, '상품');
  assert.equal(added.analysis.matchClothing[0].isCustom, true);

  const selectedCustom = {
    ...added.analysis,
    matchClothing: added.analysis.matchClothing.map((item, index) => ({
      ...item, selected: index < 2, selOrder: index < 2 ? index + 1 : undefined,
    })),
  };
  const removed = removeCustomMatchFromAnalysis(selectedCustom);
  assert.equal(removed.matchClothing.some((item) => item.isCustom), false);
  assert.deepEqual(
    removed.matchClothing.filter((item) => item.selected).map((item) => item.selOrder),
    [1],
  );
  assert.equal('matchingFit' in removed.fitProfile, false);
});

test('mock recommendation mirrors top fit category and custom compatibility round-trip', () => {
  assert.equal(fitCategoryFromMatchingMetadata({
    clothingType: 'top', category: '셔츠', length: 'regular',
  }), 'top');

  const first = recommendLegacyMatchClothing({
    clothingType: 'top', targetGenders: ['women'], current: [custom], defaultSelection: false,
  });
  assert.equal(first[0].id, custom.id);
  assert.equal(first[0].isCustom, true);
  assert.equal(first[0].isCompatible, true);
  assert.equal(first[0].selected, false);

  const incompatible = recommendLegacyMatchClothing({
    clothingType: 'bottom', targetGenders: ['men'], current: first,
  });
  assert.equal(incompatible[0].id, custom.id);
  assert.equal(incompatible[0].isCompatible, false);
  assert.equal(incompatible[0].selected, false);

  const restored = recommendLegacyMatchClothing({
    clothingType: 'top', targetGenders: ['men'], current: incompatible,
  });
  assert.equal(restored[0].id, custom.id);
  assert.equal(restored[0].isCompatible, true);
  assert.equal(restored[0].selected, false);
});

test('gender normalization always keeps one value and dress stays women', () => {
  assert.deepEqual(normalizeTargetGendersForClothingType('top', []), ['women']);
  assert.deepEqual(normalizeTargetGendersForClothingType('bottom', ['men', 'women']), ['men']);
  assert.deepEqual(normalizeTargetGendersForClothingType('dress', ['men']), ['women']);
  assert.equal(genderForClothingType('dress', ['men']), 'women');
});

// D11(2026-08-05 오너) — 내 옷은 마네킹 매칭 조정 스텝을 아예 열지 않는다. 올린 실물을
// 그대로 입혀야 하므로 실루엣·기장 축을 주면 다른 옷이 나온다.
test('custom matching garments never open a fit adjustment step', () => {
  assert.equal(matchingFitDefinition(custom, 'men'), null);
  assert.equal(matchingFitDefinition(custom, 'women'), null);
  assert.equal(matchingFitDefinition({ ...custom, clothingType: 'top', fitCategory: 'top' }, 'women'), null);
  assert.equal(fitCategoryFromMatchingMetadata(custom), null);
});

// 큐레이션 스커트가 남성 대상에 남아 있는 경우(성별 칩 전환 뒤 이월된 선택)의 중립 어휘 폴백은 유지된다.
test('curated skirt still falls back to the neutral silhouette vocabulary for men', () => {
  const curatedSkirt = { ...custom, id: 'match_women_bottom_12', isCustom: false };
  const definition = matchingFitDefinition(curatedSkirt, 'men');
  assert.equal(definition.axisKey, 'silhouette');
  assert.deepEqual(definition.values.map((value) => value.value), ['h_line', 'a_line', 'mermaid']);
});

test('curated seed stays at sixty and fifteen per gender/type bucket', () => {
  assert.equal(seedMatchingItems.length, 60);
  for (const gender of ['women', 'men']) {
    for (const clothingType of ['top', 'bottom']) {
      assert.equal(seedMatchingItems.filter(
        (item) => item.gender === gender && item.clothingType === clothingType,
      ).length, 15);
    }
  }
});

test('analysis UI keeps hooks before loading returns and direct public custom thumbnail', () => {
  const source = readFileSync(
    new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8',
  );
  const hooksEnd = source.indexOf("if (phase === 'loading')");
  assert.ok(source.indexOf('const [customMatchOpen, setCustomMatchOpen]') < hooksEnd);
  assert.ok(source.indexOf('const closeCustomMatchModal = useCallback') < hooksEnd);
  assert.match(source, /<img src=\{m\.thumb\} alt=\{m\.name\}/);
  assert.match(source, /custom-match-badge">내 옷/);
  assert.match(source, /aria-label="내 옷 삭제"/);
  assert.doesNotMatch(source, /custom.*Blob|Blob.*custom/i);
});

// 2026-08-05 회귀 — StrictMode(개발)는 마운트 직후 effect 를 실행→정리→재실행한다. 정리에서
// aliveRef 를 false 로 내리고 setup 에서 되살리지 않으면 모달이 열리자마자 죽은 상태가 되어
// addFiles 의 가드에 걸려 phase 가 'preparing' 에 영구히 갇힌다(스피너만 돌고 요청 0건).
test('upload modal revives aliveRef in effect setup, not only in cleanup', () => {
  const source = readFileSync(
    new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8',
  );
  assert.match(source, /useEffect\(\(\) => \{\s*\n\s*aliveRef\.current = true;/);
  // 정리에서만 만지는 예전 형태(즉시 정리 함수 반환)가 되살아나지 않게 고정
  assert.doesNotMatch(source, /useEffect\(\(\) => \(\) => \{\s*\n\s*aliveRef\.current = false;/);
});

test('target-gender chips disable deselection, photo-volume options stay exclusive, and modal shares abort signal', () => {
  const ui = readFileSync(new URL('../../src/components/ui.jsx', import.meta.url), 'utf8');
  const analysis = readFileSync(
    new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8',
  );
  assert.match(ui, /allowDeselect = true/);
  assert.match(ui, /v === value && allowDeselect \? null : v/);
  assert.equal((analysis.match(/allowDeselect=\{false\}/g) || []).length, 3);
  assert.match(analysis, /role="listbox" aria-label="상세페이지 사진 양"/);
  assert.match(analysis, /role="option" aria-selected=\{composeMode === mode\.value\}/);
  assert.match(analysis, /controllerRef\.current\.abort\(\)/);
  assert.match(analysis, /purpose: 'custom_match_source'/);
  assert.match(analysis, /\{ signal: controller\.signal \}/);
  assert.match(analysis, /api\.refreshMatchClothing\(projectId\)/);
});

test('custom match photos are resized once before parallel uploads', () => {
  const analysis = readFileSync(
    new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8',
  );
  assert.match(analysis, /maxEdge: 1600/);
  assert.match(analysis, /minEdge: 400/);
  assert.match(analysis, /forceJpeg: true/);
  assert.match(analysis, /picked, CUSTOM_MATCH_IMAGE_OPTIONS, \{ signal: controller\.signal \}/);
  assert.match(analysis, /Promise\.all\(files\.map\(\(\{ file \}\) => api\.uploadPhoto/);
});

test('type changes synchronously deselect and disable stale incompatible matches', () => {
  const analysis = readFileSync(
    new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8',
  );
  const adapter = readFileSync(
    new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8',
  );
  assert.match(analysis, /reconcileMatchCompatibility\(a\.matchClothing, t\)/);
  assert.match(analysis, /if \(!item \|\| !isMatchCompatible\(item\)\) return/);
  assert.match(adapter, /m\.clothingType === expectedType/);
  assert.match(adapter, /mergeMatchSelection\(\s*base\.matchClothing \|\| \[\], matchPatch, base\.clothingType/);
});

test('matching selection keeps only the earliest explicit choice', () => {
  const items = [
    { id: 'array-first', clothingType: 'bottom', selected: true, selOrder: 2 },
    { id: 'array-last-main', clothingType: 'bottom', selected: true, selOrder: 1 },
    { id: 'wrong-type', clothingType: 'top', selected: true, selOrder: 3 },
  ];
  const reconciled = reconcileMatchCompatibility(items, 'outer');
  assert.equal(reconciled[0].selected, false);
  assert.equal(reconciled[1].selOrder, 1);
  assert.equal(reconciled[2].selected, false);
  assert.equal(reconciled[2].isCompatible, false);

  const normalized = normalizeMatchClothingSelection(items);
  assert.deepEqual(normalized.filter((item) => item.selected).map((item) => item.id), ['array-last-main']);
  assert.deepEqual(normalizeMatchIds(['array-last-main', 'array-first']), ['array-last-main']);
});

test('analysis matching UI replaces the previous choice instead of adding a sub item', () => {
  const analysis = readFileSync(
    new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8',
  );
  assert.match(analysis, /selected: true, selOrder: 1/);
  assert.match(analysis, /selected: false, selOrder: undefined/);
  assert.match(analysis, /1개 선택/);
  assert.doesNotMatch(analysis, /최대 2개|subMatchId|>서브</);
});

test('HTTP custom-match add and remove responses normalize legacy two-item selections', () => {
  const adapter = readFileSync(
    new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8',
  );
  const customMutationSection = adapter.slice(
    adapter.indexOf('async addCustomMatchItem'),
    adapter.indexOf('async refreshMatchClothing'),
  );
  assert.equal(
    (customMutationSection.match(/normalizeMatchClothingSelection/g) || []).length,
    2,
  );
  assert.match(customMutationSection, /return \{ \.\.\.result, analysis \}/);
  assert.match(customMutationSection, /return \{ analysis \}/);
});
