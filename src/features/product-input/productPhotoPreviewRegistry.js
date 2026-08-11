const isObjectUrl = (url) => typeof url === 'string' && url.startsWith('blob:');

export async function createProductPhotoThumbnail(sourceUrl, maxEdge = 400) {
  const image = new Image();
  image.src = sourceUrl;
  await image.decode();

  const scale = Math.min(1, maxEdge / Math.max(image.naturalWidth, image.naturalHeight));
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  canvas.getContext('2d').drawImage(image, 0, 0, width, height);

  const blob = await new Promise((resolve, reject) => {
    canvas.toBlob((value) => {
      if (value) resolve(value);
      else reject(new Error('상품 사진 미리보기를 만들지 못했어요.'));
    }, 'image/jpeg', 0.82);
  });
  return URL.createObjectURL(blob);
}

export function createProductPhotoPreviewRegistry({
  onChange = () => {},
  createThumbnail = createProductPhotoThumbnail,
  revokeObjectUrl = (url) => URL.revokeObjectURL(url),
} = {}) {
  const entries = new Map();

  const revokeEntry = (entry) => {
    if (entry.thumbnailUrl) revokeObjectUrl(entry.thumbnailUrl);
    if (entry.ownsOriginal) revokeObjectUrl(entry.originalUrl);
  };

  const track = (imageId, originalUrl) => {
    const current = entries.get(imageId);
    if (current?.originalUrl === originalUrl) return;
    if (current) revokeEntry(current);

    const entry = {
      originalUrl,
      ownsOriginal: isObjectUrl(originalUrl),
      thumbnailUrl: null,
      failed: false,
    };
    entries.set(imageId, entry);
    if (!originalUrl) return;

    void createThumbnail(originalUrl).then((thumbnailUrl) => {
      if (entries.get(imageId) !== entry) {
        revokeObjectUrl(thumbnailUrl);
        return;
      }
      entry.thumbnailUrl = thumbnailUrl;
      onChange();
    }).catch(() => {
      if (entries.get(imageId) !== entry) return;
      entry.failed = true;
      onChange();
    });
  };

  const release = (imageId) => {
    const entry = entries.get(imageId);
    if (!entry) return;
    entries.delete(imageId);
    revokeEntry(entry);
  };

  return {
    sync(images) {
      const liveIds = new Set(images.map((image) => image.id));
      for (const imageId of entries.keys()) {
        if (!liveIds.has(imageId)) release(imageId);
      }
      images.forEach((image) => track(image.id, image.src));
    },
    displayUrl(imageId, originalUrl) {
      const entry = entries.get(imageId);
      if (!entry || entry.originalUrl !== originalUrl) return null;
      return entry?.thumbnailUrl || (entry?.failed ? entry.originalUrl : null);
    },
    release,
    dispose() {
      for (const imageId of [...entries.keys()]) release(imageId);
    },
  };
}
