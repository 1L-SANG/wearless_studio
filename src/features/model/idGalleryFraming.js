import { guideRectPercent } from './idCardGeometry.js';
import { burnGuideMask } from './idDocumentMasking.js';

export const GALLERY_FRAME = Object.freeze({ width: 1200, height: 1600 });

// 화면과 출력 캔버스가 같은 프레임 좌표를 써요.
export function galleryImageRect(width, height, zoom = 1, offset = { x: 0, y: 0 }) {
  const scale = Math.min(GALLERY_FRAME.width / width, GALLERY_FRAME.height / height) * zoom;
  const w = width * scale, h = height * scale;
  return { x: (GALLERY_FRAME.width - w) / 2 + offset.x, y: (GALLERY_FRAME.height - h) / 2 + offset.y, w, h };
}

export function setCaptureFrameStyle(element, width, height) {
  const guide = guideRectPercent(width, height);
  element.style.setProperty('--frame-aspect', `${width} / ${height}`);
  element.style.setProperty('--frame-ratio', String(width / height));
  for (const [key, value] of Object.entries(guide)) element.style.setProperty(`--guide-${key}`, `${value}%`);
}

export function frameGalleryPhoto(canvas, output, image, zoom, offset) {
  const { width, height } = GALLERY_FRAME;
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#111';
  ctx.fillRect(0, 0, width, height);
  const rect = galleryImageRect(image.naturalWidth, image.naturalHeight, zoom, offset);
  ctx.drawImage(image, rect.x, rect.y, rect.w, rect.h);
  return burnGuideMask(output, canvas, width, height);
}
