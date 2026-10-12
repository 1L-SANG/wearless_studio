/* =============================================================
   signatureCutPool — 후킹 첫 화면 '시그니처 컷' 전용 이미지 풀 (오너 확정 2026-08-17)

   시그니처 컷은 상세페이지의 첫 인상을 만드는 자리라 화면 문법이 따로 있다:
   상의·아우터와 남성 원피스 폴백은 상반신, 하의는 하반신, 여성 원피스는 전용 클로즈업,
   배경은 착장 의류 색의 연한 톤. 일반 생성예시와 목적이 달라 **카탈로그에 넣지 않고**
   이 모듈이 독립 풀로 관리한다 — 갤러리 나열·자동 배정·발행 게이트 어디에도 섞이지 않는다.

   id 는 `sig_` 접두로 시작한다. 이것이 "이 exampleId 는 생성예시가 아니라 시그니처 풀"
   이라는 유일한 표식이며, blocks 저장 스키마는 그대로 둔다(exampleId 자리를 그대로 쓴다).
   ============================================================= */

import { seededPick } from './storyboardEntryPlacement.js';

const BASE = '/assets/signature';

const UPPER_CLOTHING_TYPES = Object.freeze(['top', 'outer', 'dress']);
const BOTTOM_CLOTHING_TYPES = Object.freeze(['bottom']);
const DRESS_CLOTHING_TYPES = Object.freeze(['dress']);

const entry = (id, gender, applicableClothingTypes, bottomKind = null) => Object.freeze({
  id,
  gender,
  applicableClothingTypes,
  bottomKind,
  thumb: `${BASE}/thumb/${id}.webp`,
  url: `${BASE}/${id}.webp`,
});

export const SIGNATURE_CUTS = Object.freeze([
  entry('sig_men_01', 'men', UPPER_CLOTHING_TYPES),
  entry('sig_men_02', 'men', UPPER_CLOTHING_TYPES),
  entry('sig_men_03', 'men', UPPER_CLOTHING_TYPES),
  entry('sig_men_04', 'men', UPPER_CLOTHING_TYPES),
  entry('sig_men_05', 'men', UPPER_CLOTHING_TYPES),
  entry('sig_women_01', 'women', UPPER_CLOTHING_TYPES),
  entry('sig_women_02', 'women', UPPER_CLOTHING_TYPES),
  entry('sig_women_03', 'women', UPPER_CLOTHING_TYPES),
  entry('sig_women_04', 'women', UPPER_CLOTHING_TYPES),
  entry('sig_women_bottom_pants_01', 'women', BOTTOM_CLOTHING_TYPES, 'pants'),
  entry('sig_women_bottom_pants_02', 'women', BOTTOM_CLOTHING_TYPES, 'pants'),
  entry('sig_women_bottom_skirt_01', 'women', BOTTOM_CLOTHING_TYPES, 'skirt'),
  entry('sig_women_bottom_skirt_02', 'women', BOTTOM_CLOTHING_TYPES, 'skirt'),
  entry('sig_women_dress_01', 'women', DRESS_CLOTHING_TYPES, 'dress'),
  entry('sig_women_dress_02', 'women', DRESS_CLOTHING_TYPES, 'dress'),
  entry('sig_men_bottom_pants_01', 'men', BOTTOM_CLOTHING_TYPES, 'pants'),
  entry('sig_men_bottom_pants_02', 'men', BOTTOM_CLOTHING_TYPES, 'pants'),
]);

export const SIGNATURE_ID_PREFIX = 'sig_';

/** 이 exampleId 가 시그니처 풀 소속인가 — 생성예시 경로가 이 값을 배제할 때 쓴다. */
export const isSignatureExampleId = (id) => (
  typeof id === 'string' && id.startsWith(SIGNATURE_ID_PREFIX)
);

function signatureContext(context) {
  return typeof context === 'string' ? { gender: context } : (context || {});
}

function isSkirtCategory({ subCategory, customCategory }) {
  const category = `${subCategory || ''} ${customCategory || ''}`.toLowerCase();
  return category.includes('skirt') || category.includes('스커트') || category.includes('치마');
}

const MEN_GENDER_TOKENS = new Set(['men', 'male', '남성', '남']);

/** 서버 mannequin.select_base_gender와 같은 첫 대상 성별 규칙. */
export function signatureGenderFor({ clothingType, targetGenders } = {}) {
  if (clothingType === 'dress') return 'women';
  const first = Array.isArray(targetGenders) ? targetGenders[0] : null;
  return MEN_GENDER_TOKENS.has(String(first || '').toLowerCase()) ? 'men' : 'women';
}

/* 성별 불명이면 women, 의류 종류가 없으면 top 을 기본으로 둔다. 하위 분류를 모르는
   하의는 반바지를 포함한 나머지 하의와 같이 pants 풀을 쓴다. */
export const signatureCutsFor = (context = {}) => {
  const normalized = signatureContext(context);
  const { gender, clothingType = 'top' } = normalized;
  const want = gender === 'men' ? 'men' : 'women';
  if (clothingType === 'dress' && want === 'women') {
    return SIGNATURE_CUTS.filter((cut) => cut.gender === 'women' && cut.bottomKind === 'dress');
  }
  const bottomKind = want === 'women' && isSkirtCategory(normalized) ? 'skirt' : 'pants';
  return SIGNATURE_CUTS.filter((cut) => (
    cut.gender === want
    && cut.applicableClothingTypes.includes(clothingType)
    && (clothingType !== 'bottom' || cut.bottomKind === bottomKind)
  ));
};

export const signatureCutById = (id) => SIGNATURE_CUTS.find((cut) => cut.id === id) || null;

export const signatureCutMatches = (id, context = {}) => (
  signatureCutsFor(context).some((cut) => cut.id === id)
);

/** 프로젝트별로 안정적인 랜덤 — 같은 프로젝트를 다시 열면 같은 컷이 나온다. */
export function pickSignatureCut({ projectId, slotKey = 'signature', ...context }) {
  const pool = signatureCutsFor(context);
  return seededPick(pool, `${projectId || 'default'}:hook:${slotKey}`);
}

/** sig_ 선택 또는 시그니처 슬롯의 잘못된 풀만 바꾼다. 나머지 셀러 편집은 그대로 둔다. */
export function repairSignatureCutSelections(blocks, context = {}) {
  if (!Array.isArray(blocks)) return { blocks, changed: false, repairedIds: [] };
  const picked = pickSignatureCut(context);
  if (!picked) return { blocks, changed: false, repairedIds: [] };
  const repairedIds = [];
  const next = blocks.map((block) => {
    const belongsToSignature = block?.hookSlotRole === 'signature'
      || isSignatureExampleId(block?.exampleId);
    if (!belongsToSignature
      || signatureCutMatches(block.exampleId, context)) return block;
    repairedIds.push(block.id);
    return {
      ...block,
      exampleId: picked.id,
      exampleSelectionOrigin: block.exampleSelectionOrigin || 'auto',
      thumb: picked.thumb,
    };
  });
  return {
    blocks: repairedIds.length ? next : blocks,
    changed: repairedIds.length > 0,
    repairedIds,
  };
}
