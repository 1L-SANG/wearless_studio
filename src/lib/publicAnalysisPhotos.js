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
  return selected.slice(0, limit);
}
