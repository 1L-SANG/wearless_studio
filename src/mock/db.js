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
import { CREDIT_COSTS } from '@/lib/limits.js';
import { uid } from '@/lib/ids.js';
import { axesFor, fitProfileCategory } from '@/lib/fitAxes.js';
import { recommendMatchingItems, toLegacyMatchClothing } from '@/mock/matchingRecommendation.js';

const nowIso = () => new Date().toISOString();
const copyFitProfile = (profile) => ({ ...profile, axes: { ...(profile?.axes || {}) } });
const isMenOnly = (genders) => Array.isArray(genders) && genders.length > 0 && genders.every((g) => g === 'men');
const defaultFitProfile = (product, analysis) => {
  const category = fitProfileCategory(product?.clothingType, analysis?.subCategory) || 'top';
  const gender = isMenOnly(analysis?.targetGenders || product?.targetGenders) ? 'men' : 'women';
  const axes = Object.fromEntries(Object.keys(axesFor(category, gender)).map((axis) => [axis, null]));
  return { category, gender, axes, source: 'auto', version: 1 };
};

/* ---- Account (stable) ---- */
const account = { name: 'Jisoo Han', avatar: P.portrait('han'), credits: 196, plan: 'basic' };

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
    { value: 'full', label: '풀샷' }, { value: 'knee', label: '무릎샷' },
    { value: 'medium', label: '미디움샷' }, { value: 'close', label: '확대샷' },
  ],
  // 제품컷 전용 옵션 — 화면 하드코딩 금지 (계약 §5)
  productDirections: [{ value: 'front', label: '앞면' }, { value: 'back', label: '뒷면' }],
  productShotTypes: [
    { value: 'ghost', label: '고스트컷' }, { value: 'hanger', label: '행거컷' }, { value: 'flatlay', label: '플랫레이샷' },
  ],
  angleSlots: ['Front', 'Back', 'Detail', 'Fit'],
  angleLabels: { Front: '앞면 이미지', Back: '뒷면 이미지', Detail: '디테일 이미지', Fit: '착용 이미지' },
  // measurement schema per clothing type (PRD §6.5) — key는 영문 토큰 (계약 §4)
  measurementSchema: {
    top: ['totalLength', 'shoulderWidth', 'chestWidth', 'sleeveLength'],
    bottom: ['totalLength', 'waistWidth', 'hipWidth', 'thighWidth', 'rise', 'hemWidth'],
    outer: ['totalLength', 'shoulderWidth', 'chestWidth', 'sleeveLength'],
    dress: ['totalLength', 'shoulderWidth', 'chestWidth', 'waistWidth', 'armhole', 'sleeveLength'],
  },
  measurementLabels: {
    totalLength: '총장', shoulderWidth: '어깨너비', chestWidth: '가슴단면', sleeveLength: '소매길이',
    waistWidth: '허리단면', hipWidth: '엉덩이단면', thighWidth: '허벅지단면',
    rise: '밑위', hemWidth: '밑단단면', armhole: '암홀',
  },
  sellingPointSuggestions: ['부드러운 촉감', '여리한 핏', '단독/이너 활용 가능', '비침 없는 도톰함', '데일리하게 활용'],
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
  // 구성 방식 — count·flow는 buildStoryboard()의 실제 산출과 일치시킨다 (PRD §7.7, ADR-0004)
  composeModes: [
    { value: 'simple', label: '간단형', desc: '단일 컬러 중심으로 빠르게', count: '8', flow: ['후킹', '셀링포인트', '스타일링컷', '호리존컷', '거울샷', '제품컷'] },
    { value: 'basic', label: '기본형', desc: '대표 컬러 중심의 균형형', count: '12', flow: ['후킹', '셀링포인트', '스타일링컷', '호리존컷', '거울샷', '제품컷'], recommended: true },
    { value: 'extended', label: '확장형', desc: '여러 컬러를 자세히 소개', count: '14~24', flow: ['후킹', '셀링포인트', '컬러별 스타일링·호리존컷', '거울샷', '제품컷'] },
  ],
  poses: [
    { id: 'auto', label: 'AI 자동', auto: true }, { id: 'stand', label: '서기', thumb: P.pose('stand') },
    { id: 'walk', label: '걷기', thumb: P.pose('walk') }, { id: 'sit', label: '앉기', thumb: P.pose('sit') },
    { id: 'lean', label: '기대기', thumb: P.pose('lean') }, { id: 'turn', label: '돌아보기', thumb: P.pose('turn') },
  ],
  // 에디터 '현재 컷 변형' — 배경/포즈/표정은 예시 카드(탭당 1개).
  // '컷 변경' 탭은 스타일링컷 기준 directions/shotTypes 를 그대로 재사용한다.
  varyOptions: {
    bg: [
      { id: 'cafe', label: '햇살 카페', thumb: P.scene('v-cafe', 240, 240) },
      { id: 'street', label: '도심 거리', thumb: P.scene('v-street', 240, 240) },
      { id: 'park', label: '공원 산책로', thumb: P.scene('v-park', 240, 240) },
      { id: 'horizon', label: '화이트 호리존', thumb: P.scene('v-horizon', 240, 240) },
      { id: 'home', label: '집 거실', thumb: P.scene('v-home', 240, 240) },
      { id: 'night', label: '야경 거리', thumb: P.scene('v-night', 240, 240) },
    ],
    pose: [
      { id: 'stand', label: '정면 스탠딩', thumb: P.pose('v-stand', 240, 240) },
      { id: 'walk', label: '걷는 모습', thumb: P.pose('v-walk', 240, 240) },
      { id: 'back', label: '뒷모습', thumb: P.pose('v-back', 240, 240) },
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
  genExamples: Array.from({ length: 8 }, (_, i) => ({ id: 'ex' + i, thumb: P.photo('ex' + i, i % 2 ? 'styling' : 'horizon', 240, 320) })),
  // 컷 종류 — 공식 용어: 스타일링컷·호리존컷·제품컷 (ADR-0003) + 거울샷 (ADR-0004).
  // '내 이미지'는 컷 종류가 아니라 source('mine')로 다룬다 — UI 탭은 화면에서 합성.
  cutTypes: [
    { value: 'styling', label: '스타일링컷' }, { value: 'horizon', label: '호리존컷' }, { value: 'product', label: '제품컷' },
    { value: 'mirror', label: '거울샷' },
  ],
  frames: [
    { id: 'split2', label: '2분할', cols: 2 }, { id: 'grid3', label: '3컷 구성', cols: 3 },
    { id: 'faq', label: 'FAQ', cols: 1 }, { id: 'ba', label: 'Before / After', cols: 2 },
    { id: 'colorcmp', label: '컬러 비교', cols: 3 }, { id: 'infocard', label: '상품 정보 카드', cols: 1 },
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
  { id: 'mA', name: '모델 A', gender: 'women', thumb: '/models/women/w1.webp', recommended: true },
  { id: 'mB', name: '모델 B', gender: 'men', thumb: '/models/men/m1.webp', recommended: false },
  { id: 'mC', name: '모델 C', gender: 'men', thumb: '/models/men/m2.webp', recommended: false },
];
const matchClothing = toLegacyMatchClothing(recommendMatchingItems({
  clothingType: 'top',
  targetGenders: ['women'],
  styleTags: ['basic', 'daily', 'clean'],
}));

/* ---- Generation job steps (stable, PRD §9.2) ---- */
const genSteps = [
  { key: 'info', label: '상품 정보 정리' }, { key: 'prep', label: '이미지 생성 준비' },
  { key: 'styling', label: '스타일링컷 생성' }, { key: 'horizon', label: '호리존컷 생성' },
  { key: 'product', label: '제품컷 생성' }, { key: 'copy', label: '카피라이팅 적용' },
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
// cutType: 생성 산출물에 기록되는 컷 종류 메타데이터 — '현재 컷 변형'의 옵션 기준 (디테일 줌은 product 로 분류)
const IMG = (x, y, w, h, src, radius, cutType) => ({ id: uid('el'), type: 'image', x, y, w, h, src, radius: radius || 8, ...(cutType ? { cutType } : {}) });

/* 자동 안내 블록 (PRD §10.14) — 사이즈 안내는 product.measurements 를 "생성 시점"에 읽는다 */
function buildAutoBlocks(product) {
  return [
    {
      id: uid('b'), name: '사이즈 안내', kind: 'size', auto: true, bg: '#ffffff', elements: [
        T(60, 56, 500, 44, '사이즈 안내', { size: 28, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(60, 104, 760, 24, '단위: cm · 측정 위치에 따라 1~3cm 오차가 있을 수 있어요', { size: 14, color: '#4a4a45' }),
        ...(product.measurements || []).slice(0, 4).flatMap((m, i) => {
          const x = 60 + i * 232;
          return [
            T(x, 168, 200, 24, catalogs.measurementLabels[m.key] || m.key, { size: 14, color: '#4a4a45' }),
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
        T(60, 48, 880, 60, '본 상세페이지의 일부 이미지는 AI를 활용해 생성되었습니다. 실제 상품의 색상과 핏은 촬영 환경 및 화면 설정에 따라 다르게 보일 수 있습니다.', { size: 13, color: '#4a4a45', align: 'center' }),
      ],
    },
  ];
}

/* 저장된 콘티 → 에디터 블록 (mock 생성기, 계약 §6 generateDetailPage).
   실제 파이프라인이 할 일을 placeholder 로 흉내만 낸다 — 블록 수·종류·순서가
   콘티를 따라가고, 카피라이팅 ON 이면 후킹/셀링포인트에 카피를 넣는다. */
export function buildEditorBlocksFromStoryboard(storyboard, product, copywriting) {
  const KIND_NAMES = { hook: '후킹', selling: '셀링포인트', styling: '스타일링컷', horizon: '호리존컷', product: '제품컷', info: '블록' };
  const cat = (ct) => ct === 'product' ? 'product' : ct === 'horizon' ? 'horizon' : 'styling';
  const blocks = (storyboard || []).map((b, i) => {
    const bg = i % 2 ? '#f5f5f5' : '#ffffff';
    if (b.source === 'mine') {
      const els = (b.ownImages || []).slice(0, 1).map((src) => IMG(60, 50, 880, 560, src, 12));
      return { id: uid('b'), name: '내 이미지', kind: 'info', bg, h: 660, elements: els };
    }
    const name = b.title || KIND_NAMES[b.kind] || '컷';
    const els = [IMG(60, 50, 880, 560, P.photo('gen_' + b.id, cat(b.cutType), 880, 560), 12, b.cutType || undefined)];
    if (copywriting && b.kind === 'hook') {
      els.push(T(120, 110, 600, 80, `${product.name || '상품'}와 함께하는 하루`, { size: 40, weight: 600, font: 'Cal Sans', color: '#0e0d14' }));
    }
    if (copywriting && b.kind === 'selling') {
      els.push(T(120, 560, 760, 40, '강조 포인트를 살린 카피가 들어가는 자리예요.', { size: 18, color: '#4a4a45' }));
    }
    return { id: uid('b'), name, kind: b.kind, bg, h: 660, elements: els };
  });
  return [...blocks, ...buildAutoBlocks(product)];
}

/* =============================================================
   buildStoryboard(mode, colors) — 모드별 기본 콘티 (PRD §7.7·§8, ADR-0004).
   트렌드 규칙 내장: 첫 컷 = 베스트 전신(썸네일), 필수 3종(정면·측면/후면·
   상반신 클로즈업), 거울샷 1컷 포함, 평면(플랫레이)은 마감 1~2장,
   연속 스타일링컷은 같은 공간(spaceGroupId, 기본 subtle).
   ============================================================= */
const sb = (kind, title, cutType, direction, shot, colorId, extra) => ({
  id: uid('blk'), kind, title, source: 'ai', cutType, direction, shot, colorId,
  pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [],
  thumb: P.photo(kind + title + (colorId || '') + (shot || ''), cutType === 'product' ? 'product' : cutType === 'horizon' ? 'horizon' : 'styling', 240, 320),
  poseThumb: P.pose('stand'), poseLabel: 'AI 자동',
  ...(extra || {}),
});
// 거울샷 블록 — kind는 styling(섹션 역할), 방향 없음·얼굴 '폰으로 가림' (ADR-0004)
const mirrorSb = (colorId) => sb('styling', '거울샷', 'mirror', null, 'full', colorId, { faceExposure: 'hide' });

export function buildStoryboard(mode, colors) {
  const list = Array.isArray(colors) && colors.length ? colors : [{ id: 'col1', isBase: true }];
  // 확장형은 색상 2개 이상 전제 (PRD §7.7 — UI가 게이트). 방어: 1색이면 기본형 레이아웃으로.
  if (mode === 'extended' && list.length < 2) mode = 'basic';
  const base = (list.find((c) => c.isBase) || list[0]).id;
  // 같은 공간에서 포즈·프레이밍만 달리하는 스타일링 2컷 묶음
  const spacePair = (colorId) => {
    const sg = uid('sg');
    return [
      sb('styling', '스타일링컷', 'styling', 'front', 'full', colorId, { spaceGroupId: sg, spaceVariation: 'subtle' }),
      sb('styling', '스타일링컷', 'styling', 'side', 'knee', colorId, { spaceGroupId: sg, spaceVariation: 'subtle' }),
    ];
  };
  const out = [
    sb('hook', '후킹', 'horizon', 'front', 'full', base),
    sb('selling', '셀링포인트', 'product', 'front', 'ghost', base),
  ];
  if (mode === 'simple') {
    out.push(
      ...spacePair(base),
      sb('horizon', '호리존컷', 'horizon', 'side', 'full', base),
      sb('horizon', '호리존컷', 'horizon', 'front', 'close', base),
      mirrorSb(base),
      sb('product', '제품컷', 'product', 'front', 'flatlay', base),
    );
  } else if (mode === 'extended') {
    list.slice(0, 4).forEach((c) => {
      out.push(
        ...spacePair(c.id),
        sb('horizon', '호리존컷', 'horizon', 'front', 'knee', c.id),
        sb('horizon', '호리존컷', 'horizon', 'back', 'full', c.id),
        sb('horizon', '호리존컷', 'horizon', 'front', 'close', c.id),
      );
    });
    out.push(mirrorSb(base), sb('product', '제품컷', 'product', 'front', 'flatlay', base));
  } else { // basic
    out.push(
      ...spacePair(base),
      sb('styling', '스타일링컷', 'styling', 'front', 'medium', base),
      sb('horizon', '호리존컷', 'horizon', 'front', 'knee', base),
      sb('horizon', '호리존컷', 'horizon', 'side', 'full', base),
      sb('horizon', '호리존컷', 'horizon', 'back', 'full', base),
      sb('horizon', '호리존컷', 'horizon', 'front', 'close', base),
      mirrorSb(base),
      sb('product', '제품컷', 'product', 'back', 'ghost', base),
      sb('product', '제품컷', 'product', 'front', 'flatlay', base),
    );
  }
  return out;
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
  const measurements = () => [
    { key: 'totalLength', value: 64, unit: 'cm' },
    { key: 'shoulderWidth', value: 42, unit: 'cm' },
    { key: 'chestWidth', value: 51, unit: 'cm' },
    { key: 'sleeveLength', value: null, unit: 'cm' },
  ];

  /* ---- Seed product input (the 골지 니트 example) ---- */
  const product = {
    id: uid('prd'), name: '소프트 골지 라운드 니트', clothingType: 'top',
    uploadComplete: false, measurementsUnknown: false,
    colors: [
      {
        id: 'col1', name: '블랙', isBase: true, isMain: true, monotone: true,
        images: [
          { id: uid('img'), slot: 'Front', label: 'Front', src: P.photo('c1f', 'horizon', 300, 400) },
          { id: uid('img'), slot: 'Back', label: 'Back', src: P.photo('c1b', 'horizon', 300, 400) },
          { id: uid('img'), slot: 'Detail', label: 'Detail', src: P.detail('c1d', 300, 400) },
          { id: uid('img'), slot: 'Fit', label: 'Fit', src: P.photo('c1fit', 'styling', 300, 400) },
        ],
      },
      {
        id: 'col2', name: '아이보리', isBase: false, monotone: true,
        images: [{ id: uid('img'), slot: 'Front', label: '정면', src: P.photo('c2f', 'horizon', 300, 400) }],
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
    fit: 'semi_over', suggestedName: '소프트 골지 라운드 니트',
    materials: [{ name: '코튼', ratio: 60 }, { name: '폴리에스터', ratio: 40 }],
    sellingPoints: ['부드러운 촉감', '여리한 핏', '단독/이너 활용 가능'],
    aiSuggestedPoints: ['넉넉한 라운드 넥', '비침 없는 도톰함'],
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

  /* ---- Storyboard blocks — 모드별 기본 콘티는 buildStoryboard() (PRD §8, ADR-0003·0004) ---- */
  const storyboard = buildStoryboard(project.composeMode, product.colors);

  /* ---- Editor blocks: 5 prefilled demo + auto info blocks (PRD §10.14) ----
     (직접 /editor 진입용 데모. 생성 플로우는 generateDetailPage 가
     buildEditorBlocksFromStoryboard 로 대체한다.) ---- */
  const editorBlocks = [
    {
      id: uid('b'), name: '후킹', kind: 'hook', bg: '#ffffff', elements: [
        IMG(60, 50, 880, 560, P.photo('ed_hook', 'horizon', 880, 560), 12, 'horizon'),
        T(120, 110, 600, 80, '겨울을 부드럽게, 골지 니트', { size: 40, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(120, 200, 520, 40, '하루 종일 편안한 데일리 니트', { size: 20, color: '#0e0d14' }),
      ],
    },
    {
      id: uid('b'), name: '셀링포인트', kind: 'selling', bg: '#f5f5f5', elements: [
        IMG(60, 50, 420, 540, P.detail('ed_sell', 420, 540), 12, 'product'),
        T(540, 150, 380, 40, '부드러운 촉감', { size: 28, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(540, 210, 380, 80, '코튼 혼방으로 자연스럽게 떨어지는 결, 피부에 닿는 감촉이 부담 없습니다.', { size: 17, color: '#4a4a45' }),
      ],
    },
    {
      id: uid('b'), name: '스타일링컷', kind: 'styling', bg: '#ffffff', elements: [
        IMG(60, 50, 430, 580, P.photo('ed_st1', 'styling', 430, 580), 12, 'styling'),
        IMG(510, 50, 430, 580, P.photo('ed_st2', 'styling', 430, 580), 12, 'styling'),
      ],
    },
    {
      id: uid('b'), name: '호리존컷', kind: 'horizon', bg: '#ffffff', elements: [
        IMG(280, 50, 440, 590, P.photo('ed_hz', 'horizon', 440, 590), 12, 'horizon'),
      ],
    },
    {
      id: uid('b'), name: '제품컷', kind: 'product', bg: '#f5f5f5', elements: [
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

  // storyboardDirty: 사용자가 콘티를 저장(수정)했는지 — false면 구성 방식 변경 시 기본 콘티를 재구성한다
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
