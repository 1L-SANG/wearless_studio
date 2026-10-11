export function canvasAiProgressVisible(job) {
  return Boolean(job && !job.dismissed && ['cancel', 'busy', 'failed'].includes(job.status));
}

export function canvasAiSlotArrivalVisible(element, job) {
  return Boolean(job?.isSlot && job.status === 'arrived' && !job.dismissed
    && element?.src && element.src === (job.result?.src || job.response?.data?.src));
}

export function canvasAiProgressSize(rect) {
  return { width: Math.min(150, Math.max(40, rect.width - 16)), height: Math.max(40, rect.height - 16) };
}

export function canvasAiSourceDimensions(element, image, natural = null) {
  for (const candidate of [natural, image, { width: element?.w, height: element?.h }]) {
    if (Number(candidate?.width) > 0 && Number(candidate?.height) > 0) {
      return { width: Number(candidate.width), height: Number(candidate.height) };
    }
  }
  return { width: 1, height: 1 };
}

// 새 블록은 원본 요소나 요청 크기 대신 결과 사진의 비율을 따른다.
export function canvasAiResultDimensions(image, natural = null) {
  for (const candidate of [natural, image]) {
    const width = Number(candidate?.width);
    const height = Number(candidate?.height);
    if (Number.isFinite(width) && width > 0 && Number.isFinite(height) && height > 0) {
      return { width, height };
    }
  }
  return { width: null, height: null };
}

export function readCanvasAiResultDimensions(src, { timeoutMs = 12000, createImage = () => new Image() } = {}) {
  return new Promise((resolve) => {
    if (!src) { resolve(null); return; }
    let image;
    try { image = createImage(); } catch { resolve(null); return; }
    let settled = false;
    const finish = (dimensions) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      image.onload = null;
      image.onerror = null;
      resolve(dimensions);
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    image.onload = () => finish({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => finish(null);
    try {
      image.src = src;
      if (image.complete && image.naturalWidth > 0 && image.naturalHeight > 0) image.onload?.();
    } catch { finish(null); }
  });
}

export function canFillCanvasAiSlot(blocks, job) {
  const element = blocks?.find((block) => block.id === job.blockId)?.elements
    ?.find((item) => item.id === job.elementId);
  return Boolean(element && element.type === 'image' && !element.src);
}

export function replaceCanvasAiImage(blocks, job, image) {
  if (!image?.src) return blocks;
  const block = blocks.find((item) => item.id === job.blockId);
  const element = block?.elements?.find((item) => item.id === job.elementId);
  if (!element || element.type !== 'image' || element.src !== job.sourceSrc) return blocks;
  return blocks.map((item) => item.id !== job.blockId ? item : { ...item,
    elements: item.elements.map((el) => el.id !== job.elementId ? el : { ...el, src: image.src, crop: null }),
  });
}
