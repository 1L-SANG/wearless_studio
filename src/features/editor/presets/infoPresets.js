/* =============================================================
   features/editor/presets/infoPresets.js — 정보 블록 빌더 (PRD §10.14 `내용 추가`)
   상세페이지 실물 분석(documents/research/2026-07-29-detail-page-analysis.md)에서
   전 플랫폼 공통으로 확인된 정보 섹션을 폼 입력 → 캔버스 블록으로 생성한다.

   데이터 모델: 블록에 `info`(폼 상태 정본)를 저장하고 elements 는 info 로부터
   재생성한다. 재편집 시 폼은 info 를 읽고, 제출 시 elements 를 통째로 교체한다.
   요소는 계약 §3.5 의 기존 primitives(text/shape/line/image)만 사용한다.

   사이즈(kind='size')·세탁(kind='care')은 PRD §10.14 규정대로 기존 자동 블록을
   제자리 강화하고, 나머지는 {kind:'info', infoType} 일반 정보 블록으로 만든다.

   node --test 에서 직접 import 되므로 Vite 별칭(@/) 대신 상대 경로만 쓴다.
   ============================================================= */
import { uid } from '../../../lib/ids.js';

/* ---- 요소 헬퍼 — mock/db.js 의 T/IMG 문법과 동일한 shape ---- */
const T = (idFn) => (x, y, w, h, text, style) => ({ id: idFn('el'), type: 'text', x, y, w, h, text, style: style || {} });
const RECT = (idFn) => (x, y, w, h, fill, radius) => ({ id: idFn('el'), type: 'shape', shape: 'rect', x, y, w, h, fill, radius: radius ?? 8 });
const RULE = (idFn) => (x, y, w, stroke, strokeWidth) => ({ id: idFn('el'), type: 'line', shape: 'line', x, y, w, h: 8, stroke: stroke || '#e5e5e3', strokeWidth: strokeWidth || 1 });
const SLOT = (idFn) => (x, y, w, h) => ({ id: idFn('el'), type: 'image', x, y, w, h, src: null, radius: 8 });

export const FEATURE_ITEMS_MIN = 2;
export const FEATURE_ITEMS_MAX = 5;

const HEAD = { font: 'Cal Sans', weight: 600, color: '#0e0d14' };
const MUTED = '#4a4a45';
const FAINT = '#898989';
export const NEEDS_INPUT = '정보 입력 필요';
export const CARE_LABEL_SENTENCE = '세탁 전 실제 상품의 케어라벨을 반드시 확인해주세요.';

/* ---- 케어 문구 라이브러리 — 소재 패밀리별 규칙 기반 초안(PRD §10.14: AI가 사실을
   지어내지 않는다). 마지막 케어라벨 문장은 어떤 경로로도 빠지지 않는다. ---- */
export const CARE_COPY_LIBRARY = {
  cotton: { label: '면', lines: ['면 소재 특성상 세탁 후 약간의 수축이 생길 수 있어요.', '단독 찬물 세탁을 권장하며, 건조기 사용은 피해주세요.', '프린트가 있다면 뒤집어서 세탁해주세요.'] },
  knit: { label: '울·니트', lines: ['니트 소재는 드라이클리닝 또는 울 전용 세제 손세탁을 권장해요.', '비틀어 짜지 말고 눌러서 물기를 제거한 뒤 뉘어서 건조해주세요.', '보풀은 소재 특성으로 불량이 아니에요.'] },
  acrylic: { label: '아크릴', lines: ['아크릴 소재는 30도 이하 미지근한 물에 단독 세탁을 권장해요.', '건조기 사용 시 수축·변형이 생길 수 있으니 자연 건조해주세요.'] },
  poly: { label: '폴리·나일론', lines: ['찬물 단독 세탁 또는 세탁망 사용을 권장해요.', '다림질이 필요하면 낮은 온도로 천을 덧대어주세요.'] },
  denim: { label: '데님', lines: ['데님 특성상 초기 세탁 시 물빠짐이 있을 수 있어 단독 세탁해주세요.', '뒤집어서 세탁하면 색 유지에 도움이 돼요.'] },
  functional: { label: '기능성', lines: ['기능성 원단은 섬유유연제 사용 시 기능이 저하될 수 있어요.', '찬물 세탁 후 자연 건조를 권장해요.'] },
  generic: { label: '일반', lines: ['소재와 상품 특성에 따라 관리 방법이 달라질 수 있어요.'] },
};

export function careFamilyFor(materials) {
  const names = (materials || []).map((m) => (m && m.name) || '').join(' ');
  if (/면|코튼|cotton/i.test(names)) return 'cotton';
  // '모' 단독 매칭 금지 — '모달'(레이온)·'기모'가 니트로 오분류되고 poly 분기가 죽는다(리뷰 확정 결함)
  if (/울|wool|니트|캐시미어|앙고라|모헤어|양모/i.test(names)) return 'knit';
  if (/아크릴/i.test(names)) return 'acrylic';
  if (/데님|denim/i.test(names)) return 'denim';
  if (/쿨맥스|coolmax|기능성/i.test(names)) return 'functional';
  if (/폴리|나일론|스판|모달|레이온/i.test(names)) return 'poly';
  return 'generic';
}

/* 줄 수 추정 — 렌더는 width 고정·height auto 로 줄바꿈되므로 '\n' 개수만으로 높이를
   잡으면 긴 문단이 다음 요소를 덮는다(리뷰 확정 결함). 한글 위주 글자폭 근사(0.92em). */
function estLines(text, width, size) {
  return String(text || '').split('\n').reduce((n, ln) => n + Math.max(1, Math.ceil(((ln.length || 1) * size * 0.92) / width)), 0);
}

/* ---- 법정 고시 항목 (전자상거래 표시·광고 고시 — 의류) ---- */
export const NOTICE_FIELDS = [
  { key: 'name', label: '품명' },
  { key: 'materials', label: '소재' },
  { key: 'colors', label: '색상' },
  { key: 'sizes', label: '치수' },
  { key: 'maker', label: '제조자(수입자)' },
  { key: 'origin', label: '제조국' },
  { key: 'care', label: '세탁방법 및 취급시 주의사항' },
  { key: 'madeAt', label: '제조연월' },
  { key: 'warranty', label: '품질보증기준' },
  { key: 'contact', label: 'A/S 책임자와 전화번호' },
];

export const FIT_DESCRIPTIONS = {
  slim: '몸의 라인을 따라 붙는\n슬림한 실루엣',
  regular: '기본에 충실한\n표준 실루엣',
  semi_over: '적당한 여유가 있는\n세미오버 실루엣',
  over: '넉넉하게 떨어지는\n오버 실루엣',
};

export const POLICY_DEFAULT_SECTIONS = [
  { title: '배송 안내', body: '결제 확인 후 영업일 기준 2~5일 내 출고돼요.\n주문 폭주 및 제작 상황에 따라 일정이 지연될 수 있어요.' },
  { title: '교환·반품 안내', body: '상품 수령 후 7일 이내 신청 가능해요.\n단순 변심의 경우 왕복 배송비가 발생해요.\n택 제거·세탁·착용 흔적이 있으면 교환·반품이 어려워요.' },
  { title: '구매 전 확인해주세요', body: '모니터 해상도와 촬영 환경에 따라 실제 색상과 다소 차이가 있을 수 있어요.\n측정 위치에 따라 실측 치수는 1~3cm 오차가 있을 수 있어요.' },
];

const DEFAULT_MATRIX_SIZES = ['S', 'M', 'L', 'XL', 'XXL'];
const DEFAULT_MATRIX_HEIGHTS = ['~160cm', '160~170cm', '170~180cm', '180cm~'];
const DEFAULT_MATRIX_WEIGHTS = ['~55kg', '55~65kg', '65~75kg', '75~85kg', '85kg~'];

/* ---- 프리셋 카탈로그 — '내용' 탭 목록. PRD §10.14 중요도 순서(반드시 확인 →
   판매에 도움 → 필요할 때 추가) 고정, recommend 는 targetGenders 기반 배지 전용
   (기능·노출은 전원 동일 — UI 분기 금지). ---- */
export const INFO_PRESET_TYPES = [
  { type: 'size_table', label: '사이즈표', desc: '사이즈별 실측 치수 표 + 실측도', tier: 'must', recommend: null },
  { type: 'required_notice', label: '상품 고시정보', desc: '법정 필수 고시 항목 표', tier: 'must', recommend: null },
  { type: 'care', label: '세탁·케어 가이드', desc: '소재별 관리 방법 안내', tier: 'must', recommend: null },
  { type: 'policy', label: '배송·교환 안내', desc: '배송·교환·반품 표준 문구', tier: 'must', recommend: null },
  { type: 'header', label: '상품명 헤더', desc: '국문+영문 타이포 헤더', tier: 'boost', recommend: 'women' },
  { type: 'feature_icons', label: '특징 포인트', desc: '사진+장점 카드 2~5개', tier: 'boost', recommend: 'women' },
  { type: 'fit_guide', label: '핏 가이드', desc: '핏 실루엣 비교 도식', tier: 'boost', recommend: 'men' },
  { type: 'size_matrix', label: '추천 사이즈', desc: '키×몸무게 추천 사이즈 표', tier: 'boost', recommend: 'men' },
  { type: 'model_info', label: '모델 정보', desc: '모델 스펙 카드', tier: 'extra', recommend: 'women' },
];

/* ---- 폼 기본값 — analysis/product 컨텍스트에서 프리필 ---- */
export function defaultInfoFor(type, ctx = {}) {
  const schema = (ctx.measurementSchema && ctx.measurementSchema[ctx.clothingType]) || ['totalLength', 'shoulderWidth', 'chestWidth', 'sleeveLength'];
  switch (type) {
    case 'size_table': {
      const values = {};
      (ctx.measurements || []).forEach((m) => { if (m && m.key != null && m.value != null) values[m.key] = m.value; });
      return { unit: 'cm', columns: schema, rows: [{ label: 'FREE', values }], note: '단위: cm · 측정 위치에 따라 1~3cm 오차가 있을 수 있어요', withDiagram: false, diagramSrc: null };
    }
    case 'required_notice': {
      const materials = (ctx.materials || []).map((m) => (m.ratio != null ? `${m.name} ${m.ratio}%` : m.name)).join(', ');
      return { fields: NOTICE_FIELDS.map((f) => ({ ...f, value:
        f.key === 'name' ? (ctx.productName || '') :
        f.key === 'materials' ? materials :
        f.key === 'colors' ? (ctx.colorLabels || []).join(', ') :
        f.key === 'care' ? '케어라벨 참조' : '' })) };
    }
    case 'care': {
      const family = careFamilyFor(ctx.materials);
      const lines = [...CARE_COPY_LIBRARY[family].lines, CARE_LABEL_SENTENCE];
      return { family, text: lines.join('\n') };
    }
    case 'policy':
      return { sections: POLICY_DEFAULT_SECTIONS.map((s) => ({ ...s })) };
    case 'header':
      return { nameKo: ctx.productName || '', nameEn: '', eyebrow: 'PRODUCT INFORMATION' };
    case 'feature_icons': {
      const points = (ctx.sellingPoints || []).slice(0, FEATURE_ITEMS_MAX).map((p) => ({ title: p, desc: '', src: null }));
      // 새 블록 기본 칸수는 3 (분석 특징이 더 많으면 그 수) — MIN 이 3을 넘게 바뀌어도 하한은 지킨다
      while (points.length < Math.max(3, FEATURE_ITEMS_MIN)) points.push({ title: '', desc: '', src: null });
      return { items: points };
    }
    case 'fit_guide':
      return { fits: ['slim', 'regular', 'semi_over', 'over'], current: ctx.fit || null };
    case 'size_matrix': {
      const cells = DEFAULT_MATRIX_HEIGHTS.map((_, hi) => DEFAULT_MATRIX_WEIGHTS.map((_w, wi) =>
        DEFAULT_MATRIX_SIZES[Math.min(DEFAULT_MATRIX_SIZES.length - 1, Math.max(0, Math.round((hi + wi) / 2)))]));
      return { heights: [...DEFAULT_MATRIX_HEIGHTS], weights: [...DEFAULT_MATRIX_WEIGHTS], cells,
        note: '체형에 따라 다를 수 있어요 · 상세 실측 치수 확인을 권장드려요' };
    }
    case 'model_info': {
      // 프로젝트에서 실제 사용 중인 모델(가상 mA~mC 또는 FaceMarket 실존 모델)을 프리필.
      // 저장된 스펙 데이터는 이름·사진뿐이라 키·착용 사이즈는 사용자가 채운다.
      const sel = ctx.selectedModel;
      return { models: [sel ? { name: sel.name || 'MODEL A', height: '', size: '', src: sel.thumb || null } : { name: 'MODEL A', height: '', size: '', src: null }] };
    }
    default:
      return {};
  }
}

/* ============================ 빌더 ============================ */

function textBlockLines(text) { return String(text || '').split('\n').filter((l) => l.length); }

function buildSizeTable(info, ctx, idFn) {
  const t = T(idFn); const rect = RECT(idFn); const rule = RULE(idFn); const slot = SLOT(idFn);
  const columns = (info.columns || []).length ? info.columns : ['totalLength'];
  const rows = (info.rows || []).length ? info.rows : [{ label: 'FREE', values: {} }];
  const labels = (ctx.measurementLabels) || {};
  const labelW = 140; const colW = (880 - labelW) / columns.length;
  const els = [
    t(60, 56, 500, 44, '사이즈 안내', { size: 28, ...HEAD }),
    t(60, 104, 760, 24, info.note || '단위: cm · 측정 위치에 따라 1~3cm 오차가 있을 수 있어요', { size: 14, color: MUTED }),
    rect(60, 160, 880, 44, '#f5f5f5', 8),
    t(76, 172, labelW - 24, 20, '사이즈', { size: 14, weight: 600, color: MUTED }),
    ...columns.map((key, i) => t(60 + labelW + i * colW, 172, colW - 8, 20, labels[key] || key, { size: 14, weight: 600, color: MUTED, align: 'center' })),
  ];
  rows.forEach((row, r) => {
    const y = 216 + r * 48;
    els.push(t(76, y, labelW - 24, 22, row.label || '—', { size: 15, weight: 600, color: '#0e0d14' }));
    columns.forEach((key, i) => {
      const v = row.values ? row.values[key] : null;
      els.push(t(60 + labelW + i * colW, y, colW - 8, 22, v != null && v !== '' ? String(v) : '—', { size: 15, align: 'center', color: '#0e0d14' }));
    });
    els.push(rule(60, y + 32, 880));
  });
  let bottom = 216 + rows.length * 48;
  if (info.withDiagram) {
    // 실측도 사진은 info.diagramSrc 가 정본 — 토글을 껐다 켜도 사진이 살아난다
    els.push({ ...slot(300, bottom + 24, 400, 300), src: info.diagramSrc || null });
    bottom += 24 + 300;
  }
  return { id: idFn('b'), name: '사이즈 안내', kind: 'size', auto: true, bg: '#ffffff', h: bottom + 50, info: { ...info }, elements: els };
}

function buildRequiredNotice(info, ctx, idFn) {
  const t = T(idFn); const rule = RULE(idFn);
  const fields = (info.fields || []).length ? info.fields : NOTICE_FIELDS.map((f) => ({ ...f, value: '' }));
  const els = [t(60, 56, 500, 40, '상품 고시정보', { size: 24, ...HEAD })];
  let y = 120;
  fields.forEach((f, i) => {
    const filled = f.value != null && String(f.value).trim() !== '';
    const value = filled ? String(f.value) : NEEDS_INPUT;
    const vLines = estLines(value, 640, 14);
    const rowH = Math.max(44, vLines * 20 + 24);
    els.push(t(60, y, 220, 20, f.label, { size: 14, color: MUTED }));
    els.push(t(300, y, 640, vLines * 20, value, { size: 14, color: filled ? '#0e0d14' : FAINT, lineHeight: 20 }));
    if (i < fields.length - 1) els.push(rule(60, y + rowH - 14, 880));
    y += rowH;
  });
  return { id: idFn('b'), name: '상품 고시정보', kind: 'info', infoType: 'required_notice', bg: '#ffffff', h: y + 50, info: { fields: fields.map((f) => ({ ...f })) }, elements: els };
}

function buildCare(info, ctx, idFn) {
  const t = T(idFn);
  let text = String(info.text || '').trim();
  if (!text) text = defaultInfoFor('care', ctx).text;
  if (!text.includes('케어라벨')) text = `${text}\n${CARE_LABEL_SENTENCE}`;
  const lines = textBlockLines(text);
  const bodyH = Math.max(26, estLines(lines.join('\n'), 860, 15) * 26);
  const els = [
    t(60, 56, 500, 40, '세탁 안내', { size: 24, ...HEAD }),
    t(60, 104, 880, bodyH, lines.join('\n'), { size: 15, color: '#0e0d14', lineHeight: 26, list: 'bullet' }),
  ];
  return { id: idFn('b'), name: '세탁 안내', kind: 'care', auto: true, bg: '#f5f5f5', h: 104 + bodyH + 50, info: { family: info.family || careFamilyFor(ctx.materials), text }, elements: els };
}

function buildPolicy(info, ctx, idFn) {
  const t = T(idFn);
  const sections = (info.sections || []).length ? info.sections : POLICY_DEFAULT_SECTIONS;
  const els = [t(60, 56, 500, 40, '배송·교환·반품 안내', { size: 24, ...HEAD })];
  let y = 120;
  sections.forEach((s) => {
    const lines = textBlockLines(s.body);
    const bodyH = Math.max(22, estLines(lines.join('\n'), 880, 14) * 22);
    els.push(t(60, y, 500, 24, s.title, { size: 16, weight: 600, color: '#0e0d14' }));
    els.push(t(60, y + 30, 880, bodyH, lines.join('\n'), { size: 14, color: MUTED, lineHeight: 22 }));
    y += 30 + bodyH + 26;
  });
  return { id: idFn('b'), name: '배송·교환 안내', kind: 'info', infoType: 'shipping_returns', bg: '#ffffff', h: y + 30, info: { sections: sections.map((s) => ({ ...s })) }, elements: els };
}

function buildHeader(info, ctx, idFn) {
  const t = T(idFn);
  const els = [
    t(60, 64, 880, 20, info.eyebrow || 'PRODUCT INFORMATION', { font: 'Roboto Mono', size: 13, tracking: 4, color: FAINT, align: 'center' }),
    t(60, 96, 880, 52, info.nameKo || ctx.productName || '상품명', { size: 40, ...HEAD, align: 'center' }),
  ];
  let y = 152;
  if (info.nameEn) {
    els.push(t(60, y, 880, 30, info.nameEn, { font: 'Cormorant', size: 20, italic: true, color: FAINT, align: 'center' }));
    y += 44;
  }
  els.push({ id: idFn('el'), type: 'line', shape: 'line', x: 470, y, w: 60, h: 8, stroke: '#0e0d14', strokeWidth: 1.5 });
  return { id: idFn('b'), name: '상품명 헤더', kind: 'info', infoType: 'header', bg: '#ffffff', h: y + 58, info: { ...info }, elements: els };
}

export const FEATURE_LAYOUTS = [
  { value: 'stack', label: '세로형' },
  { value: 'center', label: '중앙형' },
  { value: 'grid', label: '그리드형' },
  { value: 'compact', label: '컴팩트' },
];

const FEATURE_LAYOUT_VALUES = new Set(FEATURE_LAYOUTS.map((l) => l.value));

/* 레이아웃 키가 없거나 모르는 값이면 compact — 이 키가 생기기 전에 만들어진 블록이
   그대로 재생성되어야 한다(마이그레이션 0건). */
export function resolveFeatureLayout(info) {
  const v = info && info.layout;
  return FEATURE_LAYOUT_VALUES.has(v) ? v : 'compact';
}

/* 입력 원본 보존 — 필터·placeholder 를 정본으로 저장하면 빈 슬롯이 영구 소실되고
   안내 문구가 판매 문구로 둔갑한다(리뷰 확정 결함). 레이아웃 4종이 같은 배열을 쓴다. */
function featureItems(info) {
  const items = (info.items || []).slice(0, FEATURE_ITEMS_MAX)
    .map((it) => ({ title: it.title || '', desc: it.desc || '', src: it.src || null }));
  while (items.length < FEATURE_ITEMS_MIN) items.push({ title: '', desc: '', src: null });
  return items;
}

/* 제목 placeholder — 하나라도 채워진 블록이면 빈 칸은 '—', 완전히 빈 블록이면 안내 문구. */
function featureTitle(it, anyFilled) {
  return it.title || (anyFilled ? '—' : '핵심 장점을 입력하세요');
}

function featureBlock(info, layout, items, h, els, idFn) {
  return { id: idFn('b'), name: '특징 포인트', kind: 'info', infoType: 'benefit_copy',
    bg: '#ffffff', h, info: { ...info, layout, items }, elements: els };
}

function buildFeatureCompact(info, ctx, idFn, items) {
  const t = T(idFn);
  const n = items.length;
  const colW = 880 / n;
  const d = Math.min(110, colW - 36);              // 원형 사진 슬롯 지름 — 개수에 맞춰 축소
  const anyFilled = items.some((it) => it.title || it.desc || it.src);
  const els = [];
  items.forEach((it, i) => {
    const x = 60 + i * colW;
    // 도형 대신 원형 이미지 슬롯 — 비어 있으면 '이미지 추가' 로 의류 탭에서 채운다
    els.push({ id: idFn('el'), type: 'image', x: x + colW / 2 - d / 2, y: 56, w: d, h: d, src: it.src || null, radius: d / 2 });
    const ty = 56 + d + 18;
    els.push(t(x, ty, colW, 18, `POINT ${i + 1}`, { font: 'Roboto Mono', size: 11, tracking: 2, color: FAINT, align: 'center' }));
    els.push(t(x + 10, ty + 26, colW - 20, 24, featureTitle(it, anyFilled), { size: n >= 5 ? 15 : 17, weight: 600, color: '#0e0d14', align: 'center' }));
    if (it.desc) els.push(t(x + 14, ty + 56, colW - 28, 40, it.desc, { size: 13, color: MUTED, align: 'center', lineHeight: 19 }));
  });
  const h = 56 + d + 18 + 26 + 30 + (items.some((it) => it.desc) ? 46 : 0) + 50;
  return { els, h };
}

const FEATURE_IMG_W = 880;          // 사진 폭 = 콘텐츠 폭
const FEATURE_STACK_IMG_H = 560;    // 사진 높이는 고정 — 이미지 dims 로 유도하면 파손 dims 가 레이아웃을 무너뜨린다
const FEATURE_STACK_GAP = 64;

function buildFeatureStack(info, ctx, idFn, items) {
  const t = T(idFn); const slot = SLOT(idFn);
  const anyFilled = items.some((it) => it.title || it.desc || it.src);
  const els = [t(60, 48, 880, 40, 'DETAIL POINT', { size: 28, ...HEAD, tracking: 1 })];
  let y = 108;
  items.forEach((it) => {
    els.push({ ...slot(60, y, FEATURE_IMG_W, FEATURE_STACK_IMG_H), src: it.src || null });
    const ty = y + FEATURE_STACK_IMG_H + 28;
    els.push(t(60, ty, 880, 32, featureTitle(it, anyFilled), { size: 22, weight: 600, color: '#0e0d14' }));
    let bottom = ty + 32;
    if (it.desc) {
      const dh = estLines(it.desc, 880, 15) * 26;
      els.push(t(60, ty + 44, 880, dh, it.desc, { size: 15, color: MUTED, lineHeight: 26 }));
      bottom = ty + 44 + dh;
    }
    y = bottom + FEATURE_STACK_GAP;
  });
  return { els, h: y - FEATURE_STACK_GAP + 50 };
}

const FEATURE_CENTER_IMG_H = 620;
const FEATURE_CENTER_GAP = 80;
const FEATURE_BADGE_W = 200;        // 텍스트 길이와 무관한 고정 폭 — 번호는 상한 5라 2자리로 안 간다

function buildFeatureCenter(info, ctx, idFn, items) {
  const t = T(idFn); const rect = RECT(idFn); const slot = SLOT(idFn);
  const anyFilled = items.some((it) => it.title || it.desc || it.src);
  const els = [];
  let y = 56;
  items.forEach((it, i) => {
    els.push({ ...slot(60, y, FEATURE_IMG_W, FEATURE_CENTER_IMG_H), src: it.src || null });
    const by = y + FEATURE_CENTER_IMG_H + 36;
    const bx = 60 + (880 - FEATURE_BADGE_W) / 2;
    els.push(rect(bx, by, FEATURE_BADGE_W, 34, '#f5f5f5', 6));
    els.push(t(bx, by + 9, FEATURE_BADGE_W, 18, `DETAIL POINT ${String(i + 1).padStart(2, '0')}`,
      { font: 'Roboto Mono', size: 12, tracking: 2, color: MUTED, align: 'center' }));
    els.push(t(60, by + 58, 880, 34, featureTitle(it, anyFilled), { size: 22, weight: 600, color: '#0e0d14', align: 'center' }));
    let bottom = by + 58 + 34;
    if (it.desc) {
      const dh = estLines(it.desc, 760, 15) * 26;
      els.push(t(120, by + 104, 760, dh, it.desc, { size: 15, color: MUTED, lineHeight: 26, align: 'center' }));
      bottom = by + 104 + dh;
    }
    y = bottom + FEATURE_CENTER_GAP;
  });
  return { els, h: y - FEATURE_CENTER_GAP + 56 };
}

const FEATURE_BUILDERS = {
  stack: buildFeatureStack,
  center: buildFeatureCenter,
  compact: buildFeatureCompact,
};

function buildFeatureIcons(info, ctx, idFn) {
  const layout = resolveFeatureLayout(info);
  const items = featureItems(info);
  const build = FEATURE_BUILDERS[layout] || FEATURE_BUILDERS.compact;
  const { els, h } = build(info, ctx, idFn, items);
  return featureBlock(info, layout, items, h, els, idFn);
}

function buildFitGuide(info, ctx, idFn) {
  const t = T(idFn); const rect = RECT(idFn);
  const fits = (info.fits || []).length ? info.fits : ['slim', 'regular', 'semi_over', 'over'];
  const fitLabels = Object.fromEntries((ctx.fits || []).map((f) => [f.value, f.label]));
  const current = info.current || null;
  const els = [t(60, 56, 500, 40, '핏 가이드', { size: 24, ...HEAD })];
  if (current && fitLabels[current]) els.push(t(60, 100, 760, 22, `이 상품은 ${fitLabels[current]} 실루엣이에요`, { size: 14, color: MUTED }));
  const gap = 16; const n = fits.length; const colW = (880 - (n - 1) * gap) / n;
  fits.forEach((fit, i) => {
    const x = 60 + i * (colW + gap);
    const on = fit === current;
    els.push(rect(x, 140, colW, 150, on ? '#0e0d14' : '#f5f5f5', 12));
    els.push(t(x, 176, colW, 24, fitLabels[fit] || fit, { size: 16, weight: 600, align: 'center', color: on ? '#ffffff' : '#0e0d14' }));
    els.push(t(x + 12, 208, colW - 24, 40, FIT_DESCRIPTIONS[fit] || '', { size: 12, align: 'center', color: on ? '#d4d4d8' : MUTED, lineHeight: 17 }));
  });
  return { id: idFn('b'), name: '핏 가이드', kind: 'info', infoType: 'fit_guide', bg: '#ffffff', h: 140 + 150 + 60, info: { fits: [...fits], current }, elements: els };
}

function buildSizeMatrix(info, ctx, idFn) {
  const t = T(idFn); const rect = RECT(idFn); const rule = RULE(idFn);
  const heights = (info.heights || []).length ? info.heights : DEFAULT_MATRIX_HEIGHTS;
  const weights = (info.weights || []).length ? info.weights : DEFAULT_MATRIX_WEIGHTS;
  const cells = info.cells || defaultInfoFor('size_matrix', ctx).cells;
  const labelW = 150; const colW = (880 - labelW) / weights.length;
  const els = [
    t(60, 56, 500, 40, '추천 사이즈', { size: 24, ...HEAD }),
    rect(60, 116, 880, 40, '#f5f5f5', 8),
    t(76, 127, labelW - 24, 18, 'cm / kg', { size: 13, weight: 600, color: MUTED }),
    ...weights.map((w, i) => t(60 + labelW + i * colW, 127, colW - 6, 18, w, { size: 13, weight: 600, color: MUTED, align: 'center' })),
  ];
  heights.forEach((hLabel, r) => {
    const y = 168 + r * 44;
    els.push(t(76, y, labelW - 24, 20, hLabel, { size: 14, weight: 600, color: '#0e0d14' }));
    weights.forEach((_w, c) => {
      const v = (cells[r] && cells[r][c]) || '—';
      els.push(t(60 + labelW + c * colW, y, colW - 6, 20, v, { size: 14, align: 'center', color: '#0e0d14' }));
    });
    els.push(rule(60, y + 30, 880));
  });
  let bottom = 168 + heights.length * 44;
  if (info.note) { els.push(t(60, bottom + 10, 880, 20, info.note, { size: 13, color: FAINT })); bottom += 32; }
  return { id: idFn('b'), name: '추천 사이즈', kind: 'info', infoType: 'size_matrix', bg: '#ffffff', h: bottom + 50, info: { heights: [...heights], weights: [...weights], cells: cells.map((r) => [...r]), note: info.note || '' }, elements: els };
}

function buildModelInfo(info, ctx, idFn) {
  const t = T(idFn); const rect = RECT(idFn);
  const models = (info.models || []).filter((m) => m.name || m.height || m.size || m.src);
  const list = models.length ? models : [{ name: 'MODEL A', height: '', size: '', src: null }];
  const n = Math.min(3, list.length);
  const gap = 16; const colW = (880 - (n - 1) * gap) / n;
  const els = [t(60, 56, 880, 24, 'MODEL INFO', { font: 'Roboto Mono', size: 15, tracking: 3, weight: 600, color: '#0e0d14', align: 'center' })];
  const d = 72; // 모델 사진 원형 슬롯 — 실제 사용 모델 썸네일 프리필, 비면 '이미지 추가'
  list.slice(0, 3).forEach((m, i) => {
    const x = 60 + i * (colW + gap);
    els.push(rect(x, 110, colW, 196, '#f5f5f5', 12));
    els.push({ id: idFn('el'), type: 'image', x: x + colW / 2 - d / 2, y: 128, w: d, h: d, src: m.src || null, radius: d / 2 });
    els.push(t(x, 214, colW, 22, m.name || `MODEL ${i + 1}`, { size: 15, weight: 600, align: 'center', color: '#0e0d14' }));
    const spec = [m.height, m.size ? `${m.size} 착용` : ''].filter(Boolean).join(' · ');
    els.push(t(x + 10, 242, colW - 20, 44, spec || '스펙을 입력하세요', { size: 13, align: 'center', color: MUTED, lineHeight: 20 }));
  });
  return { id: idFn('b'), name: '모델 정보', kind: 'info', infoType: 'model_info', bg: '#ffffff', h: 110 + 196 + 56, info: { models: list.map((m) => ({ ...m })) }, elements: els };
}

const BUILDERS = {
  size_table: buildSizeTable,
  required_notice: buildRequiredNotice,
  care: buildCare,
  policy: buildPolicy,
  header: buildHeader,
  feature_icons: buildFeatureIcons,
  fit_guide: buildFitGuide,
  size_matrix: buildSizeMatrix,
  model_info: buildModelInfo,
};

/* 단일 진입점 — type + info(폼 상태) + ctx(프로젝트 컨텍스트) → EditorBlock */
export function buildInfoBlock(type, info, ctx = {}, idFn = uid) {
  const build = BUILDERS[type];
  if (!build) throw new Error(`[infoPresets] unknown preset type: ${type}`);
  return build(info || defaultInfoFor(type, ctx), ctx, idFn);
}

/* 재생성된 블록에 기존 이미지 슬롯의 src 를 **같은 서수(ordinal)끼리** 이월한다 —
   압축(compaction)해서 앞에서부터 채우면 3번 포인트 사진이 1번 밑으로 이사한다
   (리뷰 확정 결함). n번째 이미지 요소 ↔ n번째 이미지 요소로만 매칭하고, 이미
   src 가 있는 슬롯(info 정본에서 채워진 것)은 건드리지 않는다. crop 은 지오메트리가
   달라져 무효라 이월하지 않는다. 정본 동기화는 applySlotFillToInfo 가 담당하고,
   이 함수는 info 동기화 이전에 채워진 레거시 블록의 안전망이다. */
export function carrySlotImages(prevElements, block) {
  const prevImgs = (prevElements || []).filter((e) => e.type === 'image');
  if (!prevImgs.some((e) => e.src)) return block;
  let ord = -1;
  return {
    ...block,
    elements: block.elements.map((el) => {
      if (el.type !== 'image') return el;
      ord += 1;
      const prev = prevImgs[ord];
      return !el.src && prev && prev.src ? { ...el, src: prev.src, ...(prev.cutType ? { cutType: prev.cutType } : {}) } : el;
    }),
  };
}

/* 슬롯 채움을 elements 와 info(폼 정본)에 **동시에** 기록한다 — 요소에만 쓰면
   재생성(재편집·템플릿 재적용) 때 사진-포인트 연결이 끊긴다(리뷰 확정 결함).
   이미지 요소의 서수 = info 배열 인덱스 (빌더가 같은 순서로 방출). */
export function applySlotFillToInfo(block, elId, { src, cutType }) {
  const elements = block.elements.map((e) => (e.id === elId ? { ...e, src, ...(cutType ? { cutType } : {}) } : e));
  const type = presetTypeOf(block);
  if (!type || !block.info) return { ...block, elements };
  const ord = block.elements.filter((e) => e.type === 'image').findIndex((e) => e.id === elId);
  if (ord < 0) return { ...block, elements };
  let info = block.info;
  if (type === 'feature_icons') info = { ...info, items: (info.items || []).map((it, i) => (i === ord ? { ...it, src } : it)) };
  else if (type === 'model_info') info = { ...info, models: (info.models || []).map((m, i) => (i === ord ? { ...m, src } : m)) };
  else if (type === 'size_table') info = { ...info, diagramSrc: src };
  return { ...block, info, elements };
}

/* 블록 → 프리셋 타입 역매핑 (재편집 진입용). size/care 는 kind 로, 나머지는 infoType 로. */
export function presetTypeOf(block) {
  if (!block) return null;
  if (block.kind === 'size') return 'size_table';
  if (block.kind === 'care') return 'care';
  if (block.kind !== 'info') return null;
  if (block.infoType === 'shipping_returns') return 'policy';
  if (block.infoType === 'benefit_copy') return 'feature_icons';
  return BUILDERS[block.infoType] ? block.infoType : null;
}

/* ---- 기본 정보 템플릿 — 단일 세트 일괄 삽입 (2026-07-29 회의: 소호형/브랜드형
   분기 제거, 하나로 통합). 시퀀스는 5개 플랫폼 분석의 공통 코어. 핏가이드·추천
   사이즈·모델 정보는 개별 프리셋으로 추가한다.
   컷 블록은 절대 건드리지 않는다. size/care 는 제자리 강화(교체), 이미 있는
   infoType 은 중복 삽입 대신 스킵. 상단(top) 항목은 문서 맨 앞에 순서대로. ---- */
export const DEFAULT_INFO_TEMPLATE = {
  label: '기본',
  top: ['policy', 'header'],
  flow: ['feature_icons', 'size_table', 'care', 'required_notice'],
};

/* 아직 정보 템플릿이 적용된 적 없는 "생성 직후 기본 문서"인지 판별 —
   에디터가 로드 시 자동으로 기본 템플릿을 깔아 주는 게이트(2026-07-29 결정:
   수동 버튼 대신 기본값). info 블록이 하나라도 있으면 이미 손댄 문서,
   size/care 가 info 없이 있으면 어셈블러의 옛 기본 블록 그대로인 문서다. */
export function needsDefaultTemplate(blocks) {
  if (!Array.isArray(blocks) || !blocks.length) return false;
  if (blocks.some((b) => b && b.kind === 'info')) return false;
  return blocks.some((b) => b && (b.kind === 'size' || b.kind === 'care') && !b.info);
}

export function applyInfoTemplate(blocks, ctx = {}, idFn = uid) {
  const tpl = DEFAULT_INFO_TEMPLATE;
  const presentInfoTypes = new Set(blocks.filter((b) => b.kind === 'info' && b.infoType).map((b) => b.infoType));
  const inserted = []; const skipped = [];
  const labelOf = (type) => (INFO_PRESET_TYPES.find((p) => p.type === type) || { label: type }).label;
  const isDup = (type) => {
    const built = buildInfoBlock(type, defaultInfoFor(type, ctx), ctx, idFn);
    return built.kind === 'info' && presentInfoTypes.has(built.infoType);
  };

  let next = [...blocks];

  // 상단 삽입 — top 배열 순서 그대로 문서 맨 앞에.
  const topBlocks = [];
  tpl.top.forEach((type) => {
    if (isDup(type)) { skipped.push(labelOf(type)); return; }
    const b = buildInfoBlock(type, defaultInfoFor(type, ctx), ctx, idFn);
    topBlocks.push(b); inserted.push(labelOf(type));
    if (b.kind === 'info' && b.infoType) presentInfoTypes.add(b.infoType);
  });
  next = [...topBlocks, ...next];

  // 본문 흐름 — size/care 앵커는 제자리 교체, 비앵커는 흐름 순서를 유지한 채
  // 다음 앵커 "앞"(pending 플러시) 또는 직전 앵커 "뒤"(cursor)에 삽입한다.
  const insertAt = (arr, at, items) => [...arr.slice(0, at), ...items, ...arr.slice(at)];
  let cursor = null;   // 직전 앵커 다음 위치. null = 아직 앵커를 못 만남
  let pending = [];    // 앵커를 만나기 전까지 모아둔 비앵커 블록 — 앵커 앞에 플러시
  tpl.flow.forEach((type) => {
    if (type === 'size_table' || type === 'care') {
      const kind = type === 'size_table' ? 'size' : 'care';
      let idx = next.findIndex((b) => b.kind === kind);
      if (idx >= 0) {
        // pending 플러시 위치는 뒤로만 간다 — 앵커가 문서에서 앞쪽에 있어도(사용자가
        // care 를 size 위로 옮긴 문서 등) cursor 를 역행시키면 흐름 순서가 섞인다(리뷰 확정 결함).
        const at = cursor != null ? Math.max(cursor, idx) : idx;
        if (pending.length) {
          next = insertAt(next, at, pending);
          if (idx >= at) idx += pending.length;
        }
        // 제자리 강화 — 사용자가 입력해 둔 info·블록 id·슬롯 사진을 보존한 채 elements 만 재생성.
        // 기본값으로 다시 지으면 템플릿 재적용 때 입력한 실측·케어 문구가 소실된다(리뷰 확정 결함).
        const prev = next[idx];
        const seeded = carrySlotImages(prev.elements, buildInfoBlock(type, prev.info || defaultInfoFor(type, ctx), ctx, idFn));
        next = next.map((b, i) => (i === idx ? { ...seeded, id: prev.id } : b));
        cursor = Math.max(at + pending.length, idx + 1);
        pending = [];
      } else {
        // 앵커가 없는 문서 — ai-notice 앞(없으면 끝)을 기준점으로 흐름을 이어간다.
        const built = buildInfoBlock(type, defaultInfoFor(type, ctx), ctx, idFn);
        const noticeIdx = next.findIndex((b) => b.kind === 'ai-notice');
        const at = cursor != null ? cursor : (noticeIdx >= 0 ? noticeIdx : next.length);
        next = insertAt(next, at, [...pending, built]);
        cursor = at + pending.length + 1;
        pending = [];
      }
      inserted.push(labelOf(type));
      return;
    }
    if (isDup(type)) { skipped.push(labelOf(type)); return; }
    const b = buildInfoBlock(type, defaultInfoFor(type, ctx), ctx, idFn);
    if (b.kind === 'info' && b.infoType) presentInfoTypes.add(b.infoType);
    inserted.push(labelOf(type));
    if (cursor == null) { pending.push(b); return; }
    next = insertAt(next, cursor, [b]);
    cursor += 1;
  });

  if (pending.length) {
    // 앵커(size/care)를 끝까지 못 만난 문서 — ai-notice 앞, 없으면 맨 끝에 순서대로.
    const noticeIdx = next.findIndex((b) => b.kind === 'ai-notice');
    const at = noticeIdx >= 0 ? noticeIdx : next.length;
    next = insertAt(next, at, pending);
  }

  return { blocks: next, inserted, skipped };
}
