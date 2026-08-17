/* =============================================================
   signatureCutPool — 후킹 첫 화면 '시그니처 컷' 전용 이미지 풀 (오너 확정 2026-08-17)

   시그니처 컷은 상세페이지의 첫 인상을 만드는 자리라 화면 문법이 따로 있다:
   인물 상반신 극단 클로즈업 + 얼굴 일부만 노출(뒷모습·옆얼굴·절반·하관),
   배경은 착장 의류 색의 연한 톤. 일반 생성예시와 목적이 달라 **카탈로그에 넣지 않고**
   이 모듈이 독립 풀로 관리한다 — 갤러리 나열·자동 배정·발행 게이트 어디에도 섞이지 않는다.

   id 는 `sig_` 접두로 시작한다. 이것이 "이 exampleId 는 생성예시가 아니라 시그니처 풀"
   이라는 유일한 표식이며, blocks 저장 스키마는 그대로 둔다(exampleId 자리를 그대로 쓴다).
   ============================================================= */

import { seededPick } from './storyboardEntryPlacement.js';

const BASE = '/assets/signature';

const entry = (id, gender) => Object.freeze({
  id,
  gender,
  thumb: `${BASE}/thumb/${id}.webp`,
  url: `${BASE}/${id}.webp`,
});

export const SIGNATURE_CUTS = Object.freeze([
  entry('sig_men_01', 'men'),
  entry('sig_men_02', 'men'),
  entry('sig_men_03', 'men'),
  entry('sig_men_04', 'men'),
  entry('sig_men_05', 'men'),
  entry('sig_women_01', 'women'),
  entry('sig_women_02', 'women'),
  entry('sig_women_03', 'women'),
  entry('sig_women_04', 'women'),
]);

export const SIGNATURE_ID_PREFIX = 'sig_';

/** 이 exampleId 가 시그니처 풀 소속인가 — 생성예시 경로가 이 값을 배제할 때 쓴다. */
export const isSignatureExampleId = (id) => (
  typeof id === 'string' && id.startsWith(SIGNATURE_ID_PREFIX)
);

/* 성별 불명이면 women 을 기본으로 둔다 — 현재 상품·모델 카탈로그가 여성 비중이 크고,
   남성 상품은 진입 단계에서 성별이 확정되는 경로라 불명 상태로 남는 경우가 드물다. */
export const signatureCutsFor = (gender) => {
  const want = gender === 'men' ? 'men' : 'women';
  return SIGNATURE_CUTS.filter((cut) => cut.gender === want);
};

export const signatureCutById = (id) => SIGNATURE_CUTS.find((cut) => cut.id === id) || null;

/** 프로젝트별로 안정적인 랜덤 — 같은 프로젝트를 다시 열면 같은 컷이 나온다. */
export function pickSignatureCut({ gender, projectId, slotKey = 'signature' }) {
  const pool = signatureCutsFor(gender);
  return seededPick(pool, `${projectId || 'default'}:hook:${slotKey}`);
}
