/* =============================================================
   mock/db.js — single source of fake data (conforms to lib/types.js).
   (ported from handoff/contracts/db.example.js; window.* → ES module)
   Screens NEVER hardcode data; they read it through mock/api.js,
   which reads from here. Swap this out for a real backend later.
   creditCosts is sourced from lib/limits.js (single tunable place).

   NOTE: the per-creation "draft" collections (product, analysis,
   mannequins, storyboard, editorBlocks, wardrobe) are MUTATED by the
   api (adjust/regenerate push, save* assign). buildDraft() rebuilds
   them from fresh seeds and reseedDraft() reinstalls a clean copy, so
   starting a new creation does not leak the prior session's variants.
   Stable reference data (account, catalogs, models, library …) is not
   reseeded.
   ============================================================= */
import { Placeholder as P } from '@/mock/placeholders.js';
import { CREDIT_COSTS } from '@/lib/limits.js';

const uid = (p) => p + '_' + Math.random().toString(36).slice(2, 8);

/* ---- Account (stable) ---- */
const account = { name: 'Jisoo Han', avatar: P.portrait('han'), credits: 24, plan: 'Pro' };

/* ---- Catalogs (stable closed option sets) ---- */
const catalogs = {
  clothingTypes: [
    { value: 'top', label: '상의' }, { value: 'bottom', label: '하의' },
    { value: 'outer', label: '아우터' }, { value: 'dress', label: '원피스' },
  ],
  subCategories: {
    top: ['티셔츠', '맨투맨', '셔츠', '니트'],
    bottom: ['면바지', '트레이닝 팬츠', '청바지', '슬랙스', '치마'],
    outer: ['셔츠', '자켓', '가디건', '패딩', '코트'],
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
  angleSlots: ['Front', 'Back', 'Detail', 'Fit'],
  angleLabels: { Front: '앞면 이미지', Back: '뒷면 이미지', Detail: '디테일 이미지', Fit: '착용 이미지' },
  // measurement schema per clothing type (PRD §6.5)
  measurementSchema: {
    top: ['총장', '어깨너비', '가슴단면', '소매길이'],
    bottom: ['총장', '허리단면', '엉덩이단면', '허벅지단면', '밑위', '밑단단면'],
    outer: ['총장', '어깨너비', '가슴단면', '소매길이'],
    dress: ['총장', '어깨너비', '가슴단면', '허리단면', '암홀', '소매길이'],
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
  composeModes: [
    { value: 'simple', label: '간단형', desc: '단일 컬러 중심으로 빠르게', count: '6~9', flow: ['호리존컷', '셀링포인트', '제품컷'] },
    { value: 'basic', label: '기본형', desc: '대표 컬러 중심의 균형형', count: '11~15', flow: ['후킹', '셀링포인트', '스타일링컷', '호리존컷', '제품컷'], recommended: true },
    { value: 'extended', label: '확장형', desc: '여러 컬러를 자세히 소개', count: '18~26', flow: ['후킹', '셀링포인트', '컬러별 스타일링컷', '컬러별 호리존컷', '제품컷'] },
  ],
  extendedColorPriority: [
    { value: 'main', label: '메인 색상' }, { value: 'mono', label: '모노톤 추천' }, { value: 'ai', label: 'AI 추천' },
  ],
  poses: [
    { id: 'auto', label: 'AI 자동', auto: true }, { id: 'stand', label: '서기', thumb: P.pose('stand') },
    { id: 'walk', label: '걷기', thumb: P.pose('walk') }, { id: 'sit', label: '앉기', thumb: P.pose('sit') },
    { id: 'lean', label: '기대기', thumb: P.pose('lean') }, { id: 'turn', label: '돌아보기', thumb: P.pose('turn') },
  ],
  backgrounds: [
    { id: 'same', label: '동일', thumb: P.scene('same') }, { id: 'cafe', label: '카페', thumb: P.scene('cafe') },
    { id: 'street', label: '거리', thumb: P.scene('street') }, { id: 'park', label: '공원', thumb: P.scene('park') },
    { id: 'studio', label: '스튜디오', thumb: P.scene('studio') }, { id: 'home', label: '홈', thumb: P.scene('home') },
  ],
  genExamples: Array.from({ length: 8 }, (_, i) => ({ id: 'ex' + i, thumb: P.photo('ex' + i, i % 2 ? 'styling' : 'horizon', 240, 320) })),
  cutSources: [
    { value: 'studio', label: '호리존컷' }, { value: 'daily', label: '일상컷' },
    { value: 'product', label: '제품컷' }, { value: 'mine', label: '내 이미지' },
  ],
  frames: [
    { id: 'split2', label: '2분할', cols: 2 }, { id: 'grid3', label: '3컷 구성', cols: 3 },
    { id: 'faq', label: 'FAQ', cols: 1 }, { id: 'ba', label: 'Before / After', cols: 2 },
    { id: 'colorcmp', label: '컬러 비교', cols: 3 }, { id: 'infocard', label: '상품 정보 카드', cols: 1 },
  ],
  shapes: [
    { id: 'circle', label: '원' }, { id: 'rect', label: '사각형' }, { id: 'triangle', label: '삼각형' },
  ],
  lines: [{ id: 'arrow-l', label: '←' }, { id: 'line', label: '—' }, { id: 'arrow-r', label: '→' }],
  fonts: ['Pretendard', 'Cal Sans', 'Roboto Mono'],
  downloadOptions: [
    { id: 'long', title: '전체 상세페이지 긴 PNG 1장', desc: '모든 블록을 세로로 이어 붙여 한 장으로 저장' },
    { id: 'zip', title: '블록별 PNG ZIP', desc: '각 블록을 개별 PNG로 저장해 ZIP으로 다운로드' },
  ],
  // 단계별 크레딧 단가 — lib/limits.js 가 단일 소스. 여기로 노출해 계약 shape 유지.
  creditCosts: { ...CREDIT_COSTS },
};

/* ---- Models & match clothing (stable option sets) ---- */
const models = [
  { id: 'mA', name: '모델 A', thumb: P.portrait('mA'), recommended: true },
  { id: 'mB', name: '모델 B', thumb: P.portrait('mB'), recommended: false },
  { id: 'mC', name: '모델 C', thumb: P.portrait('mC'), recommended: false },
];
const matchClothing = [
  { id: 'c1', name: '데님 팬츠', thumb: P.swatch('c1'), selected: true, selOrder: 1 },
  { id: 'c2', name: '슬랙스', thumb: P.swatch('c2'), selected: true, selOrder: 2 },
  { id: 'c3', name: '스커트', thumb: P.swatch('c3'), selected: false },
  { id: 'c4', name: '와이드 팬츠', thumb: P.swatch('c4'), selected: false },
];

/* ---- Generation job steps (stable, PRD §9.2) ---- */
const genSteps = [
  { key: 'info', label: '상품 정보 정리' }, { key: 'prep', label: '이미지 생성 준비' },
  { key: 'styling', label: '스타일링컷 생성' }, { key: 'horizon', label: '호리존컷 생성' },
  { key: 'product', label: '제품컷 생성' }, { key: 'copy', label: '카피라이팅 적용' },
  { key: 'assemble', label: '상세페이지 조립' },
];

/* ---- Library (stable list) ---- */
const library = [
  { id: uid('lib'), title: '소프트 골지 라운드 니트', cover: P.photo('lib1', 'horizon', 400, 520), clothingType: 'top', blocks: 8, status: 'done', updatedAt: '2시간 전' },
  { id: uid('lib'), title: '와이드 데님 팬츠', cover: P.photo('lib2', 'styling', 400, 520), clothingType: 'bottom', blocks: 6, status: 'done', updatedAt: '어제' },
  { id: uid('lib'), title: '오버핏 울 코트', cover: P.product('lib3', 400, 520), clothingType: 'outer', blocks: 9, status: 'generating', updatedAt: '진행 중' },
  { id: uid('lib'), title: '플리츠 미디 원피스', cover: P.photo('lib4', 'horizon', 400, 520), clothingType: 'dress', blocks: 5, status: 'draft', updatedAt: '3일 전' },
];

/* =============================================================
   buildDraft() — fresh per-creation working data. Called at init
   and on reseedDraft() so a new creation never inherits the prior
   session's mutations (mannequin variants, saved storyboard, etc.).
   ============================================================= */
function buildDraft() {
  const measurements = () => [
    { key: '총장', label: '총장', value: 64, unit: 'cm' },
    { key: '어깨너비', label: '어깨너비', value: 42, unit: 'cm' },
    { key: '가슴단면', label: '가슴단면', value: 51, unit: 'cm' },
    { key: '소매길이', label: '소매길이', value: null, unit: 'cm' },
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

  /* ---- Analysis result (AI-filled, editable) ---- */
  const analysis = {
    clothingType: 'top', subCategory: '니트', targetGenders: ['women'],
    fit: 'semi_over', suggestedName: '소프트 골지 라운드 니트',
    materials: [{ name: '코튼', ratio: 60 }, { name: '폴리에스터', ratio: 40 }],
    sellingPoints: ['부드러운 촉감', '여리한 핏', '단독/이너 활용 가능'],
    aiSuggestedPoints: ['넉넉한 라운드 넥', '비침 없는 도톰함'],
    selectedModelId: 'mA', models, matchClothing: matchClothing.map((m) => ({ ...m })),
    washCare: '', locked: false,
    measurementsUnknown: false,
    measurements: measurements(),
  };

  /* ---- Mannequin candidates (A/B + history, PRD §7.3) ---- */
  const mannequins = [
    { id: 'A-0', candidate: 'A', version: 0, src: P.photo('A0', 'mannequin'), fitLabel: '정핏', lengthLabel: '원본 기장', selected: true },
    { id: 'A-1', candidate: 'A', version: 1, src: P.photo('A1', 'mannequin'), fitLabel: '더 여유롭게', lengthLabel: '원본 기장', selected: false },
    { id: 'B-0', candidate: 'B', version: 0, src: P.photo('B0', 'mannequin'), fitLabel: '슬림핏', lengthLabel: '조금 길게', selected: false },
  ];

  /* ---- Storyboard blocks (basic mode default, PRD §8) ---- */
  const sb = (kind, title, direction, shot, colorId, source, pose, bg) => ({
    id: uid('blk'), kind, title, direction, shot, colorId, source,
    thumb: P.photo(kind + title, kind === 'product' ? 'product' : kind === 'horizon' ? 'horizon' : 'styling', 240, 320),
    poseThumb: P.pose(pose), poseLabel: pose, bgThumb: P.scene(bg), bgLabel: bg,
  });
  const storyboard = [
    sb('hook', '후킹', 'front', 'full', 'col1', 'studio', '서기', '스튜디오'),
    sb('selling', '셀링포인트', 'side', 'close', 'col1', 'product', '서기', '동일'),
    sb('styling', '스타일링컷', 'side', 'medium', 'col1', 'daily', '걷기', '거리'),
    sb('styling', '스타일링컷', 'front', 'knee', 'col1', 'daily', '기대기', '카페'),
    sb('horizon', '호리존컷', 'front', 'knee', 'col1', 'studio', '서기', '스튜디오'),
    sb('horizon', '호리존컷', 'back', 'full', 'col1', 'studio', '돌아보기', '스튜디오'),
    sb('product', '제품컷', 'front', 'close', 'col1', 'product', '서기', '동일'),
  ];

  /* ---- Editor blocks: 5 prefilled + auto info blocks (PRD §10.14) ---- */
  const T = (x, y, w, h, text, style) => ({ id: uid('el'), type: 'text', x, y, w, h, text, style: style || {} });
  const IMG = (x, y, w, h, src, radius) => ({ id: uid('el'), type: 'image', x, y, w, h, src, radius: radius || 8 });
  const editorBlocks = [
    {
      id: uid('b'), name: '후킹', kind: 'hook', bg: '#ffffff', elements: [
        IMG(60, 50, 880, 560, P.photo('ed_hook', 'horizon', 880, 560), 12),
        T(120, 110, 600, 80, '겨울을 부드럽게, 골지 니트', { size: 40, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(120, 200, 520, 40, '하루 종일 편안한 데일리 니트', { size: 20, color: '#0e0d14' }),
      ],
    },
    {
      id: uid('b'), name: '셀링포인트', kind: 'selling', bg: '#f5f5f5', elements: [
        IMG(60, 50, 420, 540, P.detail('ed_sell', 420, 540), 12),
        T(540, 150, 380, 40, '부드러운 촉감', { size: 28, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(540, 210, 380, 80, '코튼 혼방으로 자연스럽게 떨어지는 결, 피부에 닿는 감촉이 부담 없습니다.', { size: 17, color: '#4a4a45' }),
      ],
    },
    {
      id: uid('b'), name: '스타일링컷', kind: 'styling', bg: '#ffffff', elements: [
        IMG(60, 50, 430, 580, P.photo('ed_st1', 'styling', 430, 580), 12),
        IMG(510, 50, 430, 580, P.photo('ed_st2', 'styling', 430, 580), 12),
      ],
    },
    {
      id: uid('b'), name: '호리존컷', kind: 'horizon', bg: '#ffffff', elements: [
        IMG(280, 50, 440, 590, P.photo('ed_hz', 'horizon', 440, 590), 12),
      ],
    },
    {
      id: uid('b'), name: '제품컷', kind: 'product', bg: '#f5f5f5', elements: [
        IMG(90, 60, 380, 500, P.product('ed_p1', 380, 500), 12),
        IMG(530, 60, 380, 500, P.product('ed_p2', 380, 500), 12),
        T(90, 580, 200, 30, 'FRONT', { size: 15, weight: 600, font: 'Roboto Mono', color: '#0e0d14', tracking: 2 }),
        T(530, 580, 200, 30, 'BACK', { size: 15, weight: 600, font: 'Roboto Mono', color: '#0e0d14', tracking: 2 }),
      ],
    },
    /* ---- auto-appended info blocks (PRD §10.14) ---- */
    {
      id: uid('b'), name: '사이즈 안내', kind: 'size', auto: true, bg: '#ffffff', elements: [
        T(60, 56, 500, 44, '사이즈 안내', { size: 28, weight: 600, font: 'Cal Sans', color: '#0e0d14' }),
        T(60, 104, 760, 24, '단위: cm · 측정 위치에 따라 1~3cm 오차가 있을 수 있어요', { size: 14, color: '#4a4a45' }),
        ...product.measurements.slice(0, 4).flatMap((m, i) => {
          const x = 60 + i * 232;
          return [
            T(x, 168, 200, 24, m.label, { size: 14, color: '#4a4a45' }),
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

  /* ---- Editor 의류 탭: color-grouped image library ---- */
  const wardrobe = {
    '색상 1': Array.from({ length: 5 }, (_, i) => ({ id: uid('w'), src: P.photo('w1' + i, i % 2 ? 'styling' : 'horizon', 200, 260), ai: i > 2 })),
    '색상 2': Array.from({ length: 3 }, (_, i) => ({ id: uid('w'), src: P.photo('w2' + i, 'horizon', 200, 260), ai: i > 1 })),
    '기타': Array.from({ length: 2 }, (_, i) => ({ id: uid('w'), src: P.product('w3' + i, 200, 260), ai: false })),
  };

  return { product, analysis, mannequins, storyboard, editorBlocks, wardrobe };
}

export const DB = {
  account, catalogs, models, matchClothing, genSteps, library, uid,
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
