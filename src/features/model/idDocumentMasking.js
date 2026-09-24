import { rrnRectInFrame } from './idCardGeometry.js';

export const ID_DOCUMENT_TYPES = Object.freeze([{ value: 'rrc', label: '주민등록증' }]);

// 안내 틀에 맞춘 프레임에 가림 막대를 구운 결과만 올려요.
export function burnGuideMask(canvas, source, width, height, quality = 0.92) {
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(source, 0, 0, width, height);
  const r = rrnRectInFrame(width, height);
  ctx.fillStyle = '#111';
  ctx.fillRect(r.x, r.y, r.w, r.h);
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
}
