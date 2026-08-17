/* =============================================================
   features/analysis — AI(가상) 모델 카탈로그 (단일 출처)

   서버 레지스트리(server/app/data/virtual_models.json)와 동기 유지.
   컷 생성(AG-06)이 이 id('mA'…)로 아이덴티티 자산을 해석하고, 라이선스 게이트는
   비-UUID id를 no-op 처리한다(과금 없음). 실제 모델(FaceMarket)과 탭으로 구분 표시.
   이름은 인물 외형에 맞춘다(2026-08-01 사용자 결정): 서양인 = 짧은 영문 이름,
   동양인 = 짧은 한국어 이름. 'mA'… id 는 서버 자산 키라 그대로 두고 표시명만 바꾼다.

   이 파일이 프론트의 유일한 목록이다. 2026-08-17 여성 9인(mF~mN) 추가 때
   AnalysisForm 의 그리드만 늘리고 modelSelection 의 무료 판정 집합을 빼먹어,
   신규 9인이 유료 실제 모델로 분류돼 "+ 실제 모델 이용료 별도" 문구가 붙는 사고가
   났다. 목록을 여기 하나로 모아 그 종류의 결함을 없앤다 — 새 모델은 여기만 고친다.
   ============================================================= */

export const AI_MODELS = [
  { id: 'mA', displayName: 'Mia', gender: 'women', thumb: '/models/women/w1.webp' },
  { id: 'mB', displayName: 'Leo', gender: 'men', thumb: '/models/men/m1.webp' },
  { id: 'mC', displayName: '도윤', gender: 'men', thumb: '/models/men/m2.webp' },
  { id: 'mD', displayName: '수혁', gender: 'men', thumb: '/models/men/m3.webp' },
  { id: 'mE', displayName: '지안', gender: 'women', thumb: '/models/women/w2.webp' },
  { id: 'mF', displayName: '하린', gender: 'women', thumb: '/models/women/w3.webp' },
  { id: 'mG', displayName: '세아', gender: 'women', thumb: '/models/women/w4.webp' },
  { id: 'mH', displayName: '예린', gender: 'women', thumb: '/models/women/w5.webp' },
  { id: 'mI', displayName: '다인', gender: 'women', thumb: '/models/women/w6.webp' },
  { id: 'mJ', displayName: '소윤', gender: 'women', thumb: '/models/women/w7.webp' },
  { id: 'mK', displayName: '유나', gender: 'women', thumb: '/models/women/w8.webp' },
  { id: 'mL', displayName: '채원', gender: 'women', thumb: '/models/women/w9.webp' },
  { id: 'mM', displayName: '나윤', gender: 'women', thumb: '/models/women/w10.webp' },
  { id: 'mN', displayName: 'Nora', gender: 'women', thumb: '/models/women/w11.webp' },
];

/** 가상모델 id 집합 — 라이선스 과금 판정이 이걸로 '실제 모델'을 가른다. */
export const AI_MODEL_IDS = new Set(AI_MODELS.map((model) => model.id));
