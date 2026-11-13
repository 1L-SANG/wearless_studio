import { selectTemplateStyle } from '@/lib/pageTemplates/pageStyle.js';
import { mockQuery } from '@/mock/outfitV18Fixtures.js';
import mockFullDetails from './detailRecommendationsFixture.json';
/* =============================================================
   mock/db.js — single source of fake data (conforms to lib/types.js
   and documents/common_data_contract.md).
   Screens NEVER hardcode data; they read it through mock/api.js,
   which reads from here. Swap this out for a real backend later.
   creditCosts is sourced from lib/limits.js (single tunable place).

   NOTE: the per-creation "draft" collections (project, product,
   analysis, mannequins, storyboard, editorBlocks, wardrobe) are
   MUTATED by the api (adjust/regenerate push, save* assign).
   buildDraft() rebuilds them from fresh seeds; api.createProject()
   reinstalls a clean copy, so starting a new creation does not leak
   the prior session's variants. Stable reference data (account,
   catalogs, models, library …) is not reseeded.
   ============================================================= */
import { Placeholder as P } from '@/mock/placeholders.js';
import genExamples from '@/data/genExamples.json';
import { CREDIT_COSTS } from '@/lib/limits.js';
import { uid } from '@/lib/ids.js';
import { assemblePage } from '@/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog } from '@/lib/pageTemplates/catalog.js';
import { buildAutoBlocks } from '@/lib/pageTemplates/autoBlocks.js';
import { genderForClothingType } from '@/lib/productGender.js';
import {
  createMeasurementFields,
  MEASUREMENT_LABELS,
  MEASUREMENT_SCHEMA,
} from '@/lib/measurementSchema.js';
import { seedStoryboard } from '@/lib/pageTemplates/seed.js';
import { colorDisplayName } from '@/lib/colorwayMatching.js';
import { axesFor, fitProfileCategory } from '@/lib/fitAxes.js';
import { recommendMatchingItems, toLegacyMatchClothing } from '@/mock/matchingRecommendation.js';
import { CONTENT_ROLES, SECTION_ROLES } from '@/lib/storyboardTaxonomy.js';

const nowIso = () => new Date().toISOString();
const copyFitProfile = (profile) => ({
  ...profile,
  axes: { ...(profile?.axes || {}) },
  ...(profile?.matchingFit ? {
    matchingFit: { ...profile.matchingFit, axes: { ...(profile.matchingFit.axes || {}) } },
  } : {}),
});
const defaultFitProfile = (product, analysis) => {
  const category = fitProfileCategory(product?.clothingType, analysis?.subCategory) || 'top';
  const gender = genderForClothingType(
    product?.clothingType,
    analysis?.targetGenders || product?.targetGenders,
  );
  const axes = Object.fromEntries(Object.keys(axesFor(category, gender)).map((axis) => [axis, null]));
  return { category, gender, axes, source: 'auto', version: 2 };
};

/* ---- Account (stable) ---- */
const account = { name: 'Jisoo Han', avatar: P.portrait('han'), credits: 510, plan: 'free', topupBonusEnabled: false };

// 추가 색상 모두가 같은 풀샷/중간샷 촬영 예시 템플릿을 공유한다. 색상마다 별도 예시를
// 만들지 않고, 실제 생성에서 현재 colorId의 셀러 사진과 자연스러운 미세 포즈 변주를 쓴다.
const DEMO_COLORWAY_PREVIEWS = Object.freeze({
  top: Object.freeze({
    productName: '소프트 골지 라운드 니트',
    template: Object.freeze({
      full: '/assets/colorway/soft-rib-knit-ivory-western-male-full-v2.png',
      medium: '/assets/colorway/soft-rib-knit-ivory-western-male-medium-v2.png',
    }),
  }),
  bottom: Object.freeze({
    productName: '세미 와이드 치노 팬츠',
    template: Object.freeze({
      full: '/assets/colorway/semi-wide-chino-beige-western-male-full-v1.png',
      medium: '/assets/colorway/semi-wide-chino-beige-western-male-medium-v2.png',
    }),
  }),
});

const demoColorwayPreviewFor = (productName, clothingType) => {
  const preview = DEMO_COLORWAY_PREVIEWS[clothingType];
  return preview?.productName === productName ? preview : null;
};

/* ---- Catalogs (stable closed option sets) ---- */
const catalogs = {
  clothingTypes: [
    { value: 'top', label: '상의' }, { value: 'bottom', label: '하의' },
    { value: 'outer', label: '아우터' }, { value: 'dress', label: '원피스' },
  ],
  // 세부 카테고리 — 저장 값은 영문 토큰, 한국어는 라벨 (계약 §4)
  subCategories: {
    top: [
      { value: 'tshirt', label: '티셔츠' }, { value: 'sweatshirt', label: '맨투맨' },
      { value: 'shirt', label: '셔츠' }, { value: 'knit', label: '니트' },
      { value: 'hoodie', label: '후드' }, { value: 'blouse', label: '블라우스' },
    ],
    bottom: [
      { value: 'cotton_pants', label: '면바지' }, { value: 'training_pants', label: '트레이닝 팬츠' },
      { value: 'jeans', label: '청바지' }, { value: 'slacks', label: '슬랙스' },
      { value: 'leggings', label: '레깅스', gender: 'women' },
      { value: 'mini_skirt', label: '미니 치마', gender: 'women' },
      { value: 'midi_skirt', label: '미디 치마', gender: 'women' },
      { value: 'long_skirt', label: '롱 치마', gender: 'women' },
    ],
    outer: [
      { value: 'shirt', label: '셔츠' }, { value: 'jacket', label: '자켓' },
      { value: 'cardigan', label: '가디건' }, { value: 'padding', label: '패딩' }, { value: 'coat', label: '코트' },
      { value: 'blazer', label: '블레이저' }, { value: 'windbreaker', label: '바람막이' },
    ],
    dress: [
      { value: 'mini_dress', label: '미니 원피스' },
      { value: 'midi_dress', label: '미디 원피스' },
      { value: 'long_dress', label: '롱 원피스' },
    ],
  },
  genders: [{ value: 'women', label: '여자' }, { value: 'men', label: '남자' }],
  fits: [
    { value: 'slim', label: '슬림핏' }, { value: 'regular', label: '정핏' },
    { value: 'semi_over', label: '세미오버' }, { value: 'over', label: '오버핏' },
  ],
  shotTypes: [
    { value: 'full', label: '풀샷' }, { value: 'medium', label: '미디움샷' },
  ],
  // 제품 이미지 전용 옵션 — 화면 하드코딩 금지 (계약 §5)
  productSourcePhotos: [
    { value: 'front', slot: 'Front', label: '내 앞면 사진' },
    { value: 'back', slot: 'Back', label: '내 뒷면 사진' },
  ],
  productShotTypes: [
    { value: 'ghost', label: '고스트샷' }, { value: 'detail', label: '디테일샷' },
  ],
  outerClosureStates: [
    { value: 'closed', label: '전체 닫힘', caption: '전체 닫힘' },
    { value: 'openToChest', label: '가슴까지', caption: '가슴까지 열림' },
    { value: 'partial', label: '반만 열림', caption: '반만 열림' },
    { value: 'openToWaist', label: '배까지', caption: '배까지 열림' },
    { value: 'open', label: '전체 열림', caption: '전체 열림' },
  ],
  // 2026-08-07 개편: Fit 폐기(실사용 0건) → 뒷면 디테일 신설. Detail 값=앞면 디테일(재해석).
  angleSlots: ['Front', 'Back', 'Detail', 'BackDetail'],
  angleLabels: { Front: '앞면', Back: '뒷면', Detail: '앞면 디테일', BackDetail: '뒷면 디테일' },
  // measurement schema per clothing type (PRD §6.5) — key는 영문 토큰 (계약 §4)
  measurementSchema: MEASUREMENT_SCHEMA,
  measurementLabels: MEASUREMENT_LABELS,
  sellingPointSuggestions: ['골지 짜임', '라운드넥', '소매 리브', '도톰한 짜임', '세미오버핏'],
  swatchColors: [
    { id: 'white', label: '화이트', hex: '#ffffff' },
    { id: 'gray', label: '그레이', hex: '#9a9aa1' },
    { id: 'black', label: '블랙', hex: '#15141a' },
    { id: 'ivory', label: '아이보리', hex: '#f3eee1' },
    { id: 'beige', label: '베이지', hex: '#d8c4a3' },
    { id: 'brown', label: '브라운', hex: '#7a5230' },
    // 퍼플은 7번째 — 7열 그리드의 첫 줄 맨 오른쪽 자리 (2026-08-15 사용자 결정)
    { id: 'purple', label: '퍼플', hex: '#7d5ba6' },
    { id: 'red', label: '레드', hex: '#c0392b' },
    { id: 'yellow', label: '옐로우', hex: '#e7c75c' },
    { id: 'green', label: '그린', hex: '#3f7a4f' },
    { id: 'blue', label: '블루', hex: '#2a5db0' },
    { id: 'navy', label: '네이비', hex: '#1f2a44' },
    { id: 'pink', label: '핑크', hex: '#e3a7b8' },
  ],
  genExamples,
  // 프레임 = 순수 이미지 레이아웃만. 정보성 프리셋(FAQ·상품 정보 카드 등 빈 그리드만
  // 만들던 장식 항목)은 '내용' 탭(PRD §10.14 infoPresets)이 정식 대체해 제거.
  frames: [
    { id: 'split2', label: '2분할', cols: 2 }, { id: 'grid3', label: '3컷 구성', cols: 3 },
    { id: 'ba', label: 'Before / After', cols: 2 }, { id: 'colorcmp', label: '컬러 비교', cols: 3 },
  ],
  shapes: [
    { id: 'circle', label: '원' }, { id: 'rect', label: '사각형' }, { id: 'triangle', label: '삼각형' },
    { id: 'diamond', label: '마름모' }, { id: 'star', label: '별' }, { id: 'heart', label: '하트' },
    { id: 'hexagon', label: '육각형' }, { id: 'bubble', label: '말풍선' },
  ],
  lines: [{ id: 'arrow-l', label: '←' }, { id: 'line', label: '—' }, { id: 'arrow-r', label: '→' }],
  fonts: ['Pretendard', 'Gowun Dodum', 'Gowun Batang', 'Cal Sans', 'Roboto Mono', 'Cormorant'],
  downloadOptions: [
    { id: 'long', title: '전체 상세페이지 긴 PNG 1장', desc: '모든 블록을 세로로 이어 붙여 한 장으로 저장' },
    { id: 'zip', title: '블록별 PNG ZIP', desc: '각 블록을 개별 PNG로 저장해 ZIP으로 다운로드' },
  ],
  // 단계별 크레딧 단가 — lib/limits.js 가 단일 소스. 여기로 노출해 계약 shape 유지.
  creditCosts: { ...CREDIT_COSTS },
};

/* ---- Models & match clothing (stable option sets) ---- */
// 실제 AI 가상모델 썸네일 (mock 모델 교체) — wm=여성, m=남성. 이미지는 public/models/.
const models = [
  { id: 'mA', name: 'Mia', gender: 'women', thumb: '/models/women/w1.webp', recommended: true },
  { id: 'mB', name: 'Leo', gender: 'men', thumb: '/models/men/m1.webp', recommended: false },
  { id: 'mC', name: '도윤', gender: 'men', thumb: '/models/men/m2.webp', recommended: false },
  { id: 'mD', name: '수혁', gender: 'men', thumb: '/models/men/m3.webp', recommended: false },
  { id: 'mF', name: '하린', gender: 'women', thumb: '/models/women/w3.webp', recommended: false },
  { id: 'mG', name: '세아', gender: 'women', thumb: '/models/women/w4.webp', recommended: false },
  { id: 'mH', name: '예린', gender: 'women', thumb: '/models/women/w5.webp', recommended: false },
  { id: 'mI', name: '다인', gender: 'women', thumb: '/models/women/w6.webp', recommended: false },
  { id: 'mJ', name: '소윤', gender: 'women', thumb: '/models/women/w7.webp', recommended: false },
  { id: 'mK', name: '유나', gender: 'women', thumb: '/models/women/w8.webp', recommended: false },
  { id: 'mL', name: '채원', gender: 'women', thumb: '/models/women/w9.webp', recommended: false },
  { id: 'mM', name: '나윤', gender: 'women', thumb: '/models/women/w10.webp', recommended: false },
  { id: 'mN', name: 'Nora', gender: 'women', thumb: '/models/women/w11.webp', recommended: false },
];
const matchClothing = toLegacyMatchClothing(recommendMatchingItems({
  clothingType: 'top',
  targetGenders: ['women'],
  styleTags: ['basic', 'daily', 'clean'],
  productColor: 'black',
}));

/* ---- Generation job steps (stable, PRD §9.2) ---- */
const genSteps = [
  { key: 'info', label: '상품 정보 정리' }, { key: 'prep', label: '이미지 생성 준비' },
  { key: 'styling', label: '핵심 장점 이미지 생성' }, { key: 'horizon', label: '핏·코디 이미지 생성' },
  { key: 'product', label: '제품 확인 이미지 생성' }, { key: 'copy', label: '카피라이팅 적용' },
  { key: 'assemble', label: '상세페이지 조립' },
];

/* ---- Library (stable list) — ProjectSummary (계약 §2).
   updatedAt 은 ISO, '2시간 전' 표시는 화면 파생. blocks→blockCount 개명 ---- */
const hoursAgo = (h) => new Date(Date.now() - h * 3600 * 1000).toISOString();
const library = [
  { id: uid('lib'), title: '소프트 골지 라운드 니트', cover: P.photo('lib1', 'horizon', 400, 520), clothingType: 'top', blockCount: 8, status: 'done', updatedAt: hoursAgo(2) },
  { id: uid('lib'), title: '와이드 데님 팬츠', cover: P.photo('lib2', 'styling', 400, 520), clothingType: 'bottom', blockCount: 6, status: 'done', updatedAt: hoursAgo(26) },
  { id: uid('lib'), title: '오버핏 울 코트', cover: P.product('lib3', 400, 520), clothingType: 'outer', blockCount: 9, status: 'generating', updatedAt: hoursAgo(0.2) },
  { id: uid('lib'), title: '플리츠 미디 원피스', cover: P.photo('lib4', 'horizon', 400, 520), clothingType: 'dress', blockCount: 5, status: 'draft', updatedAt: hoursAgo(72) },
];

/* ---- editor element builders (seed + 콘티 기반 생성이 공유) ---- */
const T = (x, y, w, h, text, style) => ({ id: uid('el'), type: 'text', x, y, w, h, text, style: style || {} });
// cutType: 생성 산출물에 기록되는 컷 종류 메타데이터 — '현재 이미지 수정'의 옵션 기준 (디테일 줌은 product 로 분류)
const IMG = (x, y, w, h, src, radius, cutType) => ({ id: uid('el'), type: 'image', x, y, w, h, src, radius: radius ?? 8, ...(cutType ? { cutType } : {}) });

/* 자동 안내 블록 (PRD §10.14) — 사이즈 안내는 product.measurements 를 "생성 시점"에 읽는다 */
/* 저장된 콘티 → 에디터 블록 (mock 생성기, 계약 §6 generateDetailPage).
   실제 파이프라인이 할 일을 placeholder 로 흉내만 낸다 — 블록 수·종류·순서가
   콘티를 따라가고, 카피라이팅 ON 이면 첫 장면/핵심 장점에 카피를 넣는다. */
export function buildEditorBlocksFromStoryboard(storyboard, product, copywriting, analysis = {}, shopName = null) {
  const cutResults = (storyboard || []).filter(block => block.source === 'ai').map(block => ({
    blockId: block.id,
    imageUrl: P.photo(`gen_${block.id}`, block.cutType === 'product' ? 'product' : block.cutType === 'horizon' ? 'horizon' : 'styling', 800, 1200),
    width: 800, height: 1200,
  }));
  const hasFrame = (storyboard || []).some(block => block.taxonomyVersion === 4
    && pageTemplatesCatalog.designs[block.pageDesignId]?.framePhotos);
  const name = hasFrame && typeof shopName === 'string' ? shopName.trim() : '';
  return assemblePage({ storyboard, product, copywriting, analysis, cutResults,
    ...(name ? { shopName: name } : {}) });
}

export function buildStoryboard(colors, context = {}) {
  return seedStoryboard({ ...context, colors }, { ...context, pageStyle: context.pageStyle }, context);
}

/* =============================================================
   buildDraft() — fresh per-creation working data. Called at init
   and by api.createProject() so a new creation never inherits the
   prior session's mutations (mannequin variants, saved storyboard…).
   ============================================================= */
export function buildDraft() {
  /* ---- Project — 플로우 최상위 엔티티 (ADR-0001) ---- */
  const project = {
    id: uid('prj'), status: 'draft', title: '',
    copywriting: true,
    selectedMannequinId: null, adjustCount: 0, fitProfile: null,
    latestJobs: { mannequin: null, detailPage: null },
    createdAt: nowIso(), updatedAt: nowIso(),
  };

  // 실측 — key 는 영문 토큰, 라벨은 catalogs.measurementLabels (계약 §4)
  const measurements = () => createMeasurementFields('top', {
    totalLength: 64,
    shoulderWidth: 42,
    chestWidth: 51,
  });

  /* ---- Seed product input (the 골지 니트 example) ---- */
  const product = {
    id: uid('prd'), name: '소프트 골지 라운드 니트', clothingType: 'top',
    uploadComplete: false, measurementsUnknown: false,
    colors: [
      {
        id: 'col1', name: '블랙', swatchId: 'black', isBase: true, isMain: true, monotone: true,
        images: [
          { id: uid('img'), slot: 'Front', label: 'Front', src: P.photo('c1f', 'horizon', 300, 400) },
          { id: uid('img'), slot: 'Back', label: 'Back', src: P.photo('c1b', 'horizon', 300, 400) },
          { id: uid('img'), slot: 'Detail', label: 'Detail', src: P.detail('c1d', 300, 400) },
          { id: uid('img'), slot: 'BackDetail', label: 'BackDetail', src: P.detail('c1bd', 300, 400) },
        ],
      },
      {
        id: 'col2', name: '아이보리', swatchId: 'ivory', isBase: false, monotone: true,
        images: [{ id: uid('img'), slot: 'Front', label: '정면', src: P.photo('c2f', 'horizon', 300, 400) }],
      },
      {
        id: 'col3', name: '스카이블루', swatchId: 'blue', isBase: false, monotone: true,
        images: [{ id: uid('img'), slot: 'Front', label: '정면', src: P.photo('c3f', 'horizon', 300, 400) }],
      },
      {
        id: 'col4', name: '그레이', swatchId: 'gray', isBase: false, monotone: true,
        images: [{ id: uid('img'), slot: 'Front', label: '정면', src: P.photo('c4f', 'horizon', 300, 400) }],
      },
    ],
    measurements: measurements(),
  };
  project.title = product.name;

  /* ---- Analysis result (AI-filled, editable) ----
     clothingType/measurements 는 Product 가 단일 소유 (계약 §3.1).
     mock 과도기: analysis 에도 사본을 두되, api.saveAnalysis 가
     Product 소유 필드를 product 로 동기화한다. */
  const analysis = {
    clothingType: 'top', subCategory: 'knit', targetGenders: ['women'],
    customCategory: null, // enum 밖 의류의 자유 명칭 — AI 추측 + 사용자 주관식 수정 (계약 §3.2)
    fit: 'semi_over', suggestedName: '소프트 골지 라운드 니트',
    // 니트 시드 → 서버 DEFAULT_MATERIALS(팩트체크 2026-07-13)와 동일한 아크릴 100. 구 '코튼 60/폴리 40'은
    // 실제 분석이 절대 내지 않는 값이라, mock 화면을 실분석으로 오인하게 만들던 흔적을 제거(2026-07-15).
    materials: [{ name: '아크릴', ratio: 100 }],
    sellingPoints: [],
    mdComment: '',
    frontClosure: { version: 1, kind: product.clothingType === 'outer' ? 'zipper' : 'unknown',
      range: 'graded', secondary: null, evidenceSlots: ['Front'], note: null },
    detailRecommendations: { version: 1, status: 'unavailable', candidates: [] },
    detailRecommendationsHandoff: null,
    aiSuggestedPoints: ['골지 짜임', '라운드넥'],
    styleTags: ['basic', 'daily', 'clean'],
    selectedModelId: 'mA', stylingModelId: null,
    models, matchClothing: matchClothing.map((m) => ({ ...m })),
    washCare: '', locked: false,
    measurementsUnknown: false,
    measurements: measurements(),
  };
  const fitProfile = defaultFitProfile(product, analysis);
  project.fitProfile = copyFitProfile(fitProfile);
  analysis.fitProfile = copyFitProfile(fitProfile);

  /* ---- Mannequin history (PRD §7.3·§7.7, fit-profile P2) ----
     단일컷 버전 히스토리. 시드는 빈 배열 — 최초 생성(진행 UX + 1크레딧 차감)은
     마네킹 페이지 진입 시 api.generateMannequins 가 수행한다 (미리 채우면 우회됨). */
  const mannequins = [];

  const templateDemo = mockQuery().get('mockTemplate');
  if (['emo', 'show'].includes(templateDemo)) analysis.pageStyle = selectTemplateStyle(templateDemo);
  if (mockQuery().get('mockDetail') === 'full') analysis.detailRecommendations = structuredClone(mockFullDetails);

  /* ---- Storyboard blocks — 기본 콘티는 buildStoryboard() (PRD §8, ADR-0003·0004).
     첫 화면 스타일은 시드가 시그니처 컷 프레임을 이미 포함한다(2026-08-14). ---- */
  const storyboard = buildStoryboard(product.colors, {
    projectId: project.id,
    clothingType: product.clothingType,
    targetGenders: analysis.targetGenders,
    subCategory: analysis.subCategory,
    customCategory: analysis.customCategory,
    matchClothing: analysis.matchClothing,
    detailRecommendations: analysis.detailRecommendations,
    sellingPoints: analysis.sellingPoints,
    pageStyle: analysis.pageStyle,
    previewProductName: product.name,
  });

  /* ---- Editor blocks: 5 prefilled demo + auto info blocks (PRD §10.14) ----
     (직접 /editor 진입용 데모. 생성 플로우는 generateDetailPage 가
     buildEditorBlocksFromStoryboard 로 대체한다.) ---- */
  const editorBlocks = [
    {
      id: uid('b'), name: '첫 장면', kind: SECTION_ROLES.HOOKING, contentRole: CONTENT_ROLES.HERO, bg: '#ffffff', elements: [
        IMG(60, 50, 880, 560, P.photo('ed_hook', 'horizon', 880, 560), 12, 'horizon'),
        T(120, 110, 600, 80, '겨울을 부드럽게, 골지 니트', { size: 40, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(120, 200, 520, 40, '하루 종일 편안한 데일리 니트', { size: 20, color: '#0e0d14' }),
      ],
    },
    {
      id: uid('b'), name: '핵심 장점', kind: SECTION_ROLES.HOOKING, contentRole: CONTENT_ROLES.BENEFIT, bg: '#f5f5f5', elements: [
        IMG(60, 50, 420, 540, P.detail('ed_sell', 420, 540), 12, 'product'),
        T(540, 150, 380, 40, '부드러운 촉감', { size: 28, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(540, 210, 380, 80, '코튼 혼방으로 자연스럽게 떨어지는 결, 피부에 닿는 감촉이 부담 없습니다.', { size: 17, color: '#6b6b73' }),
      ],
    },
    {
      id: uid('b'), name: '코디 활용', kind: SECTION_ROLES.STYLING, contentRole: CONTENT_ROLES.COORDINATION, bg: '#ffffff', elements: [
        IMG(60, 50, 430, 580, P.photo('ed_st1', 'styling', 430, 580), 12, 'styling'),
        IMG(510, 50, 430, 580, P.photo('ed_st2', 'styling', 430, 580), 12, 'styling'),
      ],
    },
    {
      id: uid('b'), name: '핏 확인', kind: SECTION_ROLES.STUDIO, contentRole: CONTENT_ROLES.FIT, bg: '#ffffff', elements: [
        IMG(280, 50, 440, 590, P.photo('ed_hz', 'horizon', 440, 590), 12, 'horizon'),
      ],
    },
    {
      id: uid('b'), name: '제품 전체', kind: SECTION_ROLES.PRODUCT, contentRole: CONTENT_ROLES.PRODUCT_OVERVIEW, bg: '#f5f5f5', elements: [
        IMG(90, 60, 380, 500, P.product('ed_p1', 380, 500), 12, 'product'),
        IMG(530, 60, 380, 500, P.product('ed_p2', 380, 500), 12, 'product'),
        T(90, 580, 200, 30, 'FRONT', { size: 15, weight: 600, font: 'Roboto Mono', color: '#0e0d14', tracking: 2 }),
        T(530, 580, 200, 30, 'BACK', { size: 15, weight: 600, font: 'Roboto Mono', color: '#0e0d14', tracking: 2 }),
      ],
    },
    ...buildAutoBlocks(product),
  ];

  /* ---- Editor 의류 탭: 그룹 키 = colorId | 'misc' (계약 §3.6) ---- */
  const wardrobe = {
    col1: Array.from({ length: 5 }, (_, i) => ({ id: uid('w'), src: P.photo('w1' + i, i % 2 ? 'styling' : 'horizon', 200, 260), ai: i > 2, cutType: i % 2 ? 'styling' : 'horizon' })),
    col2: Array.from({ length: 3 }, (_, i) => ({ id: uid('w'), src: P.photo('w2' + i, 'horizon', 200, 260), ai: i > 1, cutType: 'horizon' })),
    misc: Array.from({ length: 2 }, (_, i) => ({ id: uid('w'), src: P.product('w3' + i, 200, 260), ai: false, cutType: 'product' })),
  };

  if (mockQuery().get('mockLegacy') === 'after') project.status = 'done';

  // storyboardDirty: 사용자가 콘티를 저장(수정)했는지 — false면 사진 양 변경 시 기본 콘티를 재구성한다
  return {
    project,
    product,
    analysis,
    mannequins: mannequins.map(cut => ({ ...cut, previewOnly: true })),
    mannequinExtensionFeePaid: false,
    storyboard: ['before', 'after'].includes(mockQuery().get('mockLegacy'))
      ? storyboard.map(({ pageSectionKey, pageSectionKind, pageSectionUsage, pageDesignId, slotKey, ...b }) => ({ ...b, taxonomyVersion: 3 }))
      : storyboard,
    storyboardDirty: false,
    editorBlocks,
    wardrobe,
    wardrobeGifs: {},
  };
}

export const DB = {
  mallConnections: {},
  mallDefaults: {},
  mallRegistrationBatches: {},
  mallRegistrations: [],
  config: { signupCredits: 0 },
  account, catalogs, models, matchClothing, genSteps, library,
  ...buildDraft(),
};

// expose models inside catalogs too, so the editor's AI panel can default to
// (and list) the same models picked earlier in the flow (PRD §10.8)
catalogs.models = models;

/** Reinstall a fresh draft (new creation) so prior-session mutations don't leak. */
export function reseedDraft() {
  Object.assign(DB, buildDraft());
}

export default DB;
