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
import { genderForClothingType } from '@/lib/productGender.js';
import {
  createMeasurementFields,
  MEASUREMENT_LABELS,
  MEASUREMENT_SCHEMA,
} from '@/lib/measurementSchema.js';
import { defaultStoryboard } from '@/lib/api/shapes.js';
import { colorDisplayName } from '@/lib/colorwayMatching.js';
import { axesFor, fitProfileCategory } from '@/lib/fitAxes.js';
import { recommendMatchingItems, toLegacyMatchClothing } from '@/mock/matchingRecommendation.js';
import { ensureSections, rowSizeFor } from '@/lib/sections.js';
import {
  CONTENT_ROLES,
  SECTION_ROLES,
  contentTitle,
  inferContentRole,
  inferSectionRole,
} from '@/lib/storyboardTaxonomy.js';

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
const account = { name: 'Jisoo Han', avatar: P.portrait('han'), credits: 196, plan: 'basic' };

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
    ],
    bottom: [
      { value: 'cotton_pants', label: '면바지' }, { value: 'training_pants', label: '트레이닝 팬츠' },
      { value: 'jeans', label: '청바지' }, { value: 'slacks', label: '슬랙스' }, { value: 'skirt', label: '치마' },
    ],
    outer: [
      { value: 'shirt', label: '셔츠' }, { value: 'jacket', label: '자켓' },
      { value: 'cardigan', label: '가디건' }, { value: 'padding', label: '패딩' }, { value: 'coat', label: '코트' },
    ],
    dress: [],
  },
  genders: [{ value: 'women', label: '여자' }, { value: 'men', label: '남자' }],
  fits: [
    { value: 'slim', label: '슬림핏' }, { value: 'regular', label: '정핏' },
    { value: 'semi_over', label: '세미오버' }, { value: 'over', label: '오버핏' },
  ],
  directions: [
    { value: 'front', label: '정면' }, { value: 'back', label: '뒷면' }, { value: 'side', label: '사이드' },
  ],
  shotTypes: [
    { value: 'full', label: '풀샷' }, { value: 'medium', label: '미디움샷' },
  ],
  // 제품 이미지 전용 옵션 — 화면 하드코딩 금지 (계약 §5)
  productDirections: [{ value: 'front', label: '앞면' }, { value: 'back', label: '뒷면' }],
  productShotTypes: [
    { value: 'ghost', label: '고스트샷' }, { value: 'detail', label: '디테일샷' },
  ],
  outerClosureStates: [
    { value: 'open', label: '전체 열림' }, { value: 'partial', label: '부분 열림' }, { value: 'closed', label: '전체 닫힘' },
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
    { id: 'red', label: '레드', hex: '#c0392b' },
    { id: 'yellow', label: '옐로우', hex: '#e7c75c' },
    { id: 'green', label: '그린', hex: '#3f7a4f' },
    { id: 'blue', label: '블루', hex: '#2a5db0' },
    { id: 'navy', label: '네이비', hex: '#1f2a44' },
    { id: 'pink', label: '핑크', hex: '#e3a7b8' },
  ],
  // 사진 양 — 두 방식은 섹션 순서가 같고 사진 수만 다르다.
  composeModes: [
    { value: 'basic', label: '기본형', desc: '대표 컬러 중심으로 필요한 사진만', count: '13', flow: ['후킹', '스타일링', '스튜디오', '의류 확인'] },
    { value: 'extended', label: '확장형', desc: '같은 순서로 사진을 더 풍부하게', count: '14~30', flow: ['후킹', '스타일링', '스튜디오', '의류 확인'] },
  ],
  poses: [
    { id: 'auto', label: 'AI 자동', auto: true }, { id: 'stand', label: '서기', thumb: P.pose('stand') },
    { id: 'walk', label: '걷기', thumb: P.pose('walk') }, { id: 'sit', label: '앉기', thumb: P.pose('sit') },
    { id: 'lean', label: '기대기', thumb: P.pose('lean') }, { id: 'turn', label: '돌아보기', thumb: P.pose('turn') },
  ],
  // 에디터 '현재 이미지 수정' — 배경/포즈/표정은 예시 카드(탭당 1개).
  // '컷 변경' 탭의 모델 착용 이미지는 directions/shotTypes 를 그대로 재사용한다.
  varyOptions: {
    bg: [
      { id: 'cafe', label: '햇살 카페', thumb: P.scene('v-cafe', 240, 240) },
      { id: 'street', label: '도심 거리', thumb: P.scene('v-street', 240, 240) },
      { id: 'park', label: '공원 산책로', thumb: P.scene('v-park', 240, 240) },
      { id: 'horizon', label: '화이트 호리존', thumb: P.scene('v-horizon', 240, 240) },
      { id: 'home', label: '집 거실', thumb: P.scene('v-home', 240, 240) },
      { id: 'night', label: '야경 거리', thumb: P.scene('v-night', 240, 240) },
    ],
    // '뒷모습'은 포즈가 아니라 방향이다 — '자리 · 방향'에 같은 게 있어 두 벌이었다(오너 8/16).
    pose: [
      { id: 'stand', label: '정면 스탠딩', thumb: P.pose('v-stand', 240, 240) },
      { id: 'walk', label: '걷는 모습', thumb: P.pose('v-walk', 240, 240) },
      { id: 'lean', label: '벽에 기대기', thumb: P.pose('v-lean', 240, 240) },
      { id: 'sit', label: '앉은 포즈', thumb: P.pose('v-sit', 240, 240) },
      { id: 'turn', label: '돌아보기', thumb: P.pose('v-turn', 240, 240) },
    ],
    face: [
      { id: 'smile', label: '은은한 미소', thumb: P.portrait('v-smile', 240, 240) },
      { id: 'laugh', label: '활짝 웃음', thumb: P.portrait('v-laugh', 240, 240) },
      { id: 'chic', label: '시크한 무표정', thumb: P.portrait('v-chic', 240, 240) },
      { id: 'gaze', label: '먼 곳 응시', thumb: P.portrait('v-gaze', 240, 240) },
    ],
  },
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
  fonts: ['Pretendard', 'Cal Sans', 'Roboto Mono', 'Cormorant'],
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
  { id: 'mE', name: '지안', gender: 'women', thumb: '/models/women/w2.webp', recommended: false },
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
function buildAutoBlocks(product) {
  return [
    {
      id: uid('b'), name: '사이즈 안내', kind: 'size', auto: true, bg: '#ffffff', elements: [
        T(60, 56, 500, 44, '사이즈 안내', { size: 28, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(60, 104, 760, 24, '단위: cm · 측정 위치에 따라 1~3cm 오차가 있을 수 있어요', { size: 14, color: '#6b6b73' }),
        ...(product.measurements || []).slice(0, 4).flatMap((m, i) => {
          const x = 60 + i * 232;
          return [
            T(x, 168, 200, 24, catalogs.measurementLabels[m.key] || m.key, { size: 14, color: '#6b6b73' }),
            T(x, 194, 200, 48, (m.value != null ? m.value + ' cm' : '—'), { size: 32, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
          ];
        }),
      ],
    },
    {
      id: uid('b'), name: '세탁 안내', kind: 'care', auto: true, bg: '#f5f5f5', elements: [
        T(60, 56, 500, 40, '세탁 안내', { size: 24, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(60, 104, 880, 64, '세탁 전 실제 상품의 케어라벨을 반드시 확인해주세요. 소재와 상품 특성에 따라 관리 방법이 달라질 수 있습니다.', { size: 16, color: '#0e0d14' }),
      ],
    },
    {
      id: uid('b'), name: 'AI 생성 안내', kind: 'ai-notice', auto: true, bg: '#ffffff', elements: [
        T(60, 48, 880, 60, '본 상세페이지의 일부 이미지는 AI를 활용해 생성되었습니다. 실제 상품의 색상과 핏은 촬영 환경 및 화면 설정에 따라 다르게 보일 수 있습니다.', { size: 13, color: '#6b6b73', align: 'center' }),
      ],
    },
  ];
}

/* 저장된 콘티 → 에디터 블록 (mock 생성기, 계약 §6 generateDetailPage).
   실제 파이프라인이 할 일을 placeholder 로 흉내만 낸다 — 블록 수·종류·순서가
   콘티를 따라가고, 카피라이팅 ON 이면 첫 장면/핵심 장점에 카피를 넣는다. */
export function buildEditorBlocksFromStoryboard(storyboard, product, copywriting, analysis = {}) {
  const ROW_LAYOUTS = {
    twoColumn: { name: '2단 구성', kind: 'twocol' },
    threeColumn: { name: '3단 구성', kind: 'threecol' },
    grid2x2: { name: '2×2단 구성', kind: 'grid2x2' },
    colorCompare: { name: '컬러 비교', kind: 'colorcmp' },
  };
  const cat = (ct) => ct === 'product' ? 'product' : ct === 'horizon' ? 'horizon' : 'styling';
  const generatedImageFor = (b, w, h) => {
    const preview = demoColorwayPreviewFor(product.name, product.clothingType);
    const previewAsset = preview?.template?.[b.shot];
    if (b.colorwayGroupId && previewAsset) return previewAsset;
    const usesWholeExample = b.exampleId && (b.refScope || 'all') === 'all' && !b.spaceGroupId;
    return P.photo(usesWholeExample ? b.exampleId : 'gen_' + b.id, cat(b.cutType), w, h);
  };
  const arr = storyboard || [];
  const blocks = [];
  const persistentRowSections = new Set(arr
    .filter((b) => b.source !== 'mine' && (b.layoutRowId || b.layoutRowVersion))
    .map((b) => b.sectionId));
  const pushSingle = (b) => {
    const bg = blocks.length % 2 ? '#f5f5f5' : '#ffffff';
    if (b.source === 'mine') {
      const els = (b.ownImages || []).slice(0, 1).map((src) => IMG(60, 50, 880, 560, src, 12));
      blocks.push({ id: uid('b'), name: '내 이미지', kind: inferSectionRole(b) || SECTION_ROLES.STYLING, contentRole: CONTENT_ROLES.CUSTOM, bg, h: 660, elements: els });
      return;
    }
    const contentRole = inferContentRole(b);
    const sectionRole = inferSectionRole(b) || SECTION_ROLES.STYLING;
    const name = contentTitle(contentRole);
    // sourceBlockId/copyRole = 서버 조립기와 같은 추적 필드(editor_wait_dev_spec §2-3) —
    // 에디터 대기 화면의 컷 채움·셀러 카피 오버라이드 매칭 키. mock-서버 패리티 유지.
    const els = [Object.assign(IMG(60, 50, 880, 560, generatedImageFor(b, 880, 560), 12, b.cutType || undefined), { sourceBlockId: b.id })];
    // 시그니처 컷 계약(스펙 2026-08-14 §1): 제품명을 이미지 중앙에 흰색으로 — 카피 토글과 무관.
    if (b.hookTitleOverlay && (product.name || '').trim()) {
      els.push(Object.assign(
        T(60, 275, 880, 110, product.name.trim(),
          { font: 'Pretendard', size: 34, weight: 700, color: '#ffffff', align: 'center' }),
        { sourceBlockId: b.id, copyRole: 'hookTitle' },
      ));
    }
    if (copywriting && contentRole === CONTENT_ROLES.HERO) {
      els.push(Object.assign(T(120, 110, 600, 80, `${product.name || '상품'}와 함께하는 하루`, { size: 40, weight: 600, font: 'Cal Sans', color: '#0e0d14' }), { sourceBlockId: b.id, copyRole: 'headline' }));
    }
    if (copywriting && contentRole === CONTENT_ROLES.BENEFIT) {
      els.push(Object.assign(T(120, 560, 760, 40, '강조 포인트를 살린 카피가 들어가는 자리예요.', { size: 17, color: '#6b6b73', lineHeight: 26 }), { sourceBlockId: b.id, copyRole: 'body' }));
    }
    blocks.push({ id: uid('b'), name, kind: sectionRole, contentRole, bg, h: 660, elements: els });
  };
  const pushRow = (chunk, layout) => {
    const rowLayout = ROW_LAYOUTS[layout];
    const n = chunk.length;
    /* 2×2 격자는 사진 넷이 딱 붙어 한 덩어리로 보여야 한다(오너 8/16) — 칸 사이 간격만
       두지 않는다. 카피 자리는 아래 imgTop 주석 참고(다른 행과 같이 사진 아래). */
    const grid = layout === 'grid2x2' && n === 4;
    const w = grid ? 440 : Math.floor((880 - (n - 1) * 20) / n);
    const h = grid ? 560 : 500;
    const hero = chunk.find((rb) => inferContentRole(rb) === CONTENT_ROLES.HERO);
    const subtitle = chunk.find((rb) => inferContentRole(rb) === CONTENT_ROLES.BENEFIT);
    const hasCopy = Boolean(copywriting && hero);
    // 사진 시작 y 는 어느 배치든 같다. 격자만 카피를 위로 올려 자리를 비워 뒀더니,
    // 에디터가 사진 행의 카피를 통째로 걷어내는 규칙(stripPhotoBlockTextElements —
    // grid2x2 도 PHOTO_ROW_KINDS 다) 때문에 격자 위에 190px 빈 띠만 남았다.
    // 카피는 다른 행과 똑같이 사진 아래에 둔다(2026-08-16 리뷰에서 실측 확인).
    const imgTop = 50;
    const els = chunk.map((rb, c) => Object.assign(
      grid
        ? IMG(60 + (c % 2) * w, imgTop + Math.floor(c / 2) * h, w, h, generatedImageFor(rb, w, h), 0, rb.cutType || undefined)
        : IMG(60 + c * (w + 20), imgTop, w, h, generatedImageFor(rb, w, h), 12, rb.cutType || undefined),
      { sourceBlockId: rb.id },
    ));
    if (hasCopy) {
      const copyTop = imgTop + (grid ? h * 2 : h) + 32;
      els.push(Object.assign(T(60, copyTop, 880, 56, `${product.name || '상품'}와 함께하는 하루`, {
        size: 40, weight: 600, font: 'Cal Sans', color: '#0e0d14',
      }), { sourceBlockId: hero.id, copyRole: 'headline' }));
      if (subtitle) {
        els.push(Object.assign(T(60, copyTop + 68, 880, 34, '강조 포인트를 살린 카피가 들어가는 자리예요.', {
          size: 17, color: '#6b6b73', lineHeight: 26,
        }), { sourceBlockId: subtitle.id, copyRole: 'body' }));
      }
    }
    blocks.push({
      id: uid('b'), name: rowLayout.name, kind: rowLayout.kind,
      bg: blocks.length % 2 ? '#f5f5f5' : '#ffffff', elements: els,
    });
  };

  const isColorwayPair = (first, second) => {
    const groupId = first?.colorwayGroupId;
    const rowId = first?.layoutRowId;
    return !!(
      groupId
      && first?.colorwayPairVersion === 1
      && second?.colorwayPairVersion === 1
      && second?.colorwayGroupId === groupId
      && rowId
      && second?.layoutRowId === rowId
      && first?.sectionLayout === 'twoColumn'
      && second?.sectionLayout === 'twoColumn'
      && first?.source !== 'mine'
      && second?.source !== 'mine'
      && first?.sectionRole === SECTION_ROLES.STUDIO
      && second?.sectionRole === SECTION_ROLES.STUDIO
      && first?.cutType === 'horizon'
      && second?.cutType === 'horizon'
      && first?.direction === 'front'
      && second?.direction === 'front'
      && first?.colorId === second?.colorId
      && new Set([first?.shot, second?.shot]).size === 2
      && [first?.shot, second?.shot].every((shot) => shot === 'full' || shot === 'medium')
      && JSON.stringify(first?.matchIds || []) === JSON.stringify(second?.matchIds || [])
      && !first?.spaceGroupId
      && !second?.spaceGroupId
    );
  };
  const colorwayLabels = (pair) => {
    const color = (product.colors || []).find((item) => String(item.id) === String(pair[0].colorId));
    const productLabel = `${String(product.name || '상품').trim().slice(0, 120)} [${colorDisplayName(color).slice(0, 120)}]`;
    const matchId = pair[0].matchIds?.[0];
    const matching = (analysis.matchClothing || analysis.matchCandidates || [])
      .find((item) => String(item.id) === String(matchId));
    if (!matching) return [productLabel, null];
    const name = String(matching.name || '매칭 의류').trim().slice(0, 120);
    const colorName = String(matching.colorName || '').trim().slice(0, 120);
    return [productLabel, colorName ? `${name} [${colorName}]` : name];
  };
  const pushColorwayPair = (pair) => {
    const ordered = pair.slice().sort((left, right) => (left.shot === 'full' ? -1 : 1) - (right.shot === 'full' ? -1 : 1));
    const width = 430;
    const height = 645;
    const els = ordered.map((rowBlock, column) => Object.assign(
      IMG(60 + column * 450, 24, width, height, generatedImageFor(rowBlock, width, height), 0, rowBlock.cutType || undefined),
      { sourceBlockId: rowBlock.id },
    ));
    const [productLabel, matchingLabel] = colorwayLabels(ordered);
    els.push(T(60, 683, 880, 24, productLabel, {
      size: 14, weight: 400, color: '#6b6b73', align: 'center', tracking: 0.2,
    }));
    if (matchingLabel) {
      els.push(T(60, 705, 880, 26, matchingLabel, {
        size: 15, weight: 700, color: '#0e0d14', align: 'center', tracking: 0.1,
      }));
    }
    const colorName = colorDisplayName(
      (product.colors || []).find((item) => String(item.id) === String(ordered[0].colorId)),
    );
    blocks.push({
      id: uid('b'), name: `컬러 룩 · ${colorName}`, kind: 'twocol', layoutType: 'colorwayPair',
      bg: '#f5f5f5', h: matchingLabel ? 781 : 757, elements: els,
    });
  };

  for (let i = 0; i < arr.length; i++) {
    const b = arr[i];
    if (i + 1 < arr.length && isColorwayPair(b, arr[i + 1])) {
      pushColorwayPair([b, arr[i + 1]]);
      i += 1;
      continue;
    }
    if (b.source === 'mine') { pushSingle(b); continue; }

    // 가로 배치 섹션 — '내 이미지'가
    // 끊는 같은 섹션의 연속 AI run을 하나의 배치 단위로 조립한다.
    if (ROW_LAYOUTS[b.sectionLayout] && b.sectionId) {
      const layout = b.sectionLayout;
      const run = [];
      let j = i;
      while (j < arr.length && arr[j].sectionId === b.sectionId && arr[j].source !== 'mine') { run.push(arr[j]); j++; }
      i = j - 1;

      // 행 id나 모델 버전이 섹션에 있으면 신규 모델이다. 그래야 내 이미지 건너편 run과
      // 완성 행이 없는 미완성 꼬리를 레거시 청킹으로 잘못 합치지 않는다.
      if (persistentRowSections.has(b.sectionId)) {
        // 신규 보드: 영속 row id가 배치의 단일 소스. 버전만 있고 id 없는 미완성 꼬리는 싱글로 남긴다.
        for (let k = 0; k < run.length;) {
          const rowId = run[k].layoutRowId;
          if (!rowId) { pushSingle(run[k]); k += 1; continue; }
          let end = k + 1;
          while (end < run.length && run[end].layoutRowId === rowId) end += 1;
          const members = run.slice(k, end);
          if (members.length > 1) pushRow(members, layout);
          else pushSingle(members[0]); // 손상된/구버전 단독 id는 안전하게 싱글로 폴백.
          k = end;
        }
      } else {
        // 레거시 보드: row id/모델 버전이 전혀 없으면 기존 rowSizeFor 순차 청킹(미완성 마지막 행 포함)을 유지한다.
        const size = rowSizeFor(layout);
        for (let k = 0; k < run.length; k += size) {
          const chunk = run.slice(k, k + size);
          if (chunk.length > 1) pushRow(chunk, layout);
          else pushSingle(chunk[0]);
        }
      }
      continue;
    }
    pushSingle(b);
  }
  return [...blocks, ...buildAutoBlocks(product)];
}

/* =============================================================
   buildStoryboard(mode, colors, context) — HTTP 기본 콘티와 동일한 빌더를
   공유해 mock에서도 선택 세트·순서·지문이 완전히 같게 유지한다.
   ============================================================= */
export function buildStoryboard(mode, colors, context = {}) {
  const blocks = defaultStoryboard(colors, mode, context);
  const preview = demoColorwayPreviewFor(context.previewProductName, context.clothingType);
  if (!preview) return blocks;
  return blocks.map((block) => (
    block.colorwayGroupId
      && preview.template?.[block.shot]
      ? {
        ...block,
        thumb: preview.template[block.shot],
        previewThumb: preview.template[block.shot],
      }
      : block
  ));
}

/* =============================================================
   buildDraft() — fresh per-creation working data. Called at init
   and by api.createProject() so a new creation never inherits the
   prior session's mutations (mannequin variants, saved storyboard…).
   ============================================================= */
function buildDraft() {
  /* ---- Project — 플로우 최상위 엔티티 (ADR-0001) ---- */
  const project = {
    id: uid('prj'), status: 'draft', title: '',
    composeMode: 'basic', copywriting: true,
    selectedMannequinId: null, adjustCount: 0, fitProfile: null,
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
    aiSuggestedPoints: ['골지 짜임', '라운드넥'],
    styleTags: ['basic', 'daily', 'clean'],
    selectedModelId: 'mA', models, matchClothing: matchClothing.map((m) => ({ ...m })),
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

  /* ---- Storyboard blocks — 모드별 기본 콘티는 buildStoryboard() (PRD §8, ADR-0003·0004).
     첫 화면 스타일은 시드가 시그니처 컷 프레임을 이미 포함한다(2026-08-14). ---- */
  const storyboard = buildStoryboard(project.composeMode, product.colors, {
    projectId: project.id,
    clothingType: product.clothingType,
    targetGenders: analysis.targetGenders,
    matchClothing: analysis.matchClothing,
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

  // storyboardDirty: 사용자가 콘티를 저장(수정)했는지 — false면 사진 양 변경 시 기본 콘티를 재구성한다
  return { project, product, analysis, mannequins, storyboard, storyboardDirty: false, editorBlocks, wardrobe };
}

export const DB = {
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
