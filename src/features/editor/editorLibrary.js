/* =============================================================
   features/editor/editorLibrary.js — 상세페이지 반복 작업용 로컬 라이브러리.
   DOM/React와 무관한 순수 정의만 두어 클릭 추가와 drag 추가가 같은 결과를 쓴다.
   ============================================================= */

export const WARDROBE_IMAGE_MIME = 'application/x-wearless-image';
export const DEFAULT_BUBBLE_STROKE = '#b9b9be';
export const DEFAULT_BUBBLE_STROKE_WIDTH = 1;
export const DEFAULT_BUBBLE_RADIUS = 45;

const templateExample = (id, variant = null) => (
  `/assets/editor/kiwi-examples/kiwi-${id}${variant ? `-${variant}` : ''}.jpg`
);
const templatePreview = (id) => `/assets/editor/kiwi-templates/kiwi-${id}-preview.jpg`;

const slot = (x, y, w, h, radius = 0) => ({ x, y, w, h, radius });
const naturalSlot = (x, y, w, h, radius = 0, layout = {}) => ({
  ...slot(x, y, w, h, radius),
  imageSizing: 'natural-height',
  ...layout,
});

const templatePhoto = (x, y, w, h, options = {}) => ({
  type: 'image', x, y, w, h, src: options.example || null, frameSlot: true, checkerboard: true,
  radius: options.radius === 'pill' ? Math.round(Math.min(w, h) / 2) : (options.radius || 0),
  ...(options.example ? { exampleImage: true } : {}),
  ...(options.stroke ? { stroke: options.stroke, strokeWidth: options.strokeWidth || 2 } : {}),
  ...(options.dash ? { dash: options.dash } : {}),
  ...(options.rotate ? { rotate: options.rotate } : {}),
  ...(options.fit ? { fit: options.fit } : {}),
  // 자동채움 힌트(content_role). 삽입 시 templates/autofill 이 역할 맞는 생성컷을 채운다.
  ...(options.role ? { roleHint: options.role } : {}),
});
const templateText = (x, y, w, h, value, style = {}) => ({
  type: 'text', x, y, w, h, text: value, fullTextHitArea: true,
  ...(style.rotate ? { rotate: style.rotate } : {}),
  style: {
    font: style.font || 'Pretendard', size: style.size || 28, weight: style.weight || 400,
    color: style.color || '#0e0d14', lineHeight: style.lineHeight || Math.round((style.size || 28) * 1.4),
    align: style.align || 'left', tracking: style.tracking || 0,
    ...(style.strike ? { strike: true } : {}),
    ...(style.underline ? { underline: true } : {}),
    ...(style.italic ? { italic: true } : {}),
  },
});
const templateBubble = (x, y, w, h, value, options = {}) => ({
  ...templateText(x, y, w, h, value, options.style || {}),
  shape: 'bubble', fill: options.fill || '#ffffff', stroke: options.stroke || '#777777',
  strokeWidth: options.strokeWidth || 2, dash: options.dash || 'dashed', radius: options.radius || 32,
  bubbleFit: options.bubbleFit || { maxWidth: w, padX: 30, padTop: 24, padBottom: 44, anchor: 'left' },
  ...(options.flipX ? { flipX: true } : {}),
});
const templateShape = (x, y, w, h, fill, options = {}) => ({
  type: 'shape', shape: options.shape || 'rect', x, y, w, h, fill,
  radius: options.radius || 0, opacity: options.opacity ?? 1,
  ...(options.stroke ? { stroke: options.stroke, strokeWidth: options.strokeWidth || 2 } : {}),
  ...(options.dash ? { dash: options.dash } : {}),
  ...(options.rotate ? { rotate: options.rotate } : {}),
});
const templateLine = (x, y, w, options = {}) => ({
  type: 'line', shape: options.shape || 'line', x, y, w, h: 24,
  stroke: options.stroke || '#0e0d14', strokeWidth: options.strokeWidth || 2,
  dash: options.dash || 'solid', ...(options.rotate ? { rotate: options.rotate } : {}),
});
const kiwiTemplate = ({ id, label, h, bg, elements, preview }) => ({
  id: `kiwi-${id}`,
  label,
  recommended: false,
  template: true,
  bg,
  h,
  elements,
  slots: elements.filter((element) => element.type === 'image' && element.frameSlot),
  // The supplied artwork is a catalog thumbnail only. Inserted blocks are native elements.
  // Catalog cards show the completed JPEG reference. The block itself still
  // inserts the native editable elements and replaceable photo slots above.
  preview: preview || templatePreview(id),
});

const imageDescriptionSlots = [70, 365, 660].map((x, index) => ({
  ...templatePhoto(x, 175, 270, 220),
  imageSizing: 'natural-height',
  imageFlowGroup: `image-description-${index + 1}`,
  imageFlowGap: 20,
}));

export const FRAME_LIBRARY_ITEMS = [
  {
    id: 'single', label: '1컷 풀폭', recommended: true, h: 600,
    slots: [naturalSlot(40, 40, 920, 520)],
  },
  /* 사진 배치 프레임은 칸끼리 딱 붙인다(오너 8/16) — 상세페이지는 사진이 이어져 흐르는
     화면이라, 칸 사이 20px 틈이 있으면 한 장면이 조각난 것처럼 보인다. 좌우 40px 여백은
     페이지 여백이라 유지한다(=칸 폭 합은 920). 글이 딸린 템플릿은 텍스트 열과 칸이 짝을
     이루므로 건드리지 않는다. */
  {
    id: 'split2', label: '2분할', recommended: true, h: 580,
    // 단순 2열 프레임은 칸의 가로 폭만 정한다. 사진을 넣으면 원본 비율에 맞는
    // 세로 길이를 계산해, 세로 사진이 짧은 카드 안에서 잘리지 않게 한다.
    slots: [
      naturalSlot(40, 60, 460, 460),
      naturalSlot(500, 60, 460, 460),
    ],
  },
  {
    id: 'grid3', label: '3컷 구성', recommended: true, h: 580,
    slots: [naturalSlot(40, 60, 307, 460), naturalSlot(347, 60, 306, 460), naturalSlot(653, 60, 307, 460)],
  },
  {
    id: 'grid4', label: '2 × 2', recommended: true, h: 640,
    slots: [
      naturalSlot(40, 40, 460, 270, 0, { imageRowFlowGroup: 'grid4', imageRowFlowRow: 0, imageRowFlowGap: 0 }),
      naturalSlot(500, 40, 460, 270, 0, { imageRowFlowGroup: 'grid4', imageRowFlowRow: 0, imageRowFlowGap: 0 }),
      naturalSlot(40, 310, 460, 270, 0, { imageRowFlowGroup: 'grid4', imageRowFlowRow: 1, imageRowFlowGap: 0 }),
      naturalSlot(500, 310, 460, 270, 0, { imageRowFlowGroup: 'grid4', imageRowFlowRow: 1, imageRowFlowGap: 0 }),
    ],
  },
  {
    id: 'hero2', label: '큰 사진 + 2장', recommended: true, h: 600,
    slots: [
      naturalSlot(40, 50, 600, 500),
      naturalSlot(640, 50, 320, 250, 0, { imageRowFlowGroup: 'hero2-side', imageRowFlowRow: 0, imageRowFlowGap: 0 }),
      naturalSlot(640, 300, 320, 250, 0, { imageRowFlowGroup: 'hero2-side', imageRowFlowRow: 1, imageRowFlowGap: 0 }),
    ],
  },
  {
    id: 'colorcmp', label: '컬러 비교', recommended: true, h: 580,
    slots: [naturalSlot(40, 60, 307, 460), naturalSlot(347, 60, 306, 460), naturalSlot(653, 60, 307, 460)],
  },
  {
    id: 'ba', label: 'Before / After', recommended: false, h: 580,
    slots: [naturalSlot(40, 60, 460, 460), naturalSlot(500, 60, 460, 460)],
  },
  {
    id: 'image-description-3', label: '이미지 설명 3단', recommended: false, h: 570, bg: '#ffffff',
    slots: imageDescriptionSlots,
    elements: [
      templateText(70, 70, 860, 48, '이미지와 설명', { size: 30, weight: 700 }),
      ...imageDescriptionSlots,
      ...[
        [70, '첫 번째 포인트', '첫 번째 특징을 이해하기 쉽게 설명해 주세요.'],
        [365, '두 번째 포인트', '두 번째 특징과 고객이 얻는 장점을 적어주세요.'],
        [660, '세 번째 포인트', '마지막 특징이나 활용 방법을 간결하게 적어주세요.'],
      ].flatMap(([x, title, description], index) => [
        { ...templateText(x, 415, 270, 34, title, { size: 21, weight: 700 }), imageFlowGroup: `image-description-${index + 1}`, imageFlowOffset: 0 },
        { ...templateText(x, 458, 270, 68, description, { size: 16, lineHeight: 23 }), imageFlowGroup: `image-description-${index + 1}`, imageFlowOffset: 43 },
      ]),
    ],
  },
  kiwiTemplate({
    id: '1', label: '리뷰 카드', h: 1460, bg: '#f3f1ee', elements: [
      templateText(300, 150, 400, 48, "Our Brand's", { font: 'Cormorant', size: 36, align: 'center' }),
      templateText(220, 235, 560, 110, 'Reviews', { font: 'Cormorant', size: 88, weight: 700, align: 'center' }),
      templateText(150, 390, 700, 90, '편안한 착용감과 감각적인 실루엣을\n직접 경험한 고객 후기를 소개합니다.', { size: 30, color: '#666666', lineHeight: 42, align: 'center' }),
      ...[
        [82, 570, -2, '가볍고 시원해서 한여름에도\n답답함 없이 잘 입고 있어요.'],
        [525, 570, 2, '허리 밴딩이 편안해서\n데일리룩으로 딱이에요.'],
        [82, 955, 2, '체형에 자연스럽게 어울리고\n편하게 움직일 수 있어요.'],
        [525, 955, -2, '세탁 후에도 변형이 적어서\n관리하기 쉬웠어요.'],
      ].flatMap(([x, y, rotate, copy], index) => [
        templateShape(x, y, 390, 320, '#ffffff', { radius: 8, rotate }),
        templatePhoto(x + 48, y - 36, 126, 126, { radius: 'pill', example: templateExample(1, index ? index + 1 : null) }),
        templateText(x + 48, y + 125, 300, 130, copy, { size: 23, lineHeight: 35 }),
      ]),
    ],
  }),
  kiwiTemplate({
    id: '2', label: '룩북 콜라주', h: 1498, bg: '#f4f2ef', elements: [
      templateText(300, 125, 400, 48, "Our Brand's", { font: 'Cormorant', size: 36, align: 'center' }),
      templateText(220, 220, 560, 115, '#Look01', { font: 'Cormorant', size: 88, weight: 700, align: 'center' }),
      templateText(135, 370, 730, 92, '하루를 특별하게 만드는 데일리 스타일을\n한 장의 룩북으로 만나보세요.', { size: 30, color: '#666666', lineHeight: 42, align: 'center' }),
      templateShape(164, 540, 500, 650, '#ffffff', { radius: 6 }),
      templatePhoto(180, 563, 466, 609, { example: templateExample(2) }),
      templateShape(533, 625, 200, 180, '#f57d9c', { radius: 4, rotate: 2 }),
      templateText(560, 665, 150, 95, 'Daily\nLook', { font: 'Cormorant', size: 36, lineHeight: 43, align: 'center' }),
      templatePhoto(0, 1010, 258, 488, { fit: 'contain', example: templateExample(2, 2) }),
      templatePhoto(258, 1010, 364, 488, { fit: 'contain', example: templateExample(2, 3) }),
      templatePhoto(622, 850, 378, 648, { fit: 'contain', example: templateExample(2, 4) }),
    ],
  }),
  kiwiTemplate({
    id: '3', label: '스타일 노트', h: 1580, bg: '#f6fbff', elements: [
      templateText(300, 80, 400, 52, "Our Brand's", { font: 'Cormorant', size: 36, align: 'center' }),
      templateText(180, 170, 640, 110, 'Style Note', { font: 'Cormorant', size: 86, weight: 700, align: 'center' }),
      templateText(175, 320, 650, 55, '하루를 특별하게 만들어주는 스타일링', { size: 30, color: '#666666', align: 'center' }),
      templateShape(145, 460, 725, 910, '#ffffff', { radius: 4, rotate: -2 }),
      templatePhoto(166, 489, 683, 851, { example: templateExample(3) }),
      templateShape(744, 650, 205, 185, '#eafa72', { radius: 3, rotate: 1 }),
      templateText(775, 705, 150, 90, 'Daily\nLook', { font: 'Cormorant', size: 35, lineHeight: 42, align: 'center' }),
      templateShape(76, 1168, 388, 215, '#2a1c12', { radius: 4, rotate: -7 }),
      templatePhoto(103, 1186, 346, 174, { rotate: -7, example: templateExample(3, 2) }),
    ],
  }),
  kiwiTemplate({
    id: '4', label: '썸머 이벤트', h: 1610, bg: '#f4f4f4', elements: [
      templateText(300, 12, 400, 45, 'SUMMER', { size: 26, weight: 500, tracking: 5, align: 'center' }),
      templateText(235, 54, 530, 95, 'EVENT', { font: 'Cormorant', size: 82, weight: 700, align: 'center' }),
      templateShape(90, 225, 780, 1020, '#ffffff', { radius: 38 }),
      templatePhoto(125, 266, 85, 85, { radius: 'pill', example: templateExample(4, 'profile') }),
      templateText(225, 280, 260, 45, '브랜드 이름', { size: 28, weight: 700 }),
      templatePhoto(127, 371, 744, 736, { example: templateExample(4) }),
      templatePhoto(597, 905, 323, 325, { example: templateExample(4, 2) }),
      templateText(125, 1130, 500, 45, '♥  ○  △', { size: 34 }),
      templateText(125, 1190, 700, 45, '#여름패션  #퀄리티보장', { size: 26 }),
      templateText(145, 1345, 710, 70, '상품 리뷰 추첨 이벤트!', { size: 46, weight: 700, align: 'center' }),
      templateText(190, 1430, 620, 45, '07.07(MON) ~ 07.13(SUN)', { size: 24, color: '#999999', align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: '5', label: '컬러 비교 카드', h: 1407, bg: '#eef8fb', elements: [
      templateText(300, 90, 400, 85, 'COLOR', { size: 54, weight: 700, tracking: 2, align: 'center' }),
      ...['#ffffff', '#328cca', '#3c4f8b', '#111111'].map((fill, index) => templateShape(385 + index * 62, 190, 50, 50, fill, { shape: 'circle', stroke: '#dddddd', strokeWidth: 1 })),
      ...[
        [100, 310, 'WHITE'], [525, 310, 'BEIGE'], [100, 797, 'KHAKI'], [525, 797, 'BLACK'],
      ].flatMap(([x, y, label], index) => [
        templateShape(x, y, 375, 438, '#ffffff', { radius: 4 }),
        templatePhoto(x + 24, y + 28, 328, 328, { fit: 'contain', example: templateExample(5, index ? index + 1 : null) }),
        templateText(x + 40, y + 382, 295, 40, label, { size: 28, align: 'center' }),
      ]),
    ],
  }),
  kiwiTemplate({
    id: '6', label: '체크 포인트 리스트', h: 1400, bg: '#f3f3f3', preview: templatePreview(6), elements: [
      templateText(78, 112, 620, 78, 'CHECK POINT', { size: 60, weight: 700 }),
      templateText(80, 220, 760, 86, '브랜드와 상품의 핵심 포인트를\n최대 2줄로 소개해 주세요.', { size: 28, lineHeight: 40 }),
      ...[382, 700, 1018].flatMap((y, index) => [
        templateShape(68, y, 864, 277, '#ffffff'),
        templatePhoto(68, y, 277, 277, { example: templateExample(6) }),
        templateShape(80, y + 14, 38, 38, '#1ce765', { radius: 5, stroke: '#111111', strokeWidth: 2 }),
        templateText(85, y + 13, 28, 32, '✓', { size: 25, weight: 700, align: 'center' }),
        templateText(400, y + 50, 465, 165,
          index === 0 ? '가볍고 편안한 소재로\n매일 손이 가는 아이템입니다.'
            : index === 1 ? '섬세한 디테일과 안정적인 핏으로\n완성도를 높였습니다.'
              : '관리하기 쉬운 소재와 실용적인 구성으로\n오래 입을 수 있습니다.',
          { size: 32, lineHeight: 48 }),
      ]),
    ],
  }),
  kiwiTemplate({
    id: '9', label: '배송·교환·반품 안내', h: 1940, bg: '#fdf8ef', preview: templatePreview(9), elements: [
      templateText(390, 70, 220, 60, 'Info', { font: 'Cormorant', size: 46, weight: 600, color: '#7abbd1', align: 'center' }),
      ...[
        [50, 220, 950, 455, '결제 및 입금', '• 주문자명과 실제 입금자명을 확인해 주세요.\n• 주문 후 7일 이내 입금이 완료되어야 합니다.\n• 카드 결제 취소는 고객센터로 문의해 주세요.'],
        [50, 720, 950, 500, '배송 및 배송비', '• 기본 배송사를 적어주세요.\n• 무료배송 기준과 기본 배송비를 안내해 주세요.\n• 상품 준비 및 배송 기간을 적어주세요.\n• 지연 시 안내 방법을 적어주세요.'],
        [50, 1265, 950, 370, '교환 및 반품', '• 교환 및 반품 가능 기준을 적어주세요.\n• 상품 훼손 및 택 제거 시 제한 사항을 안내해 주세요.\n• 세탁과 수선 이후의 정책을 적어주세요.'],
        [50, 1680, 950, 215, '고객문의', '02 - 1234 - 1234\n평일 10:00 ~ 17:00 / 점심 12:00 ~ 13:00'],
      ].flatMap(([x, y, w, h, title, copy]) => [
        templateShape(x, y, w, h, '#ffffff'),
        templateText(x + 52, y + 45, w - 104, 52, title, { size: 31, weight: 700 }),
        templateText(x + 52, y + 115, w - 104, h - 145, copy, { size: 24, color: '#55505a', lineHeight: 42 }),
      ]),
    ],
  }),
  kiwiTemplate({
    id: '10', label: 'SNS 상품 카드', h: 1510, bg: '#d5d5d5', elements: [
      templatePhoto(0, 0, 1000, 1510, { example: templateExample(10) }),
      templateShape(0, 0, 1000, 285, '#111111', { opacity: 0.42 }),
      templateText(320, 45, 360, 58, 'Product Name', { font: 'Cormorant', size: 46, weight: 700, color: '#ffffff', align: 'center' }),
      templateText(360, 120, 280, 80, 'Information\nInformation', { size: 26, color: '#ffffff', lineHeight: 38, align: 'center' }),
      templateShape(140, 284, 720, 1120, '#ffffff', { radius: 42 }),
      templatePhoto(176, 323, 103, 103, { radius: 'pill' }),
      templateText(310, 343, 360, 40, 'Product Name', { size: 27, weight: 700 }),
      templateText(310, 386, 360, 35, 'Product Information', { size: 22, color: '#aaaaaa' }),
      templateText(765, 354, 55, 30, '•••', { size: 30, weight: 700, align: 'center' }),
      templatePhoto(175, 444, 680, 710, { example: templateExample(10) }),
      templateText(175, 1185, 650, 40, '♥   ○   △                                      ▮', { size: 30 }),
      templateText(175, 1240, 650, 38, '2,860,606 likes.', { size: 24, weight: 600 }),
      templateText(175, 1280, 650, 70, 'Product Name  데일리하게 입기 좋은 아이템!', { size: 23, lineHeight: 34 }),
      templateText(175, 1350, 650, 42, '#베이직아이템  #니트  #데일리룩', { size: 24, weight: 700 }),
    ],
  }),
  kiwiTemplate({
    id: '11', label: '체크 포인트', h: 1248, bg: '#dcc4be', elements: [
      templateText(275, 45, 450, 60, 'CHECK POINT', { font: 'Cormorant', size: 48, weight: 700, color: '#ffffff', align: 'center' }),
      templatePhoto(120, 110, 768, 1018, { radius: 'pill' }),
      templateShape(675, 345, 300, 92, '#ffffff', { radius: 24 }),
      templateText(700, 366, 245, 58, '상품의 특징을 2줄 이내로\n적어주세요.', { size: 21, lineHeight: 28 }),
      templateShape(118, 655, 305, 92, '#ffffff', { radius: 24 }),
      templateText(143, 676, 250, 58, '상품의 특징을 2줄 이내로\n적어주세요.', { size: 21, lineHeight: 28 }),
      templateShape(477, 952, 310, 92, '#ffffff', { radius: 24 }),
      templateText(502, 973, 255, 58, '상품의 특징을 2줄 이내로\n적어주세요.', { size: 21, lineHeight: 28 }),
      templateShape(685, 195, 120, 120, 'transparent', { shape: 'circle', stroke: '#ffffff', strokeWidth: 4, opacity: 0.85 }),
      templateShape(300, 505, 120, 120, 'transparent', { shape: 'circle', stroke: '#ffffff', strokeWidth: 4, opacity: 0.85 }),
      templateShape(600, 805, 120, 120, 'transparent', { shape: 'circle', stroke: '#ffffff', strokeWidth: 4, opacity: 0.85 }),
    ],
  }),
  kiwiTemplate({
    id: '12', label: '핫 키워드', h: 1335, bg: '#222224', elements: [
      templateText(55, 35, 390, 100, '지금 가장 떠오르는\n패션 핫 키워드', { size: 30, weight: 500, color: '#ffffff', lineHeight: 42 }),
      templateShape(480, 0, 520, 155, 'transparent', { radius: 260, stroke: '#ffffff', strokeWidth: 1 }),
      templateText(610, 20, 300, 120, 'HOT\nKEYWORD', { size: 47, weight: 700, color: '#ffffff', lineHeight: 55, align: 'center' }),
      templateLine(0, 145, 1000, { stroke: '#dddddd', strokeWidth: 1 }),
      templateText(55, 210, 650, 80, 'KIWI TREND', { size: 66, weight: 700, color: '#ffffff' }),
      templateText(55, 285, 700, 85, 'HOT SUMMER', { size: 66, weight: 700, color: '#70e978' }),
      templatePhoto(55, 435, 428, 651, { radius: 'pill', example: templateExample(12) }),
      templatePhoto(508, 435, 430, 651, { radius: 'pill', example: templateExample(12, 2) }),
      templateText(55, 1185, 300, 75, '23 SS', { size: 70, weight: 700, color: '#ffffff' }),
      templateText(650, 1215, 300, 45, "BRAND'S PICK", { size: 34, color: '#ffffff', align: 'right' }),
    ],
  }),
  kiwiTemplate({
    id: '13', label: '빅 세일 이벤트', h: 1647, bg: '#fbf9e9', elements: [
      templateText(285, 205, 430, 62, 'Summer', { font: 'Cormorant', size: 52, weight: 600, color: '#45744b', align: 'center' }),
      templateText(150, 270, 700, 180, 'BIG SALE\nEVENT', { font: 'Cormorant', size: 92, weight: 700, color: '#45744b', lineHeight: 95, align: 'center' }),
      templatePhoto(98, 425, 814, 890, { radius: 'pill', example: templateExample(13) }),
      templateShape(0, 1290, 1000, 357, '#9be5df', { opacity: 0.9 }),
      templateText(195, 1510, 610, 55, '여름 시즌 특별 할인 이벤트', { size: 34, weight: 600, color: '#356b54', align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: '14', label: '패션 폴라로이드', h: 1508, bg: '#222222', elements: [
      templatePhoto(0, 0, 1000, 1508, { example: templateExample(14, 'bg') }),
      templateShape(615, 120, 310, 350, '#ffffff', { radius: 3, rotate: 20 }),
      templatePhoto(645, 155, 260, 278, { rotate: 20, example: templateExample(14) }),
      templateShape(535, 510, 335, 430, '#ffffff', { radius: 3, rotate: -14 }),
      templatePhoto(575, 550, 270, 365, { rotate: -14, example: templateExample(14, 2) }),
      templateShape(685, 980, 285, 380, '#ffffff', { radius: 3, rotate: 14 }),
      templatePhoto(725, 1020, 232, 324, { rotate: 14, example: templateExample(14, 3) }),
      templateText(65, 70, 540, 80, 'Brand Fashion', { size: 58, weight: 700, color: '#ffffff' }),
      templateText(55, 1030, 470, 250, 'Modern\nChic', { font: 'Cormorant', size: 92, weight: 700, color: '#ffffff', lineHeight: 90 }),
    ],
  }),
  kiwiTemplate({
    id: '15', label: '디테일 콜아웃', h: 1336, bg: '#eeeeee', elements: [
      templatePhoto(0, 0, 1000, 1336),
      templateText(55, 45, 430, 72, 'CHECK POINT', { font: 'Cormorant', size: 48, weight: 700, color: '#ffffff' }),
      templatePhoto(150, 265, 245, 245, { radius: 'pill', stroke: '#ffffff', strokeWidth: 5, dash: 'dashed', example: templateExample(15) }),
      templateShape(405, 366, 375, 66, '#fff7f4', { radius: 2 }),
      templateText(425, 382, 335, 42, '어깨라인이 살아있는 디자인', { size: 24, align: 'center' }),
      templatePhoto(520, 750, 315, 315, { radius: 'pill', stroke: '#ffffff', strokeWidth: 5, dash: 'dashed', example: templateExample(15, 2) }),
      templateShape(485, 1085, 365, 66, '#fff7f4', { radius: 2 }),
      templateText(505, 1101, 325, 42, '허리라인 위로 핀턱 디테일', { size: 24, align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: '16', label: '고객 안내문', h: 982, bg: '#eeeeee', preview: templatePreview(16), elements: [
      templateShape(0, 692, 1000, 290, '#222222'),
      templateShape(88, 72, 824, 798, '#ffffff', { stroke: '#222222', strokeWidth: 1 }),
      templateShape(745, 116, 126, 126, '#d8d8d8', { shape: 'circle' }),
      templateText(766, 163, 84, 38, 'logo', { font: 'Cormorant', size: 26, weight: 700, color: '#ffffff', align: 'center' }),
      templateText(420, 168, 160, 115, '!', { size: 90, weight: 700, align: 'center' }),
      templateLine(205, 353, 590, { stroke: '#c9c0bd', strokeWidth: 1 }),
      templateText(260, 420, 480, 55, '안녕하세요. 브랜드 이름입니다.', { size: 28, color: '#49454a', align: 'center' }),
      templateText(250, 505, 500, 118, '현재 문의량이 많아\n실시간 상담과 전화 연결이\n다소 어렵습니다.', { size: 27, color: '#49454a', lineHeight: 40, align: 'center' }),
      templateText(220, 660, 560, 122, 'Q&A 게시판을 이용해주시면\n더욱 빠른 답변을 도와드리겠습니다.\n이용에 불편을 드려 죄송합니다.', { size: 27, color: '#49454a', lineHeight: 40, align: 'center' }),
      ...Array.from({ length: 15 }, (_, index) => templateLine(index * 72 - 20, 930, 95, { stroke: '#ffffff', strokeWidth: 4, rotate: -45 })),
    ],
  }),
  kiwiTemplate({
    id: '17', label: '픽셀 할인 쿠폰', h: 782, bg: '#fff51d', preview: templatePreview(17), elements: [
      templateText(290, 132, 420, 54, '구매고객 전용', { size: 42, align: 'center' }),
      templateText(210, 195, 580, 82, '10% 할인 쿠폰', { size: 64, weight: 700, align: 'center' }),
      templateShape(220, 307, 570, 313, '#000000'),
      templateShape(182, 337, 638, 82, '#000000'),
      templateShape(205, 419, 590, 51, '#000000'),
      templateShape(182, 470, 638, 124, '#000000'),
      templateText(305, 395, 310, 130, '30', { font: 'Roboto Mono', size: 120, weight: 600, color: '#ffffff', align: 'center' }),
      templateText(590, 438, 105, 82, '%', { font: 'Roboto Mono', size: 68, weight: 600, color: '#ffffff', align: 'center' }),
      templateShape(695, 470, 190, 190, '#fff51d', { shape: 'circle', stroke: '#000000', strokeWidth: 7 }),
      templateText(735, 512, 110, 108, 'P', { font: 'Roboto Mono', size: 92, weight: 600, align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: '18', label: '플라워 쿠폰', h: 1090, bg: '#fff0eb', preview: templatePreview(18), elements: [
      ...[
        [85, 50, 170], [225, 35, 110], [795, 48, 140], [55, 835, 130], [825, 850, 150],
      ].flatMap(([x, y, size]) => [
        templateShape(x, y, size, size, '#f8b8b9', { shape: 'circle', opacity: 0.55 }),
        templateShape(x + size * 0.25, y + size * 0.25, size * 0.5, size * 0.5, '#fff0eb', { shape: 'circle', opacity: 0.8 }),
      ]),
      templateShape(275, 160, 420, 80, '#b4a48b'),
      templateText(315, 183, 340, 42, '즉시 사용 가능한', { size: 34, weight: 600, color: '#ffffff', align: 'center' }),
      templateText(220, 260, 560, 100, 'COUPON', { size: 86, weight: 700, color: '#4a2831', align: 'center' }),
      templateShape(82, 432, 664, 370, '#e76e96', { radius: 12 }),
      templateShape(744, 432, 174, 370, '#e76e96', { radius: 42 }),
      templateLine(744, 475, 300, { stroke: '#ffffff', strokeWidth: 2, rotate: 90 }),
      templateText(170, 500, 500, 60, '한꺼번에 다운받기', { size: 43, color: '#ffffff', align: 'center' }),
      templateText(190, 585, 450, 120, '~20%', { size: 112, weight: 600, color: '#ffffff', align: 'center' }),
      templateText(775, 560, 110, 100, '↓', { size: 82, color: '#ffffff', align: 'center' }),
      templateText(225, 900, 550, 58, '사용기한 : 2026.12.31', { size: 39, align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: '19', label: '리뷰 말풍선', h: 1241, bg: '#f7f7f7', preview: templatePreview(19), elements: [
      templateText(285, 65, 430, 55, 'REVIEWS', { size: 45, weight: 700, color: '#777777', align: 'center' }),
      templateText(340, 145, 320, 70, '4.8 / 5', { size: 62, weight: 700, align: 'center' }),
      templateText(200, 242, 600, 84, '누적고객 00명, 수많은 후기가 증명하는\n브랜드 리뷰', { size: 31, lineHeight: 42, align: 'center' }),
      templateBubble(325, 355, 585, 245, '★★★★★\n상품의 만족도와 착용감을\n간결하게 적어주세요.', {
        style: { size: 24, color: '#666666', lineHeight: 38 }, fill: '#ffffff', radius: 26,
      }),
      templateBubble(75, 600, 585, 245, '★★★★★\n실제 고객의 후기를\n3줄 이내로 적어주세요.', {
        style: { size: 24, color: '#666666', lineHeight: 38 }, fill: '#ffffff', radius: 26, flipX: true,
      }),
      templateBubble(360, 845, 555, 210, '★★★★★\n제품의 장점을 담은\n리뷰를 적어주세요.', {
        style: { size: 24, color: '#666666', lineHeight: 38 }, fill: '#ffffff', radius: 26,
      }),
      templateText(460, 1125, 80, 65, '•••', { size: 44, weight: 700, color: '#bbbbbb', align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: '20', label: '신규회원 쿠폰', h: 1100, bg: '#f5ead1', preview: templatePreview(20), elements: [
      templateText(265, 108, 470, 58, '신규 회원가입시', { size: 42, color: '#c97838', align: 'center' }),
      templateText(235, 195, 530, 82, '쿠폰팩 지급!', { size: 64, weight: 700, color: '#c97838', align: 'center' }),
      ...[0, 18, 36, 54].map((offset) => templateShape(75 + offset, 380 - offset * 0.45, 735, 360, '#ffffff', { stroke: '#2b2020', strokeWidth: 4 })),
      templateText(300, 505, 400, 45, 'C O U P O N', { size: 28, tracking: 8, align: 'center' }),
      templateText(235, 585, 530, 135, '20%', { size: 126, weight: 700, align: 'center' }),
      templateText(110, 945, 780, 55, '회원님께 제공해드리는 다양한 혜택을 받아보세요!', { size: 30, color: '#777777', align: 'center' }),
    ],
  }),
];

/* =============================================================
   상세페이지 "한 벌" 템플릿 — Figma "각종 프레임들"(node 52:2) 13프레임 변환.
   매핑: Figma 860폭 → 1000캔버스 수평 ×1.163(폭 채움), y/폰트/높이 그대로.
   사진 가능한 자리는 전부 checkerboard slot(templatePhoto). 모든 요소 편집가능.
   buildDetailPageTemplateBlocks 로 13개 블록을 순서대로 만들어 문서에 삽입한다.
   ============================================================= */
export const DETAIL_PAGE_TEMPLATE = [
  kiwiTemplate({
    id: 'dp-01-hero', label: '01 Hero', h: 1100, bg: '#d9d9da', elements: [
      templateText(70, 144, 860, 80, '겨울을 부드럽게', { size: 64, weight: 700, color: '#262628', lineHeight: 80 }),
      templateText(70, 224, 860, 80, '골지 니트', { size: 64, weight: 700, color: '#262628', lineHeight: 80 }),
      templatePhoto(-17, 359, 98, 520, { role: 'hero', radius: 20 }),
      templatePhoto(94, 359, 804, 520, { role: 'hero', radius: 20 }),
      templatePhoto(912, 359, 97, 520, { role: 'hero', radius: 20 }),
      templateText(70, 980, 860, 40, '하루종일 편안한 데일리 니트', { size: 28, color: '#4a4a4c', align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-02-detailcheck', label: '02 Detail Check', h: 1100, bg: '#e9e9eb', elements: [
      templateText(58, 123, 884, 68, 'Detail Check', { size: 56, weight: 700, color: '#262628' }),
      templateText(58, 203, 884, 24, '이 니트가 특별한 이유, 하나씩 짚어 드릴게요.', { size: 18, weight: 600, color: '#5a5a5c' }),
      templateShape(58, 269, 884, 220, '#fafafa', { radius: 16 }),
      templatePhoto(58, 269, 337, 220, { role: 'detail', radius: 16 }),
      templateText(444, 329, 449, 34, '부드러운 촉감', { size: 25, weight: 600, color: '#262628' }),
      templateText(444, 373, 449, 60, '코튼 혼방으로 자연스럽게 떨어지는 결, 피부에 닿는 감촉이 부담 없어요.', { size: 17, color: '#6b6b70', lineHeight: 28 }),
      templateShape(58, 513, 884, 220, '#fafafa', { radius: 16 }),
      templateText(107, 573, 449, 34, '골지 짜임', { size: 25, weight: 600, color: '#262628' }),
      templateText(107, 617, 449, 60, '촘촘한 골지가 몸선을 따라 떨어져 쉽게 부해 보이지 않아요.', { size: 17, color: '#6b6b70', lineHeight: 28 }),
      templatePhoto(605, 513, 337, 220, { role: 'detail', radius: 16 }),
      templateShape(58, 757, 884, 220, '#fafafa', { radius: 16 }),
      templatePhoto(58, 757, 337, 220, { role: 'detail', radius: 16 }),
      templateText(444, 817, 449, 34, '라운드넥', { size: 25, weight: 600, color: '#262628' }),
      templateText(444, 861, 449, 60, '목선이 편안한 라운드넥이라 이너로도 겉옷 안에도 잘 어울려요.', { size: 17, color: '#6b6b70', lineHeight: 28 }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-03-detailcheck-d', label: '03 Detail Check :D', h: 1100, bg: '#e4e4e4', elements: [
      templateText(58, 201, 700, 106, 'Detail', { size: 96, weight: 700, color: '#262628', lineHeight: 106 }),
      templateText(58, 307, 900, 74, 'Check :D', { size: 96, weight: 700, color: '#262628', lineHeight: 74 }),
      templateShape(819, 187, 38, 9, '#6fb4e8'),
      templateShape(819, 196, 86, 59, '#8cc8f5', { radius: 8 }),
      templateText(797, 261, 130, 20, 'Summer', { size: 14, color: '#3a3a3c', align: 'center' }),
      templateShape(645, 286, 38, 9, '#6fb4e8'),
      templateShape(645, 295, 86, 59, '#8cc8f5', { radius: 8 }),
      templateText(623, 360, 130, 20, 'Fashion', { size: 14, color: '#3a3a3c', align: 'center' }),
      templateShape(58, 526, 884, 574, '#ffffff', { radius: 10 }),
      templateShape(78, 543, 13, 13, '#f0685f', { radius: 7 }),
      templateShape(100, 543, 13, 13, '#f5bf4f', { radius: 7 }),
      templateShape(122, 543, 13, 13, '#61c554', { radius: 7 }),
      templateLine(58, 572, 884, { stroke: '#eaeaec', strokeWidth: 1 }),
      templateShape(98, 609, 5, 28, '#c4a24a', { radius: 2 }),
      templateText(115, 607, 200, 34, 'Point 01', { size: 27, weight: 700, color: '#c4a24a' }),
      templateText(98, 658, 500, 22, '강조 포인트를 살린 카피가 들어가는 자리예요.', { size: 16, color: '#7a7a80' }),
      templateText(98, 683, 500, 22, '마음에 안 들면 그 자리에서 고쳐 쓰면 돼요.', { size: 16, color: '#7a7a80' }),
      templatePhoto(98, 730, 395, 330, { role: 'detail', radius: 10 }),
      templatePhoto(507, 730, 395, 330, { role: 'detail', radius: 10 }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-04-point-02-03', label: '04 Point 02-03', h: 1100, bg: '#e4e4e4', elements: [
      templateShape(35, 41, 930, 1018, '#ffffff'),
      templateShape(85, 91, 5, 28, '#c4a24a', { radius: 2 }),
      templateText(102, 89, 200, 34, 'Point 02', { size: 27, weight: 700, color: '#c4a24a' }),
      templateText(85, 140, 500, 22, '강조 포인트를 살린 카피가 들어가는 자리예요.', { size: 16, color: '#7a7a80' }),
      templateText(85, 165, 500, 22, '마음에 안 들면 그 자리에서 고쳐 쓰면 돼요.', { size: 16, color: '#7a7a80' }),
      templatePhoto(85, 212, 830, 330, { role: 'detail', radius: 10 }),
      templateShape(85, 592, 5, 28, '#c4a24a', { radius: 2 }),
      templateText(102, 590, 200, 34, 'Point 03', { size: 27, weight: 700, color: '#c4a24a' }),
      templateText(85, 641, 500, 22, '강조 포인트를 살린 카피가 들어가는 자리예요.', { size: 16, color: '#7a7a80' }),
      templateText(85, 666, 500, 22, '마음에 안 들면 그 자리에서 고쳐 쓰면 돼요.', { size: 16, color: '#7a7a80' }),
      templatePhoto(85, 713, 408, 290, { role: 'detail', radius: 10 }),
      templatePhoto(507, 713, 408, 290, { role: 'detail', radius: 10 }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-05-material', label: '05 Material', h: 1100, bg: '#ededef', elements: [
      templatePhoto(0, 0, 1000, 700, { role: 'hero' }),
      templateShape(714, 350, 210, 206, '#ffffff', { radius: 12 }),
      templatePhoto(720, 356, 198, 154, { role: 'detail', radius: 9 }),
      templateText(714, 520, 210, 20, 'Texture', { size: 15, color: '#5a5a5c', align: 'center' }),
      templateShape(58, 650, 884, 307, '#ffffff', { radius: 12 }),
      templateShape(78, 666, 12, 12, '#f0685f', { radius: 6 }),
      templateShape(99, 666, 12, 12, '#f5bf4f', { radius: 6 }),
      templateShape(120, 666, 12, 12, '#61c554', { radius: 6 }),
      templateShape(98, 732, 34, 34, '#f5c33a', { radius: 17 }),
      templateText(98, 738, 34, 22, '✓', { size: 18, color: '#ffffff', align: 'center' }),
      templateText(146, 730, 500, 44, 'Acrylic 100%', { size: 33, weight: 700, color: '#1f1f21' }),
      templateText(98, 790, 700, 22, '아크릴 100% 소재로 도톰하면서도 가볍게 입을 수 있어요.', { size: 16, color: '#5a5a5c' }),
      templateText(98, 815, 700, 22, '30도 이하 미지근한 물에 단독 세탁을 권장해요.', { size: 16, color: '#5a5a5c' }),
      templateText(98, 840, 700, 22, '건조기 사용 시 수축·변형이 생길 수 있으니 자연 건조해주세요.', { size: 16, color: '#5a5a5c' }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-06-color-view', label: '06 Color View', h: 1100, bg: '#e9e9eb', elements: [
      templateShape(84, 240, 38, 9, '#4e4e50'), templateShape(84, 249, 86, 59, '#6b6b6d', { radius: 8 }),
      templateText(62, 314, 130, 20, 'Black', { size: 14, color: '#3a3a3c', align: 'center' }),
      templateShape(84, 357, 38, 9, '#bfbfc2'), templateShape(84, 366, 86, 59, '#d9d9dc', { radius: 8 }),
      templateText(62, 431, 130, 20, 'White', { size: 14, color: '#3a3a3c', align: 'center' }),
      templateShape(84, 474, 38, 9, '#3b4058'), templateShape(84, 483, 86, 59, '#4e5470', { radius: 8 }),
      templateText(62, 548, 130, 20, 'Navy', { size: 14, color: '#3a3a3c', align: 'center' }),
      templatePhoto(240, 153, 702, 500, { role: 'coordination' }),
      templateShape(499, 692, 2, 46, '#b0b0b4'),
      templateText(58, 782, 884, 63, 'COLOR VIEW', { size: 52, weight: 700, color: '#262628', align: 'center' }),
      templateText(58, 890, 884, 22, '블랙, 화이트, 네이비로 선호도가 높은 3가지 색상 구성', { size: 17, color: '#4a4a4c', align: 'center' }),
      templateText(58, 919, 884, 22, '어디든지 가볍게 코디하기 좋은 셔츠 입니다.', { size: 17, color: '#4a4a4c', align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-07-color-list', label: '07 Color List', h: 1100, bg: '#ededef', elements: [
      templatePhoto(0, 0, 332, 620, { role: 'coordination' }), templatePhoto(334, 0, 332, 620, { role: 'coordination' }), templatePhoto(668, 0, 332, 620, { role: 'coordination' }),
      templateShape(81, 684, 32, 8, '#4e4e50'), templateShape(81, 692, 72, 50, '#6b6b6d', { radius: 7 }),
      templateText(183, 684, 736, 28, '01 Black', { size: 21, weight: 700, color: '#1f1f21' }),
      templateText(183, 721, 736, 30, '어디에나 잘 어울려 가장 많이 찾는 컬러예요. 차분하고 단정한 인상을 줘요.', { size: 16, color: '#6b6b70', lineHeight: 28 }),
      templateShape(81, 793, 32, 8, '#bfbfc2'), templateShape(81, 801, 72, 50, '#d9d9dc', { radius: 7 }),
      templateText(183, 793, 736, 28, '02 White', { size: 21, weight: 700, color: '#1f1f21' }),
      templateText(183, 830, 736, 30, '다양한 스타일로 연출이 가능하며, 포멀한 자리에서도 깔끔한 인상을 줄 수 있습니다.', { size: 16, color: '#6b6b70', lineHeight: 28 }),
      templateShape(81, 902, 32, 8, '#3b4058'), templateShape(81, 910, 72, 50, '#4e5470', { radius: 7 }),
      templateText(183, 902, 736, 28, '03 Navy', { size: 21, weight: 700, color: '#1f1f21' }),
      templateText(183, 939, 736, 30, '블랙보다는 부드럽고, 흰색보다는 차분한 느낌을 줍니다. 다양한 색상과도 조화롭게 어울립니다.', { size: 16, color: '#6b6b70', lineHeight: 28 }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-08-color-detail', label: '08 Color Detail', h: 1100, bg: '#e9e9eb', elements: [
      templatePhoto(0, 0, 322, 570, { role: 'coordination' }), templatePhoto(324, 0, 322, 570, { role: 'coordination' }), templatePhoto(648, 0, 352, 570, { role: 'coordination' }),
      templateText(660, 543, 330, 16, '페이지 내 인물 사진은 샘플이미지 입니다.', { size: 11, color: '#ededef' }),
      templateShape(269, 616, 38, 9, '#4e4e50'), templateShape(269, 625, 86, 59, '#6b6b6d', { radius: 8 }),
      templateText(247, 690, 130, 20, '01 Black', { size: 14, color: '#3a3a3c', align: 'center' }),
      templateShape(369, 661, 81, 2, '#b8b8bc'),
      templateShape(450, 616, 38, 9, '#bfbfc2'), templateShape(450, 625, 86, 59, '#d9d9dc', { radius: 8 }),
      templateText(428, 690, 130, 20, '02 White', { size: 14, color: '#3a3a3c', align: 'center' }),
      templateShape(550, 661, 81, 2, '#b8b8bc'),
      templateShape(632, 616, 38, 9, '#3b4058'), templateShape(632, 625, 86, 59, '#4e5470', { radius: 8 }),
      templateText(610, 690, 130, 20, '03 Navy', { size: 14, color: '#3a3a3c', align: 'center' }),
      templateShape(81, 751, 30, 24, '#2e2e30', { radius: 12 }),
      templateText(144, 751, 130, 26, 'BLACK', { size: 19, weight: 700, color: '#1f1f21' }),
      templateText(300, 751, 619, 22, '활용도가 높아 매치하기 손 쉬워 인기 있는 컬러로', { size: 16, color: '#5a5a5c' }),
      templateText(300, 777, 619, 22, '클래식하고 세련된 느낌을 줍니다.', { size: 16, color: '#5a5a5c' }),
      templateShape(81, 832, 30, 24, '#ededf0', { radius: 12, stroke: '#d0d0d3', strokeWidth: 1 }),
      templateText(144, 832, 130, 26, 'WHITE', { size: 19, weight: 700, color: '#1f1f21' }),
      templateText(300, 832, 619, 22, '다양한 스타일로 연출이 가능하며, 흰색은 포멀한', { size: 16, color: '#5a5a5c' }),
      templateText(300, 858, 619, 22, '자리에서 깔끔한 인상을 줄 수 있습니다.', { size: 16, color: '#5a5a5c' }),
      templateShape(81, 913, 30, 24, '#4e5470', { radius: 12 }),
      templateText(144, 913, 130, 26, 'NAVY', { size: 19, weight: 700, color: '#1f1f21' }),
      templateText(300, 913, 619, 22, '블랙보다는 부드럽고, 흰색보다는 차분한 분위기로', { size: 16, color: '#5a5a5c' }),
      templateText(300, 939, 619, 22, '다양한 색상과도 조화롭게 어울립니다.', { size: 16, color: '#5a5a5c' }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-09-cap', label: '09 Cap', h: 1100, bg: '#e9e9eb', elements: [
      templateShape(58, 83, 740, 484, '#ffffff', { radius: 10 }),
      templateShape(78, 99, 12, 12, '#f0685f', { radius: 6 }),
      templateShape(99, 99, 12, 12, '#f5bf4f', { radius: 6 }),
      templateShape(120, 99, 12, 12, '#61c554', { radius: 6 }),
      templatePhoto(58, 127, 740, 440, { role: 'coordination' }),
      templateShape(839, 99, 36, 9, '#6fb4e8'), templateShape(839, 108, 82, 55, '#8cc8f5', { radius: 8 }),
      templateText(815, 169, 130, 20, 'Summer', { size: 14, color: '#3a3a3c', align: 'center' }),
      templateShape(839, 202, 36, 9, '#6fb4e8'), templateShape(839, 211, 82, 55, '#8cc8f5', { radius: 8 }),
      templateText(815, 272, 130, 20, 'Fashion', { size: 14, color: '#3a3a3c', align: 'center' }),
      templatePhoto(490, 300, 470, 400, { role: 'detail', radius: 10 }),
      templateText(58, 600, 430, 16, '페이지 내 인물 사진은 샘플이미지 입니다.', { size: 12, color: '#a0a0a4', align: 'left' }),
      templateText(58, 898, 700, 56, ': Knit', { size: 44, weight: 700, color: '#1f1f21' }),
      templateText(58, 967, 700, 24, '함께 입기 좋은 아이템도 같이 보여줄 수 있어요.', { size: 17, color: '#5a5a5c' }),
      templateText(58, 996, 700, 24, '제품 설명은 1-2줄이면 충분해요.', { size: 17, color: '#5a5a5c' }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-10-look-1', label: '10 Look 1', h: 1100, bg: '#e9e9eb', elements: [
      templateShape(58, 136, 884, 127, '#ffffff', { radius: 16 }),
      templateShape(84, 158, 26, 26, '#2e2e30', { radius: 6 }),
      templateText(122, 157, 300, 30, 'LOOK 1', { size: 22, weight: 700, color: '#1f1f21' }),
      templateText(816, 161, 100, 22, 'now', { size: 16, color: '#9a9aa0', align: 'right' }),
      templateText(84, 196, 700, 22, '이 코디에서 함께 입은 아이템을 적어 주세요.', { size: 16, color: '#4a4a4c' }),
      templateText(84, 221, 700, 22, '여러 제품을 한 번에 소개할 수 있어요.', { size: 16, color: '#4a4a4c' }),
      templatePhoto(58, 286, 649, 632, { role: 'coordination', radius: 12 }),
      templatePhoto(726, 286, 216, 200, { role: 'detail', radius: 12 }),
      templatePhoto(726, 502, 216, 200, { role: 'detail', radius: 12 }),
      templatePhoto(726, 718, 216, 200, { role: 'detail', radius: 12 }),
      templateShape(58, 947, 7, 7, '#8e8e93', { radius: 4 }), templateText(74, 943, 120, 20, '제품명1', { size: 15, color: '#8e8e93' }),
      templateShape(173, 947, 7, 7, '#8e8e93', { radius: 4 }), templateText(189, 943, 120, 20, '제품명2', { size: 15, color: '#8e8e93' }),
      templateShape(291, 947, 7, 7, '#8e8e93', { radius: 4 }), templateText(307, 943, 120, 20, '제품명3', { size: 15, color: '#8e8e93' }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-11-product-point', label: '11 Product Point', h: 1100, bg: '#9e9ea2', elements: [
      templatePhoto(0, 0, 1000, 1100, { role: 'detail' }),
      templateShape(35, 63, 930, 127, '#ffffff', { radius: 16 }),
      templateShape(61, 85, 26, 26, '#2e2e30', { radius: 6 }),
      templateText(99, 84, 400, 30, 'Product name', { size: 22, weight: 700, color: '#1f1f21' }),
      templateText(839, 88, 100, 22, 'now', { size: 16, color: '#9a9aa0', align: 'right' }),
      templateText(61, 124, 800, 22, '이 제품에 대한 중요 포인트를 간략하게 적어주세요', { size: 16, color: '#4a4a4c' }),
      templateText(61, 149, 800, 22, '포인트는 굵게 만들어 강조 하는 것이 좋습니다.', { size: 16, color: '#4a4a4c' }),
      templateShape(70, 391, 220, 220, '#ffffff', { radius: 110, opacity: 0.28, stroke: '#ffffff', strokeWidth: 2 }),
      templateLine(238, 590, 96, { stroke: '#ffffff', strokeWidth: 2, rotate: 38 }),
      templateText(331, 681, 300, 24, '스크린 프린팅', { size: 17, weight: 700, color: '#1f1f21' }),
      templateShape(326, 704, 235, 24, '#ffffff'), templateText(331, 706, 230, 20, '원 영역을 이동하여 강조하고', { size: 15, color: '#1f1f21' }),
      templateShape(326, 730, 235, 24, '#ffffff'), templateText(331, 732, 230, 20, '싶은 포인트를 집중 시켜주세요', { size: 15, color: '#1f1f21' }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-12-sale-row', label: '12 Sale Row', h: 1100, bg: '#e4e4e2', elements: [
      templateText(58, 176, 884, 24, '겨울 시즌 오프', { size: 17, color: '#8a8a8e', align: 'center' }),
      templateText(58, 207, 884, 60, 'UP TO 80% 특가', { size: 46, weight: 700, color: '#1f1f21', align: 'center' }),
      templateShape(58, 304, 884, 298, '#ffffff', { radius: 16 }),
      templatePhoto(82, 328, 250, 250, { role: 'productOverview', radius: 10 }),
      templateText(358, 356, 560, 22, '소프트 골지 라운드 니트', { size: 15, color: '#8a8a8e' }),
      templateText(358, 384, 560, 34, '겨울을 부드럽게,', { size: 24, weight: 700, color: '#1f1f21', lineHeight: 34 }),
      templateText(358, 420, 560, 34, '골지 니트', { size: 24, weight: 700, color: '#1f1f21', lineHeight: 34 }),
      templateShape(358, 476, 64, 46, '#8e8e92', { radius: 23, rotate: -28 }),
      templateText(358, 490, 64, 20, '77%', { size: 17, weight: 600, color: '#ffffff', align: 'center' }),
      templateText(440, 472, 200, 20, '30,000', { size: 16, color: '#a8a8ac', strike: true }),
      templateText(440, 493, 200, 34, '4,000원', { size: 26, weight: 700, color: '#1f1f21' }),
      templateShape(58, 626, 884, 298, '#ffffff', { radius: 16 }),
      templatePhoto(82, 650, 250, 250, { role: 'productOverview', radius: 10 }),
      templateText(358, 671, 560, 22, '그레이 와이드 슬랙스', { size: 15, color: '#8a8a8e' }),
      templateText(358, 699, 560, 34, '데일리로 편한', { size: 24, weight: 700, color: '#1f1f21', lineHeight: 34 }),
      templateText(358, 735, 560, 34, '와이드 슬랙스', { size: 24, weight: 700, color: '#1f1f21', lineHeight: 34 }),
      templateShape(358, 791, 64, 46, '#8e8e92', { radius: 23, rotate: -28 }),
      templateText(358, 805, 64, 20, '77%', { size: 17, weight: 600, color: '#ffffff', align: 'center' }),
      templateText(440, 787, 200, 20, '30,000', { size: 16, color: '#a8a8ac', strike: true }),
      templateText(440, 808, 200, 34, '4,000원', { size: 26, weight: 700, color: '#1f1f21' }),
    ],
  }),
  kiwiTemplate({
    id: 'dp-13-sale-grid', label: '13 Sale Grid', h: 1100, bg: '#e4e4e2', elements: [
      templateText(58, 180, 884, 24, '서브텍스트를 작성해주세요', { size: 17, color: '#8a8a8e', align: 'center' }),
      templateText(58, 211, 884, 60, 'UP TO 80% 특가', { size: 46, weight: 700, color: '#1f1f21', align: 'center' }),
      templateShape(58, 308, 428, 612, '#ffffff', { radius: 16 }),
      templatePhoto(74, 324, 396, 420, { role: 'productOverview', radius: 4 }),
      templateText(80, 765, 380, 26, '소프트 골지 라운드 니트', { size: 19, weight: 700, color: '#1f1f21' }),
      templateText(80, 796, 380, 22, '아크릴 100% · 세미오버핏', { size: 15, color: '#8a8a8e' }),
      templateText(80, 830, 384, 20, '30,000', { size: 15, color: '#a8a8ac', align: 'right', strike: true }),
      templateText(80, 858, 100, 30, '77%', { size: 22, weight: 600, color: '#8a8a8e' }),
      templateText(264, 862, 200, 34, '4,000원', { size: 26, weight: 700, color: '#1f1f21', align: 'right' }),
      templateShape(514, 308, 428, 612, '#ffffff', { radius: 16 }),
      templatePhoto(530, 324, 396, 420, { role: 'productOverview', radius: 4 }),
      templateText(536, 765, 380, 26, '그레이 와이드 슬랙스', { size: 19, weight: 700, color: '#1f1f21' }),
      templateText(536, 796, 380, 22, '데일리로 좋은 와이드 핏', { size: 15, color: '#8a8a8e' }),
      templateText(536, 830, 384, 20, '30,000', { size: 15, color: '#a8a8ac', align: 'right', strike: true }),
      templateText(536, 858, 100, 30, '77%', { size: 22, weight: 600, color: '#8a8a8e' }),
      templateText(720, 862, 200, 34, '4,000원', { size: 26, weight: 700, color: '#1f1f21', align: 'right' }),
    ],
  }),
];

export const DETAIL_PAGE_TEMPLATE_2 = [
  // ---- T1 MD'S PICK ✅ ----
  kiwiTemplate({
    id: 't2-01-mdspick', label: "T1 MD'S PICK", h: 1290, bg: '#efefef', elements: [
      templateText(56, 62, 391, 40, '웨어 쇼핑몰 베스트 상품 세일전', { size: 29, color: '#111111' }),
      templateText(676, 62, 294, 40, '2050.6.1 ~ 6.15', { size: 29, color: '#111111', align: 'right' }),
      templateShape(0, 121, 1000, 2, '#111111'),
      templateText(45, 167, 900, 138, "MD'S PICK", { font: 'Cormorant', size: 130, weight: 700, color: '#111111', lineHeight: 138 }),
      templateText(735, 262, 255, 55, 'Wear Mall', { font: 'Cormorant', size: 44, weight: 700, color: '#111111', lineHeight: 55 }),
      templateShape(0, 355, 1000, 5, '#111111'),
      templateShape(0, 370, 1000, 2, '#111111'),
      templatePhoto(64, 372, 891, 821, { role: 'hero' }),
      templateText(86, 1140, 847, 24, '* 본 상세페이지의 일부 이미지는 AI를 활용해 생성되었습니다.', { size: 19, color: '#efefef', align: 'right' }),
      templateShape(0, 1193, 1000, 2, '#111111'),
      templateText(0, 1230, 1000, 40, '하루 종일 편안한 겨울 데일리 니트 상세페이지', { size: 32, color: '#111111', align: 'center' }),
    ],
  }),

  // ---- T2 SUMMER MOOD ✅ ----
  kiwiTemplate({
    id: 't2-02-summermood', label: 'T2 SUMMER MOOD', h: 1304, bg: '#efefef', elements: [
      templatePhoto(584, 0, 416, 373, { role: 'detail', stroke: '#000000', strokeWidth: 2 }),
      templatePhoto(584, 392, 416, 374, { role: 'detail', stroke: '#000000', strokeWidth: 2 }),
      templateText(49, 56, 60, 50, '✦', { size: 40, color: '#111111' }),
      templateText(504, 704, 60, 50, '✦', { size: 40, color: '#111111' }),
      templateText(39, 104, 508, 70, '2030 Wear', { font: 'Cormorant', size: 64, weight: 700, italic: true, color: '#111111', align: 'center', lineHeight: 70 }),
      templateText(20, 216, 547, 110, 'SUMMER', { font: 'Cormorant', size: 103, weight: 700, color: '#111111', align: 'center', lineHeight: 110 }),
      templateText(20, 347, 547, 110, 'MOOD', { font: 'Cormorant', size: 101, weight: 700, color: '#111111', align: 'center', lineHeight: 110 }),
      templateText(20, 504, 547, 30, '본격적인 상품 디테일을 보기 전에', { size: 24, color: '#222222', align: 'center' }),
      templateText(20, 550, 547, 30, '소프트 골지 라운드 니트를 먼저 소개해요.', { size: 24, color: '#222222', align: 'center' }),
      templateText(20, 596, 547, 30, '말로 설명하듯 친절하고 자세하게', { size: 24, color: '#222222', align: 'center' }),
      templateText(20, 642, 547, 30, '상품에 대해 소개해 보세요.', { size: 24, color: '#222222', align: 'center' }),
      templatePhoto(0, 784, 1000, 520, { role: 'hero', stroke: '#000000', strokeWidth: 2 }),
      templateText(24, 1250, 952, 24, '* 본 상세페이지의 일부 이미지는 AI를 활용해 생성되었습니다.', { size: 19, color: '#efefef', align: 'right' }),
    ],
  }),

  // ---- T3 LOOK 01 ✅ ----
  kiwiTemplate({
    id: 't2-03-look01', label: 'T3 LOOK 01', h: 1310, bg: '#efefef', elements: [
      templateShape(925, 6, 2, 1304, '#111111'),
      templateText(946, 44, 60, 60, '✦', { size: 50, color: '#111111' }),
      templateText(-137, 212, 470, 110, 'LOOK 01', { font: 'Cormorant', size: 104, weight: 700, color: '#111111', align: 'center', lineHeight: 110, rotate: -90 }),
      templateText(747, 118, 230, 34, '2030 SUMMER', { size: 24, color: '#111111', align: 'center', rotate: 90 }),
      templatePhoto(178, 66, 492, 701, { role: 'coordination', stroke: '#111111', strokeWidth: 2 }),
      templatePhoto(573, 393, 400, 748, { role: 'coordination', stroke: '#111111', strokeWidth: 2 }),
      templatePhoto(49, 815, 481, 449, { role: 'realWear', stroke: '#111111', strokeWidth: 2 }),
      templateText(69, 1220, 441, 24, '* 일부 이미지는 AI로 생성되었습니다.', { size: 19, color: '#efefef' }),
      templateText(568, 1180, 420, 70, 'ONEPIECE', { font: 'Cormorant', size: 69, weight: 700, color: '#111111', lineHeight: 70 }),
    ],
  }),

  // ---- T4 DETAIL CHECK ✅ ----
  kiwiTemplate({
    id: 't2-04-detailcheck', label: 'T4 DETAIL CHECK', h: 1317, bg: '#efefef', elements: [
      templateText(39, 44, 960, 110, 'DETAIL CHECK', { font: 'Cormorant', size: 100, weight: 600, color: '#111111', lineHeight: 110 }),
      templateShape(0, 208, 1000, 1, '#111111'),
      templatePhoto(0, 209, 497, 367, { role: 'detail', stroke: '#000000', strokeWidth: 2 }),
      templateShape(498, 209, 502, 63, '#ffffff'),
      templateText(524, 227, 200, 34, 'POINT 01', { size: 28, color: '#111111' }),
      templateText(944, 222, 40, 40, '✦', { size: 24, color: '#111111' }),
      templateText(550, 352, 398, 44, '쫀쫀한 넥라인', { size: 37, weight: 700, color: '#111111' }),
      templateText(550, 418, 398, 32, '목선이 편안한 라운드넥,', { size: 26, color: '#222222' }),
      templateText(550, 452, 398, 32, '이너로도 잘 어울려요.', { size: 26, color: '#222222' }),
      templateShape(0, 576, 1000, 1, '#111111'),
      templateShape(0, 577, 497, 63, '#ffffff'),
      templateText(26, 595, 200, 34, 'POINT 02', { size: 28, color: '#111111' }),
      templateText(441, 590, 40, 40, '✦', { size: 24, color: '#111111' }),
      templateText(30, 720, 437, 44, '완벽한 핏', { size: 37, weight: 700, color: '#111111' }),
      templateText(30, 786, 437, 32, '촘촘한 골지가 몸선을 따라 떨어져', { size: 26, color: '#222222' }),
      templateText(30, 820, 437, 32, '쉽게 부해 보이지 않아요.', { size: 26, color: '#222222' }),
      templatePhoto(498, 577, 502, 367, { role: 'detail', stroke: '#000000', strokeWidth: 2 }),
      templateShape(0, 944, 1000, 1, '#111111'),
      templatePhoto(0, 945, 497, 372, { stroke: '#000000', strokeWidth: 2 }),
      templateText(20, 1273, 457, 24, '* 일부 이미지는 AI로 생성되었습니다.', { size: 19, color: '#555555' }),
      templateShape(498, 945, 502, 63, '#ffffff'),
      templateText(524, 963, 200, 34, 'POINT 03', { size: 28, color: '#111111' }),
      templateText(944, 958, 40, 40, '✦', { size: 24, color: '#111111' }),
      templateText(550, 1090, 398, 44, '다양한 사이즈', { size: 37, weight: 700, color: '#111111' }),
      templateText(550, 1156, 398, 32, '세미오버핏이라 여유 있고,', { size: 26, color: '#222222' }),
      templateText(550, 1190, 398, 32, '받쳐 입기도 좋아요.', { size: 26, color: '#222222' }),
    ],
  }),

  // ---- T5 SIZE INFO ✅ ----
  kiwiTemplate({
    id: 't2-05-sizeinfo', label: 'T5 SIZE INFO', h: 1285, bg: '#efefef', elements: [
      templateText(0, 40, 1000, 110, 'SIZE INFO', { font: 'Cormorant', size: 103, weight: 600, color: '#111111', align: 'center', lineHeight: 110 }),
      templateShape(0, 204, 1000, 1, '#111111'),
      templatePhoto(402, 205, 598, 627, { role: 'fit' }),
      templateText(28, 233, 356, 34, 'PRODUCT', { font: 'Cormorant', size: 28, weight: 700, color: '#111111' }),
      templateText(28, 283, 356, 32, '소프트 골지 라운드 니트', { size: 25, color: '#222222' }),
      templateShape(0, 343, 402, 2, '#111111'),
      templateText(28, 374, 356, 34, 'COLOR OPTION', { font: 'Cormorant', size: 28, weight: 700, color: '#111111' }),
      templateShape(28, 426, 38, 38, '#ece3cd', { radius: 19 }), templateText(80, 431, 120, 30, 'Ivory', { size: 26, color: '#111111' }),
      templateShape(175, 426, 38, 38, '#111111', { radius: 19 }), templateText(227, 431, 120, 30, 'Black', { size: 26, color: '#111111' }),
      templateShape(0, 493, 402, 1, '#111111'),
      templateText(28, 526, 356, 34, 'FABRIC', { font: 'Cormorant', size: 28, weight: 700, color: '#111111' }),
      templateText(28, 576, 356, 32, '아크릴 100%', { size: 25, color: '#222222' }),
      templateShape(0, 641, 402, 2, '#111111'),
      templateText(28, 676, 356, 34, 'CARE', { font: 'Cormorant', size: 28, weight: 700, color: '#111111' }),
      templateText(28, 726, 356, 32, '미지근한 물에 단독 세탁하고,', { size: 25, color: '#222222' }),
      templateText(28, 764, 356, 32, '자연 건조해 주세요.', { size: 25, color: '#222222' }),
      templateShape(0, 832, 1000, 1, '#111111'),
      // 사이즈표
      templateShape(71, 897, 214, 58, '#0b0b0b'), templateText(71, 912, 214, 30, '총장', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(285, 897, 214, 58, '#0b0b0b'), templateText(285, 912, 214, 30, '어깨', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(499, 897, 214, 58, '#0b0b0b'), templateText(499, 912, 214, 30, '가슴', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(713, 897, 215, 58, '#0b0b0b'), templateText(713, 912, 215, 30, '소매', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(71, 955, 857, 48, '#111111'),
      templateShape(71, 955, 213, 48, '#ffffff'), templateText(71, 967, 213, 26, '64', { size: 23, color: '#111111', align: 'center' }),
      templateShape(285, 955, 213, 48, '#ffffff'), templateText(285, 967, 213, 26, '42', { size: 23, color: '#111111', align: 'center' }),
      templateShape(499, 955, 213, 48, '#ffffff'), templateText(499, 967, 213, 26, '51', { size: 23, color: '#111111', align: 'center' }),
      templateShape(713, 955, 215, 48, '#ffffff'), templateText(713, 967, 215, 26, '58', { size: 23, color: '#111111', align: 'center' }),
      // 물성표 (검은 bg + 1px 인셋 셀 → 외곽 테두리·격자선)
      templateShape(71, 1041, 857, 219, '#111111'),
      templateShape(72, 1042, 214, 72, '#0b0b0b'), templateText(72, 1066, 214, 30, '두께감', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(287, 1042, 213, 72, '#ffffff'), templateText(287, 1067, 213, 26, '얇음', { size: 23, color: '#111111', align: 'center' }),
      templateShape(501, 1042, 213, 72, '#ffffff'), templateText(501, 1067, 213, 26, '보통', { size: 23, color: '#111111', align: 'center' }),
      templateShape(715, 1042, 212, 72, '#c9c9cc'), templateText(715, 1067, 212, 26, '두꺼움', { size: 23, color: '#111111', align: 'center' }),
      templateShape(72, 1115, 214, 72, '#0b0b0b'), templateText(72, 1139, 214, 30, '비 침', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(287, 1115, 213, 72, '#ffffff'), templateText(287, 1140, 213, 26, '있음', { size: 23, color: '#111111', align: 'center' }),
      templateShape(501, 1115, 213, 72, '#ffffff'), templateText(501, 1140, 213, 26, '약간', { size: 23, color: '#111111', align: 'center' }),
      templateShape(715, 1115, 212, 72, '#c9c9cc'), templateText(715, 1140, 212, 26, '없음', { size: 23, color: '#111111', align: 'center' }),
      templateShape(72, 1188, 214, 71, '#0b0b0b'), templateText(72, 1211, 214, 30, '신 축 성', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(287, 1188, 213, 71, '#c9c9cc'), templateText(287, 1212, 213, 26, '있음', { size: 23, color: '#111111', align: 'center' }),
      templateShape(501, 1188, 213, 71, '#ffffff'), templateText(501, 1212, 213, 26, '약간', { size: 23, color: '#111111', align: 'center' }),
      templateShape(715, 1188, 212, 71, '#ffffff'), templateText(715, 1212, 212, 26, '없음', { size: 23, color: '#111111', align: 'center' }),
    ],
  }),

  // ---- T6 NOTICE ✅ (마지막) ----
  kiwiTemplate({
    id: 't2-06-notice', label: 'T6 NOTICE', h: 1308, bg: '#efefef', elements: [
      templatePhoto(0, 0, 1000, 1089, { role: 'hero' }),
      templateShape(93, 97, 814, 899, '#ffffff', { stroke: '#111111', strokeWidth: 2 }),
      templateText(93, 129, 814, 130, 'NOTICE', { font: 'Cormorant', size: 104, weight: 700, color: '#111111', align: 'center', lineHeight: 130 }),
      templateShape(93, 291, 814, 2, '#111111'),
      templateText(93, 407, 202, 34, '배송 안내', { size: 28, weight: 700, color: '#111111', align: 'center' }),
      templateShape(295, 293, 2, 263, '#111111'),
      templateText(327, 327, 24, 30, '•', { size: 24, color: '#111111' }),
      templateText(357, 327, 500, 40, '결제 확인 후 영업일 기준 2~5일 내 출고돼요.', { size: 24, color: '#111111', lineHeight: 38 }),
      templateText(327, 377, 24, 30, '•', { size: 24, color: '#111111' }),
      templateText(357, 377, 500, 40, '주문 폭주 및 제작 상황에 따라 일정이 지연될 수 있어요.', { size: 24, color: '#111111', lineHeight: 38 }),
      templateText(327, 465, 24, 30, '•', { size: 24, color: '#111111' }),
      templateText(357, 465, 500, 80, '도서 및 산간지역은 배송비가 추가될 수 있어요.\n(합 배송 시 영업일 기준 3~4일 소요)', { size: 24, color: '#111111', lineHeight: 38 }),
      templateShape(93, 556, 814, 2, '#111111'),
      templateText(93, 737, 202, 80, '교환 및\n반품 안내', { size: 28, weight: 700, color: '#111111', align: 'center', lineHeight: 40 }),
      templateShape(295, 558, 2, 438, '#111111'),
      templateText(327, 592, 24, 30, '•', { size: 24, color: '#111111' }),
      templateText(357, 592, 500, 40, '상품 수령 후 7일 이내 신청 가능해요.', { size: 24, color: '#111111', lineHeight: 38 }),
      templateText(327, 644, 24, 30, '•', { size: 24, color: '#111111' }),
      templateText(357, 644, 500, 80, '단순 변심의 경우 왕복 배송비가 발생해요. 택 제거·세탁·착용 흔적이 있으면 교환·반품이 어려워요.', { size: 24, color: '#111111', lineHeight: 38 }),
      templateText(327, 734, 24, 30, '•', { size: 24, color: '#111111' }),
      templateText(357, 734, 500, 80, '모니터 해상도와 촬영 환경에 따라 실제 색상과 다소 차이가 있을 수 있어요.', { size: 24, color: '#111111', lineHeight: 38 }),
      templateText(327, 824, 24, 30, '•', { size: 24, color: '#111111' }),
      templateText(357, 824, 500, 40, '측정 위치에 따라 실측 치수는 1~3cm 오차가 있을 수 있어요.', { size: 24, color: '#111111', lineHeight: 38 }),
      templateShape(0, 1089, 1000, 219, '#0b0b0b'),
      templateText(56, 1168, 400, 60, 'MIRI MALL', { font: 'Cormorant', size: 56, weight: 700, color: '#ffffff' }),
      templateText(523, 1140, 421, 48, '02-1234-5678', { size: 40, color: '#ffffff' }),
      templateText(523, 1196, 421, 26, '고객센터 : 평일 오전 9시 ~ 오후 5시', { size: 20, color: '#cfcfcf' }),
      templateText(523, 1224, 421, 26, '주소 : 미리시 미리구 미리로 13번길', { size: 20, color: '#cfcfcf' }),
    ],
  }),
];

export const DETAIL_PAGE_TEMPLATE_3 = [
  // ---- U01 PHOTO LAYOUT ✅ ----
  kiwiTemplate({
    id: 't3-01-photolayout', label: 'U01 PHOTO LAYOUT', h: 1283, bg: '#9a9a9c', elements: [
      templatePhoto(0, 0, 1000, 697, { role: 'hero' }),
      templatePhoto(0, 697, 1000, 586, { role: 'coordination' }),
      templateShape(0, 697, 1000, 587, '#c8c8ca', { opacity: 0.45 }),
      templateText(52, 54, 240, 30, 'Real Review', { font: 'Cormorant', size: 20, color: '#ffffff' }),
      templateShape(192, 68, 578, 2, '#ffffff'),
      templateText(720, 54, 228, 30, 'Soft Rib Knit', { font: 'Cormorant', size: 20, color: '#ffffff', align: 'right' }),
      templateText(0, 169, 1000, 40, '부드럽게 떨어지는 골지 니트 디테일 컷', { size: 29, color: '#ffffff', align: 'center' }),
      templateText(0, 222, 1000, 140, '사진 레이아웃', { size: 114, weight: 700, color: '#ffffff', align: 'center', lineHeight: 136 }),
      templateText(236, 346, 685, 120, 'photo', { font: 'Cormorant', size: 94, italic: true, color: '#a9c3d6', align: 'center', lineHeight: 120 }),
      templatePhoto(72, 491, 327, 792, { role: 'hero' }),
      templateShape(339, 1094, 47, 47, '#a9c3d6', { radius: 24 }),
      templateText(339, 1100, 47, 34, '+', { size: 28, color: '#ffffff', align: 'center' }),
      templateText(403, 1094, 300, 44, 'Daily Knit', { font: 'Cormorant', size: 35, color: '#ffffff' }),
      templateShape(403, 1140, 162, 2, '#ffffff'),
      templateShape(515, 1159, 17, 17, '#a9c3d6', { radius: 8 }),
      templateShape(543, 1159, 17, 17, '#a9c3d6', { radius: 8 }),
      templateShape(570, 1159, 17, 17, '#9a9a9c', { radius: 8, stroke: '#ffffff', strokeWidth: 2 }),
      templateText(476, 1216, 472, 24, '일부 이미지는 AI로 생성되었습니다.', { size: 17, color: '#e8e8e8', align: 'right' }),
    ],
  }),

  // ---- U02 BEST ITEM ✅ (프레임 전체 배경도 사진 슬롯) ----
  kiwiTemplate({
    id: 't3-02-bestitem', label: 'U02 BEST ITEM', h: 1260, bg: '#5c5c5b', elements: [
      templatePhoto(0, 0, 1000, 1260, { role: 'hero' }),
      templateText(53, 88, 240, 30, 'WEARLESS', { font: 'Cormorant', size: 21, color: '#ffffff' }),
      templateText(227, 70, 546, 60, 'Soft Rib Knit', { font: 'Cormorant', size: 42, color: '#ffffff', align: 'center', lineHeight: 53 }),
      templateText(720, 88, 227, 30, 'Best item', { font: 'Cormorant', size: 21, color: '#ffffff', align: 'right' }),
      templateShape(53, 140, 894, 2, '#ffffff'),
      templatePhoto(345, 193, 610, 1061, { role: 'coordination' }),
      templatePhoto(55, 365, 406, 548, { role: 'detail' }),
      templateText(114, 928, 100, 70, '↲', { size: 60, color: '#ffffff' }),
      templateShape(76, 1031, 47, 47, '#a9c3d6', { radius: 24 }),
      templateText(76, 1037, 47, 34, '+', { size: 28, color: '#ffffff', align: 'center' }),
      templateText(140, 1031, 300, 44, 'Daily Knit', { font: 'Cormorant', size: 35, color: '#ffffff' }),
      templateText(470, 1207, 470, 24, '일부 이미지는 AI로 생성되었습니다.', { size: 17, color: '#e8e8e8', align: 'right' }),
    ],
  }),

  // ---- U03 DETAIL CUT ✅ (상단 배너·중앙 시안테두리 제거) ----
  kiwiTemplate({
    id: 't3-03-detailcut', label: 'U03 DETAIL CUT', h: 1252, bg: '#f7f6f3', elements: [
      templateText(0, 131, 1000, 34, '부드럽게 떨어지는 골지, 매일 입기 좋은', { size: 26, color: '#3a3a3a', align: 'center' }),
      templateText(0, 182, 1000, 90, 'Soft Rib Knit', { font: 'Cormorant', size: 70, color: '#2e2e2e', align: 'center', lineHeight: 90 }),
      templateText(159, 250, 683, 90, 'knit', { font: 'Cormorant', size: 70, italic: true, color: '#a9c3d6', align: 'center', lineHeight: 90 }),
      templatePhoto(17, 417, 260, 656, { role: 'detail', rotate: -3, stroke: '#ffffff', strokeWidth: 11 }),
      templatePhoto(740, 431, 243, 656, { role: 'detail', rotate: 3, stroke: '#ffffff', strokeWidth: 11 }),
      templateShape(241, 372, 531, 698, '#ffffff'),
      templatePhoto(264, 395, 485, 652, { role: 'hero' }),
      templateText(264, 1017, 485, 24, '일부 이미지는 AI로 생성되었습니다.', { size: 15, color: '#ededed', align: 'center' }),
      templateShape(52, 1175, 897, 2, '#2e2e2e'),
      templateText(52, 1199, 400, 30, 'Product name', { size: 20, color: '#3a3a3a' }),
      templateText(645, 1199, 304, 30, 'Detail Cut', { size: 20, color: '#3a3a3a', align: 'right' }),
    ],
  }),

  // ---- U04 ROUND KNIT ✅ (시안 테두리 제거) ----
  kiwiTemplate({
    id: 't3-04-roundknit', label: 'U04 ROUND KNIT', h: 1278, bg: '#a7b8c3', elements: [
      templateShape(52, 53, 897, 2, '#3a3a3a'),
      templateText(52, 73, 400, 30, 'Product Name', { font: 'Cormorant', size: 23, color: '#2e2e2e' }),
      templateText(645, 73, 304, 30, 'Detail Cut', { font: 'Cormorant', size: 23, color: '#2e2e2e', align: 'right' }),
      templateShape(52, 112, 897, 2, '#3a3a3a'),
      templateText(70, 252, 320, 65, 'Soft Rib', { font: 'Cormorant', size: 47, color: '#2e2e2e', lineHeight: 65 }),
      templateText(70, 317, 320, 65, 'Round Knit', { font: 'Cormorant', size: 47, color: '#2e2e2e', lineHeight: 65 }),
      templatePhoto(52, 416, 435, 578, { role: 'coordination' }),
      templatePhoto(513, 182, 435, 485, { role: 'detail' }),
      templatePhoto(513, 696, 435, 582, { role: 'detail' }),
      templateText(52, 1024, 600, 30, 'I wear every day, WEARLESS Knit', { font: 'Cormorant', size: 21, color: '#4a4a4a' }),
      templateText(52, 1054, 600, 30, "Soft Rib Round Knit's detail cut", { font: 'Cormorant', size: 21, color: '#4a4a4a' }),
    ],
  }),

  // ---- U05 GRID TEXT ✅ ----
  kiwiTemplate({
    id: 't3-05-gridtext', label: 'U05 GRID TEXT', h: 1266, bg: '#a7b8c3', elements: [
      templatePhoto(0, 0, 502, 638, { role: 'coordination' }),
      templateText(563, 238, 377, 34, 'I wear every day,', { font: 'Cormorant', size: 26, color: '#4a4a4a', align: 'right' }),
      templateText(563, 281, 377, 34, 'WEARLESS Knit', { font: 'Cormorant', size: 26, color: '#4a4a4a', align: 'right' }),
      templateText(563, 324, 377, 34, 'Soft Rib Round Knit', { font: 'Cormorant', size: 26, color: '#4a4a4a', align: 'right' }),
      templateText(563, 367, 377, 34, 'detail cut', { font: 'Cormorant', size: 26, color: '#4a4a4a', align: 'right' }),
      templatePhoto(43, 684, 416, 544, { role: 'detail', rotate: -3, stroke: '#ffffff', strokeWidth: 14 }),
      templatePhoto(502, 638, 499, 628, { role: 'coordination' }),
    ],
  }),

  // ---- U06 SNS POST ✅ (배경 전체 사진 슬롯) ----
  kiwiTemplate({
    id: 't3-06-snspost', label: 'U06 SNS POST', h: 1282, bg: '#9a9a9c', elements: [
      templatePhoto(0, 0, 1000, 1282, { role: 'hero' }),
      templateShape(0, 696, 1000, 586, '#c8c8ca', { opacity: 0.45 }),
      templateLine(52, 52, 898, { stroke: '#ffffff', strokeWidth: 2, dash: 'dashed' }),
      templatePhoto(49, 97, 97, 97, { role: 'coordination', radius: 49, stroke: '#ffffff', strokeWidth: 2 }),
      templateText(163, 120, 220, 34, 'wearless_studio', { size: 24, color: '#ffffff' }),
      templateText(366, 122, 150, 30, '2 hours', { size: 22, color: '#e0e0e0' }),
      templateText(913, 106, 61, 40, '···', { size: 30, color: '#ffffff', align: 'right' }),
      templatePhoto(55, 361, 327, 921, { role: 'coordination' }),
      templateShape(327, 906, 47, 47, '#a9c3d6', { radius: 24 }),
      templateText(327, 912, 47, 34, '+', { size: 28, color: '#ffffff', align: 'center' }),
      templateText(391, 906, 300, 44, 'Daily Knit', { font: 'Cormorant', size: 35, color: '#ffffff' }),
      templateShape(391, 952, 162, 2, '#ffffff'),
      templateShape(503, 962, 17, 17, '#a9c3d6', { radius: 8 }),
      templateShape(531, 962, 17, 17, '#a9c3d6', { radius: 8 }),
      templateShape(558, 962, 17, 17, '#9a9a9c', { radius: 8, stroke: '#ffffff', strokeWidth: 2 }),
      templateText(476, 1215, 472, 24, '일부 이미지는 AI로 생성되었습니다.', { size: 17, color: '#e8e8e8', align: 'right' }),
    ],
  }),

  // ---- U07 DAILY (사진4) ✅ ----
  kiwiTemplate({
    id: 't3-07-daily', label: 'U07 DAILY', h: 1277, bg: '#f2f2f0', elements: [
      templatePhoto(88, 91, 412, 547, { role: 'coordination' }),
      templatePhoto(500, 91, 412, 547, { role: 'coordination' }),
      templatePhoto(88, 638, 412, 547, { role: 'coordination' }),
      templatePhoto(500, 638, 412, 547, { role: 'coordination' }),
      templateShape(88, 585, 825, 91, '#e9e9e7', { opacity: 0.55 }),
      templateText(200, 575, 300, 100, 'Daily', { font: 'Cormorant', size: 80, color: '#ffffff', align: 'right', lineHeight: 100 }),
      templateText(512, 575, 300, 100, 'Knit', { font: 'Cormorant', size: 80, italic: true, color: '#a9c3d6', lineHeight: 100 }),
      templateText(88, 681, 825, 34, 'I wear every day, WEARLESS Knit', { font: 'Cormorant', size: 24, color: '#ffffff', align: 'center' }),
    ],
  }),

  // ---- U08 PRODUCT DETAIL ✅ (하단 이미지3 정렬) ----
  kiwiTemplate({
    id: 't3-08-productdetail', label: 'U08 PRODUCT DETAIL', h: 1278, bg: '#f7f6f3', elements: [
      templateText(52, 53, 360, 30, 'Product Detail Cut', { font: 'Cormorant', size: 20, color: '#2e2e2e' }),
      templateShape(250, 67, 531, 2, '#2e2e2e'),
      templateText(675, 53, 273, 30, 'Soft Rib Knit', { font: 'Cormorant', size: 20, color: '#2e2e2e', align: 'right' }),
      templateText(0, 141, 1000, 90, 'Soft Rib Knit', { font: 'Cormorant', size: 70, color: '#2e2e2e', align: 'center', lineHeight: 90 }),
      templateText(0, 223, 1000, 34, 'I wear every day, WEARLESS Knit', { font: 'Cormorant', size: 23, color: '#5a5a5a', align: 'center' }),
      templatePhoto(52, 285, 896, 443, { role: 'hero' }),
      templatePhoto(55, 756, 285, 474, { role: 'detail' }),
      templatePhoto(353, 756, 355, 474, { role: 'detail' }),
      templatePhoto(721, 756, 279, 474, { role: 'detail' }),
      templateShape(237, 923, 55, 55, '#a9c3d6', { radius: 28 }),
      templateText(237, 931, 55, 40, '+', { size: 30, color: '#ffffff', align: 'center' }),
      templateText(301, 873, 80, 60, '↱', { size: 47, color: '#5a5a5a' }),
    ],
  }),

  // ---- U09 SIDE/BACK ✅ (좌측 이미지→전체 배경 사진 슬롯) ----
  kiwiTemplate({
    id: 't3-09-sideback', label: 'U09 SIDE/BACK', h: 1281, bg: '#bebfbc', elements: [
      templatePhoto(0, 0, 1000, 1281, { role: 'hero' }),
      templateShape(52, 81, 897, 2, '#3a3a3a'),
      templateText(52, 50, 400, 30, 'Product name', { font: 'Cormorant', size: 21, color: '#2e2e2e' }),
      templateText(645, 50, 304, 30, 'Detail Cut', { font: 'Cormorant', size: 21, color: '#2e2e2e', align: 'right' }),
      templatePhoto(532, 190, 391, 448, { role: 'detail', stroke: '#3a3a3a', strokeWidth: 2 }),
      templateText(547, 205, 300, 40, 'Side', { font: 'Cormorant', size: 30, color: '#2e2e2e' }),
      templatePhoto(532, 655, 391, 479, { role: 'detail', stroke: '#3a3a3a', strokeWidth: 2 }),
      templateText(547, 670, 300, 40, 'Back', { font: 'Cormorant', size: 30, color: '#2e2e2e' }),
      templateText(547, 1079, 395, 190, 'Detail', { font: 'Cormorant', size: 114, italic: true, color: '#a9c3d6', align: 'right', lineHeight: 141 }),
      templateShape(52, 1225, 897, 2, '#3a3a3a'),
    ],
  }),

  // ---- U10 IVORY KNIT ✅ (마지막) ----
  kiwiTemplate({
    id: 't3-10-ivory', label: 'U10 IVORY KNIT', h: 1282, bg: '#a7b8c3', elements: [
      templateShape(49, 53, 899, 2, '#3a3a3a'),
      templateText(49, 73, 400, 30, 'Product Name', { font: 'Cormorant', size: 23, color: '#2e2e2e' }),
      templateText(646, 73, 305, 30, 'Detail Cut', { font: 'Cormorant', size: 23, color: '#2e2e2e', align: 'right' }),
      templateShape(49, 113, 899, 2, '#3a3a3a'),
      templatePhoto(49, 228, 587, 767, { role: 'coordination' }),
      templateText(67, 953, 550, 24, '일부 이미지는 AI로 생성되었습니다.', { size: 15, color: '#ededed' }),
      templatePhoto(655, 172, 262, 366, { role: 'detail' }),
      templateText(587, 358, 80, 60, '→', { size: 47, color: '#2e2e2e' }),
      templatePhoto(572, 716, 373, 482, { role: 'detail', stroke: '#ffffff', strokeWidth: 14 }),
      templateText(49, 1045, 700, 60, 'Ivory Knit', { font: 'Cormorant', size: 50, color: '#2e2e2e', lineHeight: 60 }),
      templateText(49, 1114, 700, 30, 'I wear every day, WEARLESS Knit', { font: 'Cormorant', size: 21, color: '#4a4a4a' }),
      templateText(49, 1144, 700, 30, "Soft Rib Round Knit's detail cut", { font: 'Cormorant', size: 21, color: '#4a4a4a' }),
    ],
  }),
];

export const DETAIL_PAGE_TEMPLATE_4 = [
  // ---- V01 MD PICK ✅ ----
  kiwiTemplate({
    id: 't4-01-mdpick', label: 'V01 MD PICK', h: 1277, bg: '#e4e4e0', elements: [
      templateShape(0, 852, 1000, 425, '#c3c2be'),
      templateText(0, 40, 1000, 205, 'MD PICK', { font: 'Cormorant', size: 185, weight: 700, color: '#a5a39d', align: 'center', lineHeight: 205 }),
      templatePhoto(188, 319, 625, 711, { role: 'hero', radiusTopRight: 300 }), // 아치(통합 시 per-corner 지원)
      templateText(-64, 510, 392, 27, '* 일부 이미지는 AI로 생성되었습니다.', { size: 18, color: '#8e8c87', align: 'center', rotate: -90 }),
      templateText(0, 1035, 1000, 90, 'SIMPLE & MODERN', { font: 'Cormorant', size: 67, weight: 700, color: '#111111', align: 'center', lineHeight: 90 }),
      templateText(0, 1152, 1000, 44, '부드럽게 떨어지는 골지, 매일 입기 좋게.', { size: 33, weight: 700, color: '#111111', align: 'center' }),
    ],
  }),

  // ---- V02 NEW MODERN ✅ ----
  kiwiTemplate({
    id: 't4-02-newmodern', label: 'V02 NEW MODERN', h: 1280, bg: '#e4e4e0', elements: [
      templatePhoto(42, 42, 760, 1196, { role: 'hero' }),
      templateText(56, 56, 480, 27, '* 일부 이미지는 AI로 생성되었습니다.', { size: 20, color: '#6e6c67' }),
      templateText(290, 572, 1196, 136, 'NEW MODERN', { font: 'Cormorant', size: 116, weight: 700, color: '#a5a39d', align: 'left', lineHeight: 136, rotate: 90 }),
    ],
  }),

  // ---- V03 MODERN CHIC ✅ ----
  kiwiTemplate({
    id: 't4-03-modernchic', label: 'V03 MODERN CHIC', h: 1281, bg: '#e4e4e0', elements: [
      templateText(40, 91, 1000, 119, 'MODERN &', { font: 'Cormorant', size: 120, weight: 700, color: '#a5a39d', lineHeight: 119 }),
      templateText(40, 210, 1000, 119, 'CHIC', { font: 'Cormorant', size: 120, weight: 700, color: '#a5a39d', lineHeight: 119 }),
      templatePhoto(465, 379, 493, 856, { role: 'hero' }),
      templateText(40, 677, 480, 27, '* 일부 이미지는 AI로 생성되었습니다.', { size: 20, color: '#4a4844' }),
      templatePhoto(40, 711, 410, 523, { role: 'detail' }),
    ],
  }),

  // ---- V04 ELEGANT ✅ (ELEGANT 95px 한 줄) ----
  kiwiTemplate({
    id: 't4-04-elegant', label: 'V04 ELEGANT', h: 1283, bg: '#e4e4e0', elements: [
      templatePhoto(38, 46, 919, 594, { role: 'hero' }),
      templateText(38, 57, 480, 27, '* 일부 이미지는 AI로 생성되었습니다.', { size: 20, color: '#4a4844' }),
      templatePhoto(38, 668, 346, 544, { role: 'detail' }),
      templateText(339, 680, 620, 110, 'ELEGANT', { font: 'Cormorant', size: 95, weight: 700, color: '#a5a39d', align: 'right', lineHeight: 110 }),
      templatePhoto(420, 823, 537, 389, { role: 'detail' }),
    ],
  }),

  // ---- V05 PRODUCT ✅ ----
  kiwiTemplate({
    id: 't4-05-product', label: 'V05 PRODUCT', h: 1279, bg: '#c3c2be', elements: [
      templateText(0, 75, 1000, 60, 'PRODUCT', { font: 'Cormorant', size: 51, weight: 700, color: '#111111', align: 'center', lineHeight: 60 }),
      templateShape(41, 145, 917, 2, '#111111'),
      templatePhoto(42, 177, 916, 675, { role: 'hero' }),
      templateText(0, 908, 1000, 44, '소프트 골지 라운드 니트', { size: 35, weight: 700, color: '#111111', align: 'center' }),
      templateText(44, 994, 912, 30, '본격적인 상품 디테일을 보기 전, 소프트 골지 라운드 니트를', { size: 24, color: '#2e2e2c', align: 'center' }),
      templateText(44, 1038, 912, 30, '간략하게 소개하는 페이지예요. 말로 설명하듯 친절하고', { size: 24, color: '#2e2e2c', align: 'center' }),
      templateText(44, 1082, 912, 30, '자세하게 상품에 대해 소개해 보세요.', { size: 24, color: '#2e2e2c', align: 'center' }),
    ],
  }),

  // ---- V06 EVENT ✅ ----
  kiwiTemplate({
    id: 't4-06-event', label: 'V06 EVENT', h: 1278, bg: '#e4e4e0', elements: [
      templateText(0, 76, 1000, 60, 'EVENT', { font: 'Cormorant', size: 51, weight: 700, color: '#111111', align: 'center', lineHeight: 60 }),
      templateShape(43, 145, 914, 2, '#111111'),
      templateShape(44, 177, 913, 700, '#f8f8f5'),
      templatePhoto(245, 254, 509, 545, { role: 'hero' }),
      templateShape(651, 242, 151, 151, '#c3c2be', { radius: 75 }),
      templateText(651, 292, 151, 60, 'Free', { font: 'Cormorant', size: 58, italic: true, color: '#111111', align: 'center' }),
      templateShape(44, 880, 913, 271, '#c3c2be'),
      templateText(44, 930, 913, 44, '무료배송 이벤트', { size: 36, weight: 700, color: '#111111', align: 'center' }),
      templateText(44, 1014, 913, 36, '하나를 주문해도,', { size: 25, color: '#2e2e2c', align: 'center' }),
      templateText(44, 1052, 913, 36, 'wearless studio는 전상품 무료배송입니다.', { size: 25, color: '#2e2e2c', align: 'center' }),
    ],
  }),

  // ---- V07 REVIEW ✅ (리뷰 카드 3) ----
  kiwiTemplate({
    id: 't4-07-review', label: 'V07 REVIEW', h: 1275, bg: '#c3c2be', elements: [
      templateText(0, 73, 1000, 60, 'REVIEW', { font: 'Cormorant', size: 51, weight: 700, color: '#111111', align: 'center', lineHeight: 60 }),
      templateShape(41, 143, 917, 2, '#111111'),
      templateShape(42, 175, 916, 313, '#f8f8f5'), templatePhoto(57, 190, 348, 283, { role: 'realWear' }),
      templateText(432, 206, 200, 34, '★★★★★', { size: 27, color: '#111111' }),
      templateText(700, 210, 231, 30, 'kim000 님', { size: 24, color: '#111111', align: 'right' }),
      templateShape(432, 246, 499, 1, '#c9c7c2'),
      templateText(432, 260, 499, 200, '골지가 촘촘해서 얇아 보이지 않아요. 이너로 입어도 목선이 편하고, 어깨선이 자연스럽게 떨어져서 부해 보이지 않았어요. 아이보리는 얼굴도 환해 보여서 겨울 내내 손이 갈 것 같아요.', { size: 20, color: '#2e2e2c', lineHeight: 35 }),
      templateShape(42, 503, 916, 313, '#f8f8f5'), templatePhoto(57, 518, 348, 283, { role: 'realWear' }),
      templateText(432, 534, 200, 34, '★★★★☆', { size: 27, color: '#111111' }),
      templateText(700, 538, 231, 30, 'lee333 님', { size: 24, color: '#111111', align: 'right' }),
      templateShape(432, 574, 499, 1, '#c9c7c2'),
      templateText(432, 588, 499, 200, '세미오버핏이라 안에 얇은 티 하나 받쳐 입기 딱 좋아요. 아크릴 100%인데 까슬거림 없이 부드럽고, 세탁 후에도 늘어나지 않았어요. 색은 사진보다 살짝 차분한 편이에요.', { size: 20, color: '#2e2e2c', lineHeight: 35 }),
      templateShape(42, 835, 916, 313, '#f8f8f5'), templatePhoto(57, 850, 348, 283, { role: 'realWear' }),
      templateText(432, 866, 200, 34, '★★★★☆', { size: 27, color: '#111111' }),
      templateText(700, 870, 231, 30, 'wear00 님', { size: 24, color: '#111111', align: 'right' }),
      templateShape(432, 906, 499, 1, '#c9c7c2'),
      templateText(432, 920, 499, 200, '블랙으로 샀는데 어디에나 잘 어울려서 자주 입어요. 도톰해서 아우터 없이도 따뜻하고, 목선이 편해서 하루 종일 답답하지 않았어요. 소매 기장도 딱 맞았어요.', { size: 20, color: '#2e2e2c', lineHeight: 35 }),
      templateText(42, 1177, 600, 27, '* 일부 이미지는 AI로 생성되었습니다.', { size: 20, color: '#4a4844' }),
    ],
  }),

  // ---- V08 OUR BEST ✅ ----
  kiwiTemplate({
    id: 't4-08-ourbest', label: 'V08 OUR BEST', h: 1279, bg: '#e4e4e0', elements: [
      templateText(0, 77, 1000, 60, 'OUR BEST', { font: 'Cormorant', size: 51, weight: 700, color: '#111111', align: 'center', lineHeight: 60 }),
      templateShape(42, 146, 916, 2, '#111111'),
      templateShape(41, 177, 222, 222, '#c3c2be'), templateText(41, 250, 222, 80, '🛒', { size: 60, align: 'center' }),
      templateShape(263, 177, 698, 222, '#f8f8f5'),
      templateText(263, 239, 698, 34, '누적 판매 수', { size: 27, color: '#3e3e3c', align: 'center' }),
      templateText(263, 274, 698, 70, '100,000개', { size: 56, weight: 700, color: '#5a5a57', align: 'center' }),
      templateShape(41, 412, 222, 222, '#c3c2be'), templateText(41, 485, 222, 80, '✎', { size: 60, align: 'center' }),
      templateShape(263, 412, 698, 222, '#f8f8f5'),
      templateText(263, 474, 698, 34, '누적 리뷰 수', { size: 27, color: '#3e3e3c', align: 'center' }),
      templateText(263, 509, 698, 70, '5,000건', { size: 56, weight: 700, color: '#5a5a57', align: 'center' }),
      templateShape(41, 645, 222, 222, '#c3c2be'), templateText(41, 718, 222, 80, '🏆', { size: 60, align: 'center' }),
      templateShape(263, 645, 698, 222, '#f8f8f5'),
      templateText(263, 707, 698, 34, '15주 연속', { size: 27, color: '#3e3e3c', align: 'center' }),
      templateText(263, 742, 698, 70, 'Best 1위', { size: 56, weight: 700, color: '#5a5a57', align: 'center' }),
      templateShape(41, 882, 222, 222, '#c3c2be'), templateText(41, 955, 222, 80, '☝', { size: 60, align: 'center' }),
      templateShape(263, 882, 698, 222, '#f8f8f5'),
      templateText(263, 944, 698, 34, '최고의 만족도', { size: 27, color: '#3e3e3c', align: 'center' }),
      templateText(263, 979, 698, 70, '5.0', { size: 56, weight: 700, color: '#5a5a57', align: 'center' }),
    ],
  }),

  // ---- V09 DETAIL ✅ ----
  kiwiTemplate({
    id: 't4-09-detail', label: 'V09 DETAIL', h: 1281, bg: '#c3c2be', elements: [
      templateText(0, 75, 1000, 60, 'DETAIL', { font: 'Cormorant', size: 51, weight: 700, color: '#111111', align: 'center', lineHeight: 60 }),
      templateShape(41, 145, 917, 2, '#111111'),
      templateShape(42, 177, 916, 974, '#f8f8f5'),
      templatePhoto(81, 220, 418, 444, { role: 'detail' }),
      templateText(538, 226, 383, 70, 'POINT 1', { font: 'Cormorant', size: 57, weight: 700, color: '#8e8c87', lineHeight: 64 }),
      templateText(538, 412, 392, 200, '골지 짜임을 가까이서 볼까요? 촘촘한 골지가 몸선을 따라 자연스럽게 떨어져 쉽게 부해 보이지 않아요. 아크릴 100%라 도톰하면서도 무겁지 않아요.', { size: 23, color: '#2e2e2c', lineHeight: 40 }),
      templatePhoto(499, 665, 422, 444, { role: 'detail' }),
      templateText(81, 704, 383, 70, 'POINT 2', { font: 'Cormorant', size: 57, weight: 700, color: '#8e8c87', lineHeight: 64 }),
      templateText(81, 921, 392, 180, '목선이 편안한 라운드넥이에요. 이너로도, 겉옷 안에도 잘 어울려요. 세미오버핏이라 받쳐 입기도 좋아요.', { size: 23, color: '#2e2e2c', lineHeight: 40 }),
      templateText(81, 1050, 392, 40, '말로 설명하듯, 친절하고 자세하게.', { size: 23, color: '#2e2e2c', lineHeight: 40 }),
    ],
  }),

  // ---- V10 PRODUCT INFO ✅ ----
  kiwiTemplate({
    id: 't4-10-productinfo', label: 'V10 PRODUCT INFO', h: 1276, bg: '#e4e4e0', elements: [
      templateText(0, 76, 1000, 60, 'PRODUCT INFO', { font: 'Cormorant', size: 51, weight: 700, color: '#111111', align: 'center', lineHeight: 60 }),
      templateShape(43, 145, 914, 2, '#111111'),
      // 아이콘 패널
      templateShape(44, 176, 913, 365, '#f8f8f5'),
      templateShape(101, 210, 115, 115, '#e4e4e0', { radius: 57 }), templateText(101, 240, 115, 60, '❀', { size: 44, align: 'center' }),
      templateText(44, 345, 228, 34, '원단', { size: 27, weight: 700, color: '#111111', align: 'center' }),
      templateText(44, 385, 228, 34, '아크릴 100%', { size: 24, color: '#3e3e3c', align: 'center' }),
      templateShape(329, 210, 115, 115, '#e4e4e0', { radius: 57 }), templateText(329, 240, 115, 60, '✋', { size: 44, align: 'center' }),
      templateText(272, 345, 228, 34, '세탁 방법', { size: 27, weight: 700, color: '#111111', align: 'center' }),
      templateText(272, 385, 228, 34, '단독 손세탁', { size: 24, color: '#3e3e3c', align: 'center' }),
      templateShape(557, 210, 115, 115, '#e4e4e0', { radius: 57 }), templateText(557, 240, 115, 60, '▤', { size: 44, align: 'center' }),
      templateText(500, 345, 228, 34, '두께감', { size: 27, weight: 700, color: '#111111', align: 'center' }),
      templateText(500, 385, 228, 34, '도톰함 (비침 없음)', { size: 24, color: '#3e3e3c', align: 'center' }),
      templateShape(785, 210, 115, 115, '#e4e4e0', { radius: 57 }), templateText(785, 240, 115, 60, '✥', { size: 44, align: 'center' }),
      templateText(728, 345, 228, 34, '신축성', { size: 27, weight: 700, color: '#111111', align: 'center' }),
      templateText(728, 385, 228, 34, '있음', { size: 24, color: '#3e3e3c', align: 'center' }),
      // 사이즈 패널
      templateShape(44, 556, 913, 593, '#f8f8f5'),
      // 실측 도식 (직접 그림)
      templateShape(132, 700, 300, 58, '#d9d8d4', { radius: 12 }),
      templateShape(182, 700, 200, 232, '#d9d8d4', { radius: 12 }),
      templateShape(250, 694, 64, 26, '#f8f8f5', { radius: 13 }),
      templateShape(452, 700, 2, 232, '#8b8983'), templateShape(447, 700, 12, 2, '#8b8983'), templateShape(447, 930, 12, 2, '#8b8983'),
      templateText(462, 802, 20, 30, 'A', { size: 22, weight: 700, color: '#6d6b66' }),
      templateShape(132, 680, 300, 2, '#8b8983'), templateShape(132, 674, 2, 12, '#8b8983'), templateShape(430, 674, 2, 12, '#8b8983'),
      templateText(272, 650, 20, 30, 'B', { size: 22, weight: 700, color: '#6d6b66' }),
      templateShape(132, 732, 50, 2, '#8b8983'), templateText(146, 700, 20, 30, 'C', { size: 22, weight: 700, color: '#6d6b66' }),
      templateShape(182, 806, 200, 2, '#5a5957'), templateText(388, 792, 20, 30, 'D', { size: 22, weight: 700, color: '#3e3e3c' }),
      // 사이즈표
      templateShape(534, 636, 346, 222, '#e4e4e0'),
      templateShape(534, 636, 205, 54, '#f8f8f5'), templateText(534, 651, 205, 30, '총기장 (A)', { size: 24, weight: 700, color: '#111111', align: 'center' }),
      templateShape(741, 636, 139, 54, '#f8f8f5'), templateText(741, 651, 139, 30, '64', { size: 24, color: '#111111', align: 'center' }),
      templateShape(534, 692, 205, 54, '#f8f8f5'), templateText(534, 707, 205, 30, '어깨너비 (B)', { size: 24, weight: 700, color: '#111111', align: 'center' }),
      templateShape(741, 692, 139, 54, '#f8f8f5'), templateText(741, 707, 139, 30, '42', { size: 24, color: '#111111', align: 'center' }),
      templateShape(534, 748, 205, 54, '#f8f8f5'), templateText(534, 763, 205, 30, '소매길이 (C)', { size: 24, weight: 700, color: '#111111', align: 'center' }),
      templateShape(741, 748, 139, 54, '#f8f8f5'), templateText(741, 763, 139, 30, '58', { size: 24, color: '#111111', align: 'center' }),
      templateShape(534, 804, 205, 54, '#f8f8f5'), templateText(534, 819, 205, 30, '가슴단면 (D)', { size: 24, weight: 700, color: '#111111', align: 'center' }),
      templateShape(741, 804, 139, 54, '#f8f8f5'), templateText(741, 819, 139, 30, '51', { size: 24, color: '#111111', align: 'center' }),
      templateText(534, 871, 346, 34, 'Free / cm', { size: 27, color: '#3e3e3c', align: 'right' }),
      templateText(44, 1078, 913, 30, '사이즈는 측정 방법에 따라 1~3cm의 오차가 있을 수 있습니다.', { size: 22, color: '#3e3e3c', align: 'center' }),
    ],
  }),

  // ---- V11 MODEL SIZE ✅ ----
  kiwiTemplate({
    id: 't4-11-modelsize', label: 'V11 MODEL SIZE', h: 1250, bg: '#c3c2be', elements: [
      templateText(0, 73, 1000, 55, 'MODEL SIZE', { font: 'Cormorant', size: 51, weight: 700, color: '#111111', align: 'center', lineHeight: 55 }),
      templateShape(40, 144, 919, 2, '#111111'),
      templatePhoto(207, 217, 582, 582, { role: 'coordination', radius: 291 }),
      templateText(0, 846, 1000, 50, 'Model 나윤', { size: 37, weight: 700, color: '#111111', align: 'center' }),
      templateShape(500, 960, 2, 155, '#8e8c87'),
      templateText(107, 966, 137, 40, 'Height', { size: 29, weight: 700, color: '#111111' }), templateText(268, 966, 180, 40, '167cm', { size: 29, color: '#2e2e2c' }),
      templateText(107, 1022, 137, 40, 'Weight', { size: 29, weight: 700, color: '#111111' }), templateText(268, 1022, 180, 40, '47kg', { size: 29, color: '#2e2e2c' }),
      templateText(107, 1078, 137, 40, 'Top', { size: 29, weight: 700, color: '#111111' }), templateText(268, 1078, 180, 40, '55(s)', { size: 29, color: '#2e2e2c' }),
      templateText(539, 966, 210, 40, 'Bottom', { size: 29, weight: 700, color: '#111111' }), templateText(773, 966, 140, 40, '26 (s)', { size: 29, color: '#2e2e2c' }),
      templateText(539, 1022, 210, 40, 'Fitting Color', { size: 29, weight: 700, color: '#111111' }), templateText(773, 1022, 140, 40, 'Black', { size: 29, color: '#2e2e2c' }),
      templateText(539, 1078, 210, 40, 'Size', { size: 29, weight: 700, color: '#111111' }), templateText(773, 1078, 140, 40, 'Free', { size: 29, color: '#2e2e2c' }),
      templateText(0, 1216, 1000, 30, '* 일부 이미지는 AI로 생성되었습니다.', { size: 20, color: '#4a4844', align: 'center' }),
    ],
  }),

  // ---- V12 NOTICE ✅ ----
  kiwiTemplate({
    id: 't4-12-notice', label: 'V12 NOTICE', h: 1200, bg: '#e4e4e0', elements: [
      templateText(0, 71, 1000, 55, 'NOTICE', { font: 'Cormorant', size: 51, weight: 700, color: '#111111', align: 'center', lineHeight: 55 }),
      templateShape(40, 143, 919, 2, '#111111'),
      templateShape(456, 192, 88, 88, '#c3c2be', { radius: 44 }), templateText(456, 205, 88, 60, '🚚', { size: 40, align: 'center' }),
      templateText(0, 311, 1000, 55, '배송 안내', { size: 42, weight: 700, color: '#111111', align: 'center' }),
      templateText(43, 393, 914, 34, '• 결제 확인 후 영업일 기준 2~5일 내 출고돼요.', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateText(43, 433, 914, 34, '• 주문 폭주 및 제작 상황에 따라 일정이 지연될 수 있어요.', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateText(43, 473, 914, 34, '• 도서 및 산간지역은 배송비가 추가될 수 있어요.', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateText(43, 513, 914, 34, '(합 배송 시 영업일 기준 3~4일 소요)', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateShape(456, 632, 88, 88, '#c3c2be', { radius: 44 }), templateText(456, 642, 88, 60, '!', { size: 46, weight: 700, color: '#2e2e2c', align: 'center' }),
      templateText(0, 751, 1000, 55, '교환 및 반품 안내', { size: 42, weight: 700, color: '#111111', align: 'center' }),
      templateText(43, 837, 914, 34, '• 상품 수령 후 7일 이내 신청 가능해요.', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateText(43, 877, 914, 34, '• 단순 변심의 경우 왕복 배송비가 발생해요.', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateText(43, 917, 914, 34, '• 택 제거·세탁·착용 흔적이 있으면 교환·반품이 어려워요.', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateText(43, 957, 914, 34, '• 모니터 해상도와 촬영 환경에 따라', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateText(43, 997, 914, 34, '실제 색상과 다소 차이가 있을 수 있어요.', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateText(43, 1037, 914, 34, '• 측정 위치에 따라 실측 치수는', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
      templateText(43, 1077, 914, 34, '1~3cm 오차가 있을 수 있어요.', { size: 26, color: '#2e2e2c', align: 'center', lineHeight: 34 }),
    ],
  }),
];

export const DETAIL_PAGE_TEMPLATE_5 = [
  // ---- W01 FASHION COVER ✅ ----
  kiwiTemplate({
    id: 't5-01-cover', label: 'W01 FASHION COVER', h: 1280, bg: '#f7f6f3', elements: [
      templateShape(51, 57, 896, 2, '#333333'),
      templateText(51, 66, 366, 32, 'Brand name', { font: 'Cormorant', size: 24, color: '#333333' }),
      templateText(764, 66, 183, 32, '#1', { font: 'Cormorant', size: 24, color: '#333333', align: 'right' }),
      templateShape(51, 104, 896, 2, '#333333'),
      templateText(0, 128, 1000, 101, 'Fashion Cover', { font: 'Cormorant', size: 110, color: '#2e2e2d', align: 'center', lineHeight: 119 }),
      templateText(0, 249, 1000, 40, '강렬한 타이틀 뒤 숨겨진 디테일한 설명. 표지에 어떤 포인트를 내세우고 싶나요?', { size: 27, color: '#333333', align: 'center' }),
      templateShape(51, 307, 896, 2, '#333333'),
      templatePhoto(49, 345, 899, 879, { role: 'hero' }),
      templateText(70, 1181, 858, 26, '페이지 내 인물 사진은 샘플이미지 입니다.', { size: 18, color: '#e8e8e4', align: 'right' }),
    ],
  }),

  // ---- W02 SUMMER SALE ✅ ----
  kiwiTemplate({
    id: 't5-02-sale', label: 'W02 SUMMER SALE', h: 1277, bg: '#2e2e2d', elements: [
      templatePhoto(0, 0, 1000, 1277, { role: 'hero' }),
      templateText(46, 55, 458, 32, 'Summer sale event', { size: 24, color: '#c9c9c6' }),
      templateText(588, 55, 366, 32, '미리몰 썸머 세일', { size: 24, color: '#c9c9c6', align: 'right' }),
      templateShape(351, 172, 299, 49, '#8fa3b4', { radius: 25 }),
      templateText(351, 182, 299, 32, '08.01 - 08.30 한 달간', { size: 26, weight: 700, color: '#ffffff', align: 'center' }),
      templateText(184, 250, 420, 120, 'Summer', { font: 'Cormorant', size: 103, color: '#ffffff', lineHeight: 120 }),
      templateText(590, 236, 320, 120, 'Sale', { font: 'Cormorant', italic: true, size: 118, color: '#ffffff', lineHeight: 120 }),
      templateText(0, 389, 1000, 119, 'EVENT', { font: 'Cormorant', size: 103, color: '#ffffff', align: 'center', lineHeight: 119 }),
      templateShape(44, 594, 912, 1, '#b0b0ad'),
      templateText(44, 632, 366, 32, 'Benefit 01', { size: 24, color: '#dcdcd9' }),
      templateText(44, 673, 912, 34, '품목 상관없이 사용 가능', { size: 26, color: '#dcdcd9', align: 'right' }),
      templateText(44, 713, 912, 50, '10% 할인 쿠폰', { size: 37, weight: 700, color: '#ffffff', align: 'right' }),
      templateShape(44, 788, 912, 1, '#b0b0ad'),
      templateText(44, 826, 366, 32, 'Benefit 02', { size: 24, color: '#dcdcd9' }),
      templateText(44, 867, 912, 34, '세일 아이템 중복할인 가능', { size: 26, color: '#dcdcd9', align: 'right' }),
      templateText(44, 907, 912, 50, '스토어찜 1,000원 쿠폰', { size: 37, weight: 700, color: '#ffffff', align: 'right' }),
      templateShape(44, 983, 912, 1, '#b0b0ad'),
      templateText(44, 1020, 366, 32, 'Benefit 03', { size: 24, color: '#dcdcd9' }),
      templateText(44, 1061, 912, 34, '단 한 개만 구매해도', { size: 26, color: '#dcdcd9', align: 'right' }),
      templateText(44, 1101, 912, 50, '배송비 무료', { size: 37, weight: 700, color: '#ffffff', align: 'right' }),
      templateShape(44, 1177, 912, 1, '#b0b0ad'),
    ],
  }),

  // ---- W03 WHY PANTS ✅ ----
  kiwiTemplate({
    id: 't5-03-whypants', label: 'W03 WHY PANTS', h: 1280, bg: '#616160', elements: [
      templatePhoto(0, 0, 1000, 1280, { role: 'hero' }),
      ...Array.from({ length: 19 }, (_, i) => templateShape(44 + 48 * i, 51, 37, 3, '#ffffff')),
      templatePhoto(52, 97, 81, 81, { role: 'coordination', radius: 40, stroke: '#ffffff' }),
      templateText(160, 119, 200, 40, 'Brand_name', { size: 27, color: '#ffffff' }),
      templateText(345, 121, 150, 36, '2 hours', { size: 26, color: '#d6d6d3' }),
      templateText(879, 110, 73, 40, '···', { size: 35, color: '#ffffff', align: 'right' }),
      templateText(44, 648, 220, 110, 'Why', { font: 'Cormorant', italic: true, size: 95, color: '#a9c3d6', lineHeight: 110 }),
      templateText(269, 676, 420, 90, 'MIRI Pants?', { font: 'Cormorant', size: 66, color: '#ffffff' }),
      templateShape(227, 755, 513, 3, '#a9c3d6'),
      templateShape(44, 784, 912, 1, '#dcdcd9'),
      templateText(44, 811, 183, 36, 'Daily', { size: 26, weight: 700, color: '#ffffff' }), templateText(211, 811, 696, 36, '매일매일 입을 수 있게 편하고 활동성이 좋은', { size: 24, color: '#ededea' }),
      templateShape(44, 859, 912, 1, '#dcdcd9'),
      templateText(44, 894, 183, 36, 'Fit', { size: 26, weight: 700, color: '#ffffff' }), templateText(211, 894, 696, 36, '디테일하게 나눠져 있는 사이즈와 기장 옵션으로 본인에게 맞게', { size: 24, color: '#ededea' }),
      templateShape(44, 942, 912, 1, '#dcdcd9'),
      templateText(44, 976, 183, 36, 'Mererial', { size: 26, weight: 700, color: '#ffffff' }), templateText(211, 976, 696, 36, '고급 원단과 고급 부자재 사용으로 오래 입을 수 있는', { size: 24, color: '#ededea' }),
      templateShape(44, 1024, 912, 1, '#dcdcd9'),
      templateShape(52, 1092, 685, 79, 'transparent', { radius: 39, stroke: '#ffffff' }),
      templateText(89, 1118, 610, 36, '원하는 바지, 미리팬츠에는 다 있으니까!', { size: 27, color: '#ffffff' }),
      templateText(777, 1099, 64, 50, '➤', { size: 32, color: '#ffffff', align: 'center' }),
      templateText(878, 1099, 64, 50, '♡', { size: 36, color: '#ffffff', align: 'center' }),
    ],
  }),

  // ---- W04 BEST ITEM ✅ ----
  kiwiTemplate({
    id: 't5-04-bestitem', label: 'W04 BEST ITEM', h: 1280, bg: '#f7f6f3', elements: [
      templateShape(55, 58, 893, 2, '#333333'),
      templateText(55, 69, 364, 32, 'Best item', { font: 'Cormorant', size: 22, color: '#333333' }),
      templateText(583, 69, 364, 32, 'MIRI Daily Pants', { font: 'Cormorant', size: 22, color: '#333333', align: 'right' }),
      templateShape(55, 99, 893, 2, '#333333'),
      templateText(179, 128, 273, 55, 'Best', { font: 'Cormorant', italic: true, size: 49, color: '#a9c3d6', align: 'center', lineHeight: 55 }),
      templateText(0, 169, 1000, 73, '미리 데일리 팬츠', { size: 58, weight: 700, color: '#2e2e2d', align: 'center', lineHeight: 73 }),
      templateText(45, 264, 911, 38, '고급 원단과 정교한 재단으로 만들어진 이 바지는 어떤 룩에도 잘 어울리며,', { size: 26, color: '#333333', align: 'center' }),
      templateText(45, 306, 911, 38, '하루 종일 편안함을 유지할 수 있습니다. 지금 바로 만나보세요!', { size: 26, color: '#333333', align: 'center' }),
      templatePhoto(54, 389, 895, 660, { role: 'hero' }),
      templateShape(54, 1089, 894, 2, '#333333'),
      templateShape(350, 1115, 2, 91, '#8e8c87'), templateShape(650, 1115, 2, 91, '#8e8c87'),
      templateText(54, 1128, 297, 34, '사이즈', { size: 27, weight: 700, color: '#2e2e2d', align: 'center' }), templateText(54, 1170, 297, 34, 'S / M / L / XL', { size: 26, color: '#333333', align: 'center' }),
      templateText(352, 1128, 297, 34, '색상', { size: 27, weight: 700, color: '#2e2e2d', align: 'center' }),
      templateShape(442, 1170, 31, 31, '#a9c3d6', { radius: 15 }), templateShape(486, 1170, 31, 31, '#26374a', { radius: 15 }),
      templateShape(530, 1170, 31, 31, '#8e8c87', { radius: 15 }), templateText(530, 1172, 31, 28, '✓', { size: 18, color: '#ffffff', align: 'center' }),
      templateText(652, 1128, 297, 34, '기장', { size: 27, weight: 700, color: '#2e2e2d', align: 'center' }), templateText(652, 1170, 297, 34, 'Short / Long', { size: 26, color: '#333333', align: 'center' }),
      templateShape(54, 1218, 894, 2, '#333333'),
    ],
  }),

  // ---- W05 PRODUCT DETAIL ✅ ----
  kiwiTemplate({
    id: 't5-05-detail', label: 'W05 PRODUCT DETAIL', h: 1282, bg: '#f7f6f3', elements: [
      templateText(51, 64, 365, 32, 'Detail Check', { font: 'Cormorant', size: 22, color: '#333333' }),
      templateShape(197, 78, 511, 2, '#333333'),
      templateText(584, 64, 365, 32, 'MIRI Daily Wide Pants', { font: 'Cormorant', size: 22, color: '#333333', align: 'right' }),
      templateText(0, 157, 1000, 40, '미리 데일리 와이드 팬츠', { size: 29, color: '#333333', align: 'center' }),
      templateText(0, 204, 1000, 82, 'Product Detail', { font: 'Cormorant', size: 78, color: '#2e2e2d', align: 'center', lineHeight: 82 }),
      templateText(347, 275, 547, 64, 'Check', { font: 'Cormorant', italic: true, size: 58, color: '#a9c3d6', align: 'center', lineHeight: 64 }),
      templatePhoto(310, 383, 319, 812, { role: 'hero' }),
      templatePhoto(104, 452, 252, 252, { role: 'detail', radius: 126 }),
      templateShape(184, 436, 51, 51, '#8fa3b4', { radius: 25 }), templateText(184, 443, 51, 40, '+', { size: 32, color: '#ffffff', align: 'center' }),
      templateText(230, 400, 119, 55, '↗', { size: 40, color: '#a9c3d6', align: 'center' }),
      templateText(55, 766, 383, 40, '깔끔하게 허리를 잡아주는', { size: 27, weight: 700, color: '#2e2e2d' }),
      templateText(55, 806, 383, 55, 'Hidden Banding', { font: 'Cormorant', size: 36, color: '#2e2e2d' }),
      templatePhoto(638, 855, 261, 261, { role: 'detail', radius: 130 }),
      templateShape(755, 784, 51, 51, '#8fa3b4', { radius: 25 }), templateText(755, 791, 51, 40, '+', { size: 32, color: '#ffffff', align: 'center' }),
      templateText(644, 772, 119, 55, '↘', { size: 40, color: '#a9c3d6', align: 'center' }),
      templateText(565, 1122, 383, 40, '다리가 길어보이는', { size: 27, weight: 700, color: '#2e2e2d', align: 'right' }),
      templateText(565, 1162, 383, 55, 'Straight Fit', { font: 'Cormorant', size: 36, color: '#2e2e2d', align: 'right' }),
    ],
  }),

  // ---- W06 MORE DETAIL ✅ ----
  kiwiTemplate({
    id: 't5-06-moredetail', label: 'W06 MORE DETAIL', h: 1276, bg: '#f7f6f3', elements: [
      templateShape(52, 93, 892, 2, '#333333'),
      templateText(298, 118, 182, 100, 'More', { font: 'Cormorant', italic: true, size: 82, color: '#a9c3d6', lineHeight: 100 }),
      templateText(494, 118, 208, 100, 'Detail', { font: 'Cormorant', size: 78, color: '#2e2e2d', lineHeight: 100 }),
      templateText(0, 223, 1000, 40, '데일리 팬츠의 디테일을 확인해보세요', { size: 27, color: '#333333', align: 'center' }),
      templateShape(52, 279, 892, 2, '#333333'),
      templatePhoto(482, 282, 465, 450, { role: 'detail' }),
      templateText(73, 314, 382, 45, '01', { size: 31, weight: 700, color: '#2e2e2d' }),
      templateText(73, 367, 382, 42, '깔끔한 핏, 히든밴딩', { size: 29, weight: 700, color: '#2e2e2d' }),
      templateText(73, 428, 382, 38, '무릎 나올 걱정 없는 튼튼하고', { size: 25, color: '#4a4a48' }),
      templateText(73, 466, 382, 38, '핏을 잡아주는 원단과', { size: 25, color: '#4a4a48' }),
      templateText(73, 503, 382, 38, '디테일이 남다른 단추와 지퍼로', { size: 25, color: '#4a4a48' }),
      templateText(73, 540, 382, 38, '고급진 마감으로 완성한 팬츠', { size: 25, color: '#4a4a48' }),
      templatePhoto(482, 735, 465, 449, { role: 'detail' }),
      templateText(73, 768, 382, 45, '02', { size: 31, weight: 700, color: '#2e2e2d' }),
      templateText(73, 822, 382, 42, '길어보이는 스트레이트 핏', { size: 29, weight: 700, color: '#2e2e2d' }),
      templateText(73, 883, 382, 38, '무릎 나올 걱정 없는 튼튼하고', { size: 25, color: '#4a4a48' }),
      templateText(73, 920, 382, 38, '핏을 잡아주는 원단과', { size: 25, color: '#4a4a48' }),
      templateText(73, 957, 382, 38, '디테일이 남다른 단추와 지퍼로', { size: 25, color: '#4a4a48' }),
      templateText(73, 994, 382, 38, '고급진 마감으로 완성한 팬츠', { size: 25, color: '#4a4a48' }),
      templateShape(52, 1184, 892, 2, '#333333'),
    ],
  }),

  // ---- W07 SUMMER DENIM ✅ ----
  kiwiTemplate({
    id: 't5-07-denim', label: 'W07 SUMMER DENIM', h: 1275, bg: '#3a3a39', elements: [
      templatePhoto(0, 0, 1000, 1275, { role: 'hero' }),
      templateShape(52, 71, 896, 2, '#dcdcd9'),
      templateText(52, 84, 365, 32, 'Fabric Check', { font: 'Cormorant', size: 22, color: '#ededea' }),
      templateText(583, 84, 365, 32, 'MIRI Daily Pants', { font: 'Cormorant', size: 22, color: '#ededea', align: 'right' }),
      templateShape(52, 119, 896, 2, '#dcdcd9'),
      templateText(36, 228, 340, 120, 'Summer', { font: 'Cormorant', italic: true, size: 99, color: '#a9c3d6', lineHeight: 120 }),
      templateText(335, 244, 320, 110, 'Denim', { font: 'Cormorant', size: 84, color: '#ffffff', lineHeight: 110 }),
      templateShape(52, 394, 57, 2, '#dcdcd9'),
      templateText(52, 427, 821, 40, '기능성 소재로 더욱 쾌적한 여름을 위한 원단', { size: 29, weight: 700, color: '#ffffff' }),
      templateText(52, 474, 821, 38, '얇고 가벼운 여름용 데님 소재로 제작되어', { size: 26, color: '#e4e4e1' }),
      templateText(52, 516, 821, 38, '더운 여름 피부에 달라붙지 않아 쾌적한 착용감과', { size: 26, color: '#e4e4e1' }),
      templateText(52, 558, 821, 38, '시원하게 착용하기 좋은 두께입니다.', { size: 26, color: '#e4e4e1' }),
      templatePhoto(337, 613, 483, 483, { role: 'coordination', radius: 242 }),
      templateShape(823, 631, 106, 106, '#98b1bc', { radius: 53 }),
      templateText(823, 655, 106, 28, 'COOL', { size: 20, weight: 700, color: '#2e2e2d', align: 'center' }),
      templateText(823, 688, 106, 40, '❄', { size: 26, color: '#2e2e2d', align: 'center' }),
      templateText(730, 757, 100, 55, '↙', { size: 36, color: '#dcdcd9', align: 'center' }),
    ],
  }),

  // ---- W08 DETAIL CUT ✅ ----
  kiwiTemplate({
    id: 't5-08-detailcut', label: 'W08 DETAIL CUT', h: 1281, bg: '#bebfbc', elements: [
      templatePhoto(0, 0, 1000, 1281, { role: 'hero' }),
      templateShape(52, 88, 896, 2, '#333333'),
      templateText(52, 53, 365, 32, 'Product name', { font: 'Cormorant', size: 24, color: '#2e2e2d' }),
      templateText(583, 53, 365, 32, 'Detail Cut', { font: 'Cormorant', size: 24, color: '#2e2e2d', align: 'right' }),
      templatePhoto(527, 187, 403, 443, { role: 'detail', stroke: '#4a4a48' }),
      templateText(545, 205, 300, 50, 'Side', { font: 'Cormorant', size: 35, color: '#2e2e2d' }),
      templatePhoto(527, 657, 403, 438, { role: 'detail', stroke: '#4a4a48' }),
      templateText(545, 675, 300, 50, 'Back', { font: 'Cormorant', size: 35, color: '#2e2e2d' }),
      templateText(511, 1031, 438, 164, 'Detail', { font: 'Cormorant', italic: true, size: 128, color: '#a9c3d6', align: 'right', lineHeight: 160 }),
      templateShape(52, 1186, 896, 2, '#333333'),
    ],
  }),

  // ---- W09 DAILY SERIES ✅ ----
  kiwiTemplate({
    id: 't5-09-dailyseries', label: 'W09 DAILY SERIES', h: 1281, bg: '#f7f6f3', elements: [
      templateText(0, 97, 1000, 40, '두 가지 라인의 데일리 팬츠', { size: 27, weight: 700, color: '#2e2e2d', align: 'center' }),
      templateText(0, 137, 1000, 91, 'Daily Series', { font: 'Cormorant', size: 84, color: '#2e2e2d', align: 'center', lineHeight: 91 }),
      templatePhoto(50, 252, 448, 552, { role: 'coordination' }), templatePhoto(499, 252, 449, 552, { role: 'coordination' }),
      templateShape(197, 285, 154, 44, '#a7c0d2', { radius: 22 }), templateText(197, 294, 154, 30, '데일리 데님', { size: 24, weight: 700, color: '#2e2e2d', align: 'center' }),
      templateShape(645, 285, 154, 44, '#333333', { radius: 22 }), templateText(645, 294, 154, 30, '데일리 코튼', { size: 24, weight: 700, color: '#ffffff', align: 'center' }),
      templateShape(50, 804, 897, 137, '#ffffff'),
      templateText(50, 842, 897, 38, '허리는 잡아주고 허벅지의 군살은 커버하는,', { size: 26, color: '#333333', align: 'center' }),
      templateText(50, 884, 897, 38, '툭 떨어지는 실루엣의 하이웨스트의 롱 스트레이트 핏', { size: 26, color: '#333333', align: 'center' }),
      templateShape(50, 942, 897, 2, '#333333'),
      templateShape(498, 943, 2, 135, '#333333'),
      templateText(50, 972, 448, 34, 'Light / Deep / Greish', { size: 26, color: '#333333', align: 'center' }),
      templateShape(211, 1013, 35, 35, '#a9c3d6', { radius: 17 }), templateShape(256, 1013, 35, 35, '#26374a', { radius: 17 }), templateShape(301, 1013, 35, 35, '#6f7f8c', { radius: 17 }),
      templateText(500, 972, 447, 34, 'White / Black', { size: 26, color: '#333333', align: 'center' }),
      templateShape(683, 1013, 35, 35, '#f2f2f0', { radius: 17, stroke: '#cccccc' }), templateShape(728, 1013, 35, 35, '#222222', { radius: 17 }),
      templateShape(50, 1079, 897, 2, '#333333'),
      templateShape(498, 1081, 2, 133, '#333333'),
      templateText(50, 1131, 448, 38, '사계절 입을 수 있는 데님 소재', { size: 26, color: '#333333', align: 'center' }),
      templateText(500, 1131, 447, 38, '봄, 여름 가벼운 코튼 소재', { size: 26, color: '#333333', align: 'center' }),
      templateShape(50, 1213, 897, 2, '#333333'),
    ],
  }),

  // ---- W10 REAL REVIEW ✅ ----
  kiwiTemplate({
    id: 't5-10-realreview', label: 'W10 REAL REVIEW', h: 1279, bg: '#ffffff', elements: [
      templatePhoto(0, 0, 1000, 328, { role: 'hero' }),
      templateText(244, 119, 220, 120, 'Real', { font: 'Cormorant', italic: true, size: 100, color: '#a9c3d6', lineHeight: 120 }),
      templateText(475, 131, 290, 110, 'Review', { font: 'Cormorant', size: 88, color: '#2e2e2d', lineHeight: 110 }),
      templateText(0, 254, 1000, 40, '누적 리뷰 약 00,000건, 고객님들의 리뷰 모음', { size: 26, weight: 700, color: '#2e2e2d', align: 'center' }),
      templateShape(53, 327, 893, 2, '#333333'),
      templateText(66, 346, 300, 34, '★★★★★', { size: 26, color: '#a9c3d6' }),
      templateText(633, 348, 300, 34, 'Miri****님', { size: 22, color: '#6e6c67', align: 'right' }),
      templatePhoto(53, 396, 470, 357, { role: 'realWear' }),
      templateShape(523, 396, 423, 357, '#f7f6f3'),
      templateText(554, 424, 60, 50, '↳', { size: 36, color: '#a9c3d6' }),
      templateText(554, 486, 361, 42, '요즘 저의 최애 바지에요', { size: 27, weight: 700, color: '#2e2e2d' }),
      templateText(554, 538, 361, 220, '허리는 딱 잡아주고 와이드로 허벅지 군살은 싹 가려줘서 핏이 너무 좋아보여요! 심지어 신축성도 있어서 편하기까지 하네요..b 요즘 거의 맨날 이것만 입는 것 같아요~', { size: 24, color: '#4a4a48', lineHeight: 38 }),
      templateShape(53, 753, 893, 2, '#333333'),
      templateText(66, 806, 300, 34, '★★★★★', { size: 26, color: '#a9c3d6' }),
      templateText(633, 808, 300, 34, 'Miri****님', { size: 22, color: '#6e6c67', align: 'right' }),
      templatePhoto(53, 855, 470, 357, { role: 'realWear' }),
      templateShape(523, 855, 423, 357, '#f7f6f3'),
      templateText(554, 883, 60, 50, '↳', { size: 36, color: '#a9c3d6' }),
      templateText(554, 945, 361, 42, '어떤 옷이랑 입어도 찰떡같은', { size: 27, weight: 700, color: '#2e2e2d' }),
      templateText(554, 997, 361, 220, '어떤 상의랑 입어도 예뻐서 요즘 제 교복템 됐네요 일단 바지가 엄청 편한데 딱 깔끔 와이드한 핏이라 고민 안 하고 휘뚜루마뚜루 입기 좋아서 손이 엄청 자주 가요', { size: 24, color: '#4a4a48', lineHeight: 38 }),
      templateShape(53, 1213, 893, 2, '#333333'),
    ],
  }),

  // ---- W11 SIZE GUIDE ✅ ----
  kiwiTemplate({
    id: 't5-11-sizeguide', label: 'W11 SIZE GUIDE', h: 1288, bg: '#f7f6f3', elements: [
      templateShape(265, 94, 471, 2, '#333333'),
      templateText(0, 102, 1000, 91, 'Size Guide', { font: 'Cormorant', size: 88, color: '#2e2e2d', align: 'center', lineHeight: 91 }),
      templateShape(265, 208, 471, 2, '#333333'),
      templateText(0, 230, 1000, 40, 'Size Guide를 통해 나에게 꼭 맞는 핏을 찾아보세요', { size: 26, weight: 700, color: '#2e2e2d', align: 'center' }),
      templateShape(71, 337, 167, 167, '#9fb1bd'), templateText(71, 362, 167, 100, 'S', { font: 'Cormorant', size: 82, weight: 700, color: '#ffffff', align: 'center' }), templateText(71, 453, 167, 34, 'Small', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(301, 337, 167, 167, '#9fb1bd'), templateText(301, 362, 167, 100, 'M', { font: 'Cormorant', size: 82, weight: 700, color: '#ffffff', align: 'center' }), templateText(301, 453, 167, 34, 'Medium', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(531, 337, 167, 167, '#9fb1bd'), templateText(531, 362, 167, 100, 'L', { font: 'Cormorant', size: 82, weight: 700, color: '#ffffff', align: 'center' }), templateText(531, 453, 167, 34, 'Large', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(760, 337, 167, 167, '#9fb1bd'), templateText(760, 362, 167, 100, 'XL', { font: 'Cormorant', size: 82, weight: 700, color: '#ffffff', align: 'center' }), templateText(760, 453, 167, 34, 'X-Large', { size: 24, color: '#ffffff', align: 'center' }),
      templateText(71, 522, 167, 34, '44 ~ 슬림 55', { size: 22, weight: 700, color: '#333333', align: 'center' }),
      templateText(301, 522, 167, 34, '55 ~ 55 반', { size: 22, weight: 700, color: '#333333', align: 'center' }),
      templateText(531, 522, 167, 34, '슬림 66 ~ 66반', { size: 22, weight: 700, color: '#333333', align: 'center' }),
      templateText(760, 522, 167, 34, '66반 이상', { size: 22, weight: 700, color: '#333333', align: 'center' }),
      templateShape(52, 596, 895, 71, '#9fb1bd'),
      templateText(234, 615, 178, 34, 'S', { size: 24, color: '#ffffff', align: 'center' }), templateText(412, 615, 178, 34, 'M', { size: 24, color: '#ffffff', align: 'center' }),
      templateText(590, 615, 178, 34, 'L', { size: 24, color: '#ffffff', align: 'center' }), templateText(768, 615, 178, 34, 'XL', { size: 24, color: '#ffffff', align: 'center' }),
      templateShape(52, 752, 895, 1, '#e5e4e0'), templateShape(52, 837, 895, 1, '#e5e4e0'), templateShape(52, 922, 895, 1, '#e5e4e0'), templateShape(52, 1007, 895, 1, '#e5e4e0'), templateShape(52, 1092, 895, 1, '#e5e4e0'),
      templateText(52, 693, 182, 34, '허리', { size: 24, weight: 700, color: '#2e2e2d', align: 'center' }), templateText(234, 693, 178, 34, '29', { size: 24, color: '#333333', align: 'center' }), templateText(412, 693, 178, 34, '32', { size: 24, color: '#333333', align: 'center' }), templateText(590, 693, 178, 34, '34.5', { size: 24, color: '#333333', align: 'center' }), templateText(768, 693, 178, 34, '36', { size: 24, color: '#333333', align: 'center' }),
      templateText(52, 778, 182, 34, '엉덩이', { size: 24, weight: 700, color: '#2e2e2d', align: 'center' }), templateText(234, 778, 178, 34, '44', { size: 24, color: '#333333', align: 'center' }), templateText(412, 778, 178, 34, '46', { size: 24, color: '#333333', align: 'center' }), templateText(590, 778, 178, 34, '47.5', { size: 24, color: '#333333', align: 'center' }), templateText(768, 778, 178, 34, '49', { size: 24, color: '#333333', align: 'center' }),
      templateText(52, 863, 182, 34, '밑위', { size: 24, weight: 700, color: '#2e2e2d', align: 'center' }), templateText(234, 863, 178, 34, '28', { size: 24, color: '#333333', align: 'center' }), templateText(412, 863, 178, 34, '30', { size: 24, color: '#333333', align: 'center' }), templateText(590, 863, 178, 34, '30', { size: 24, color: '#333333', align: 'center' }), templateText(768, 863, 178, 34, '30', { size: 24, color: '#333333', align: 'center' }),
      templateText(52, 948, 182, 34, '허벅지', { size: 24, weight: 700, color: '#2e2e2d', align: 'center' }), templateText(234, 948, 178, 34, '27', { size: 24, color: '#333333', align: 'center' }), templateText(412, 948, 178, 34, '28', { size: 24, color: '#333333', align: 'center' }), templateText(590, 948, 178, 34, '29', { size: 24, color: '#333333', align: 'center' }), templateText(768, 948, 178, 34, '30', { size: 24, color: '#333333', align: 'center' }),
      templateText(52, 1033, 182, 34, '밑단', { size: 24, weight: 700, color: '#2e2e2d', align: 'center' }), templateText(234, 1033, 178, 34, '23', { size: 24, color: '#333333', align: 'center' }), templateText(412, 1033, 178, 34, '23.5', { size: 24, color: '#333333', align: 'center' }), templateText(590, 1033, 178, 34, '24', { size: 24, color: '#333333', align: 'center' }), templateText(768, 1033, 178, 34, '24.5', { size: 24, color: '#333333', align: 'center' }),
      templateText(52, 1118, 182, 34, '총장', { size: 24, weight: 700, color: '#2e2e2d', align: 'center' }), templateText(234, 1118, 178, 34, '103', { size: 24, color: '#333333', align: 'center' }), templateText(412, 1118, 178, 34, '104', { size: 24, color: '#333333', align: 'center' }), templateText(590, 1118, 178, 34, '105', { size: 24, color: '#333333', align: 'center' }), templateText(768, 1118, 178, 34, '105', { size: 24, color: '#333333', align: 'center' }),
      templateShape(52, 1176, 895, 2, '#333333'),
      templateText(0, 1213, 1000, 34, '상기 사이즈는 측정방법에 따라 오차가 있을 수 있습니다', { size: 22, color: '#9a9893', align: 'center' }),
    ],
  }),
];

export const DETAIL_PAGE_TEMPLATE_SETS = [
  { id: 'basic-knit', label: '베이직 니트', desc: '겨울 골지 니트 · 13컷', accent: '#d9d9da', frames: DETAIL_PAGE_TEMPLATE },
  { id: 'magazine', label: '매거진 세리프', desc: '세리프 매거진 · 6컷', accent: '#efefef', frames: DETAIL_PAGE_TEMPLATE_2 },
  { id: 'editorial-blue', label: '에디토리얼 블루', desc: '더스티 블루 · 10컷', accent: '#9a9a9c', frames: DETAIL_PAGE_TEMPLATE_3 },
  { id: 'modern-minimal', label: '모던 미니멀', desc: '모던 미니멀 · 12컷', accent: '#e4e4e0', frames: DETAIL_PAGE_TEMPLATE_4 },
  { id: 'denim-casual', label: '데님 캐주얼', desc: '미리팬츠 데님 · 11컷', accent: '#3a3a39', frames: DETAIL_PAGE_TEMPLATE_5 },
];

export function buildDetailPageTemplateSet(setId, idFn) {
  const set = DETAIL_PAGE_TEMPLATE_SETS.find((entry) => entry.id === setId) || DETAIL_PAGE_TEMPLATE_SETS[0];
  return set.frames.map((frame) => buildFrameBlock(frame, idFn));
}

// 상세페이지 세트의 개별 프레임을 id 로 찾는다(모달에서 프레임 하나씩 클릭·드래그 삽입할 때).
const DETAIL_PAGE_FRAME_BY_ID = new Map(
  DETAIL_PAGE_TEMPLATE_SETS.flatMap((set) => set.frames.map((frame) => [frame.id, frame])),
);
export function getDetailPageFrame(id) {
  return DETAIL_PAGE_FRAME_BY_ID.get(id) || null;
}


/* 13프레임을 순서대로 EditorBlock 으로 만든다(문서 삽입용). */
export function buildDetailPageTemplateBlocks(idFn) {
  return DETAIL_PAGE_TEMPLATE.map((frame) => buildFrameBlock(frame, idFn));
}

export const OBJECT_LIBRARY_ITEMS = [
  { id: 'text-box', label: '반투명 텍스트 박스', preview: 'TEXT' },
  { id: 'single-bubble', label: '말풍선', preview: '말풍선' },
  { id: 'qa-bubbles', label: 'Q&A 말풍선', preview: 'Q · A' },
  { id: 'divider', label: '구분선', preview: '—' },
  { id: 'arrow-callout', label: '화살표 콜아웃', preview: 'POINT →' },
  { id: 'label-badge', label: 'POINT 배지', preview: 'POINT' },
];

const clamp01 = (value) => Math.min(1, Math.max(0, Number.isFinite(Number(value)) ? Number(value) : 1));

export function normalizeHexColor(value) {
  const raw = String(value || '').trim().replace(/^#/, '');
  if (/^[0-9a-f]{3}$/i.test(raw)) {
    return `#${raw.split('').map((character) => character + character).join('').toUpperCase()}`;
  }
  if (/^[0-9a-f]{6}$/i.test(raw)) return `#${raw.toUpperCase()}`;
  return null;
}

export function colorWithOpacity(color = '#ffffff', opacity = 1) {
  const alpha = clamp01(opacity);
  if (alpha >= 1) return color || '#ffffff';
  const raw = String(color || '#ffffff').trim();
  const short = /^#([0-9a-f]{3})$/i.exec(raw);
  const full = /^#([0-9a-f]{6})$/i.exec(raw);
  const hex = short
    ? short[1].split('').map((c) => c + c).join('')
    : full?.[1];
  if (!hex) return raw;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${+alpha.toFixed(3)})`;
}

export function buildFrameBlock(frameOrId, idFn) {
  const definition = typeof frameOrId === 'string'
    ? FRAME_LIBRARY_ITEMS.find((item) => item.id === frameOrId)
    : frameOrId;
  const hasNativeElements = Array.isArray(definition?.elements) && definition.elements.length > 0;
  const hasPhotoSlots = Array.isArray(definition?.slots) && definition.slots.length > 0;
  if (!definition || (!hasNativeElements && !hasPhotoSlots)) {
    throw new Error(`[editorLibrary] unknown frame: ${typeof frameOrId === 'string' ? frameOrId : frameOrId?.id}`);
  }
  const sourceElements = definition.elements || definition.slots.map((item) => ({
    ...item,
    type: 'image',
    src: null,
    frameSlot: true,
  }));
  return {
    id: idFn('b'),
    name: definition.label,
    ...(definition.template ? { templateId: definition.id } : {}),
    kind: 'styling',
    contentRole: 'custom',
    bg: definition.bg || '#ffffff',
    bgOpacity: 1,
    h: definition.h,
    elements: sourceElements.map((item) => {
      const element = {
        id: idFn('el'),
        ...item,
        x: item.x,
        y: item.y,
        w: item.w,
        h: item.h,
        ...(item.type === 'image' ? {
          src: item.src || null,
          radius: item.radius ?? 0,
          frameSlot: Boolean(item.frameSlot),
        } : {}),
      };
      if (!definition.template || element.type !== 'image' || !element.frameSlot) return element;
      const { exampleImage: _exampleImage, ...emptyFrame } = element;
      return { ...emptyFrame, src: null };
    }),
  };
}

const LEGACY_KIWI_OVERLAY = /\/kiwi-(\d+)-overlay\.png(?:\?.*)?$/;

function copyLegacySlotSource(slot, legacySlot) {
  if (!legacySlot?.src) return slot.exampleImage ? { ...slot, src: null } : slot;
  const crop = legacySlot.crop && legacySlot.w && legacySlot.h ? {
    ox: Math.round(legacySlot.crop.ox * slot.w / legacySlot.w),
    oy: Math.round(legacySlot.crop.oy * slot.h / legacySlot.h),
    iw: Math.round(legacySlot.crop.iw * slot.w / legacySlot.w),
    ih: Math.round(legacySlot.crop.ih * slot.h / legacySlot.h),
  } : undefined;
  return {
    ...slot,
    src: legacySlot.src,
    ...(crop ? { crop } : {}),
    ...(legacySlot.cutType ? { cutType: legacySlot.cutType } : {}),
    ...(legacySlot.userUploaded ? { userUploaded: true } : {}),
    ...(legacySlot.wardrobeGroup ? { wardrobeGroup: legacySlot.wardrobeGroup } : {}),
    ...(legacySlot.hidden ? { hidden: true } : {}),
    ...(legacySlot.opacity != null ? { opacity: legacySlot.opacity } : {}),
  };
}

/**
 * Early Kiwi frames were saved as editable photo slots plus a locked screenshot.
 * Replace those blocks with native editor elements while preserving filled photos.
 */
export function upgradeLegacyKiwiTemplateBlocks(blocks, idFn) {
  if (!Array.isArray(blocks)) return blocks;
  return blocks.map((block) => {
    const legacyOverlay = block?.elements?.find((element) => (
      element.type === 'template-overlay' && LEGACY_KIWI_OVERLAY.test(String(element.src || ''))
    ));
    const templateNumber = legacyOverlay
      ? LEGACY_KIWI_OVERLAY.exec(String(legacyOverlay.src || ''))?.[1]
      : null;
    const definition = templateNumber
      ? FRAME_LIBRARY_ITEMS.find((item) => item.id === `kiwi-${templateNumber}`)
      : null;
    if (!definition) return block;

    const legacySlots = block.elements.filter((element) => element.type === 'image' && element.frameSlot);
    let slotIndex = 0;
    const upgraded = buildFrameBlock(definition, idFn);
    return {
      ...upgraded,
      id: block.id,
      h: Math.max(upgraded.h, Number(block.h) || 0),
      elements: upgraded.elements.map((element) => {
        if (element.type !== 'image' || !element.frameSlot) return element;
        return copyLegacySlotSource(element, legacySlots[slotIndex++]);
      }),
    };
  });
}

export function buildImageBlock(image, idFn) {
  if (!image?.src) throw new Error('[editorLibrary] image source is required');
  const blockId = idFn('b');
  const sourceWidth = Number(image.width || image.w);
  const sourceHeight = Number(image.height || image.h);
  const imageWidth = 880;
  const imageHeight = sourceWidth > 0 && sourceHeight > 0
    ? Math.max(1, Math.round(imageWidth * sourceHeight / sourceWidth))
    : 1320;
  const element = {
    id: idFn('el'),
    type: 'image',
    x: 60,
    y: 50,
    w: imageWidth,
    h: imageHeight,
    src: image.src,
    radius: 0,
    ...(image.cutType ? { cutType: image.cutType } : {}),
    ...(image.userUploaded ? { userUploaded: true } : {}),
    ...(image.wardrobeGroup ? { wardrobeGroup: image.wardrobeGroup } : {}),
  };
  return {
    id: blockId,
    name: '이미지',
    kind: 'styling',
    contentRole: 'custom',
    bg: '#ffffff',
    bgOpacity: 1,
    h: imageHeight + 100,
    elements: [element],
  };
}

const text = (idFn, groupId, x, y, w, h, value, style = {}) => ({
  id: idFn('el'), type: 'text', groupId, x, y, w, h, text: value, style,
});
const rect = (idFn, groupId, x, y, w, h, fill, radius, opacity = 1) => ({
  id: idFn('el'), type: 'shape', shape: 'rect', groupId, x, y, w, h, fill, radius, opacity,
});
const speechBubble = (idFn, groupId, x, y, w, h, value, style, fill, bubbleFit, flipX = false) => ({
  id: idFn('el'), type: 'text', shape: 'bubble', groupId, x, y, w, h, text: value, style, fill,
  stroke: DEFAULT_BUBBLE_STROKE, strokeWidth: DEFAULT_BUBBLE_STROKE_WIDTH, radius: DEFAULT_BUBBLE_RADIUS,
  bubbleFit, ...(flipX ? { flipX: true } : {}),
});
const line = (idFn, groupId, x, y, w, shape = 'line') => ({
  id: idFn('el'), type: 'line', shape, groupId, x, y, w, h: 24, stroke: '#0e0d14', strokeWidth: 3,
});

export function buildObjectPreset(presetId, { x = 120, y = 120, idFn }) {
  if (!OBJECT_LIBRARY_ITEMS.some((item) => item.id === presetId)) {
    throw new Error(`[editorLibrary] unknown object preset: ${presetId}`);
  }
  const groupId = presetId === 'qa-bubbles' ? null : idFn('grp');
  let elements;
  if (presetId === 'text-box') {
    elements = [
      rect(idFn, groupId, x, y, 520, 150, '#0e0d14', 18, 0.94),
      text(idFn, groupId, x + 30, y + 38, 460, 72, '강조할 내용을 입력하세요', { size: 26, weight: 600, color: '#ffffff', lineHeight: 36 }),
    ];
  } else if (presetId === 'single-bubble') {
    elements = [
      {
        ...speechBubble(idFn, groupId, x, y, 320, 100, '내용을 입력하세요',
          { size: 20, weight: 500, color: '#000000', lineHeight: 29 }, '#FFFFFF',
          { maxWidth: 560, padX: 24, padTop: 20, padBottom: 38, anchor: 'left' }),
        stroke: '#000000',
        strokeWidth: 2,
        radius: 28,
      },
    ];
  } else if (presetId === 'qa-bubbles') {
    elements = [
      speechBubble(idFn, idFn('grp'), x, y, 380, 104, 'Q. 가장 궁금한 점은?',
        { size: 20, weight: 600, color: '#0e0d14' }, '#ffffff',
        { maxWidth: 620, padX: 24, padTop: 22, padBottom: 42, anchor: 'left' }),
      speechBubble(idFn, idFn('grp'), x + 110, y + 112, 520, 142, 'A. 답변을 간결하게 입력하세요.',
        { size: 19, weight: 500, color: '#0e0d14', lineHeight: 29 }, '#dcecff',
        { maxWidth: 660, padX: 30, padTop: 26, padBottom: 50, anchor: 'right' }, true),
    ];
  } else if (presetId === 'divider') {
    elements = [line(idFn, groupId, x, y + 12, 620)];
  } else if (presetId === 'arrow-callout') {
    elements = [
      text(idFn, groupId, x, y, 180, 36, 'POINT', { size: 22, weight: 700, color: '#0e0d14', tracking: 1 }),
      line(idFn, groupId, x + 150, y + 5, 240, 'arrow-r'),
    ];
  } else {
    elements = [
      rect(idFn, groupId, x, y, 190, 62, '#0e0d14', 31),
      text(idFn, groupId, x + 20, y + 18, 150, 28, 'POINT', { size: 18, weight: 700, color: '#ffffff', align: 'center', tracking: 1.5 }),
    ];
  }
  const minX = Math.min(...elements.map((element) => element.x));
  const maxX = Math.max(...elements.map((element) => element.x + element.w));
  const minY = Math.min(...elements.map((element) => element.y));
  const shiftX = minX < 0 ? -minX : maxX > 1000 ? 1000 - maxX : 0;
  const shiftY = minY < 0 ? -minY : 0;
  return elements.map((element) => ({
    ...element,
    x: element.x + shiftX,
    y: element.y + shiftY,
    libraryItemId: presetId,
  }));
}

export function objectPresetInitialSelectionIds(presetId, elements) {
  const ids = (elements || []).map((element) => element.id).filter(Boolean);
  return presetId === 'qa-bubbles' ? ids.slice(0, 1) : ids;
}

export function encodeWardrobeImage(image, dimensions = {}) {
  const payload = { src: image?.src || null, cutType: image?.cutType || null };
  if (image?.id) payload.id = image.id;
  if (image?.userUploaded) payload.userUploaded = true;
  if (image?.wardrobeGroup) payload.wardrobeGroup = image.wardrobeGroup;
  const width = Number(dimensions.width || image?.width);
  const height = Number(dimensions.height || image?.height);
  if (width > 0) payload.width = width;
  if (height > 0) payload.height = height;
  return JSON.stringify(payload);
}

export function decodeWardrobeImage(value) {
  try {
    const parsed = JSON.parse(value || '');
    if (!parsed || typeof parsed.src !== 'string' || !parsed.src) return null;
    const image = { src: parsed.src, cutType: parsed.cutType || null };
    if (parsed.id) image.id = parsed.id;
    if (parsed.userUploaded) image.userUploaded = true;
    if (parsed.wardrobeGroup) image.wardrobeGroup = parsed.wardrobeGroup;
    if (Number(parsed.width) > 0) image.width = Number(parsed.width);
    if (Number(parsed.height) > 0) image.height = Number(parsed.height);
    return image;
  } catch {
    return null;
  }
}
