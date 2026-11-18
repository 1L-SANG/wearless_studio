import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import React from 'react';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import * as fit from '../../src/lib/fitZone.js';
import { mockFitRead } from '../../src/mock/fitRead.js';
import { analysisAxesFor, axesFor, fitProfileCategory, mannequinFitProfile, subcategoryLength } from '../../src/lib/fitAxes.js';
import { genderForClothingType } from '../../src/lib/productGender.js';
import { matchingFitDefinition, matchingFitFromProfile, resolveMainMatchingItem } from '../../src/lib/matchingFit.js';

const base = { clothingType: 'top', subCategory: 'knit', targetGenders: ['women'], fit: 'semi_over', measurements: [] };
const fitRead = { photoType: 'flat', sleeveType: 'long', topLength: { value: 'basic', confident: true },
  sleeve: { value: 'wrist', confident: false }, hemLength: { value: 'above_knee', confident: true },
  pantsLength: { value: 'ankle_cover', confident: true } };
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const nodes = (tree) => Array.isArray(tree) ? tree.flatMap(nodes) : !tree || typeof tree !== 'object'
  ? [] : [tree, ...nodes(tree.props?.children)];
const text = (tree) => Array.isArray(tree) ? tree.map(text).join('') : tree == null || typeof tree === 'boolean'
  ? '' : typeof tree === 'object' ? text(tree.props?.children) : String(tree);

test('analysis arrival, measurement edits and resets share the same source and value rules', () => {
  const initial = fit.fillAnalysisFitZones({ ...base, fitRead });
  assert.deepEqual(initial.fitProfile.axes, { fit: 'semi_over', length: 'basic' });
  assert.deepEqual(initial.fitProfile.axisSources, { fit: 'ai', length: 'ai', sleeve: 'unsure' });
  assert.equal(initial.fitRead, fitRead);
  assert.equal(fit.fillAnalysisFitZones(initial), initial);
  const measured = fit.fillAnalysisFitZones({ ...initial, measurements: [{ key: 'totalLength', value: 38 }] });
  assert.equal(measured.fitProfile.axes.length, 'slight_crop');
  assert.equal(measured.fitProfile.axisSources.length, 'measure');
  const chosen = fit.selectAnalysisFitZone(measured, 'length', 'semi_long');
  assert.equal(chosen.fitProfile.axisSources.length, 'seller');
  assert.equal(fit.fillAnalysisFitZones({ ...chosen, measurements: [{ key: 'totalLength', value: 32 }] }).fitProfile.axes.length, 'semi_long');
  const reset = fit.resetFitZone('length', fit.analysisFitInput(chosen));
  assert.equal(reset.axes.length, 'slight_crop');
  assert.equal(reset.axisSources.length, 'measure');
  const noMeasurements = fit.fillAnalysisFitZones({ ...measured, measurements: [], fitRead: undefined });
  assert.equal(noMeasurements.fitProfile.axes.length, undefined);
  assert.equal(noMeasurements.fitProfile.axisSources.length, 'unsure');
  const unknown = fit.fillAnalysisFitZones({ ...measured, measurementsUnknown: true });
  assert.equal(unknown.fitProfile.axes.length, 'basic');
  assert.equal(unknown.fitProfile.axisSources.length, 'ai');
});

test('seller fit changes the analysis chip while preserving the legacy fit and matching fit', () => {
  const matchingFit = { clothingId: 'pants', fitCategory: 'pants', axes: { cut: 'wide' } };
  let initial = fit.fillAnalysisFitZones({ ...base, fitRead });
  initial = { ...initial, fitProfile: { ...initial.fitProfile, matchingFit } };
  const chosen = fit.selectAnalysisFitZone(initial, 'fit', 'regular');
  assert.equal(chosen.fit, base.fit);
  assert.equal(chosen.fitProfile.axes.fit, 'regular');
  assert.equal(chosen.fitProfile.axisSources.fit, 'seller');
  assert.equal(chosen.fitProfile.matchingFit, matchingFit);
  assert.equal(fit.fillAnalysisFitZones({ ...chosen, fit: 'over' }).fitProfile.axes.fit, 'regular');
  const pants = fit.fillAnalysisFitZones({ ...chosen, clothingType: 'bottom', subCategory: 'jeans' });
  assert.deepEqual(pants.fitProfile.axes, { length: 'ankle_cover' });
  assert.deepEqual(pants.fitProfile.axisSources, { length: 'ai' });
  const men = fit.fillAnalysisFitZones({ ...chosen, targetGenders: ['men'], fit: 'over' });
  assert.deepEqual(fit.fitZones(fit.analysisFitInput(men)).map(({ axis }) => axis), ['fit', 'sleeve']);
  assert.equal(men.fitProfile.axisSources.fit, 'ai');
  assert.equal(men.fitProfile.axes.length, undefined);
});

test('analysis-only defaults and seller length choices are excluded from mannequin input', () => {
  const initial = fit.fillAnalysisFitZones({ ...base, fitRead });
  const crop = fit.selectAnalysisFitZone(initial, 'length', 'crop');
  const elbow = fit.selectAnalysisFitZone(crop, 'sleeve', 'elbow');
  assert.equal(mannequinFitProfile(initial.fitProfile), null);
  assert.equal(mannequinFitProfile(elbow.fitProfile), null);
});

test('missing and pending fit reads show photo-only zones while keeping legacy hem defaults', () => {
  for (const input of [{ ...base }, { ...base, fitRead, featureRun: { coreReceived: false } }]) {
    const filled = fit.fillAnalysisFitZones(input);
    const states = fit.fitZones(fit.analysisFitInput(filled)).map((zone) => fit.fitZoneState(zone, filled.fitProfile));
    assert.equal(states[0].label, '세미오버');
    assert.deepEqual(states.slice(1).map(({ label }) => label), ['사진대로', '사진대로']);
  }
  const dress = fit.fillAnalysisFitZones({ clothingType: 'dress', subCategory: 'midi_dress', targetGenders: ['women'] });
  assert.equal(dress.fitProfile.axes.length, 'midi');
  assert.equal(dress.fitProfile.axisSources.length, 'unsure');
  assert.equal(fit.fitZoneState(fit.fitZones(fit.analysisFitInput(dress))[0], dress.fitProfile).label, '사진대로');
});

function editCategory(a, subCategory) {
  const source = read('../../src/features/analysis/AnalysisForm.jsx');
  const body = source.slice(source.indexOf('  const fitOptsOf ='), source.indexOf('  // subCategory 는'));
  const edit = new Function('a', 'analysisAxesFor', 'axesFor', 'fitProfileCategory', 'subcategoryLength',
    'genderForClothingType', 'matchingFitFromProfile', 'matchingFitDefinition', 'resolveMainMatchingItem',
    `${body}; return withFitProfile;`)(a, analysisAxesFor, axesFor, fitProfileCategory, subcategoryLength,
    genderForClothingType, matchingFitFromProfile, matchingFitDefinition, resolveMainMatchingItem);
  return edit({ subCategory });
}

test('the existing category editor retains its legacy hem initialization and per-axis sources', () => {
  const initial = fit.fillAnalysisFitZones({ ...base, fitRead });
  const changed = editCategory(initial, 'tshirt');
  assert.deepEqual(changed.fitProfile.axisSources, initial.fitProfile.axisSources);
  const skirt = fit.fillAnalysisFitZones({ clothingType: 'bottom', subCategory: 'mini_skirt', targetGenders: ['women'] });
  const midi = editCategory(skirt, 'midi_skirt');
  assert.equal(midi.fitProfile.axes.length, 'midi');
  assert.equal(fit.fillAnalysisFitZones({ ...skirt, ...midi }).fitProfile.axisSources.length, 'unsure');
});

test('changing a T-shirt to a shirt preserves seller fit, measured length and AI sleeve through refilling', () => {
  const initial = fit.fillAnalysisFitZones({ ...base, subCategory: 'tshirt', fit: 'regular',
    measurements: [{ key: 'totalLength', value: 42 }],
    fitRead: { ...fitRead, photoType: 'worn', sleeve: { value: 'wrist', confident: true } } });
  const chosen = fit.selectAnalysisFitZone(initial, 'fit', 'over');
  const patch = editCategory(chosen, 'shirt');
  const saved = fit.fillAnalysisFitZones({ ...chosen, ...patch });
  assert.equal(saved.fit, 'regular');
  assert.deepEqual(saved.fitProfile.axes, { fit: 'over', length: 'semi_crop', sleeve: 'wrist' });
  assert.deepEqual(saved.fitProfile.axisSources, { fit: 'seller', length: 'measure', sleeve: 'ai' });
  assert.equal(mannequinFitProfile(saved.fitProfile, saved.subCategory), null);
});

test('dress and skirt subcategory changes preserve lengths selected or already read from the photos', () => {
  for (const [clothingType, kind] of [['dress', 'dress'], ['bottom', 'skirt']]) {
    const initial = fit.fillAnalysisFitZones({ ...base, clothingType, subCategory: `mini_${kind}`, fitRead });
    for (const source of ['seller', 'ai']) {
      const before = source === 'seller' ? fit.selectAnalysisFitZone(initial, 'length', 'above_knee') : initial;
      const after = fit.fillAnalysisFitZones({ ...before, ...editCategory(before, `midi_${kind}`) });
      assert.equal(after.fitProfile.axes.length, 'above_knee');
      assert.equal(after.fitProfile.axisSources.length, source);
      assert.deepEqual(mannequinFitProfile(after.fitProfile, after.subCategory)?.axes, { length: 'midi' });
    }
  }
});

async function basic(t, initial = { ...base, fitRead }) {
  const events = [], focus = [], listeners = new Map();
  const previous = globalThis.document;
  globalThis.document = { addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: (type, fn) => { if (listeners.get(type) === fn) listeners.delete(type); } };
  t.after(() => { globalThis.document = previous; });
  const runtime = hooks();
  const { BasicInformation } = await componentModule('src/features/analysis/BasicInformation.jsx', ['BasicInformation'], {
    ...fit, ...runtime, Fragment: React.Fragment, useId: () => runtime.useRef('basic-test').current,
  });
  let a = fit.fillAnalysisFitZones(initial), tree;
  const catalogs = { clothingTypes: [{ value: 'top', label: '상의' }, { value: 'bottom', label: '하의' },
    { value: 'outer', label: '아우터' }, { value: 'dress', label: '원피스' }],
  genders: [{ value: 'women', label: '여자' }, { value: 'men', label: '남자' }] };
  const show = () => {
    tree = runtime.render(BasicInformation, { analysis: a, catalogs,
      subCats: [{ value: 'knit', label: '니트' }, { value: 'tshirt', label: '티셔츠' }],
      onTypeChange(value) { events.push(['type', value]); a = fit.fillAnalysisFitZones({ ...a, clothingType: value, subCategory: null }); },
      onGenderChange(value) { events.push(['gender', value]); a = fit.fillAnalysisFitZones({ ...a, targetGenders: [value] }); },
      onCategoryChange(patch) { events.push(['category', patch]); a = fit.fillAnalysisFitZones({ ...a, ...patch }); },
      onAxisChange(axis, value) { events.push(['axis', axis, value]); a = fit.selectAnalysisFitZone(a, axis, value); },
      onAxisReset(reset) { events.push(['reset', reset]); a = { ...a, fitProfile: { ...a.fitProfile, ...reset } }; },
    });
    for (const tile of nodes(tree).filter((node) => /^t2(?: |$)/.test(node.props?.className))) {
      tile.ref({ focus: () => focus.push(tile.props['aria-label']) });
    }
    runtime.flushEffects();
    return tree;
  };
  const tile = (name) => nodes(tree).find((node) => /^t2(?: |$)/.test(node.props?.className) && node.props['aria-label'].startsWith(name + ' '));
  const click = (node, pointer = false) => {
    nodes(tree).find((item) => item.props?.className === 'g2').props[pointer ? 'onPointerDownCapture' : 'onKeyDownCapture']();
    node.props.onClick(); show();
  };
  show();
  return { show, tile, click, events, focus, all: () => nodes(tree), analysis: () => a,
    escape: (feature = false) => {
      listeners.get('keydown')?.({ key: 'Escape', target: { closest: () => feature ? { querySelector: () => ({}) } : null } }); show();
    },
  };
}

test('tiles open one panel, point to the selected column and separate keyboard from pointer focus', async (t) => {
  const h = await basic(t);
  assert.equal(h.all().filter((node) => /^t2(?: |$)/.test(node.props?.className)).length, 6);
  h.click(h.tile('기장'), true);
  assert.ok(h.all().filter((node) => node.props?.role === 'radiogroup').every((node) => node.props.onKeyDown === undefined));
  const basicLength = h.all().find((node) => node.props?.role === 'radio' && text(node) === '골반 (기본)');
  assert.equal(text(nodes(basicLength).find((node) => node.props?.className === 'g-card-nowrap')), '(기본)');
  assert.equal(h.all().find((node) => node.props?.className === 'g-panel').props['data-col'], 1);
  assert.equal(h.tile('기장').props['aria-expanded'], true);
  assert.ok(h.tile('기장').props['aria-controls']);
  h.click(h.all().find((node) => node.props?.role === 'radio' && text(node) === '살짝 크롭'), true);
  assert.equal(h.analysis().fitProfile.axisSources.length, 'seller');
  assert.equal(h.tile('기장').props['aria-expanded'], false);
  assert.equal(h.focus.length, 0);
  h.click(h.tile('소매 길이'));
  h.escape(true);
  assert.equal(h.tile('소매 길이').props['aria-expanded'], true);
  h.escape();
  assert.equal(h.tile('소매 길이').props['aria-expanded'], false);
  assert.match(h.focus.at(-1), /^소매 길이/);
  h.click(h.tile('의류 종류'));
  h.click(h.all().find((node) => node.props?.role === 'radio' && text(node) === '하의'));
  assert.deepEqual(h.events.at(-1), ['type', 'bottom']);
  assert.equal(h.tile('세부 카테고리').props['aria-expanded'], true);
  assert.match(h.tile('세부 카테고리').props['aria-label'], /선택해 주세요/);
  assert.equal(h.tile('핏'), undefined);
});

test('custom category still saves on blur and Enter, and seller reset uses the computed fallback', async (t) => {
  const h = await basic(t);
  h.click(h.tile('세부 카테고리'));
  let input = h.all().find((node) => node.type === 'input');
  input.props.onFocus(); input.props.onChange({ target: { value: '특수 니트' } }); h.show();
  assert.ok(h.all().some((node) => node.props?.className === 'chip-input-pending'));
  input = h.all().find((node) => node.type === 'input');
  input.props.onKeyDown({ key: 'Enter', nativeEvent: {}, currentTarget: { blur: () => input.props.onBlur() } }); h.show();
  assert.deepEqual(h.events.at(-1), ['category', { customCategory: '특수 니트', subCategory: null }]);
  assert.equal(h.tile('세부 카테고리').props['aria-expanded'], false);
  h.click(h.tile('기장'));
  h.click(h.all().find((node) => node.props?.role === 'radio' && text(node) === '크롭'));
  h.click(h.tile('기장'));
  const back = h.all().find((node) => node.props?.className === 'g-revert');
  assert.equal(text(back), '처음 값으로'); h.click(back);
  assert.equal(h.analysis().fitProfile.axes.length, 'basic');
  assert.equal(h.analysis().fitProfile.axisSources.length, 'ai');
  h.click(h.tile('핏'));
  assert.equal(h.all().some((node) => node.props?.className === 'g-revert'), false);
});

test('fit thumbnails follow the target gender and the fit panel keeps its text choices', async (t) => {
  const h = await basic(t, { ...base, fit: 'slim', fitRead: { ...fitRead, photoType: 'worn',
    sleeve: { value: 'wrist', confident: true } } });
  const thumbnail = (name) => nodes(h.tile(name)).find((node) => node.props?.className === 't2-thumb')?.props.src;
  const fitPanel = () => nodes(h.all().find((node) => node.props?.className === 'g-panel'));
  assert.equal(thumbnail('핏'), '/fit-cards/fit_slim.jpg');
  assert.equal(thumbnail('기장'), '/fit-cards/lenw_hip.jpg');
  assert.equal(thumbnail('소매 길이'), '/fit-cards/long_wrist.jpg');
  h.click(h.tile('핏'));
  assert.equal(thumbnail('핏'), '/fit-cards/fit_slim.jpg');
  assert.equal(fitPanel().some((node) => node.type === 'img'), false);
  assert.equal(text(fitPanel().find((node) => node.props?.className === 'g-note')), '몸선을 따라 붙어 깔끔하고 단정해요.');

  h.click(h.tile('대상 성별'));
  h.click(h.all().find((node) => node.props?.role === 'radio' && text(node) === '남자'));
  assert.equal(thumbnail('핏'), undefined);
  assert.equal(h.tile('핏').props.children.filter(Boolean)[0].props.className, 'tx');
  assert.match(h.tile('핏').props['aria-label'], /^핏 슬림,/);
  assert.equal(thumbnail('소매 길이'), '/fit-cards/long_wrist.jpg');
  h.click(h.tile('핏'));
  assert.equal(thumbnail('핏'), undefined);
  assert.equal(fitPanel().some((node) => node.type === 'img'), false);
  assert.deepEqual(fitPanel().filter((node) => node.props?.role === 'radio').map(text), ['슬림', '레귤러', '세미오버', '오버']);
  assert.equal(text(fitPanel().find((node) => node.props?.className === 'g-note')), '몸에 붙어 가슴과 팔 라인이 드러나요.');
});

test('measured long sleeves show the measurement badge until the seller selects a value', async (t) => {
  const measurements = [{ key: 'shoulderWidth', value: 48, unit: 'cm' }, { key: 'sleeveLength', value: 64, unit: 'cm' }];
  const h = await basic(t, { ...base, targetGenders: ['men'], measurements, fitRead: { ...fitRead, photoType: 'worn',
    sleeve: { value: 'above_wrist', confident: true } } });
  const badge = () => nodes(h.tile('소매 길이')).find((node) => node.props?.className === 'g-tag');
  assert.equal(h.analysis().fitProfile.axes.sleeve, 'back_of_hand');
  assert.equal(h.analysis().fitProfile.axisSources.sleeve, 'measure');
  assert.equal(text(badge()), '실측');

  h.click(h.tile('소매 길이'));
  h.click(h.all().find((node) => node.props?.role === 'radio' && text(node) === '손목'));
  const refilled = fit.fillAnalysisFitZones(h.analysis());
  assert.equal(refilled.fitProfile.axes.sleeve, 'wrist');
  assert.equal(refilled.fitProfile.axisSources.sleeve, 'seller');
  assert.equal(badge(), undefined);
});

test('unknown sleeves show both labeled groups, photo-only tiles omit thumbnails and reset without a default', async (t) => {
  const h = await basic(t, { ...base });
  assert.equal(h.all().filter((node) => node.type === 'img').length, 1);
  h.click(h.tile('소매 길이'));
  assert.deepEqual(h.all().filter((node) => node.props?.className === 'g-group-label').map(text), ['반팔', '긴팔']);
  assert.equal(h.all().filter((node) => node.props?.role === 'radio').length, 9);
  assert.ok(h.all().filter((node) => node.type === 'img').every((node) => node.props.alt === '' && node.props.loading === 'lazy'));
  h.click(h.all().find((node) => node.props?.role === 'radio' && text(node) === '손목'));
  h.click(h.tile('소매 길이'));
  const back = h.all().find((node) => node.props?.className === 'g-revert');
  assert.equal(text(back), '사진대로 두기'); h.click(back);
  assert.equal(h.analysis().fitProfile.axes.sleeve, undefined);
  assert.match(h.tile('소매 길이').props['aria-label'], /사진대로/);
});

test('mock readings match each garment and ship exactly the 28 referenced card images', () => {
  const files = new Set();
  for (const [clothingType, subCategory] of [['top', 'knit'], ['outer', 'coat'], ['dress', 'custom'], ['bottom', 'custom_skirt'], ['bottom', 'jeans']]) {
    const analysis = { ...base, clothingType, subCategory };
    const reading = mockFitRead(analysis);
    assert.equal(reading.photoType, 'flat');
    if (reading.sleeveType === 'long') assert.equal(reading.sleeve.confident, false);
    for (const zone of fit.fitZones({ clothingType, subCategory, gender: 'women' })) {
      for (const option of zone.options) files.add(option.card);
    }
    if (clothingType === 'bottom' && subCategory === 'jeans') assert.equal(reading.pantsLength.value, 'ankle_cover');
  }
  const shipped = readdirSync(new URL('../../public/fit-cards/', import.meta.url));
  assert.equal(shipped.length, 28);
  assert.deepEqual([...files].sort(), shipped.sort());
});

test('analysis ingress and layout retain the contracted width, order, and save metadata', () => {
  const source = read('../../src/features/analysis/AnalysisForm.jsx');
  const sections = source.slice(source.indexOf('  const sections ='));
  assert.ok(sections.indexOf('<BasicInformation') < sections.indexOf('강조하고 싶은 특징'));
  assert.ok(sections.indexOf('강조하고 싶은 특징') < sections.indexOf('>소재<'));
  assert.ok(sections.indexOf('>소재<') < sections.indexOf('>실측 정보<'));
  assert.ok(sections.indexOf('>실측 정보<') < sections.indexOf('{/* MD 코멘트 */}'));
  assert.doesNotMatch(source, /typeLabel|fontSize: 12\.5/);
  assert.match(source, /axisSources: \{ \.\.\.previousProfile.axisSources \}/);
  const input = read('../../src/features/product-input/ProductInput.jsx');
  assert.doesNotMatch(input, /const wide =|wide \? ' wide'/);
  assert.match(input, /normalizeAnalysisFit\(mergeProductOwnedAnalysisFields\(a, nextProduct\)\)/);
  assert.match(read('../../src/lib/featureRunDraft.js'), /receiveCore\(fillAnalysisFitZones\(/);
});
