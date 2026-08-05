export const mineImageUrl = (image) => image?.url || image;

export const normalizeMineImages = (images) => (
  (images || []).map(mineImageUrl).filter(Boolean)
);

export function promoteMineImage(images, selected) {
  const selectedUrl = mineImageUrl(selected);
  if (!selectedUrl) return [];
  return [
    selectedUrl,
    ...normalizeMineImages(images).filter((url) => url !== selectedUrl),
  ];
}
