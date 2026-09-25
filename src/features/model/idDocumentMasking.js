import { guideRectInFrame, rrnRectInFrame } from './idCardGeometry.js';

export const ID_DOCUMENT_TYPES = Object.freeze([{ value: 'rrc', label: '주민등록증' }]);

export function clampMaskRatio({ xr, yr, wr, hr }) {
  const w = Math.min(1, Math.max(.02, wr));
  const h = Math.min(1, Math.max(.02, hr));
  return { xr: Math.max(0, Math.min(1 - w, xr)), yr: Math.max(0, Math.min(1 - h, yr)), wr: w, hr: h };
}

export function buildMaskedBlob(canvas, image, region) {
  const width = image.naturalWidth, height = image.naturalHeight;
  if (!width || !height || !region || !['xr', 'yr', 'wr', 'hr'].every(k => Number.isFinite(region[k]))
    || region.xr < 0 || region.yr < 0 || region.wr < .02 || region.hr < .02
    || region.xr + region.wr > 1.000001 || region.yr + region.hr > 1.000001) {
    throw new Error('가릴 위치를 사진 안에 지정해 주세요.');
  }
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(image, 0, 0, width, height);
  const x = Math.floor(region.xr * width), y = Math.floor(region.yr * height);
  ctx.fillStyle = '#111';
  ctx.fillRect(x, y, Math.ceil((region.xr + region.wr) * width) - x, Math.ceil((region.yr + region.hr) * height) - y);
  return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .92));
}

// 원본 프레임은 단말 안의 필수 편집 단계로만 전달한다. 이 결과를 업로드하지 않는다.
export function captureFrameBlob(canvas, source, width, height) {
  const g = guideRectInFrame(width, height);
  canvas.width = g.w; canvas.height = g.h;
  canvas.getContext('2d').drawImage(source, g.x, g.y, g.w, g.h, 0, 0, g.w, g.h);
  return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .92));
}

// 구버전 호환용. 새 촬영·앨범 경로는 고정 좌표 대신 buildMaskedBlob을 사용한다.
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
