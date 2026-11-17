/* 분석 페이지 입었을 때 칸의 구성과 채우기. React와 저장 부수효과가 없는 순수 함수. */
import { analysisAxesFor, fitProfileCategory, subcategoryLength } from './fitAxes.js';

export const ANALYSIS_FIT_AXES = Object.freeze({
  top: { women: ['fit', 'length', 'sleeve'], men: ['fit', 'sleeve'] },
  outer: { women: ['fit', 'sleeve'], men: ['fit', 'sleeve'] },
  dress: { women: ['length', 'sleeve'], men: [] },
  skirt: { women: ['length'], men: [] },
  pants: { women: ['length'], men: ['length'] },
});

// server/app/agents/analysis_fit.py의 출처 정책 미러. null은 모든 카탈로그 값이다.
export const FIT_SOURCE_POLICY = Object.freeze({
  length: { seller: null, measure: null, ai: null },
  sleeve: { seller: null, measure: null, ai: null },
  fit: { seller: null, ai: ['slim', 'semi_over', 'over'] },
});

const choices = (rows) => rows.map(([value, label, card]) => ({ value, label, card: `${card}.jpg` }));
const FIT_OPTIONS = choices([
  ['slim', '슬림', 'fit_slim'], ['regular', '레귤러', 'fit_regular'],
  ['semi_over', '세미오버', 'fit_semi_over'], ['over', '오버', 'fit_over'],
]);
const TOP_LENGTH_OPTIONS = choices([
  ['crop', '크롭', 'lenw_crop'], ['slight_crop', '살짝 크롭', 'lenw_crop_s'],
  ['semi_crop', '허리선', 'lenw_waist'], ['basic', '골반 (기본)', 'lenw_hip'],
  ['semi_long', '엉덩이 덮음', 'lenw_seat'],
]);
const SHORT_SLEEVE_OPTIONS = choices([
  ['cap', '어깨만 덮음', 'short_cap'], ['cap_short', '위팔 위쪽', 'short_upper'],
  ['short', '위팔 중간', 'short_mid'], ['above_elbow', '팔꿈치 위', 'short_above_elbow'],
  ['elbow', '팔꿈치', 'short_elbow'],
]);
const LONG_SLEEVE_OPTIONS = choices([
  ['above_wrist', '손목 위', 'long_above_wrist'], ['wrist', '손목', 'long_wrist'],
  ['back_of_hand', '손등 덮음', 'long_back_of_hand'], ['fingers', '손끝 덮음', 'long_fingers'],
]);
const HEM_OPTIONS = choices([
  ['mini', '허벅지', 'skirt_thigh'], ['above_knee', '무릎 위', 'skirt_above_knee'],
  ['knee', '무릎', 'skirt_knee'], ['midi', '종아리', 'skirt_calf'],
  ['long', '발목 위', 'skirt_above_ankle'], ['floor', '발목 덮음', 'skirt_ankle_cover'],
]);
const PANTS_LENGTH_OPTIONS = choices([
  ['above_ankle', '발목 위', 'pants_above_ankle'], ['ankle', '발목', 'pants_ankle'],
  ['ankle_cover', '복숭아뼈 덮음', 'pants_ankle_cover'], ['below_ankle', '발등 덮음', 'pants_foot_cover'],
]);
const AXIS_LABELS = { fit: '핏', length: '기장', sleeve: '소매 길이' };
const FIT_NOTE = {
  slim: '몸선을 따라 붙어 깔끔하고 단정해요.',
  regular: '붙지도 크지도 않은 기본 핏이에요.',
  semi_over: '어깨가 살짝 내려오고 여유 있지만 부해 보이지 않아요.',
  over: '어깨가 확 내려오고 몸에서 떨어져 넉넉하게 떨어져요.',
};

function optionsFor(category, axis) {
  if (axis === 'fit') return FIT_OPTIONS;
  if (axis === 'sleeve') return [...SHORT_SLEEVE_OPTIONS, ...LONG_SLEEVE_OPTIONS];
  if (category === 'top') return TOP_LENGTH_OPTIONS;
  if (category === 'pants') return PANTS_LENGTH_OPTIONS;
  return HEM_OPTIONS;
}

function measurementNumber(measurements, key) {
  const raw = Array.isArray(measurements)
    ? measurements.find((entry) => entry.key === key)?.value
    : measurements?.[key];
  if ((typeof raw !== 'number' && typeof raw !== 'string') || String(raw).trim() === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function sleeveReach(measurements) {
  const sleeve = measurementNumber(measurements, 'sleeveLength');
  if (sleeve == null) return null;
  const shoulder = measurementNumber(measurements, 'shoulderWidth');
  return sleeve + (shoulder == null ? 0 : (shoulder - 37) / 2);
}

export function resolveSleeveType(sleeveType, measurements) {
  if (['sleeveless', 'short', 'long'].includes(sleeveType)) return sleeveType;
  const reach = sleeveReach(measurements);
  return reach == null ? null : reach < 40 ? 'short' : 'long';
}

export function fitZones({ clothingType, subCategory, gender = 'women', sleeveType, measurements } = {}) {
  if (subCategory === 'leggings') return [];
  const category = fitProfileCategory(clothingType, subCategory);
  const catalog = analysisAxesFor(category, gender);
  const sleeve = resolveSleeveType(sleeveType, measurements);
  return (ANALYSIS_FIT_AXES[category]?.[gender] || [])
    .filter((axis) => axis !== 'sleeve' || sleeve !== 'sleeveless')
    .map((axis) => {
      let options = optionsFor(category, axis);
      let groups;
      if (axis === 'sleeve') {
        groups = [
          { type: 'short', label: '반팔', options: SHORT_SLEEVE_OPTIONS },
          { type: 'long', label: '긴팔', options: LONG_SLEEVE_OPTIONS },
        ].filter(({ type }) => !sleeve || type === sleeve);
        options = groups.flatMap((group) => group.options);
      } else if (axis === 'length' && (category === 'skirt' || category === 'dress')) {
        const length = subcategoryLength(subCategory);
        const pair = { mini: ['mini', 'above_knee'], midi: ['knee', 'midi'], long: ['long', 'floor'] }[length];
        if (pair) options = options.filter(({ value }) => pair.includes(value));
      }
      options = options.filter(({ value }) => catalog[axis]?.some((entry) => entry.value === value));
      return { axis, label: AXIS_LABELS[axis], category, gender, options, ...(groups ? { groups } : {}) };
    });
}

// 쇼핑몰 착용 사진으로 맞춘 기준이 나오면 채운다.
export function longSleeveFromMeasurements(measurements) {
  return null;
}

export function fitValueFromMeasurements(axis, { clothingType, subCategory, gender = 'women', sleeveType, measurements } = {}) {
  const category = fitProfileCategory(clothingType, subCategory);
  if (!ANALYSIS_FIT_AXES[category]?.[gender]?.includes(axis)) return null;
  if (axis === 'length' && category === 'top' && gender === 'women') {
    const total = measurementNumber(measurements, 'totalLength');
    if (total == null) return null;
    return total < 35 ? 'crop' : total < 40 ? 'slight_crop' : total < 50 ? 'semi_crop' : total < 60 ? 'basic' : 'semi_long';
  }
  if (axis === 'sleeve') {
    const sleeve = resolveSleeveType(sleeveType, measurements);
    if (sleeve === 'long') return longSleeveFromMeasurements(measurements);
    const reach = sleeveReach(measurements);
    if (sleeve !== 'short' || reach == null) return null;
    return reach < 12 ? 'cap' : reach < 18 ? 'cap_short' : reach < 26 ? 'short' : reach < 32 ? 'above_elbow' : 'elbow';
  }
  return null;
}

export function fillFitZones({ fitRead, fit, profile, ...context } = {}) {
  const category = fitProfileCategory(context.clothingType, context.subCategory);
  const gender = context.gender || 'women';
  const sameScope = profile?.category === category && profile?.gender === gender;
  const axes = sameScope ? { ...profile.axes } : {};
  const axisSources = sameScope ? { ...profile.axisSources } : {};
  const sleeveType = resolveSleeveType(context.sleeveType ?? fitRead?.sleeveType, context.measurements);
  for (const zone of fitZones({ ...context, gender, sleeveType })) {
    const { axis, options } = zone;
    if (axisSources[axis] === 'seller' && optionsFor(category, axis).some(({ value }) => value === axes[axis])) continue;
    const measured = fitValueFromMeasurements(axis, { ...context, gender, sleeveType });
    if (measured != null) {
      axes[axis] = measured;
      axisSources[axis] = 'measure';
      continue;
    }
    const readKey = axis === 'length'
      ? { top: 'topLength', skirt: 'hemLength', dress: 'hemLength', pants: 'pantsLength' }[category]
      : axis;
    const reading = fitRead?.[readKey];
    const aiValue = axis === 'fit' ? fit : reading?.value;
    let confident = axis === 'fit' || reading?.confident === true;
    if (category === 'top' && axis === 'length' && fitRead?.photoType === 'hanger') confident = false;
    if (axis === 'sleeve' && LONG_SLEEVE_OPTIONS.some(({ value }) => value === aiValue) && fitRead?.photoType !== 'worn') confident = false;
    if (confident && options.some(({ value }) => value === aiValue)) {
      axes[axis] = aiValue;
      axisSources[axis] = 'ai';
    } else {
      // 옛 기본값은 운영 마네킹이 계속 쓴다. 분석 화면은 출처를 보고 사진대로 표시한다.
      axisSources[axis] = 'unsure';
    }
  }
  return { axes, axisSources };
}

export function fitZoneState(zone, profile) {
  const rawSource = profile?.axisSources?.[zone.axis];
  const source = ['ai', 'measure', 'seller', 'unsure'].includes(rawSource) ? rawSource : null;
  const applies = source && source !== 'unsure' && profile?.category === zone.category && profile?.gender === zone.gender;
  const option = applies ? optionsFor(zone.category, zone.axis).find(({ value }) => value === profile?.axes?.[zone.axis]) : null;
  return {
    value: option?.value ?? null,
    source,
    label: option ? option.label.replace(' (기본)', '') : '사진대로',
    photoOnly: !option,
    showMeasurement: !!option && source === 'measure',
    card: option?.card ?? null,
  };
}

export function fitNote(value, gender) {
  if (value === 'slim' && gender === 'men') return '몸에 붙어 가슴과 팔 라인이 드러나요.';
  return Object.hasOwn(FIT_NOTE, value) ? FIT_NOTE[value] : '';
}
