const FIT_HOTSPOTS = Object.freeze({
  top: Object.freeze({
    fit: Object.freeze([{ id: 'top-fit', label: '몸통·소매 핏' }]),
    length: Object.freeze([{ id: 'top-hem', label: '상의 밑단' }]),
  }),
  outer: Object.freeze({
    fit: Object.freeze([{ id: 'outer-fit', label: '몸통·소매 핏' }]),
    length: Object.freeze([{ id: 'outer-hem', label: '아우터 밑단' }]),
  }),
  pants: Object.freeze({
    cut: Object.freeze([{ id: 'pants-cut', label: '바지 통·실루엣' }]),
    length: Object.freeze([{ id: 'pants-hem', label: '바지 밑단' }]),
  }),
  skirt: Object.freeze({
    silhouette: Object.freeze([{ id: 'skirt-shape', label: '스커트 실루엣' }]),
    length: Object.freeze([{ id: 'skirt-hem', label: '스커트 밑단' }]),
  }),
  dress: Object.freeze({
    silhouette: Object.freeze([{ id: 'dress-shape', label: '원피스 실루엣' }]),
    length: Object.freeze([{ id: 'dress-hem', label: '원피스 밑단' }]),
  }),
});

export function fitHotspotsFor(category, axis) {
  return FIT_HOTSPOTS[category]?.[axis] || [];
}
