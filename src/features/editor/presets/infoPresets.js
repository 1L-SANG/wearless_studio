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
const CIRCLE = (idFn) => (x, y, d, fill) => ({ id: idFn('el'), type: 'shape', shape: 'circle', x, y, w: d, h: d, fill });
const RULE = (idFn) => (x, y, w, stroke, strokeWidth) => ({ id: idFn('el'), type: 'line', shape: 'line', x, y, w, h: 8, stroke: stroke || '#e5e5e3', strokeWidth: strokeWidth || 1 });
const SLOT = (idFn) => (x, y, w, h) => ({ id: idFn('el'), type: 'image', x, y, w, h, src: null, radius: 8 });

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
  if (/울|wool|니트|캐시미어|모/i.test(names)) return 'knit';
  if (/아크릴/i.test(names)) return 'acrylic';
  if (/데님|청/i.test(names)) return 'denim';
  if (/쿨맥스|coolmax|기능성/i.test(names)) return 'functional';
  if (/폴리|나일론|스판/i.test(names)) return 'poly';
  return 'generic';
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
  { type: 'feature_icons', label: '특징 포인트 3종', desc: '핵심 장점 아이콘 카드', tier: 'boost', recommend: 'women' },
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
      return { unit: 'cm', columns: schema, rows: [{ label: 'FREE', values }], note: '단위: cm · 측정 위치에 따라 1~3cm 오차가 있을 수 있어요', withDiagram: false };
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
    case 'feature_icons':
      return { items: (ctx.sellingPoints || []).slice(0, 3).map((p, i) => ({ title: p, desc: '' }))
        .concat(Array.from({ length: Math.max(0, 3 - Math.min(3, (ctx.sellingPoints || []).length)) }, () => ({ title: '', desc: '' }))) };
    case 'fit_guide':
      return { fits: ['slim', 'regular', 'semi_over', 'over'], current: ctx.fit || null };
    case 'size_matrix': {
      const cells = DEFAULT_MATRIX_HEIGHTS.map((_, hi) => DEFAULT_MATRIX_WEIGHTS.map((_w, wi) =>
        DEFAULT_MATRIX_SIZES[Math.min(DEFAULT_MATRIX_SIZES.length - 1, Math.max(0, Math.round((hi + wi) / 2)))]));
      return { heights: [...DEFAULT_MATRIX_HEIGHTS], weights: [...DEFAULT_MATRIX_WEIGHTS], cells,
        note: '체형에 따라 다를 수 있어요 · 상세 실측 치수 확인을 권장드려요' };
    }
    case 'model_info':
      return { models: [{ name: 'MODEL A', height: '', size: '' }] };
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
    els.push(slot(300, bottom + 24, 400, 300));
    bottom += 24 + 300;
  }
  return { id: idFn('b'), name: '사이즈 안내', kind: 'size', auto: true, bg: '#ffffff', h: bottom + 50, info: { ...info }, elements: els };
}

function buildRequiredNotice(info, ctx, idFn) {
  const t = T(idFn); const rule = RULE(idFn);
  const fields = (info.fields || []).length ? info.fields : NOTICE_FIELDS.map((f) => ({ ...f, value: '' }));
  const els = [t(60, 56, 500, 40, '상품 고시정보', { size: 24, ...HEAD })];
  fields.forEach((f, i) => {
    const y = 120 + i * 44;
    const filled = f.value != null && String(f.value).trim() !== '';
    els.push(t(60, y, 220, 20, f.label, { size: 14, color: MUTED }));
    els.push(t(300, y, 640, 20, filled ? String(f.value) : NEEDS_INPUT, { size: 14, color: filled ? '#0e0d14' : FAINT }));
    if (i < fields.length - 1) els.push(rule(60, y + 30, 880));
  });
  const bottom = 120 + fields.length * 44;
  return { id: idFn('b'), name: '상품 고시정보', kind: 'info', infoType: 'required_notice', bg: '#ffffff', h: bottom + 50, info: { fields: fields.map((f) => ({ ...f })) }, elements: els };
}

function buildCare(info, ctx, idFn) {
  const t = T(idFn);
  let text = String(info.text || '').trim();
  if (!text) text = defaultInfoFor('care', ctx).text;
  if (!text.includes('케어라벨')) text = `${text}\n${CARE_LABEL_SENTENCE}`;
  const lines = textBlockLines(text);
  const bodyH = Math.max(26, lines.length * 26);
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
    const bodyH = Math.max(22, lines.length * 22);
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

function buildFeatureIcons(info, ctx, idFn) {
  const t = T(idFn); const circle = CIRCLE(idFn);
  const items = (info.items || []).filter((it) => it.title || it.desc);
  const list = items.length ? items : [{ title: '핵심 장점을 입력하세요', desc: '' }];
  const n = Math.min(3, list.length);
  const colW = 880 / n;
  const els = [];
  list.slice(0, 3).forEach((it, i) => {
    const x = 60 + i * colW;
    els.push(circle(x + colW / 2 - 36, 56, 72, '#f5f5f5'));
    els.push(t(x, 144, colW, 18, `POINT ${i + 1}`, { font: 'Roboto Mono', size: 11, tracking: 2, color: FAINT, align: 'center' }));
    els.push(t(x + 10, 170, colW - 20, 24, it.title || '—', { size: 17, weight: 600, color: '#0e0d14', align: 'center' }));
    if (it.desc) els.push(t(x + 14, 200, colW - 28, 40, it.desc, { size: 13, color: MUTED, align: 'center', lineHeight: 19 }));
  });
  return { id: idFn('b'), name: '특징 포인트', kind: 'info', infoType: 'benefit_copy', bg: '#ffffff', h: 300, info: { items: list.map((it) => ({ ...it })) }, elements: els };
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
  const models = (info.models || []).filter((m) => m.name || m.height || m.size);
  const list = models.length ? models : [{ name: 'MODEL A', height: '', size: '' }];
  const n = Math.min(3, list.length);
  const gap = 16; const colW = (880 - (n - 1) * gap) / n;
  const els = [t(60, 56, 880, 24, 'MODEL INFO', { font: 'Roboto Mono', size: 15, tracking: 3, weight: 600, color: '#0e0d14', align: 'center' })];
  list.slice(0, 3).forEach((m, i) => {
    const x = 60 + i * (colW + gap);
    els.push(rect(x, 110, colW, 130, '#f5f5f5', 12));
    els.push(t(x, 136, colW, 22, m.name || `MODEL ${i + 1}`, { size: 15, weight: 600, align: 'center', color: '#0e0d14' }));
    const spec = [m.height, m.size ? `${m.size} 착용` : ''].filter(Boolean).join(' · ');
    els.push(t(x + 10, 166, colW - 20, 44, spec || '스펙을 입력하세요', { size: 13, align: 'center', color: MUTED, lineHeight: 20 }));
  });
  return { id: idFn('b'), name: '모델 정보', kind: 'info', infoType: 'model_info', bg: '#ffffff', h: 110 + 130 + 56, info: { models: list.map((m) => ({ ...m })) }, elements: els };
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

/* ---- 정보 템플릿 — 작은 토글(브랜드형/소호형) + 일괄 삽입.
   컷 블록은 절대 건드리지 않는다. size/care 는 제자리 강화(교체), 이미 있는
   infoType 은 중복 삽입 대신 스킵. 상단(top) 항목은 문서 맨 앞에 순서대로. ---- */
export const INFO_TEMPLATES = {
  soho: { label: '소호형', top: ['policy', 'header'], flow: ['feature_icons', 'model_info', 'size_table', 'care'] },
  brand: { label: '브랜드형', top: ['header'], flow: ['fit_guide', 'size_table', 'size_matrix', 'care', 'required_notice'] },
};

export function templateStyleFor(targetGenders) {
  const g = targetGenders || [];
  return g.length && g.every((x) => x === 'men') ? 'brand' : 'soho';
}

export function applyInfoTemplate(blocks, styleKey, ctx = {}, idFn = uid) {
  const tpl = INFO_TEMPLATES[styleKey];
  if (!tpl) throw new Error(`[infoPresets] unknown template style: ${styleKey}`);
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
      const built = buildInfoBlock(type, defaultInfoFor(type, ctx), ctx, idFn);
      if (idx >= 0) {
        if (pending.length) { next = insertAt(next, idx, pending); idx += pending.length; pending = []; }
        next = next.map((b, i) => (i === idx ? built : b));
        cursor = idx + 1;
      } else {
        // 앵커가 없는 문서 — ai-notice 앞(없으면 끝)을 기준점으로 흐름을 이어간다.
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
