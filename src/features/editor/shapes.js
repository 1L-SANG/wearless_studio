/* =============================================================
   features/editor/shapes.js — SVG path 도형 정의.
   캔버스 렌더(Editor.jsx)와 오브젝트 탭 글리프(EditorPanels.jsx)가
   같은 path 를 공유해 미리보기와 실제 도형이 어긋나지 않게 한다.
   viewBox 0 0 100 100 기준 — preserveAspectRatio:none 으로 요소
   w/h 에 맞춰 늘리고, 테두리는 non-scaling-stroke 로 균일하게 그린다.
   (circle/rect 는 div, triangle 은 기존 polygon 렌더를 유지)
   ============================================================= */
export const SHAPE_D = {
  diamond: 'M50 2 L98 50 L50 98 L2 50 Z',
  hexagon: 'M25 5 L75 5 L97 50 L75 95 L25 95 L3 50 Z',
  star: 'M50 2 L60.8 35.2 L95.6 35.2 L67.4 55.7 L78.2 88.8 L50 68.3 L21.8 88.8 L32.6 55.7 L4.4 35.2 L39.2 35.2 Z',
  heart: 'M50 90 C24 68 6 50 6 30 C6 14 18 4 31 4 C40 4 47 9 50 17 C53 9 60 4 69 4 C82 4 94 14 94 30 C94 50 76 68 50 90 Z',
  bubble: 'M14 6 H86 Q96 6 96 16 V58 Q96 68 86 68 H40 L22 88 L26 68 H14 Q4 68 4 58 V16 Q4 6 14 6 Z',
};
export default SHAPE_D;
