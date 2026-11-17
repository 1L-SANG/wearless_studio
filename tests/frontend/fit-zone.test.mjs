import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';

import { fitZones, resolveSleeveType, sleeveReach, fitValueFromMeasurements, longSleeveFromMeasurements,
  fillFitZones, resetFitZone, fitZoneState, fitNote, ANALYSIS_FIT_AXES, FIT_SOURCE_POLICY } from '../../src/lib/fitZone.js';
import { mannequinFitProfile } from '../../src/lib/fitAxes.js';

const context = { clothingType: 'top', subCategory: 'tshirt', gender: 'women' };
const profile = (axes = {}, axisSources = {}, category = 'top', gender = 'women') => ({ category, gender, axes, axisSources, source: 'auto', version: 2 });
const keys = (options) => fitZones(options).map(({ axis }) => axis);
const values = (zone) => zone.options.map(({ value }) => value);
const read = (length, sleeve, extra = {}) => ({ photoType: 'worn', sleeveType: 'short',
  topLength: { value: length, confident: true }, hemLength: { value: length, confident: true },
  pantsLength: { value: length, confident: true }, sleeve: { value: sleeve, confident: true }, ...extra });

test('the live axis and source policies mirror the server, including gender restrictions', () => {
  const server = JSON.parse(execFileSync(process.env.PAGE_TEMPLATE_PYTHON || 'server/.venv/bin/python', ['-c',
    "import json,sys;sys.path.insert(0,'server');from app.agents.analysis_fit import ANALYSIS_FIT_AXES,FIT_SOURCE_POLICY;print(json.dumps([ANALYSIS_FIT_AXES,FIT_SOURCE_POLICY]))"], { encoding: 'utf8' }));
  assert.deepEqual([ANALYSIS_FIT_AXES, FIT_SOURCE_POLICY], server);
  for (const [category, genders] of Object.entries(ANALYSIS_FIT_AXES)) {
    for (const [gender, axes] of Object.entries(genders)) {
      const clothingType = ['pants', 'skirt'].includes(category) ? 'bottom' : category;
      const subCategory = category === 'skirt' ? 'custom_skirt' : '';
      assert.deepEqual(keys({ clothingType, subCategory, gender }), axes);
    }
  }
});

test('each garment has only its specified zones and leggings have none', () => {
  assert.deepEqual(keys(context), ['fit', 'length', 'sleeve']);
  assert.deepEqual(keys({ ...context, gender: 'men' }), ['fit', 'sleeve']);
  for (const gender of ['women', 'men']) {
    assert.deepEqual(keys({ clothingType: 'outer', gender }), ['fit', 'sleeve']);
    assert.deepEqual(keys({ clothingType: 'bottom', subCategory: 'jeans', gender }), ['length']);
    assert.deepEqual(keys({ clothingType: 'bottom', subCategory: 'leggings', gender }), []);
  }
  assert.deepEqual(keys({ clothingType: 'dress', gender: 'women' }), ['length', 'sleeve']);
  assert.deepEqual(keys({ clothingType: 'bottom', subCategory: 'mini_skirt', gender: 'women' }), ['length']);
  assert.deepEqual(keys({ clothingType: 'unknown', gender: 'women' }), []);
});

test('analysis options use stored values, body-position labels and the contracted card files', () => {
  const zones = fitZones({ ...context, sleeveType: 'short' });
  assert.deepEqual(zones[0].options, [
    { value: 'slim', label: '슬림', card: 'fit_slim.jpg' },
    { value: 'regular', label: '레귤러', card: 'fit_regular.jpg' },
    { value: 'semi_over', label: '세미오버', card: 'fit_semi_over.jpg' },
    { value: 'over', label: '오버', card: 'fit_over.jpg' },
  ]);
  assert.deepEqual(zones[1].options, [
    { value: 'crop', label: '크롭', card: 'lenw_crop.jpg' },
    { value: 'slight_crop', label: '살짝 크롭', card: 'lenw_crop_s.jpg' },
    { value: 'semi_crop', label: '허리선', card: 'lenw_waist.jpg' },
    { value: 'basic', label: '골반 (기본)', card: 'lenw_hip.jpg' },
    { value: 'semi_long', label: '엉덩이 덮음', card: 'lenw_seat.jpg' },
  ]);
  assert.deepEqual(zones[2].options, [
    { value: 'cap', label: '어깨만 덮음', card: 'short_cap.jpg' },
    { value: 'cap_short', label: '위팔 위쪽', card: 'short_upper.jpg' },
    { value: 'short', label: '위팔 중간', card: 'short_mid.jpg' },
    { value: 'above_elbow', label: '팔꿈치 위', card: 'short_above_elbow.jpg' },
    { value: 'elbow', label: '팔꿈치', card: 'short_elbow.jpg' },
  ]);
  const pants = fitZones({ clothingType: 'bottom', subCategory: 'jeans', gender: 'women' })[0];
  assert.deepEqual(pants.options, [
    { value: 'above_ankle', label: '발목 위', card: 'pants_above_ankle.jpg' },
    { value: 'ankle', label: '발목', card: 'pants_ankle.jpg' },
    { value: 'ankle_cover', label: '복숭아뼈 덮음', card: 'pants_ankle_cover.jpg' },
    { value: 'below_ankle', label: '발등 덮음', card: 'pants_foot_cover.jpg' },
  ]);
});

test('sleeveless garments hide the sleeve zone and unknown sleeves retain both groups', () => {
  for (const clothingType of ['top', 'outer', 'dress']) {
    assert.equal(keys({ ...context, clothingType, sleeveType: 'sleeveless' }).includes('sleeve'), false);
    const sleeve = fitZones({ ...context, clothingType, sleeveType: 'unknown' }).find(({ axis }) => axis === 'sleeve');
    assert.deepEqual(sleeve.groups.map(({ type, options }) => [type, options.length]), [['short', 5], ['long', 4]]);
    assert.deepEqual(values(sleeve), ['cap', 'cap_short', 'short', 'above_elbow', 'elbow', 'above_wrist', 'wrist', 'back_of_hand', 'fingers']);
  }
  const long = fitZones({ ...context, sleeveType: 'long' }).find(({ axis }) => axis === 'sleeve');
  assert.deepEqual(long.options, [
    { value: 'above_wrist', label: '손목 위', card: 'long_above_wrist.jpg' },
    { value: 'wrist', label: '손목', card: 'long_wrist.jpg' },
    { value: 'back_of_hand', label: '손등 덮음', card: 'long_back_of_hand.jpg' },
    { value: 'fingers', label: '손끝 덮음', card: 'long_fingers.jpg' },
  ]);
});

test('unknown sleeve type uses measured reach at the 40cm boundary and explicit AI type wins', () => {
  assert.equal(resolveSleeveType(null, { sleeveLength: 39.9 }), 'short');
  assert.equal(resolveSleeveType(null, { sleeveLength: 40 }), 'long');
  assert.equal(resolveSleeveType('unknown', { sleeveLength: 38, shoulderWidth: 41 }), 'long');
  assert.equal(resolveSleeveType('short', { sleeveLength: 60 }), 'short');
  assert.equal(resolveSleeveType('sleeveless', { sleeveLength: 60 }), 'sleeveless');
  assert.equal(resolveSleeveType(null, {}), null);
  const sleeve = (measurements) => fitZones({ ...context, measurements }).find(({ axis }) => axis === 'sleeve');
  assert.equal(sleeve({ sleeveLength: 39.9 }).options.length, 5);
  assert.equal(sleeve({ sleeveLength: 40 }).options.length, 4);
  assert.equal(sleeve({}).options.length, 9);
});

test('skirt and dress subcategories restrict the choices to their two values', () => {
  for (const [prefix, expected] of [['mini', ['mini', 'above_knee']], ['midi', ['knee', 'midi']], ['long', ['long', 'floor']]]) {
    for (const [clothingType, suffix] of [['bottom', 'skirt'], ['dress', 'dress']]) {
      const zone = fitZones({ clothingType, subCategory: `${prefix}_${suffix}`, gender: 'women' })[0];
      assert.deepEqual(values(zone), expected);
    }
  }
  for (const [clothingType, subCategory] of [['bottom', '직접 입력 치마'], ['dress', '직접 입력']]) {
    assert.deepEqual(values(fitZones({ clothingType, subCategory, gender: 'women' })[0]), ['mini', 'above_knee', 'knee', 'midi', 'long', 'floor']);
  }
});

test('women top length measurements honor every boundary and accept the stored measurement array', () => {
  for (const [totalLength, expected] of [[34.9, 'crop'], [35, 'slight_crop'], [39.9, 'slight_crop'], [40, 'semi_crop'], [49.9, 'semi_crop'], [50, 'basic'], [59.9, 'basic'], [60, 'semi_long']]) {
    assert.equal(fitValueFromMeasurements('length', { ...context, measurements: [{ key: 'totalLength', value: totalLength, unit: 'cm' }] }), expected);
  }
  for (const totalLength of [null, '', ' ', 0, -1, NaN, Infinity, true]) {
    assert.equal(fitValueFromMeasurements('length', { ...context, measurements: { totalLength } }), null);
  }
});

test('short sleeve reach uses the signed shoulder correction and every boundary', () => {
  for (const [sleeveLength, expected] of [[11.9, 'cap'], [12, 'cap_short'], [17.9, 'cap_short'], [18, 'short'], [25.9, 'short'], [26, 'above_elbow'], [31.9, 'above_elbow'], [32, 'elbow']]) {
    assert.equal(fitValueFromMeasurements('sleeve', { ...context, sleeveType: 'short', measurements: { sleeveLength } }), expected);
  }
  assert.equal(sleeveReach({ sleeveLength: 12, shoulderWidth: 35 }), 11);
  assert.equal(sleeveReach({ sleeveLength: 12, shoulderWidth: 39 }), 13);
  assert.equal(sleeveReach([{ key: 'sleeveLength', value: 12 }]), 12);
  assert.equal(sleeveReach({ shoulderWidth: 39 }), null);
  assert.equal(fitValueFromMeasurements('sleeve', { ...context, sleeveType: 'short', measurements: { sleeveLength: 12, shoulderWidth: 35 } }), 'cap');
});

test('uncalibrated lengths and long sleeves never guess a measured chip', () => {
  const measurements = { totalLength: 120, sleeveLength: 60, shoulderWidth: 50 };
  for (const options of [{ ...context, gender: 'men' }, { clothingType: 'outer', gender: 'women' },
    { clothingType: 'dress', gender: 'women' }, { clothingType: 'bottom', subCategory: 'jeans', gender: 'men' },
    { clothingType: 'bottom', subCategory: 'long_skirt', gender: 'women' }]) {
    assert.equal(fitValueFromMeasurements('length', { ...options, measurements }), null);
  }
  assert.equal(longSleeveFromMeasurements(measurements), null);
  assert.equal(fitValueFromMeasurements('sleeve', { ...context, sleeveType: 'long', measurements }), null);
  assert.equal(fitValueFromMeasurements('sleeve', { ...context, sleeveType: 'sleeveless', measurements }), null);
});

test('fill priority is seller, measurement, confident AI, then photo-only', () => {
  const options = { ...context, fit: 'over', fitRead: read('crop', 'elbow'), measurements: { totalLength: 45, sleeveLength: 19 } };
  const current = profile({ fit: 'regular', length: 'basic', sleeve: 'cap' }, { fit: 'seller', length: 'seller', sleeve: 'seller' });
  assert.deepEqual(fillFitZones({ ...options, profile: current }), { axes: current.axes, axisSources: current.axisSources });
  assert.deepEqual(fillFitZones(options), { axes: { fit: 'over', length: 'semi_crop', sleeve: 'short' },
    axisSources: { fit: 'ai', length: 'measure', sleeve: 'measure' } });
  assert.deepEqual(fillFitZones({ ...options, measurements: {} }), { axes: { fit: 'over', length: 'crop', sleeve: 'elbow' },
    axisSources: { fit: 'ai', length: 'ai', sleeve: 'ai' } });
  assert.deepEqual(fillFitZones(context), { axes: {}, axisSources: { fit: 'unsure', length: 'unsure', sleeve: 'unsure' } });
  assert.deepEqual(fillFitZones({ ...context, fit: 'regular' }).axes, { fit: 'regular' });
});

test('unknown, unconfident, hanger length and flat long sleeves stay photo-only', () => {
  for (const fitRead of [undefined, read('seller text', 'seller text'), read('crop', 'elbow', { topLength: { value: 'crop', confident: false }, sleeve: { value: 'elbow', confident: false } })]) {
    assert.deepEqual(fillFitZones({ ...context, fitRead }).axes, {});
  }
  assert.deepEqual(fillFitZones({ ...context, fitRead: read('crop', 'elbow', { photoType: 'hanger' }) }).axes, { sleeve: 'elbow' });
  const fitRead = read('basic', 'back_of_hand', { photoType: 'flat', sleeveType: 'long' });
  assert.deepEqual(fillFitZones({ ...context, fitRead }).axes, { length: 'basic' });
  assert.equal(fillFitZones({ ...context, fitRead: { ...fitRead, photoType: 'worn' } }).axes.sleeve, 'back_of_hand');
});

test('filling keeps old default values and inactive axes without inventing new defaults', () => {
  const current = profile({ length: 'midi', silhouette: 'a_line' }, {}, 'dress');
  const result = fillFitZones({ clothingType: 'dress', subCategory: 'midi_dress', gender: 'women', profile: current });
  assert.deepEqual(result, { axes: { length: 'midi', silhouette: 'a_line' }, axisSources: { length: 'unsure', sleeve: 'unsure' } });
  assert.deepEqual(fillFitZones({ ...context, profile: profile({ fit: 'regular', length: 'basic' }) }),
    { axes: { fit: 'regular', length: 'basic' }, axisSources: { fit: 'unsure', length: 'unsure', sleeve: 'unsure' } });
});

test('changing category or gender discards prior seller choices before refilling', () => {
  const current = profile({ fit: 'slim', length: 'crop' }, { fit: 'seller', length: 'seller' });
  const men = fillFitZones({ ...context, gender: 'men', fit: 'over', profile: current });
  assert.deepEqual(men, { axes: { fit: 'over' }, axisSources: { fit: 'ai', sleeve: 'unsure' } });
  assert.deepEqual(fillFitZones({ clothingType: 'bottom', subCategory: 'jeans', gender: 'women', profile: current }),
    { axes: {}, axisSources: { length: 'unsure' } });
});

test('clearing the seller source recomputes the axis and leaves input untouched', () => {
  const current = profile({ length: 'crop' }, { length: 'seller' });
  const input = { ...context, profile: current, fitRead: read('basic', 'elbow'), measurements: [{ key: 'totalLength', value: 45 }] };
  const before = structuredClone(input);
  fillFitZones(input);
  assert.deepEqual(input, before);
  const reset = { ...input, profile: { ...current, axisSources: {} } };
  assert.equal(fillFitZones(reset).axes.length, 'semi_crop');
  assert.equal(fillFitZones(reset).axisSources.length, 'measure');
});

test('filling honors visible subcategory and sleeve choices without changing valid seller values', () => {
  assert.deepEqual(fillFitZones({ clothingType: 'dress', subCategory: 'mini_dress', gender: 'women', fitRead: read('floor', 'elbow') }).axes, { sleeve: 'elbow' });
  assert.deepEqual(fillFitZones({ ...context, fitRead: read('basic', 'fingers') }).axes, { length: 'basic' });
  const current = profile({ sleeve: 'short' }, { sleeve: 'seller' });
  assert.equal(fillFitZones({ ...context, profile: current, fitRead: read('basic', 'wrist', { sleeveType: 'long' }) }).axes.sleeve, 'short');
  assert.deepEqual(fillFitZones({ clothingType: 'bottom', subCategory: 'leggings', gender: 'women' }), { axes: {}, axisSources: {} });
});

test('the same fitRead selects top, skirt/dress or pants length by the current garment', () => {
  const fitRead = { photoType: 'worn', sleeveType: 'sleeveless',
    topLength: { value: 'slight_crop', confident: true },
    hemLength: { value: 'above_knee', confident: true },
    pantsLength: { value: 'ankle_cover', confident: true } };
  const before = structuredClone(fitRead);
  assert.deepEqual(fillFitZones({ ...context, fitRead }).axes, { length: 'slight_crop' });
  for (const [clothingType, subCategory] of [['bottom', 'mini_skirt'], ['dress', 'mini_dress']]) {
    assert.deepEqual(fillFitZones({ clothingType, subCategory, gender: 'women', fitRead }).axes, { length: 'above_knee' });
    const rejected = fillFitZones({ clothingType, subCategory: `long_${clothingType === 'bottom' ? 'skirt' : 'dress'}`, gender: 'women', fitRead });
    assert.deepEqual(rejected, { axes: {}, axisSources: { length: 'unsure' } });
  }
  assert.deepEqual(fillFitZones({ clothingType: 'bottom', subCategory: 'jeans', gender: 'women', fitRead }).axes, { length: 'ankle_cover' });
  assert.deepEqual(fillFitZones({ ...context, gender: 'men', fitRead }).axes, {});
  assert.deepEqual(fillFitZones({ clothingType: 'outer', gender: 'women', fitRead }).axes, {});
  assert.deepEqual(fitRead, before);
});

test('zone display uses source, short tile labels, measurement tag and a small card', () => {
  const zone = fitZones(context).find(({ axis }) => axis === 'length');
  assert.deepEqual(fitZoneState(zone, profile({ length: 'basic' }, { length: 'measure' })),
    { value: 'basic', source: 'measure', label: '골반', photoOnly: false, showMeasurement: true, card: 'lenw_hip.jpg' });
  for (const axisSources of [{}, { length: 'unsure' }, { length: 'auto' }]) {
    const state = fitZoneState(zone, profile({ length: 'basic' }, axisSources));
    assert.equal(state.value, null);
    assert.equal(state.label, '사진대로');
    assert.equal(state.photoOnly, true);
    assert.equal(state.showMeasurement, false);
    assert.equal(state.card, null);
  }
  const fit = fitZones(context)[0];
  assert.equal(fitZoneState(fit, profile({ fit: 'regular' }, { fit: 'ai' })).label, '레귤러');
  assert.equal(fitZoneState(fit, profile({ fit: 'slim' }, { fit: 'seller' })).showMeasurement, false);
  assert.equal(fitZoneState(zone, profile({ length: 'seller text' }, { length: 'seller' })).photoOnly, true);
});

test('fit notes match the seller text with the distinct male slim explanation', () => {
  assert.equal(fitNote('slim', 'women'), '몸선을 따라 붙어 깔끔하고 단정해요.');
  assert.equal(fitNote('slim', 'men'), '몸에 붙어 가슴과 팔 라인이 드러나요.');
  for (const gender of ['women', 'men']) {
    assert.equal(fitNote('regular', gender), '붙지도 크지도 않은 기본 핏이에요.');
    assert.equal(fitNote('semi_over', gender), '어깨가 살짝 내려오고 여유 있지만 부해 보이지 않아요.');
    assert.equal(fitNote('over', gender), '어깨가 확 내려오고 몸에서 떨어져 넉넉하게 떨어져요.');
  }
  assert.equal(fitNote('seller text', 'women'), '');
});

test('unreadable axes discard previously applied values but retain unsourced legacy defaults', () => {
  for (const source of ['ai', 'measure', 'seller']) {
    const input = profile({ length: 'basic', sleeve: 'wrist' }, { length: source, sleeve: source });
    // 유효한 셀러 선택은 유지된다. 카탈로그 밖 값은 사진대로가 되어 제거된다.
    if (source === 'seller') input.axes = { length: 'obsolete', sleeve: 'obsolete' };
    const before = structuredClone(input);
    const result = fillFitZones({ ...context, profile: input });
    assert.deepEqual(result.axes, {});
    assert.equal(result.axisSources.length, 'unsure');
    assert.deepEqual(input, before);
  }
  const measured = fillFitZones({ ...context, measurements: { totalLength: 42 } });
  assert.equal(fillFitZones({ ...context, profile: profile(measured.axes, measured.axisSources) }).axes.length, undefined);
  for (const axisSources of [{}, { length: 'unsure' }]) {
    assert.equal(fillFitZones({ ...context, profile: profile({ length: 'basic' }, axisSources) }).axes.length, 'basic');
  }
  for (const [clothingType, subCategory, category, fallback] of [
    ['dress', 'midi_dress', 'dress', 'midi'], ['bottom', 'long_skirt', 'skirt', 'long'],
  ]) {
    const result = fillFitZones({ clothingType, subCategory, gender: 'women',
      profile: profile({ length: 'floor' }, { length: 'ai' }, category) });
    assert.equal(result.axes.length, fallback);
    assert.equal(result.axisSources.length, 'unsure');
  }
});

test('reset previews and applies measured, AI and photo-only fallbacks without mutating the input', () => {
  const input = { ...context, profile: profile({ length: 'crop', sleeve: 'short' }, { length: 'seller', sleeve: 'seller' }) };
  const before = structuredClone(input);
  assert.deepEqual(resetFitZone('length', input), { axes: { sleeve: 'short' },
    axisSources: { fit: 'unsure', length: 'unsure', sleeve: 'seller' } });
  assert.equal(resetFitZone('length', { ...input, measurements: { totalLength: 45 } }).axes.length, 'semi_crop');
  assert.equal(resetFitZone('length', { ...input, fitRead: read('basic', 'elbow') }).axisSources.length, 'ai');
  assert.deepEqual(input, before);
  const dress = { clothingType: 'dress', subCategory: 'mini_dress', gender: 'women',
    profile: profile({ length: 'above_knee' }, { length: 'seller' }, 'dress') };
  const reset = resetFitZone('length', dress);
  assert.equal(reset.axes.length, 'mini');
  assert.equal(fitZoneState(fitZones(dress)[0], { ...dress.profile, ...reset }).photoOnly, true);
});

test('mannequin receives only the legacy axes and keeps matching garments unchanged', () => {
  const matchingFit = { clothingId: 'coat', fitCategory: 'outer', axes: { length: 'long' } };
  const input = { ...profile({ fit: 'over', length: 'basic', sleeve: 'short' },
    { fit: 'ai', length: 'measure', sleeve: 'seller' }), matchingFit };
  const before = structuredClone(input);
  const result = mannequinFitProfile(input);
  assert.deepEqual(result.axes, { fit: 'over', sleeve: 'short' });
  assert.equal('axisSources' in result, false);
  assert.equal(result.matchingFit, matchingFit);
  assert.deepEqual(input, before);
  for (const [category, axes] of [['top', { length: 'slight_crop', sleeve: 'wrist' }],
    ['outer', { sleeve: 'short' }], ['dress', { sleeve: 'elbow' }], ['pants', { length: 'ankle_cover' }]]) {
    assert.deepEqual(mannequinFitProfile(profile(axes, Object.fromEntries(Object.keys(axes).map((key) => [key, 'seller'])), category)).axes, {});
  }
  for (const axisSources of [{}, { length: 'seller' }, { length: 'unsure' }]) {
    assert.deepEqual(mannequinFitProfile(profile({ length: 'basic' }, axisSources)).axes, { length: 'basic' });
  }
  assert.deepEqual(mannequinFitProfile(profile({ sleeve: null }, { sleeve: 'unsure' }, 'outer')).axes, {});
  const legacy = { category: 'top', gender: 'women', axes: { length: 'basic' }, source: 'auto' };
  assert.equal(mannequinFitProfile(legacy), legacy);
  assert.equal(mannequinFitProfile(null), null);
});
