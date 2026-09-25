const SOURCE_SLOT_ORDER = { Front: 0, Back: 1, Detail: 2, BackDetail: 3 };

export function orderAnalysisPhotos(images) {
  return [...(images || [])].sort((a, b) => (SOURCE_SLOT_ORDER[a.slot] ?? 99) - (SOURCE_SLOT_ORDER[b.slot] ?? 99));
}

export function selectPublicAnalysisPhotos(images, limit = 4) {
  const source = Array.isArray(images) ? images : [];
  const selected = [];

  const takeFirst = (slot) => {
    const photo = source.find((candidate) => candidate?.slot === slot && !selected.includes(candidate));
    if (photo) selected.push(photo);
  };

  takeFirst('Front');
  takeFirst('Back');
  for (const photo of source) {
    if (selected.length >= limit) break;
    if (!selected.includes(photo)) selected.push(photo);
  }
  return orderAnalysisPhotos(selected.slice(0, limit));
}
