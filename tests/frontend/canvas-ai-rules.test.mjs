import test from 'node:test';
import assert from 'node:assert/strict';
import { calculateCanvasAiPlacement } from '../../src/features/editor/canvasAiPlacement.js';
import { buildCanvasAiMenu, buildCanvasAiRequest, canvasAiKind, canvasAiRo } from '../../src/features/editor/canvasAiActions.js';
import { canReplaceCanvasAiResult, createCanvasAiJobController } from '../../src/features/editor/canvasAiJobs.js';
import { canvasAiHookHarness, editorCanvasAiHandlers } from './helpers/editorCanvasAiHarness.mjs';

const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height });
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 0.000001, `${actual} != ${expected}`);

// These literal screen anchors catch photo-relative positioning and scale-dependent drift.
for (const row of [
  { width: 1440, scale: 0.4, canvasWidth: 832, blockLeft: 536, orbLeft: 484, orbTop: 156, fixed: false },
  { width: 1440, scale: 0.6, canvasWidth: 832, blockLeft: 436, orbLeft: 336, orbTop: 76, fixed: true },
  { width: 1440, scale: 1, canvasWidth: 832, blockLeft: 236, orbLeft: 336, orbTop: 76, fixed: true },
  { width: 1280, scale: 0.4, canvasWidth: 752, blockLeft: 496, orbLeft: 444, orbTop: 156, fixed: false },
  { width: 1280, scale: 0.6, canvasWidth: 752, blockLeft: 396, orbLeft: 336, orbTop: 76, fixed: true },
  { width: 1280, scale: 1, canvasWidth: 752, blockLeft: 196, orbLeft: 336, orbTop: 76, fixed: true },
]) {
  test(`${row.width}px ${row.scale * 100}% keeps a block anchor for 2col right and 3col middle`, () => {
    const blockRect = rect(row.blockLeft, 140, 1000 * row.scale, 800 * row.scale);
    const viewportRect = rect(320, 60, row.canvasWidth, 740);
    for (const elementRect of [rect(row.blockLeft + 510 * row.scale, 176, 430 * row.scale, 640 * row.scale),
      rect(row.blockLeft + 360 * row.scale, 176, 280 * row.scale, 420 * row.scale),
      rect(row.blockLeft + 510 * row.scale, 422, 430 * row.scale, 640 * row.scale)]) {
      const placement = calculateCanvasAiPlacement({ blockRect, elementRect, viewportRect, scale: row.scale, menuHeight: 120, resultHeight: 200 });
      assert.equal(placement.fixed, row.fixed);
      close(blockRect.right - placement.right * row.scale - 40, row.orbLeft);
      close(blockRect.top + placement.top * row.scale, !row.fixed && elementRect.top === 422 ? 402 : row.orbTop);
      assert.equal(placement.menuUp, false);
      assert.equal(placement.resultWidth, 150);
    }
  });
}

test('2x2 lower-row orb follows the selected top without covering the upper row', () => {
  const placement = calculateCanvasAiPlacement({ blockRect: rect(576, 120, 400, 614), elementRect: rect(780, 422, 172, 258), viewportRect: rect(320, 60, 832, 840), scale: 0.4, menuHeight: 160, resultHeight: 200 });
  close(placement.right, 1030);
  close(placement.top, 705);
  close(placement.resultTop, 825);
  close(placement.menuTop, 830);
});

test('orb clamps vertically at the viewport edges while retaining the left block margin', () => {
  const inputs = { blockRect: rect(576, 0, 400, 900), viewportRect: rect(320, 60, 832, 840), scale: 0.4, menuHeight: 160, resultHeight: 200 };
  const top = calculateCanvasAiPlacement({ ...inputs, elementRect: rect(600, 20, 352, 528) });
  close(top.top, 170); // screen top 68, center 88
  assert.equal(top.fixed, false);
  const bottom = calculateCanvasAiPlacement({ ...inputs, elementRect: rect(600, 950, 352, 528) });
  close(bottom.top, 2130); // screen top 852, center 872
  assert.equal(bottom.menuUp, true);
  close(bottom.menuTop, 1705);
  close(bottom.resultTop, 1610);
});

test('156px margin is still anchored and 155px alone triggers fixed placement', () => {
  const viewportRect = rect(320, 60, 832, 740);
  const anchored = calculateCanvasAiPlacement({ blockRect: rect(476, 140, 400, 568), elementRect: rect(500, 166, 352, 528), viewportRect, scale: 0.4, menuHeight: 120, resultHeight: 200 });
  const fixed = calculateCanvasAiPlacement({ blockRect: rect(475, 140, 400, 568), elementRect: rect(780, 650, 172, 258), viewportRect, scale: 0.4, menuHeight: 120, resultHeight: 200 });
  assert.equal(anchored.fixed, false);
  assert.equal(fixed.fixed, true);
  close(875 - fixed.right * 0.4 - 40, 336);
  close(140 + fixed.top * 0.4, 76);
  close(875 - fixed.resultRight * 0.4 - 150, 661);
  close(140 + fixed.resultTop * 0.4, 330);
  assert.equal(fixed.tooltipSide, 'right');
});

test('menu goes up when below overflows and its complete height fits above', () => {
  const input = { blockRect: rect(576, 120, 400, 614), elementRect: rect(780, 600, 172, 258), viewportRect: rect(320, 60, 832, 740), scale: 0.4, resultHeight: 200 };
  assert.equal(calculateCanvasAiPlacement({ ...input, menuHeight: 158 }).menuUp, false);
  const above = calculateCanvasAiPlacement({ ...input, menuHeight: 159 });
  assert.equal(above.menuUp, true);
  close(above.menuTop, 727.5);
  assert.equal(above.menuMaxHeight, 498);
});

for (const row of [
  { name: 'fixed fallback', blockLeft: 451, photoTop: 150, menuHeight: 248, menuUp: false, menuMaxHeight: 222, menuTop: -35 },
  { name: 'long detail menu near top', blockLeft: 576, photoTop: 150, menuHeight: 500, menuUp: false, menuMaxHeight: 168, menuTop: 100 },
  { name: 'long detail menu near bottom', blockLeft: 576, photoTop: 280, menuHeight: 500, menuUp: true, menuMaxHeight: 178, menuTop: -170 },
]) {
  test(`${row.name} uses the larger scrollable side when neither side fits`, () => {
    const placement = calculateCanvasAiPlacement({ blockRect: rect(row.blockLeft, 140, 400, 568), elementRect: rect(600, row.photoTop, 352, 528), viewportRect: rect(320, 60, 832, 300), scale: 0.4, menuHeight: row.menuHeight, resultHeight: 200 });
    assert.equal(placement.menuUp, row.menuUp);
    assert.equal(placement.menuMaxHeight, row.menuMaxHeight);
    close(placement.menuTop, row.menuTop);
    assert.ok(placement.menuMaxHeight > 0);
  });
}

test('invalid layout input hides the layer instead of exposing nonfinite coordinates', () => {
  assert.equal(calculateCanvasAiPlacement({}), null);
  assert.equal(calculateCanvasAiPlacement({ blockRect: rect(0, 0, 20, 20), elementRect: rect(0, 0, 20, 20), viewportRect: rect(0, 0, 20, 20), scale: 0 }), null);
});

const product = { colors: [
  { id: 'black', name: '블랙', hex: '#111111', isBase: true, images: [{ slot: 'Front', src: '/black-front.jpg' }, { slot: 'Back', src: '/black-back.jpg' }] },
  { id: 'beige', name: '베이지', hex: '#b8a88c', images: [{ slot: 'Front', src: '/beige-front.jpg' }] },
  { id: 'back-only', name: '카멜', hex: '#ad8157', images: [{ slot: 'Back', src: '/camel-back.jpg' }] },
] };
const detailRecommendations = { version: 1, status: 'ready', candidates: [
  { id: 'pocket', label: '포켓', informationGroup: 'pocket', rank: 1, photoUse: 'standalone', visibility: 'clear', direction: 'front', sourceSlot: 'Front', sourceIndex: 0 },
  { id: 'seam', label: '봉제', informationGroup: 'seam', rank: 2, photoUse: 'standalone', visibility: 'clear', direction: 'back', sourceSlot: 'Back', sourceIndex: 1 },
  { id: 'hidden', label: '안감', informationGroup: 'lining', rank: 3, photoUse: 'standalone', visibility: 'occluded', direction: 'front', sourceSlot: 'Front', sourceIndex: 0 },
] };
const image = { id: 'photo', type: 'image', src: '/selected.jpg', sourceBlockId: 'stale-storyboard-id', cutType: 'product', wardrobeGroup: 'beige' };
const block = { id: 'b1', kind: 'twocol', contentRole: 'custom', elements: [image] };
const wornRecipe = { origin: 'editor_new', cutType: 'styling', colorId: 'black', upload: false, recipe: { cutType: 'styling', colorId: 'black', outerClosureState: 'partial', direction: 'front' } };
const menuInput = { block, element: image, assetRecipe: wornRecipe, product, clothingType: 'outer', detailRecommendations };
const ids = (menu) => menu.branches.map((branch) => branch.id);

test('menu kinds use resolved asset recipes and ignore stale element cut or sourceBlockId', () => {
  assert.equal(canvasAiKind(menuInput), 'worn');
  assert.equal(canvasAiKind({ ...menuInput, assetRecipe: { upload: true, recipe: null, cutType: null } }), 'upload');
  assert.equal(canvasAiKind({ ...menuInput, assetRecipe: { cutType: 'product', recipe: { shot: 'ghost' } } }), 'ghost');
  assert.equal(canvasAiKind({ ...menuInput, assetRecipe: { cutType: 'product', recipe: { shot: 'detail' } } }), 'detail');
  assert.equal(canvasAiKind({ ...menuInput, assetRecipe: { cutType: 'product', recipe: null } }), 'product_unknown');
  assert.equal(canvasAiKind({ ...menuInput, assetRecipe: null }), 'unknown');
});

test('worn and upload actions include immediate similar and optional outer closure', () => {
  const worn = buildCanvasAiMenu(menuInput);
  assert.deepEqual(ids(worn), ['similar', 'color', 'closure']);
  assert.equal(worn.branches[0].direct, true);
  assert.equal(worn.branches[0].ring, true);
  assert.equal(worn.branches[0].description, '지금 이미지와 비슷한 컷을 생성해요');
  assert.deepEqual(ids(buildCanvasAiMenu({ ...menuInput, assetRecipe: { upload: true, recipe: null } })), ['similar', 'color', 'closure']);
  assert.deepEqual(ids(buildCanvasAiMenu({ ...menuInput, clothingType: 'top' })), ['similar', 'color']);
  assert.deepEqual(ids(buildCanvasAiMenu({ ...menuInput, frontClosure: { version: 1, kind: 'none', range: null } })), ['similar', 'color']);
});

test('outer closure shows five or two steps and marks only a known effective current value', () => {
  const graded = buildCanvasAiMenu(menuInput).branches.find((branch) => branch.id === 'closure').options;
  assert.deepEqual(graded.map((option) => option.value), ['closed', 'openToChest', 'partial', 'openToWaist', 'open']);
  assert.deepEqual(graded.filter((option) => option.current).map((option) => [option.value, option.disabled]), [['partial', true]]);
  const binary = buildCanvasAiMenu({ ...menuInput, frontClosure: { version: 1, kind: 'belt', range: 'binary' } }).branches.find((branch) => branch.id === 'closure').options;
  assert.deepEqual(binary.map((option) => option.value), ['closed', 'open']);
  assert.deepEqual(binary.filter((option) => option.current).map((option) => option.value), ['closed']);
  const unknown = buildCanvasAiMenu({ ...menuInput, assetRecipe: { upload: true, recipe: null } });
  assert.equal(unknown.branches.flatMap((branch) => branch.options).some((option) => option.current), false);
});

test('ghost has color, direction and observed detail choices with unavailable faces disabled', () => {
  const ghost = buildCanvasAiMenu({ ...menuInput, assetRecipe: { cutType: 'product', colorId: 'beige', recipe: { cutType: 'product', shot: 'ghost', colorId: 'beige', direction: 'front' } } });
  assert.deepEqual(ids(ghost), ['color', 'direction', 'detail']);
  const colors = ghost.branches[0].options;
  assert.deepEqual(colors.map((option) => option.value), ['black', 'beige']);
  assert.equal(colors.find((option) => option.value === 'beige').current, true);
  assert.equal(colors.find((option) => option.value === 'black').description, '블랙으로 생성해요');
  assert.deepEqual(ghost.branches[1].options.map((option) => [option.value, option.current, option.disabled]), [['front', true, true], ['back', false, true]]);
  assert.equal(ghost.branches[2].disabled, true);
  assert.match(ghost.branches[2].description, /기준색/);
  assert.deepEqual(ghost.branches[2].options, []);
});

test('detail only offers same-area reshoot and other observed targets', () => {
  const recommendations = { ...detailRecommendations, candidates: [...detailRecommendations.candidates,
    { id: 'cuff', label: '소매 끝', informationGroup: 'cuff', rank: 4, photoUse: 'standalone', visibility: 'clear', direction: 'front', sourceSlot: 'Front', sourceIndex: 0 },
  ] };
  const menu = buildCanvasAiMenu({ ...menuInput, detailRecommendations: recommendations,
    assetRecipe: { cutType: 'product', recipe: { cutType: 'product', shot: 'detail', detailTargetId: 'pocket', colorId: 'black', direction: 'front' } } });
  assert.deepEqual(ids(menu), ['reshoot', 'detail']);
  assert.equal(menu.branches[0].description, '같은 부위를 다시 생성해요');
  assert.equal(menu.branches[0].ring, true);
  assert.equal(menu.branches[1].label, '다른 부위');
  assert.deepEqual(menu.branches[1].options.map((option) => [option.value, option.current, option.disabled]), [['pocket', true, true], ['cuff', false, false]]);
  const legacy = buildCanvasAiMenu({ ...menuInput, assetRecipe: { cutType: 'product', recipe: null } });
  assert.deepEqual(ids(legacy), ['detail']);
  assert.deepEqual(legacy.branches[0].options.map((option) => option.value), ['pocket', 'seam']);
});

test('known color group without a recipe gets a current color while direction and closure stay unknown', () => {
  const menu = buildCanvasAiMenu({ ...menuInput, assetRecipe: { cutType: 'horizon', colorId: 'black', recipe: null } });
  assert.equal(menu.current.colorId, 'black');
  assert.equal(menu.current.outerClosureState, null);
  assert.equal(menu.current.direction, null);
  assert.equal(menu.branches.find((branch) => branch.id === 'color').options.find((option) => option.value === 'black').disabled, true);
});

test('server current values override recipe requests and explicit null leaves values unknown', () => {
  const menu = buildCanvasAiMenu({ ...menuInput, assetRecipe: { ...wornRecipe,
    current: { colorId: null, direction: 'back', outerClosureState: 'closed', detailTargetId: null } } });
  assert.deepEqual(menu.current, { colorId: null, direction: 'back', outerClosureState: 'closed', detailTargetId: null });
  assert.equal(menu.branches.find((branch) => branch.id === 'color').options.some((option) => option.current), false);
  assert.deepEqual(menu.branches.find((branch) => branch.id === 'closure').options.filter((option) => option.current).map((option) => option.value), ['closed']);
  const uploaded = buildCanvasAiMenu({ ...menuInput, assetRecipe: { kind: 'upload', colorId: 'black', current: { colorId: null, outerClosureState: null }, recipe: { outerClosureState: 'open' } } });
  assert.equal(uploaded.branches.flatMap((branch) => branch.options).some((option) => option.current), false);
  assert.deepEqual(buildCanvasAiMenu({ ...menuInput, assetRecipe: null }).branches, []);
});

test('catalog color options provide canonical labels and hex for swatchId-only product colors', () => {
  const menu = buildCanvasAiMenu({ ...menuInput, product: { colors: [
    { id: 'black', swatchId: 'black-swatch', images: [{ slot: 'Front', src: '/front.jpg' }] },
  ] }, colorOpts: [{ id: 'black', label: '블랙', hex: '#000000' }] });
  assert.deepEqual(menu.branches.find((branch) => branch.id === 'color').options.map((option) => [option.value, option.label, option.hex]), [['black', '블랙', '#000000']]);
});

test('planned and unplanned slots preserve ADR0019 classification and direct action branches', () => {
  const empty = { ...image, src: null };
  const storyboard = [{ id: empty.sourceBlockId, source: 'ai', cutType: 'styling' }];
  assert.deepEqual(ids(buildCanvasAiMenu({ ...menuInput, element: empty, storyboard })), ['upload', 'retry']);
  assert.deepEqual(ids(buildCanvasAiMenu({ ...menuInput, block: { ...block, templateId: 'template' }, element: empty, storyboard })), ['upload', 'fill']);
  const fill = buildCanvasAiMenu({ ...menuInput, block: { ...block, templateId: 'template' }, element: empty, storyboard });
  assert.equal(fill.kind, 'unplanned');
  assert.equal(fill.branches[0].price, '무료');
  assert.equal(fill.branches[1].description, '빈 칸에 어울리는 컷을 생성해요');
});

test('pending, hidden and automatic slots have no actions', () => {
  for (const input of [
    { ...menuInput, element: { ...image, src: null, genPending: true } },
    { ...menuInput, element: { ...image, src: null, hidden: true } },
    { ...menuInput, block: { ...block, kind: 'info' }, element: { ...image, src: null } },
  ]) assert.deepEqual(buildCanvasAiMenu(input).branches, []);
});

test('Korean generation particle uses vowel and rieul exception without changing the label', () => {
  assert.equal(canvasAiRo('블랙'), '블랙으로');
  assert.equal(canvasAiRo('베이지'), '베이지로');
  assert.equal(canvasAiRo('카멜'), '카멜로');
  assert.equal(canvasAiRo('반만 열림'), '반만 열림으로');
});

test('wear and upload color or closure send only the selected source and target change', () => {
  const source = { ...image, assetId: 'asset-original', wardrobeGroup: 'black' };
  assert.deepEqual(buildCanvasAiRequest({ action: 'color', value: 'beige', source, assetRecipe: wornRecipe }), { mode: 'vary', variationMode: 'color', source: { src: '/selected.jpg' }, colorId: 'beige' });
  assert.deepEqual(buildCanvasAiRequest({ action: 'closure', value: 'openToChest', source, assetRecipe: { upload: true, recipe: null } }), { mode: 'vary', variationMode: 'closure', source: { src: '/selected.jpg' }, outerClosureState: 'openToChest' });
});

test('similar and same-area reshoot keep the existing AG07 request shape', () => {
  assert.deepEqual(buildCanvasAiRequest({ action: 'similar', source: image, assetRecipe: wornRecipe }), { mode: 'vary', variationMode: 'similar', source: { src: '/selected.jpg', cutType: 'styling' } });
  assert.deepEqual(buildCanvasAiRequest({ action: 'reshoot', source: image, assetRecipe: { cutType: 'product', recipe: { cutType: 'product', shot: 'detail' } } }), { mode: 'vary', source: { src: '/selected.jpg', cutType: 'product' } });
});

const richRecipe = {
  sectionRole: 'product', contentRole: 'productOverview', cutType: 'product', shot: 'ghost', direction: 'front', sideStyle: null,
  colorId: 'black', faceExposure: null, modelId: null, matchIds: ['keep-match'], matchIdsOrigin: 'user',
  refAssetIds: ['keep-reference'], refScope: 'all', exampleId: 'keep-example', exampleSelectionOrigin: 'user',
  outerClosureState: null, detailTargetId: null, detailTargetOrigin: null, spaceGroupId: 'keep-space',
  spaceVariation: 'keep-variation', spaceSetMemberOrder: 2, horizonBackgroundMode: 'keep-background',
  thumb: 'not-a-request-field', pose: 'removed', colorIds: ['removed'], refImages: [{ assetId: 'removed' }],
};
const ghostRecipe = { cutType: 'product', recipe: richRecipe };
const preservedRecipe = {
  sectionRole: 'product', contentRole: 'productOverview', cutType: 'product', shot: 'ghost', direction: 'front', sideStyle: null,
  colorId: 'black', faceExposure: null, modelId: null, matchIds: ['keep-match'], matchIdsOrigin: 'user',
  refAssetIds: ['keep-reference'], refScope: 'all', exampleId: 'keep-example', exampleSelectionOrigin: 'user',
  outerClosureState: null, detailTargetId: null, detailTargetOrigin: null, spaceGroupId: 'keep-space',
  spaceVariation: 'keep-variation', spaceSetMemberOrder: 2, horizonBackgroundMode: 'keep-background',
};

test('ghost color and face regenerate with the entire original recipe and only requested field changed', () => {
  assert.deepEqual(buildCanvasAiRequest({ action: 'color', value: 'beige', source: image, assetRecipe: ghostRecipe }), { mode: 'new', ...preservedRecipe, colorId: 'beige' });
  assert.deepEqual(buildCanvasAiRequest({ action: 'direction', value: 'back', source: image, assetRecipe: ghostRecipe }), { mode: 'new', ...preservedRecipe, direction: 'back' });
  assert.equal(richRecipe.colorId, 'black');
  assert.equal(richRecipe.direction, 'front');
});

test('detail requests use an observed target and preserve the base recipe without invented settings', () => {
  assert.deepEqual(buildCanvasAiRequest({ action: 'detail', value: 'pocket', source: image, assetRecipe: ghostRecipe, product, detailRecommendations }), { mode: 'new', ...preservedRecipe, shot: 'detail', direction: 'front', detailTargetId: 'pocket', detailTargetOrigin: 'user' });
  assert.deepEqual(buildCanvasAiRequest({ action: 'detail', value: 'pocket', source: image, assetRecipe: { cutType: 'product', colorId: 'black', recipe: null }, detailRecommendations }), { mode: 'new', cutType: 'product', shot: 'detail', colorId: 'black', direction: 'front', detailTargetId: 'pocket', detailTargetOrigin: 'user' });
  assert.equal(buildCanvasAiRequest({ action: 'detail', value: 'hidden', source: image, assetRecipe: ghostRecipe, detailRecommendations }), null);
});

for (const direction of ['front', 'back']) {
  test(`detail menu and request accept only the original ${direction} face`, () => {
    const assetRecipe = { kind: 'ghost', recipe: { cutType: 'product', shot: 'ghost', colorId: 'black', direction } };
    const input = { ...menuInput, assetRecipe };
    const options = buildCanvasAiMenu(input).branches.find((item) => item.id === 'detail').options;
    assert.deepEqual(options.map((item) => item.value), direction === 'front' ? ['pocket'] : ['seam']);
    const accepted = direction === 'front' ? 'pocket' : 'seam';
    const rejected = direction === 'front' ? 'seam' : 'pocket';
    const requestInput = { action: 'detail', source: image, assetRecipe, product, detailRecommendations };
    assert.equal(buildCanvasAiRequest({ ...requestInput, value: accepted }).direction, direction);
    assert.equal(buildCanvasAiRequest({ ...requestInput, value: accepted }).colorId, 'black');
    assert.equal(buildCanvasAiRequest({ ...requestInput, value: rejected }), null);
  });
}

for (const kind of ['ghost', 'detail', 'product_unknown']) {
  test(`${kind} with a non-base color disables detail with a reason and cannot submit`, () => {
    const assetRecipe = { kind, current: { colorId: 'beige', direction: 'front' }, recipe: null };
    const branch = buildCanvasAiMenu({ ...menuInput, assetRecipe }).branches.find((item) => item.id === 'detail');
    assert.equal(branch.disabled, true);
    assert.match(branch.description, /기준색/);
    assert.deepEqual(branch.options, []);
    assert.equal(buildCanvasAiRequest({ action: 'detail', value: 'pocket', source: image, assetRecipe, product, detailRecommendations }), null);
  });
}

test('detail base color is the flagged color, otherwise the first color, as on the server', () => {
  for (const colors of [
    [product.colors[1], product.colors[0]],
    product.colors.map(({ isBase, ...color }) => color),
  ]) {
    const assetRecipe = { kind: 'ghost', current: { colorId: 'black', direction: 'front' }, recipe: null };
    const input = { ...menuInput, assetRecipe, product: { colors } };
    const branch = buildCanvasAiMenu(input).branches.find((item) => item.id === 'detail');
    assert.notEqual(branch.disabled, true);
    assert.deepEqual(branch.options.map((item) => item.value), ['pocket']);
    assert.equal(buildCanvasAiRequest({ ...input, source: image, action: 'detail', value: 'pocket' }).colorId, 'black');
  }
});

test('unknown source face keeps each observed candidate face in both menu and request', () => {
  const assetRecipe = { kind: 'product_unknown', colorId: 'black', recipe: null };
  const input = { ...menuInput, assetRecipe };
  const options = buildCanvasAiMenu(input).branches[0].options;
  assert.deepEqual(options.map((item) => [item.value, item.direction]), [['pocket', 'front'], ['seam', 'back']]);
  assert.equal(buildCanvasAiRequest({ ...input, source: image, action: 'detail', value: 'seam' }).direction, 'back');
});

test('ghost regeneration without recipe uses known actual direction and color', () => {
  const assetRecipe = { kind: 'ghost', current: { colorId: 'black', direction: 'back' }, recipe: null };
  assert.deepEqual(buildCanvasAiRequest({ action: 'color', value: 'beige', source: image, assetRecipe }), { mode: 'new', cutType: 'product', shot: 'ghost', colorId: 'beige', direction: 'back' });
  assert.deepEqual(buildCanvasAiRequest({ action: 'direction', value: 'front', source: image, assetRecipe }), { mode: 'new', cutType: 'product', shot: 'ghost', colorId: 'black', direction: 'front' });
});

test('empty-slot fill sends position only and planned retry retains its original request', () => {
  assert.deepEqual(buildCanvasAiRequest({ action: 'fill', slot: { blockId: 'b1', elementId: 'photo', sourceBlockId: 'ignored' }, assetRecipe: richRecipe }), { mode: 'fill', slot: { blockId: 'b1', elementId: 'photo' } });
  const request = { mode: 'new', retryBlockId: 'planned-1', cutType: 'horizon', refAssetIds: ['ref'], modelId: 'm1' };
  const retry = buildCanvasAiRequest({ action: 'retry', failedCutRetry: { retryable: true, request } });
  assert.deepEqual(retry, request);
  retry.refAssetIds.push('changed');
  assert.deepEqual(request.refAssetIds, ['ref']);
  assert.equal(buildCanvasAiRequest({ action: 'retry', failedCutRetry: { retryable: false, request } }), null);
  assert.equal(buildCanvasAiRequest({ action: 'upload' }), null);
});

test('replace requires the original image element and unchanged exact source address', () => {
  const job = { blockId: 'b1', elementId: 'photo', sourceSrc: '/selected.jpg' };
  assert.equal(canReplaceCanvasAiResult([block], job), true);
  assert.equal(canReplaceCanvasAiResult([{ ...block, elements: [{ ...image, src: '/changed.jpg' }] }], job), false);
  assert.equal(canReplaceCanvasAiResult([{ ...block, elements: [{ ...image, type: 'text' }] }], job), false);
  assert.equal(canReplaceCanvasAiResult([], job), false);
  assert.equal(canReplaceCanvasAiResult([block], { ...job, sourceSrc: '' }), false);
});

function fakeScheduler() {
  let time = 0, nextId = 0;
  const timers = new Map();
  return {
    now: () => time,
    schedule(callback, delay) { const id = ++nextId; timers.set(id, { callback, due: time + delay }); return id; },
    cancelSchedule(id) { timers.delete(id); },
    async advance(ms) {
      const end = time + ms;
      for (;;) {
        const next = [...timers].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due || a[0] - b[0])[0];
        if (!next) break;
        time = next[1].due; timers.delete(next[0]); next[1].callback();
        await Promise.resolve(); await Promise.resolve();
      }
      time = end;
      await Promise.resolve(); await Promise.resolve();
    },
  };
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const jobMeta = { blockId: 'b1', elementId: 'photo', sourceSrc: '/selected.jpg', sourceAssetId: 'source', sourceWidth: 600, sourceHeight: 900, isSlot: false, group: 'black', action: 'color', value: 'beige', request: { mode: 'vary', variationMode: 'color', colorId: 'beige', source: { src: '/selected.jpg' } } };

test('cancelling inside two seconds sends no request and releases the element', async () => {
  const scheduler = fakeScheduler(); let calls = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => 'key-1' });
  controller.start('b1:photo', jobMeta, async () => { calls += 1; return { data: { src: '/result.jpg' } }; });
  assert.equal(controller.get('b1:photo').status, 'cancel');
  await scheduler.advance(1999);
  assert.equal(calls, 0);
  assert.equal(controller.cancel('b1:photo'), true);
  await scheduler.advance(1);
  assert.equal(calls, 0);
  assert.equal(controller.get('b1:photo'), undefined);
});

test('exactly two seconds sends once and callback metadata settles into arrived', async () => {
  const scheduler = fakeScheduler(), pending = deferred(), events = [], changes = []; let calls = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => 'key-1',
    onStarted: (job) => events.push(['started', job.status]), onFinished: (job) => events.push(['finished', job.status]),
    onResult: (job, response) => events.push(['result', job.result.src, response.credits]), onChange: (jobs) => changes.push(jobs),
  });
  const subscription = []; const unsubscribe = controller.subscribe((jobs) => subscription.push(jobs));
  const original = controller.start('b1:photo', jobMeta, ({ idempotencyKey, onJobStarted }) => {
    calls += 1; assert.equal(idempotencyKey, 'key-1'); onJobStarted('server-job', { label: '고스트샷 · 블랙' }); return pending.promise;
  });
  await scheduler.advance(2000);
  assert.equal(calls, 1);
  assert.equal(controller.get('b1:photo').status, 'busy');
  assert.equal(controller.get('b1:photo').jobId, 'server-job');
  assert.equal(controller.get('b1:photo').contextFill.label, '고스트샷 · 블랙');
  assert.equal(controller.cancel('b1:photo'), false);
  pending.resolve({ data: { src: '/result.jpg' }, credits: 81 }); await scheduler.advance(0);
  assert.equal(controller.get('b1:photo').status, 'arrived');
  assert.equal(controller.get('b1:photo').result.src, '/result.jpg');
  assert.equal(original.sourceAssetId, 'source');
  assert.deepEqual(events, [['started', 'busy'], ['result', '/result.jpg', 81], ['finished', 'arrived']]);
  assert.ok(changes.every((snapshot) => snapshot instanceof Map));
  assert.ok(subscription.length >= 3); unsubscribe();
});

test('same element joins its active job even when another action is pressed', async () => {
  const scheduler = fakeScheduler(), pending = deferred(); let calls = 0, keys = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => `key-${++keys}` });
  const first = controller.start('b1:photo', jobMeta, () => { calls += 1; return pending.promise; });
  const duplicate = controller.start('b1:photo', { ...jobMeta, action: 'closure', value: 'open' }, () => { calls += 1; return pending.promise; });
  assert.equal(duplicate, first);
  await scheduler.advance(2000);
  assert.equal(calls, 1);
  assert.equal(controller.start('b1:photo', jobMeta, () => pending.promise), first);
  assert.equal(keys, 1);
  pending.resolve({ data: { src: '/result.jpg' } }); await scheduler.advance(0);
});

test('independent elements continue together and retain per-photo state while selection changes', async () => {
  const scheduler = fakeScheduler(), first = deferred(), second = deferred(); let keys = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => `key-${++keys}` });
  controller.start('b1:photo', jobMeta, () => first.promise);
  controller.start('b2:other', { ...jobMeta, blockId: 'b2', elementId: 'other', sourceSrc: '/other.jpg' }, () => second.promise);
  await scheduler.advance(2000);
  assert.equal(controller.jobs.size, 2);
  assert.equal(controller.get('b1:photo').status, 'busy');
  first.resolve({ data: { src: '/first.jpg' } }); await scheduler.advance(0);
  assert.equal(controller.get('b1:photo').status, 'arrived');
  assert.equal(controller.get('b2:other').status, 'busy');
  second.resolve({ data: { src: '/second.jpg' } }); await scheduler.advance(0);
  assert.equal(controller.get('b2:other').result.src, '/second.jpg');
});

test('photo action-value keys survive cancellation and unknown errors, but different values get new keys', async () => {
  const scheduler = fakeScheduler(); let keys = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => `key-${++keys}` });
  const first = controller.start('b1:photo', jobMeta, async () => ({ data: { src: '/result.jpg' } }));
  controller.cancel('b1:photo');
  const retry = controller.start('b1:photo', jobMeta, async () => { throw Object.assign(new Error('접수를 확인하지 못했어요.'), { submissionUnknown: true }); });
  assert.equal(retry.idempotencyKey, first.idempotencyKey);
  await scheduler.advance(2000);
  assert.equal(controller.get('b1:photo').status, 'failed');
  const unknownRetry = controller.start('b1:photo', jobMeta, async () => ({ data: { src: '/result.jpg' } }));
  assert.equal(unknownRetry.idempotencyKey, first.idempotencyKey);
  controller.cancel('b1:photo');
  const nextValue = controller.start('b1:photo', { ...jobMeta, value: 'black' }, async () => ({ data: { src: '/result.jpg' } }));
  assert.notEqual(nextValue.idempotencyKey, first.idempotencyKey);
  controller.cancel('b1:photo');
});

test('identical source across elements and slot identifiers get stable independent identities', () => {
  const scheduler = fakeScheduler(); let keys = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => `key-${++keys}` });
  const first = controller.start('b1:photo', jobMeta, async () => ({ data: { src: '/result.jpg' } }));
  const duplicateSource = controller.start('b2:photo', { ...jobMeta, blockId: 'b2' }, async () => ({ data: { src: '/result.jpg' } }));
  assert.equal(duplicateSource.idempotencyKey, first.idempotencyKey);
  const slot1 = controller.start('b3:slot', { blockId: 'b3', elementId: 'slot', isSlot: true, action: 'fill' }, async () => ({}));
  const slot2 = controller.start('b4:slot', { blockId: 'b4', elementId: 'slot', isSlot: true, action: 'fill' }, async () => ({}));
  assert.notEqual(slot1.idempotencyKey, slot2.idempotencyKey);
  controller.dispose();
});

test('a completed or definitely rejected photo tuple gets a new idempotency key for the next click', async () => {
  const scheduler = fakeScheduler(); let keys = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => `key-${++keys}` });
  const first = controller.start('b1:photo', jobMeta, async () => ({ data: { src: '/result.jpg' } }));
  await scheduler.advance(2000);
  const repeated = controller.start('b1:photo', jobMeta, async () => { throw Object.assign(new Error('크레딧이 부족해요.'), { status: 402 }); });
  assert.notEqual(repeated.idempotencyKey, first.idempotencyKey);
  await scheduler.advance(2000);
  const rejectedRetry = controller.start('b1:photo', jobMeta, async () => ({ data: { src: '/result.jpg' } }));
  assert.notEqual(rejectedRetry.idempotencyKey, repeated.idempotencyKey);
  assert.equal(keys, 3);
  controller.cancel('b1:photo');
});

for (const meta of [
  { ...jobMeta, action: 'similar', value: undefined, request: { mode: 'vary', variationMode: 'similar', source: { src: '/selected.jpg', cutType: 'styling' } } },
  { blockId: 'b1', elementId: 'photo', isSlot: true, action: 'fill', request: { mode: 'fill', slot: { blockId: 'b1', elementId: 'photo' } } },
  { blockId: 'b1', elementId: 'photo', isSlot: true, action: 'retry', request: { mode: 'new', retryBlockId: 'planned' } },
]) {
  for (const outcome of [
    { name: 'success', result: { data: { src: '/result.jpg' } }, nextKey: 'key-2' },
    { name: 'job failure', error: { code: 'job_failed' }, nextKey: 'key-2' },
    { name: '4xx rejection', error: { status: 409, code: 'fill_unavailable' }, nextKey: 'key-2' },
    { name: 'network uncertainty', error: { code: 'api_network', submissionUnknown: true }, nextKey: 'key-1' },
    { name: '5xx uncertainty', error: { status: 503, submissionUnknown: true }, nextKey: 'key-1' },
    { name: 'unresolved timeout', error: { code: 'job_timeout', jobId: 'accepted', submissionUnknown: true }, nextKey: 'key-1' },
  ]) {
    test(`${meta.action} after ${outcome.name} uses ${outcome.nextKey}`, async () => {
      const scheduler = fakeScheduler(); let keys = 0;
      const controller = createCanvasAiJobController({ ...scheduler, createKey: () => `key-${++keys}` });
      const first = controller.start('photo', meta, async () => {
        if (outcome.error) throw Object.assign(new Error(outcome.name), outcome.error);
        return outcome.result;
      });
      assert.equal(first.idempotencyKey, 'key-1');
      await scheduler.advance(2000);
      const next = controller.start('photo', { ...meta, request: { ...meta.request, modelId: 'later-setting' } }, async () => ({ data: { src: '/next.jpg' } }));
      assert.equal(next.idempotencyKey, outcome.nextKey);
      assert.deepEqual(next.request, outcome.nextKey === 'key-1' ? meta.request : { ...meta.request, modelId: 'later-setting' });
      await scheduler.advance(2000);
      const afterSuccess = controller.start('photo', meta, async () => ({ data: { src: '/third.jpg' } }));
      assert.notEqual(afterSuccess.idempotencyKey, next.idempotencyKey);
      controller.cancel('photo');
    });
  }
  test(`${meta.action} repeated while pending or generating joins the active job`, async () => {
    const scheduler = fakeScheduler(), pending = deferred(); let keys = 0;
    const controller = createCanvasAiJobController({ ...scheduler, createKey: () => `key-${++keys}` });
    const first = controller.start('photo', meta, () => pending.promise);
    assert.equal(controller.start('photo', meta, () => assert.fail('duplicate submission')), first);
    await scheduler.advance(2000);
    assert.equal(controller.start('photo', meta, () => assert.fail('duplicate submission')), first);
    assert.equal(keys, 1);
    pending.resolve({ data: { src: '/result.jpg' } }); await scheduler.advance(0);
  });
}

test('a definitive failure after an unknown submission releases that original request', async () => {
  const scheduler = fakeScheduler(); let keys = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => `key-${++keys}` });
  controller.start('photo', jobMeta, async () => { throw Object.assign(new Error('network'), { submissionUnknown: true }); });
  await scheduler.advance(2000);
  const retry = controller.start('photo', jobMeta, async () => { throw Object.assign(new Error('failed'), { code: 'job_failed' }); });
  assert.equal(retry.idempotencyKey, 'key-1');
  await scheduler.advance(2000);
  assert.equal(controller.start('photo', jobMeta, async () => ({})).idempotencyKey, 'key-2');
  controller.cancel('photo');
});

test('unknown and preflight retries keep the original request snapshot across another photo job', async () => {
  const scheduler = fakeScheduler(); let keys = 0;
  const sent = [];
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => `key-${++keys}` });
  const first = controller.start('b1:photo', jobMeta, async ({ request, idempotencyKey }) => {
    sent.push({ request, idempotencyKey });
    throw Object.assign(new Error('접수를 확인하지 못했어요.'), { submissionUnknown: true });
  });
  await scheduler.advance(2000);
  controller.start('b2:other', { ...jobMeta, blockId: 'b2', elementId: 'other', sourceAssetId: 'other-source', sourceSrc: '/other.jpg' }, async () => ({ data: { src: '/other-result.jpg' } }));
  const changed = { ...jobMeta, request: { ...jobMeta.request, modelId: 'a-later-setting' } };
  const retry = controller.start('b1:photo', changed, async ({ request, idempotencyKey }) => {
    sent.push({ request, idempotencyKey });
    throw Object.assign(new Error('사진 저장 중이에요.'), { requestNotSent: true });
  });
  assert.deepEqual(retry.request, { mode: 'vary', variationMode: 'color', colorId: 'beige', source: { src: '/selected.jpg' } });
  await scheduler.advance(2000);
  const preflightRetry = controller.start('b1:photo', changed, async ({ request, idempotencyKey }) => {
    sent.push({ request, idempotencyKey }); return { data: { src: '/result.jpg' } };
  });
  assert.equal(preflightRetry.idempotencyKey, first.idempotencyKey);
  await scheduler.advance(2000);
  assert.equal(sent.length, 3);
  for (const request of sent) assert.deepEqual(request, { request: { mode: 'vary', variationMode: 'color', colorId: 'beige', source: { src: '/selected.jpg' } }, idempotencyKey: 'key-1' });
});

for (const failure of [
  { name: 'save 503', fields: { status: 503 } },
  { name: 'save network error', fields: { code: 'api_network' } },
  { name: 'save conflict', fields: { status: 409, code: 'editor_conflict' } },
  { name: 'active page generation', active: true },
]) {
  test(`unknown fill keeps its request number after ${failure.name} before resubmission`, async () => {
    const blocks = [{ id: 'block', kind: 'twocol', elements: [{ id: 'empty', type: 'image', src: null, w: 300, h: 450 }] }];
    const genActiveRef = { current: false }, sent = []; let saves = 0;
    const handlers = editorCanvasAiHandlers({ blocks, latestBlocks: { current: blocks }, genActiveRef,
      setSlotImage() {}, api: { saveEditorBlocks: async () => {
        saves += 1;
        if (saves === 2 && !failure.active) throw Object.assign(new Error(failure.name), failure.fields);
      } } });
    const harness = canvasAiHookHarness({ blocks, selectedId: 'empty', handlers,
      api: { fillEmptySlot: async (projectId, { idempotencyKey }) => {
        sent.push(idempotencyKey);
        if (sent.length === 1) throw Object.assign(new Error('unknown submission'), { submissionUnknown: true });
        return { data: { src: '/result.jpg' } };
      } } });
    const original = harness.start('fill'); await harness.run();
    genActiveRef.current = Boolean(failure.active);
    harness.start('fill'); const failedSave = await harness.run();
    assert.equal(failedSave.status, 'failed');
    assert.equal(sent.length, 1, 'saving did not send another image request');
    genActiveRef.current = false;
    const next = harness.start('fill'); await harness.run();
    assert.equal(next.idempotencyKey, original.idempotencyKey);
    assert.deepEqual(sent, [original.idempotencyKey, original.idempotencyKey]);
  });
}

test('server failure message is retained and onError receives the original error once', async () => {
  const scheduler = fakeScheduler(), error = Object.assign(new Error('이 칸에 만들 수 있는 컷이 없어요.'), { code: 'fill_unavailable' }); const errors = [], finished = [];
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => 'key', onError: (job, failure) => errors.push([job.status, failure]), onFinished: (job) => finished.push(job.status) });
  controller.start('b1:photo', jobMeta, async () => { throw error; });
  await scheduler.advance(2000);
  assert.equal(controller.get('b1:photo').message, '이 칸에 만들 수 있는 컷이 없어요.');
  assert.deepEqual(errors, [['failed', error]]);
  assert.deepEqual(finished, ['failed']);
});

test('dismissing only hides the frame and cannot erase an arriving wardrobe result', async () => {
  const scheduler = fakeScheduler(), pending = deferred(); let arrivals = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => 'key', onResult: () => { arrivals += 1; } });
  controller.start('b1:photo', jobMeta, () => pending.promise);
  await scheduler.advance(2000);
  assert.equal(controller.dismiss('b1:photo'), true);
  pending.resolve({ data: { src: '/result.jpg' } }); await scheduler.advance(0);
  assert.equal(controller.get('b1:photo').dismissed, true);
  assert.equal(controller.get('b1:photo').result.src, '/result.jpg');
  assert.equal(arrivals, 1);
});

test('request snapshots and dispose keep delayed requests safe and controller reusable after effect replay', async () => {
  const scheduler = fakeScheduler(); let calls = 0;
  const controller = createCanvasAiJobController({ ...scheduler, createKey: () => 'key' });
  const meta = { ...jobMeta, request: { ...jobMeta.request, source: { src: '/selected.jpg' } } };
  const job = controller.start('b1:photo', meta, async () => { calls += 1; return { data: { src: '/result.jpg' } }; });
  meta.request.source.src = '/changed.jpg';
  assert.equal(job.request.source.src, '/selected.jpg');
  controller.dispose(); await scheduler.advance(2000);
  assert.equal(calls, 0);
  controller.start('b1:photo', jobMeta, async () => { calls += 1; return { data: { src: '/result.jpg' } }; });
  await scheduler.advance(2000);
  assert.equal(calls, 1);
  assert.equal(controller.get('b1:photo').status, 'arrived');
});
test('취소 대기 시작과 취소를 알리고 요청이 시작되지 않으면 완료 신호가 없다', () => {
  const events = [];
  const scheduled = new Map();
  const controller = createCanvasAiJobController({
    createKey: () => 'queued', schedule: (callback) => { scheduled.set(1, callback); return 1; },
    cancelSchedule: (id) => scheduled.delete(id),
    onQueued: () => events.push('queued'), onCancelled: () => events.push('cancelled'),
    onStarted: () => events.push('started'), onFinished: () => events.push('finished'),
  });
  controller.start('element', { blockId: 'b', elementId: 'element', action: 'fill' }, async () => assert.fail('취소한 요청'));
  controller.cancel('element');
  assert.deepEqual(events, ['queued', 'cancelled']);
  assert.equal(scheduled.size, 0);
});
test('원래 콘티 설정을 찾을 수 없는 계획된 칸은 유료 재시도를 누를 수 없다', () => {
  const menu = buildCanvasAiMenu({ block: { id: 'b', kind: 'twocol', elements: [] },
    element: { id: 'el', type: 'image', src: null, genFailed: true } });
  assert.equal(menu.kind, 'planned');
  assert.equal(menu.branches.find((item) => item.id === 'retry').disabled, true);
  assert.equal(menu.branches.find((item) => item.id === 'upload').disabled, undefined);
});
