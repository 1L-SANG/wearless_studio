import { attachResponseError, clientApiFetch, readClientResponse } from '../../lib/api/clientRequest.js';
import { recordClientAction } from '../../lib/clientEvents.js';
/* 에디터 다운로드 — 블록 DOM 을 그대로 PNG 로 캡처한다 (서버 렌더 job 없음).
 *
 * 왜 클라 캡처인가: 에디터 화면 자체가 완성된 렌더라, 헤드리스 크롬 같은 서버 인프라 없이
 * 화면 그대로를 저장하는 게 가장 작고 정확한 경로다. 이미지 픽셀을 캔버스로 읽으려면 CORS 가
 * 필요한데 `/v1/assets/{id}/file` 은 R2 로 302 라 보장이 없다 → 같은 계약의 바이트 직접
 * 서빙 라우트 `/v1/assets/{id}/bytes` 로 바꿔 읽는다 (routes.py `_tone_bytes` 와 같은 근거).
 *
 * 이미지 실패 정책: 사진 바이트를 읽지 못하거나 캡처에서 사진이 빠지면 저장을 중단한다.
 * 원래 빈 블록은 저장하고, 실패한 다운로드는 새 캐시로 재시도할 수 있다.
 *
 * 경로 3개: ① 블록 1개 PNG(퀵바) ② 전체 세로 이어붙인 긴 PNG ③ 블록별 PNG ZIP (다운로드 모달).
 */
import { getFontEmbedCSS, toSvg } from 'html-to-image';
import { collectExportImageProbes, rasterizeExportSvg } from './editorExportRaster.js';
import JSZip from 'jszip';
import { ASSET_CACHE_VERSION } from '../../lib/assetUrl.js';
import { notarize } from './publicationNotarize.js';

export const EXPORT_WIDTH = 1000; // .ed-canvas 설계 폭 — 캡처는 화면 scale 과 무관하게 이 폭 기준
const PIXEL_RATIO = 2; // 상세페이지 업로드 기준 2000px — 쇼핑몰 권장폭(860~1280)을 여유 있게 커버
export const MAX_CANVAS_DIM = 30000; // 캔버스 한 변 한계(32767) 아래 여유 — 넘으면 긴 PNG 대신 ZIP 안내
export const MAX_CAPTURE_DIM = 16384; // html-to-image가 이 길이를 넘으면 자동 축소한다
export const MAX_CANVAS_AREA = 16_777_216; // Safari가 빈 캔버스로 돌려줄 수 있는 총 픽셀 상한
export const MAX_SINGLE_PAGE_HEIGHT = 20000;
export const MAX_SPLIT_HEIGHT = 3000;
export const MAX_JPEG_BYTES = 3 * 1024 * 1024;
export const JPEG_QUALITY = 0.9;
export const ORIGINAL_PNG_MAX_HEIGHT = Math.min(
  MAX_CAPTURE_DIM, MAX_CANVAS_DIM, Math.floor(MAX_CANVAS_AREA / (EXPORT_WIDTH * PIXEL_RATIO)),
);
export const EXPORT_SETTINGS_KEY = 'wearless.editor.export.v1';

export const EXPORT_PRESETS = Object.freeze([
  { id: 'smartstore', width: 860, label: '스마트스토어·통용' },
  { id: 'coupang', width: 780, label: '쿠팡' },
  { id: 'high', width: 1290, label: '고화질', desc: '휴대폰에서 더 선명, 파일이 큼' },
  { id: 'original', width: 2000, label: '원본 PNG', original: true },
]);

export const DEFAULT_EXPORT_SETTINGS = Object.freeze({
  preset: 'smartstore',
  format: 'jpeg',
  delivery: 'split',
});

const EXPORT_FORMATS = new Set(['jpeg', 'png']);
const EXPORT_DELIVERIES = new Set(['split', 'single']);

/* ---- 순수 헬퍼 (테스트 대상) ---- */

export function exportPreset(presetId) {
  return EXPORT_PRESETS.find((preset) => preset.id === presetId) || EXPORT_PRESETS[0];
}

export function exportScaleForPreset(presetId) {
  return exportPreset(presetId).width / EXPORT_WIDTH;
}

export function normalizeExportSettings(value) {
  const preset = exportPreset(value?.preset).id;
  const original = exportPreset(preset).original;
  return {
    preset,
    format: original ? 'png' : (EXPORT_FORMATS.has(value?.format) ? value.format : DEFAULT_EXPORT_SETTINGS.format),
    delivery: EXPORT_DELIVERIES.has(value?.delivery) ? value.delivery : DEFAULT_EXPORT_SETTINGS.delivery,
  };
}

function resolveExportStorage(storage) {
  if (storage !== undefined) return storage;
  return typeof localStorage === 'undefined' ? null : localStorage;
}

export function readExportSettings(storage) {
  try {
    const target = resolveExportStorage(storage);
    if (!target) return { ...DEFAULT_EXPORT_SETTINGS };
    const raw = target.getItem(EXPORT_SETTINGS_KEY);
    return raw ? normalizeExportSettings(JSON.parse(raw)) : { ...DEFAULT_EXPORT_SETTINGS };
  } catch {
    return { ...DEFAULT_EXPORT_SETTINGS };
  }
}

export function writeExportSettings(storage, settings) {
  try {
    const target = resolveExportStorage(storage);
    if (!target) return false;
    target.setItem(EXPORT_SETTINGS_KEY, JSON.stringify(normalizeExportSettings(settings)));
    return true;
  } catch {
    return false;
  }
}

export function groupScaledBlockHeights(blockHeights, ratio, maxHeight = MAX_SPLIT_HEIGHT) {
  const groups = [];
  let current = 0;
  const flush = () => {
    if (current > 0) groups.push(current);
    current = 0;
  };
  for (const cssHeight of blockHeights) {
    let height = Math.max(0, Math.trunc((Number(cssHeight) || 0) * ratio));
    if (height <= 0) continue;
    if (height <= maxHeight) {
      if (current + height > maxHeight) flush();
      current += height;
      continue;
    }
    flush();
    while (height > maxHeight) {
      groups.push(maxHeight);
      height -= maxHeight;
    }
    current = height;
  }
  flush();
  return groups;
}

export function singlePageFallbackReason({
  width,
  height,
  maxCanvasDim = MAX_CAPTURE_DIM,
  maxCanvasArea = MAX_CANVAS_AREA,
  maxPageHeight = MAX_SINGLE_PAGE_HEIGHT,
}) {
  if (height > maxPageHeight) {
    return `세로가 ${maxPageHeight.toLocaleString('ko-KR')}px를 넘어 나눠서 받기로 바꿨어요.`;
  }
  if (width > maxCanvasDim || height > maxCanvasDim || width * height > maxCanvasArea) {
    return '브라우저가 안전하게 만들 수 있는 캔버스 크기를 넘어 나눠서 받기로 바꿨어요.';
  }
  return null;
}

export function detailFileName(index, count, format) {
  const digits = Math.max(2, String(Math.max(1, count)).length);
  const ext = format === 'png' ? 'png' : 'jpg';
  return `detail_${String(index + 1).padStart(digits, '0')}.${ext}`;
}

export function publicationKindForSingle(format) {
  return format === 'jpeg' ? 'jpeg' : 'long_png';
}

export function buildExportSummary(blockHeights, rawSettings) {
  const settings = normalizeExportSettings(rawSettings);
  const preset = exportPreset(settings.preset);
  const ratio = preset.width / EXPORT_WIDTH;
  const scaled = blockHeights.map((height) => Math.max(0, Math.trunc((Number(height) || 0) * ratio)));
  const height = scaled.reduce((sum, value) => sum + value, 0);
  const grouped = preset.original
    ? scaled.flatMap((value) => {
      const pieces = [];
      for (let left = value; left > 0; left -= ORIGINAL_PNG_MAX_HEIGHT) pieces.push(Math.min(left, ORIGINAL_PNG_MAX_HEIGHT));
      return pieces;
    })
    : groupScaledBlockHeights(blockHeights, ratio);
  const fileCount = settings.delivery === 'single' ? (height > 0 ? 1 : 0) : grouped.length;
  const bytesPerPixel = settings.format === 'jpeg' ? 0.24 : 1.1;
  return {
    width: preset.width,
    height,
    fileCount,
    maxPieceHeight: settings.delivery === 'split' ? (preset.original ? ORIGINAL_PNG_MAX_HEIGHT : MAX_SPLIT_HEIGHT) : null,
    approxBytes: Math.round(preset.width * height * bytesPerPixel),
    approximate: true,
    fallbackReason: settings.delivery === 'single'
      ? singlePageFallbackReason({ width: preset.width, height })
      : null,
  };
}

function rowIsUniform(pixels, width, y, tolerance) {
  const start = y * width * 4;
  for (let x = 1; x < width; x += 1) {
    const at = start + x * 4;
    for (let channel = 0; channel < 4; channel += 1) {
      if (Math.abs(pixels[at + channel] - pixels[start + channel]) > tolerance) return false;
    }
  }
  return true;
}

export function findUniformCutRow(
  pixels,
  width,
  targetHeight,
  { searchWindow = 400, tolerance = 2 } = {},
) {
  const height = Math.floor(pixels.length / Math.max(1, width * 4));
  const target = Math.min(height, Math.max(1, Math.trunc(targetHeight)));
  const first = Math.max(1, target - searchWindow);
  for (let y = target - 1; y >= first; y -= 1) {
    if (rowIsUniform(pixels, width, y, tolerance)) return y;
  }
  return target;
}

export function splitCanvasRanges(canvas, maxHeight = MAX_SPLIT_HEIGHT) {
  if (canvas.height <= maxHeight) return [{ y: 0, height: canvas.height }];
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('이미지를 나눌 수 없어요. 다시 시도해 주세요.');
  const ranges = [];
  let y = 0;
  while (canvas.height - y > maxHeight) {
    const sample = ctx.getImageData(0, y, canvas.width, maxHeight);
    const height = findUniformCutRow(sample.data, canvas.width, maxHeight);
    ranges.push({ y, height });
    y += height;
  }
  if (canvas.height > y) ranges.push({ y, height: canvas.height - y });
  return ranges;
}

export async function splitOversizeJpegRanges(
  range,
  encodeRange,
  { maxBytes = MAX_JPEG_BYTES, quality = JPEG_QUALITY } = {},
) {
  const blob = await encodeRange(range, quality);
  if (blob.size <= maxBytes) return [{ range, blob }];
  if (range.height <= 1) {
    throw new Error('JPG 조각을 3MB 이하로 만들 수 없어요. PNG로 다시 시도해 주세요.');
  }
  const firstHeight = Math.max(1, Math.floor(range.height / 2));
  const first = { y: range.y, height: firstHeight };
  const second = { y: range.y + firstHeight, height: range.height - firstHeight };
  const left = await splitOversizeJpegRanges(first, encodeRange, { maxBytes, quality });
  const right = await splitOversizeJpegRanges(second, encodeRange, { maxBytes, quality });
  return [...left, ...right];
}

export function singlePageSizeWarning(byteSize) {
  return byteSize > 5 * 1024 * 1024
    ? '카페24 직접 등록, 쿠팡은 장당 5MB 한도라 나눠서 받기를 권해요.'
    : null;
}

// `/v1/assets/{id}/file`(상대·절대 모두) → 같은 자산의 `/bytes`. 그 외 URL 은 그대로.
// id 는 서버(/file 라우트)가 uuid 검증을 담당하므로 여기선 경로 모양만 본다 — 프론트가
// 더 엄격하면(예: uuid 강제) 서버는 받는데 프론트만 못 바꾸는 어긋남이 생긴다(리뷰 반영).
const ASSET_FILE_RE = /^(.*\/v1\/assets\/[^/?#]+)\/file$/;
/* capability 버전은 예전 빌드가 crossOrigin 없이 먼저 받은 immutable 사본을 우회한다.
   불러왔고, 그 응답에는 Origin 이 없어 CORS 헤더도 Vary 도 붙지 않았다. 응답이
   `immutable, max-age=1년` 이라 그 "허가 도장 없는" 사본이 브라우저 캐시에 눌러앉고,
   뒤이은 fetch(CORS 필수)가 같은 사본을 재사용해 영구히 차단됐다 — 실서버 재현 확인.
   이미 오염된 캐시는 서버 수정으로 못 풀기 때문에 주소를 한 번 갈아 준다. */
const ASSET_BYTES_RE = /\/v1\/assets\/[^/?#]+\/bytes(\?|$)/;
export function toBytesUrl(src) {
  const m = ASSET_FILE_RE.exec(String(src || '').split('?')[0]);
  return m ? `${m[1]}/bytes?e=${ASSET_CACHE_VERSION}` : src;
}
/** 핵심 상품컷(자산 바이트) 여부. 쿼리가 붙어도 판정이 깨지면 실패가 soft 로 강등돼
    빈 이미지인 채 "저장 완료" 로 속인다 — 이 파일이 막으려는 실패 모드다. */
export function isAssetBytesUrl(src) {
  return ASSET_BYTES_RE.test(String(src || ''));
}

// 긴 PNG 스티치 배치 — 각 블록 캔버스의 y 오프셋과 전체 크기. 폭은 최댓값 기준(전부 동일이 정상).
export function stitchLayout(sizes) {
  let y = 0;
  let width = 0;
  const offsets = sizes.map((s) => {
    const at = y;
    y += s.height;
    width = Math.max(width, s.width);
    return at;
  });
  return { width, height: y, offsets };
}

// 긴 PNG pixelRatio — 한 변 한계 안에서 최대. 어떤 배율로도 못 담으면 null(호출부가 ZIP 안내).
export function fitPixelRatio(totalCssHeight, wanted = PIXEL_RATIO) {
  if (totalCssHeight <= 0) return wanted;
  if (totalCssHeight > MAX_CANVAS_DIM) return null;
  return Math.max(1, Math.min(wanted, Math.floor((MAX_CANVAS_DIM / totalCssHeight) * 100) / 100));
}

// 편집 DOM의 offsetHeight에는 .canvas-block padding이 포함된다. dataset 값은 콘텐츠 높이이므로
// 기존 콘텐츠 높이만 export 높이로 갈아 끼워 긴 PNG 예약 높이와 실제 clone 캡처를 맞춘다.
export function exportNodeHeight(node) {
  const offsetHeight = Number(node?.offsetHeight) || 0;
  const editorHeight = Number.parseFloat(node?.style?.height);
  const exportHeight = Number(node?.dataset?.exportHeight);
  if (!Number.isFinite(exportHeight) || exportHeight <= 0) return offsetHeight;
  if (!Number.isFinite(editorHeight)) return exportHeight;
  return Math.max(1, offsetHeight - editorHeight + exportHeight);
}

// 파일명 — 상품명에서 파일시스템 금지문자만 걷어낸다. 빈 값은 '상세페이지'.
export function exportFileName(productName, suffix) {
  const base = String(productName || '').replace(/[\\/:*?"<>|]/g, ' ').replace(/\s+/g, ' ').trim();
  return `${base || '상세페이지'}${suffix ? `_${suffix}` : ''}.png`;
}

/* ---- DOM 캡처 ---- */

// 캡처에서 제외할 에디터 크롬 — 블록 안에 떠 있는 UI. 목록은 렌더 지점과 눈으로 맞춘
// 블록리스트라, 새 크롬을 추가할 때 여기도 같이 봐야 한다(허용리스트 전환은 백로그).
const CHROME_SELECTOR = [
  '.quick', '.blk-label', '.blk-resize', '.selection-marquee', '.crop-bar', '.crop-layer',
  '.align-bar', '.rot', '.hdl', '.edge', '.moveable-control-box',
  '.ed-genwait', '.ed-uploadwait', '.canvas-dropline', '.slot-add', '.image-drop-guide',
  '.el-empty-text', '.canvas-ai-layer', '.canvas-ai-slot-progress', '.canvas-ai-slot-arrived',
  // 빈 사진 자리(.el-slot)는 "여기에 넣으세요"라는 편집기 안내지 상품 페이지의 내용이
  // 아니다. 남겨 두면 완성본 PNG에 회색 네모가 그대로 찍힌다 — 사진을 비워 둔 채
  // 내려받는 순간 상품 페이지에 빈 상자가 인쇄되는 셈이다(2026-08-17).
  '.el-slot',
].join(',');

// 한 번의 다운로드 안에서만 성공한 이미지 바이트를 재사용한다. 재시도는 새 캐시다.
async function preflightImages(clone, imageCache) {
  const imgs = [...clone.querySelectorAll('img[src]')];
  let failed = 0;
  let firstError;
  await Promise.all(imgs.map(async (img) => {
    const src = img.getAttribute('src');
    if (!src || src.startsWith('data:')) return;
    if (!imageCache.has(src)) {
      imageCache.set(src, (async () => {
        const res = await clientApiFetch(src);
        const type = res.headers.get('content-type') || '';
        if (!res.ok || (type && !type.startsWith('image/') && type !== 'application/octet-stream')
          || (isAssetBytesUrl(src) && !type.startsWith('image/'))) {
          throw attachResponseError(new Error('image unavailable'), res);
        }
        const blob = await readClientResponse(res, 'blob');
        return new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = reject;
          reader.readAsDataURL(blob);
        });
      })());
    }
    try {
      img.removeAttribute('srcset');
      img.setAttribute('src', await imageCache.get(src));
    } catch (error) {
      imageCache.delete(src);
      firstError ||= error;
      failed += 1;
    }
  }));
  if (failed) {
    const error = new Error(`사진 ${failed}장을 불러오지 못해 다운로드를 중단했어요. 다시 시도해 주세요.`);
    error.cause = firstError;
    if (firstError?.requestId) error.requestId = firstError.requestId;
    if (firstError?.status != null) error.status = firstError.status;
    throw error;
  }
  return { softFailed: 0 };
}

async function prepareClone(blockNode, imageCache = new Map()) {
  const holder = document.createElement('div');
  holder.style.cssText = `position:fixed;left:-100000px;top:0;width:${EXPORT_WIDTH}px;pointer-events:none;`;
  const clone = blockNode.cloneNode(true);
  clone.classList.remove('on', 'obj-over');
  // 카드 그림자·간격은 화면용 — 결과물에 회색 이음새를 남긴다(이어보기 모드와 불일치, 리뷰 반영).
  clone.style.boxShadow = 'none';
  clone.style.margin = '0';
  clone.querySelectorAll(CHROME_SELECTOR).forEach((n) => n.remove());
  const exportHeight = Number(clone.dataset.exportHeight);
  if (Number.isFinite(exportHeight) && exportHeight > 0) clone.style.height = `${exportHeight}px`;
  clone.querySelectorAll('.el').forEach((n) => {
    n.classList.remove('on');
    n.style.outline = 'none';
  });
  replaceGifPosters(clone);
  // 이미지: /file → /bytes 로 바꿔 CORS 가 보장된 경로에서 픽셀을 읽는다.
  clone.querySelectorAll('img').forEach((img) => {
    const next = toBytesUrl(img.getAttribute('src'));
    if (next !== img.getAttribute('src')) {
      // crossOrigin 을 src 대입 **전에** 세운다(뒤에 세우면 무효). 이러면 이 <img> 요청도
      // CORS 요청이라 응답에 허가 헤더와 Vary 가 붙고, 곧이어 도는 preflight fetch 가
      // 같은 캐시 항목을 안전하게 재사용한다 — 캐시 오염이 구조적으로 재발하지 않는다.
      // 내보내기는 어차피 fetch(CORS)가 성공해야 하므로 새로 생기는 제약은 없다.
      img.crossOrigin = 'anonymous';
      img.setAttribute('src', next);
    }
    img.removeAttribute('loading');
  });
  holder.appendChild(clone);
  document.body.appendChild(holder);
  try {
    const { softFailed } = await preflightImages(clone, imageCache);
    return { clone, softFailed, dispose: () => holder.remove() };
  } catch (e) {
    holder.remove();
    throw e;
  }
}

export function flattenOnWhite(canvas) {
  const out = document.createElement('canvas');
  out.width = canvas.width;
  out.height = canvas.height;
  const ctx = out.getContext('2d');
  if (!ctx) return canvas;
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(canvas, 0, 0);
  canvas.width = 0; // 원본 픽셀 버퍼 해제
  canvas.height = 0;
  return out;
}

async function captureBlockCanvas(blockNode, pixelRatio, fontEmbedCSS, imageCache, onVerify) {
  const { clone, softFailed, dispose } = await prepareClone(blockNode, imageCache);
  try {
    // backgroundColor 옵션을 넘기면 html-to-image 가 **캡처 대상(블록) 자체의 배경색을 그 값으로
    // 덮어쓴다**(apply-style: style.backgroundColor = options.backgroundColor). 그래서 셀러가 바꾼
    // 블록 배경이 다운로드에서 전부 흰색이 됐다(2026-09-28). 블록 배경은 그대로 캡처하고,
    // 흰 바탕 합성은 우리가 한다 — 반투명 배경(bgOpacity)도 화면처럼 흰 바탕 위에 보인다.
    const height = clone.offsetHeight;
    const probes = await collectExportImageProbes(clone, { ratio: pixelRatio, height });
    const svg = await toSvg(clone, { width: EXPORT_WIDTH, height, fontEmbedCSS });
    onVerify?.();
    const canvas = await rasterizeExportSvg(svg, {
      width: Math.trunc(EXPORT_WIDTH * pixelRatio),
      height: Math.trunc(height * pixelRatio),
      probes,
    });
    return { canvas, softFailed };
  } finally {
    dispose();
  }
}

async function captureBlockCanvasSlices(
  blockNode,
  pixelRatio,
  fontEmbedCSS,
  maxHeight = MAX_SPLIT_HEIGHT,
  imageCache,
  onVerify,
) {
  maxHeight = Math.min(maxHeight, MAX_CAPTURE_DIM, MAX_CANVAS_DIM,
    Math.floor(MAX_CANVAS_AREA / Math.trunc(EXPORT_WIDTH * pixelRatio)));
  const { clone, softFailed, dispose } = await prepareClone(blockNode, imageCache);
  const canvases = [];
  try {
    const totalCssHeight = clone.offsetHeight;
    const totalHeight = Math.trunc(totalCssHeight * pixelRatio);
    const holder = clone.parentElement;
    if (!holder) throw new Error('내보내기용 블록을 준비하지 못했어요. 다시 시도해 주세요.');
    const viewport = document.createElement('div');
    viewport.style.cssText = `position:relative;width:${EXPORT_WIDTH}px;overflow:hidden;background:#ffffff;`;
    holder.appendChild(viewport);
    viewport.appendChild(clone);
    clone.style.position = 'absolute';
    clone.style.left = '0';
    clone.style.width = `${EXPORT_WIDTH}px`;
    clone.style.margin = '0';

    let y = 0;
    while (y < totalHeight) {
      const wantedHeight = Math.min(maxHeight, totalHeight - y);
      const cssY = y / pixelRatio;
      const cssHeight = wantedHeight / pixelRatio;
      viewport.style.height = `${cssHeight}px`;
      clone.style.top = `${-cssY}px`;
      const probes = await collectExportImageProbes(clone, { ratio: pixelRatio, top: cssY, height: cssHeight });
      const svg = await toSvg(viewport, { width: EXPORT_WIDTH, height: cssHeight, fontEmbedCSS });
      // 최종 픽셀 수를 직접 지정해 CSS 높이 왕복에서 생기는 1px 손실을 막는다.
      onVerify?.();
      let flattened = await rasterizeExportSvg(svg, {
        width: Math.trunc(EXPORT_WIDTH * pixelRatio), height: wantedHeight, probes,
      }); // eslint-disable-line no-await-in-loop
      if (flattened.width !== Math.trunc(EXPORT_WIDTH * pixelRatio)
        || Math.abs(flattened.height - wantedHeight) > 1) {
        releaseExportCanvas(flattened);
        throw new Error('브라우저 한계로 이미지 아랫부분이 잘릴 수 있어 다운로드를 중단했어요.');
      }
      let keepHeight = flattened.height;
      if (y + keepHeight < totalHeight) {
        const ctx = flattened.getContext('2d');
        if (!ctx) throw new Error('이미지를 나눌 수 없어요. 다시 시도해 주세요.');
        const pixels = ctx.getImageData(0, 0, flattened.width, flattened.height).data;
        keepHeight = findUniformCutRow(pixels, flattened.width, flattened.height);
      }
      if (keepHeight < flattened.height) {
        const trimmed = drawCanvasRange(
          createExportCanvas,
          flattened,
          { y: 0, height: keepHeight },
          flattened.width,
        );
        releaseExportCanvas(flattened);
        flattened = trimmed;
      }
      canvases.push(flattened);
      y += keepHeight;
    }
    return { canvases, softFailed };
  } catch (error) {
    canvases.forEach(releaseExportCanvas);
    throw error;
  } finally {
    dispose();
  }
}

// canvas.toBlob 은 초대형 캔버스에서 콜백이 영영 안 올 수 있다(리뷰 반영) — 타임아웃 방어.
function canvasToBlob(canvas, mime = 'image/png', quality, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error('이미지 변환이 너무 오래 걸려 중단했어요. 블록별 ZIP으로 시도해 주세요.')),
      timeoutMs,
    );
    canvas.toBlob((b) => {
      clearTimeout(timer);
      if (b) resolve(b);
      else reject(new Error('이미지 인코딩에 실패했어요. 페이지가 길면 나눠서 받아주세요.'));
    }, mime, quality);
  });
}

function createExportCanvas(width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('저장용 캔버스를 만들지 못했어요. 다시 시도해 주세요.');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, width, height);
  return canvas;
}

function releaseExportCanvas(canvas) {
  canvas.width = 0;
  canvas.height = 0;
}

function drawCanvasRange(createCanvas, source, range, width = source.width) {
  const out = createCanvas(width, range.height);
  const ctx = out.getContext('2d');
  if (!ctx) throw new Error('저장용 캔버스를 만들지 못했어요. 다시 시도해 주세요.');
  ctx.drawImage(
    source,
    0, range.y, source.width, range.height,
    0, 0, width, range.height,
  );
  return out;
}

export async function renderStorefrontFiles(blockNodes, rawSettings, deps = {}) {
  const settings = normalizeExportSettings(rawSettings);
  const preset = exportPreset(settings.preset);
  const width = preset.width;
  const ratio = width / EXPORT_WIDTH;
  const maxHeight = deps.maxHeight || (preset.original ? ORIGINAL_PNG_MAX_HEIGHT : MAX_SPLIT_HEIGHT);
  const createCanvas = deps.createCanvas || createExportCanvas;
  const releaseCanvas = deps.releaseCanvas || releaseExportCanvas;
  const encodeCanvas = deps.encodeCanvas || canvasToBlob;
  const captureBlock = deps.captureBlock;
  const measureBlock = deps.measureBlock || exportNodeHeight;
  if (!captureBlock) throw new Error('내보내기 캡처 함수를 찾지 못했어요.');
  const mime = settings.format === 'png' ? 'image/png' : 'image/jpeg';
  let softFailed = 0;
  let totalHeight = 0;
  const validateCapture = (canvases, node) => {
    const expectedHeight = Math.trunc(measureBlock(node) * ratio);
    const actualHeight = canvases.reduce((sum, canvas) => sum + canvas.height, 0);
    if (canvases.some((canvas) => canvas.width !== width)
      || Math.abs(actualHeight - expectedHeight) > 1) {
      canvases.forEach(releaseCanvas);
      throw new Error('브라우저 한계로 이미지 아랫부분이 잘릴 수 있어 다운로드를 중단했어요. 나눠서 다시 시도해 주세요.');
    }
    return actualHeight;
  };

  if (settings.delivery === 'single') {
    const plannedHeight = blockNodes.reduce(
      (sum, node) => sum + Math.trunc(measureBlock(node) * ratio), 0,
    );
    const fallbackReason = singlePageFallbackReason({ width, height: plannedHeight });
    if (fallbackReason) throw new Error(fallbackReason);
    const out = createCanvas(width, plannedHeight);
    const ctx = out.getContext('2d');
    if (!ctx) {
      releaseCanvas(out);
      throw new Error('저장용 캔버스를 만들지 못했어요. 나눠서 받아주세요.');
    }
    try {
      let y = 0;
      for (let i = 0; i < blockNodes.length; i += 1) {
        deps.onProgress?.(i, blockNodes.length);
        const captured = await captureBlock(blockNodes[i], ratio, i); // eslint-disable-line no-await-in-loop
        try {
          validateCapture([captured.canvas], blockNodes[i]);
          if (y + captured.canvas.height > out.height) {
            throw new Error('예상한 캔버스 높이와 실제 높이가 달라 한 장 저장을 중단했어요. 나눠서 받아주세요.');
          }
          ctx.drawImage(captured.canvas, 0, y);
          y += captured.canvas.height;
          totalHeight += captured.canvas.height;
          softFailed += captured.softFailed;
        } finally {
          releaseCanvas(captured.canvas);
        }
      }
      deps.onProgress?.(blockNodes.length - 1, blockNodes.length, 'encode');
      const blob = await encodeCanvas(out, mime, settings.format === 'jpeg' ? JPEG_QUALITY : undefined);
      return { files: [blob], softFailed, width, height: totalHeight };
    } finally {
      releaseCanvas(out);
    }
  }

  const files = [];
  let current = null;
  let currentHeight = 0;

  const flush = async () => {
    if (!current || currentHeight <= 0) return;
    const active = current;
    let exact = null;
    try {
      exact = drawCanvasRange(createCanvas, active, { y: 0, height: currentHeight }, width);
      if (settings.format === 'jpeg') {
        const parts = await splitOversizeJpegRanges(
          { y: 0, height: currentHeight },
          async (range, quality) => {
            const slice = drawCanvasRange(createCanvas, exact, range, width);
            try {
              return await encodeCanvas(slice, mime, quality);
            } finally {
              releaseCanvas(slice);
            }
          },
        );
        files.push(...parts.map(({ blob }) => blob));
      } else {
        files.push(await encodeCanvas(exact, mime));
      }
    } finally {
      if (exact) releaseCanvas(exact);
      releaseCanvas(active);
      current = null;
      currentHeight = 0;
    }
  };

  const append = async (source, range) => {
    if (!current) current = createCanvas(width, maxHeight);
    if (currentHeight + range.height > maxHeight) {
      await flush();
      current = createCanvas(width, maxHeight);
    }
    const ctx = current.getContext('2d');
    if (!ctx) throw new Error('저장용 캔버스를 만들지 못했어요. 다시 시도해 주세요.');
    ctx.drawImage(
      source,
      0, range.y, source.width, range.height,
      0, currentHeight, width, range.height,
    );
    currentHeight += range.height;
  };

  try {
    for (let i = 0; i < blockNodes.length; i += 1) {
      deps.onProgress?.(i, blockNodes.length);
      const captured = await captureBlock(blockNodes[i], ratio, i); // eslint-disable-line no-await-in-loop
      const canvases = captured.canvases || [captured.canvas];
      totalHeight += validateCapture(canvases, blockNodes[i]);
      softFailed += captured.softFailed;
      try {
        for (const canvas of canvases) {
          if (preset.original && canvas.height <= maxHeight) {
            // 이미 2배율로 안전하게 캡처한 PNG 조각이다. 다시 그리거나 축소하지 않는다.
            files.push(await encodeCanvas(canvas, 'image/png')); // eslint-disable-line no-await-in-loop
            releaseCanvas(canvas);
            continue;
          }
          const ranges = splitCanvasRanges(canvas, maxHeight);
          for (const range of ranges) await append(canvas, range); // eslint-disable-line no-await-in-loop
        }
      } finally {
        canvases.forEach(releaseCanvas);
      }
    }
    deps.onProgress?.(blockNodes.length - 1, blockNodes.length, 'encode');
    await flush();
  } finally {
    if (current) releaseCanvas(current);
  }
  return { files, softFailed, width, height: totalHeight };
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

// Export clones use a fixed first frame, while the live canvas keeps playing.
export function replaceGifPosters(clone) {
  clone.querySelectorAll('img[data-gif-poster]').forEach((img) => {
    const poster = img.getAttribute('data-gif-poster');
    if (!poster) return;
    img.removeAttribute('srcset');
    img.setAttribute('src', poster);
  });
}

async function readGifBlob(src) {
  const response = await clientApiFetch(toBytesUrl(src));
  if (!response.ok) throw attachResponseError(new Error('GIF를 읽지 못했어요. 다시 시도해 주세요'), response);
  const blob = await readClientResponse(response, 'blob');
  const header = await blob.slice(0, 6).text();
  if (!['GIF87a', 'GIF89a'].includes(header)) throw new Error('GIF 파일을 확인하지 못했어요. 다시 시도해 주세요');
  return blob;
}

export async function appendGifFiles(zip, images = []) {
  const sources = [...new Set(images.map((image) => image.src).filter(Boolean))];
  for (let index = 0; index < sources.length; index += 1) {
    const blob = await readGifBlob(sources[index]);
    zip.file(`gif_${String(index + 1).padStart(2, '0')}.gif`, await blob.arrayBuffer());
  }
  return sources.length;
}

export async function packageStorefrontZip(files, format, opts = {}, sign = notarize) {
  const zip = new JSZip();
  // ArrayBuffer also works with JSZip outside browsers, without a FileReader shim.
  for (let index = 0; index < files.length; index += 1) {
    zip.file(detailFileName(index, files.length, format), await files[index].arrayBuffer());
  }
  let blob = await zip.generateAsync({ type: 'blob' });
  let verifyUrl = null;
  let notarizeWarning = null;
  if (opts.provenance?.projectId) {
    const result = await sign(blob, { projectId: opts.provenance.projectId, kind: 'zip' });
    blob = result.blob;
    verifyUrl = result.verifyUrl;
    notarizeWarning = result.warning;
  }
  // GIFs never enter the notarization request. Signed raster bytes stay untouched.
  if (opts.gifImages?.length) {
    const deliveredZip = await JSZip.loadAsync(await blob.arrayBuffer());
    await appendGifFiles(deliveredZip, opts.gifImages);
    blob = await deliveredZip.generateAsync({ type: 'blob' });
  }
  return { blob, verifyUrl, notarizeWarning };
}

export async function downloadGifImage(image) {
  saveBlob(await readGifBlob(image.src), 'thumbnail.gif');
}

export async function downloadGifImages(images) {
  const zip = new JSZip();
  await appendGifFiles(zip, images);
  saveBlob(await zip.generateAsync({ type: 'blob' }), 'gifs.zip');
}

/* ---- 공개 API — 각 함수는 { softFailed, verifyUrl, notarizeWarning } 를 돌려준다
   (softFailed 0 이면 캡처 완전 성공). opts.provenance = { projectId } 를 넘기면 saveBlob
   직전에 공증 왕복(publicationNotarize.js)을 끼운다 — 안 넘기면 공증 생략(verifyUrl null). ---- */

export async function packageBlockDownload(png, filename, opts = {}, sign = notarize) {
  let blob = png, verifyUrl = null, notarizeWarning = null;
  if (opts.provenance?.projectId) {
    const signed = await sign(png, { projectId: opts.provenance.projectId, kind: 'block_png' });
    blob = signed.blob; verifyUrl = signed.verifyUrl; notarizeWarning = signed.warning;
  }
  if (opts.gifImages?.length) {
    const zip = new JSZip();
    zip.file(filename, await blob.arrayBuffer());
    await appendGifFiles(zip, opts.gifImages);
    blob = await zip.generateAsync({ type: 'blob' });
  }
  return { blob, verifyUrl, notarizeWarning };
}

export async function downloadBlockGifs(images) {
  const unique = [...new Map(images.map(image => [image.src, image])).values()];
  if (unique.length === 1) await downloadGifImage(unique[0]);
  else if (unique.length) await downloadGifImages(unique);
}

// A block with GIFs includes the signed first-frame PNG and untouched originals.
export async function exportBlockPng(blockNode, productName, blockIndex, opts = {}) {
  recordClientAction('download');
  const fontEmbedCSS = await getFontEmbedCSS(blockNode);
  const { canvas, softFailed } = await captureBlockCanvas(blockNode, PIXEL_RATIO, fontEmbedCSS);
  let out;
  try { out = await canvasToBlob(canvas); }
  finally { releaseExportCanvas(canvas); }
  const filename = exportFileName(productName, `블록${blockIndex + 1}`);
  const result = await packageBlockDownload(out, filename, opts);
  saveBlob(result.blob, opts.gifImages?.length ? filename.replace(/\.png$/i, '.zip') : filename);
  return { softFailed, verifyUrl: result.verifyUrl, notarizeWarning: result.notarizeWarning };
}

// ② 전체 블록 → 세로로 이어붙인 긴 PNG 1장.
// 스티치 캔버스를 먼저 만들고 블록을 그리는 즉시 해제해 피크 메모리를 절반으로(리뷰 반영).
export async function exportLongPng(blockNodes, productName, onProgress, opts = {}) {
  recordClientAction('download');
  const heights = blockNodes.map(exportNodeHeight);
  const totalCssHeight = heights.reduce((a, b) => a + b, 0);
  const ratio = fitPixelRatio(totalCssHeight);
  if (ratio == null) {
    throw new Error('페이지가 너무 길어 한 장으로는 저장할 수 없어요. 블록별 ZIP으로 받아주세요.');
  }
  const fontEmbedCSS = await getFontEmbedCSS(blockNodes[0]);
  const imageCache = new Map();
  const out = document.createElement('canvas');
  out.width = Math.trunc(EXPORT_WIDTH * ratio);
  // 블록별 캔버스 높이는 trunc(offsetHeight×ratio) — 합계도 같은 식으로 예약해 이음새를 없앤다.
  out.height = heights.reduce((sum, h) => sum + Math.trunc(h * ratio), 0);
  let softFailed = 0;
  let outBlob;
  try {
    const ctx = out.getContext('2d');
    if (!ctx) throw new Error('저장용 캔버스를 만들지 못했어요. 블록별 ZIP으로 시도해 주세요.');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, out.width, out.height);
    let y = 0;
    for (let i = 0; i < blockNodes.length; i += 1) {
      onProgress?.(i, blockNodes.length);
      const r = await captureBlockCanvas(blockNodes[i], ratio, fontEmbedCSS, imageCache,
        () => onProgress?.(i, blockNodes.length, 'verify')); // eslint-disable-line no-await-in-loop
      try {
        ctx.drawImage(r.canvas, 0, y);
        y += r.canvas.height;
        softFailed += r.softFailed;
      } finally { releaseExportCanvas(r.canvas); }
    }
    onProgress?.(blockNodes.length - 1, blockNodes.length, 'encode');
    outBlob = await canvasToBlob(out);
  } finally { releaseExportCanvas(out); imageCache.clear(); }
  let verifyUrl = null;
  let notarizeWarning = null;
  if (opts.provenance?.projectId) {
    const n = await notarize(outBlob, { projectId: opts.provenance.projectId, kind: 'long_png' });
    outBlob = n.blob;
    verifyUrl = n.verifyUrl;
    notarizeWarning = n.warning;
  }
  saveBlob(outBlob, exportFileName(productName));
  return {
    softFailed,
    verifyUrl,
    notarizeWarning,
    byteSize: outBlob.size,
    fileCount: 1,
    sizeWarning: singlePageSizeWarning(outBlob.size),
  };
}

// ③ 전체 블록 → 블록별 PNG 를 담은 ZIP
export async function exportBlocksZip(blockNodes, productName, onProgress, opts = {}) {
  return exportStorefrontDownload(blockNodes, productName, onProgress,
    { preset: 'original', format: 'png', delivery: 'split' }, opts);
}

export async function exportStorefrontDownload(
  blockNodes,
  productName,
  onProgress,
  rawSettings,
  opts = {},
) {
  const settings = normalizeExportSettings(rawSettings);
  const heights = blockNodes.map(exportNodeHeight);
  const summary = buildExportSummary(heights, settings);
  const autoSwitchReason = summary.fallbackReason;
  const effective = autoSwitchReason ? { ...settings, delivery: 'split' } : settings;
  const preset = exportPreset(effective.preset);

  if (preset.original && effective.delivery === 'single') {
    const result = await exportLongPng(blockNodes, productName, onProgress, opts);
    return { ...result, autoSwitchReason, delivery: effective.delivery };
  }

  recordClientAction('download');
  const fontEmbedCSS = await getFontEmbedCSS(blockNodes[0]);
  const imageCache = new Map();
  let rendered;
  try {
    rendered = await renderStorefrontFiles(blockNodes, effective, {
      onProgress,
      captureBlock: effective.delivery === 'split'
        ? (node, ratio, index) => captureBlockCanvasSlices(node, ratio, fontEmbedCSS,
          preset.original ? ORIGINAL_PNG_MAX_HEIGHT : MAX_SPLIT_HEIGHT, imageCache,
          () => onProgress?.(index, blockNodes.length, 'verify'))
        : (node, ratio, index) => captureBlockCanvas(node, ratio, fontEmbedCSS, imageCache,
          () => onProgress?.(index, blockNodes.length, 'verify')),
    });
  } finally { imageCache.clear(); }
  let blob;
  let verifyUrl = null;
  let notarizeWarning = null;

  if (effective.delivery === 'split') {
    onProgress?.(blockNodes.length - 1, blockNodes.length, 'zip');
    ({ blob, verifyUrl, notarizeWarning } = await packageStorefrontZip(rendered.files, effective.format, opts));
    saveBlob(blob, exportFileName(productName).replace(/\.png$/, '.zip'));
  } else {
    [blob] = rendered.files;
    if (opts.provenance?.projectId) {
      const result = await notarize(blob, {
        projectId: opts.provenance.projectId,
        kind: publicationKindForSingle(effective.format),
      });
      blob = result.blob;
      verifyUrl = result.verifyUrl;
      notarizeWarning = result.warning;
    }
    const ext = effective.format === 'jpeg' ? '.jpg' : '.png';
    saveBlob(blob, exportFileName(productName).replace(/\.png$/, ext));
  }

  return {
    softFailed: rendered.softFailed,
    verifyUrl,
    notarizeWarning,
    autoSwitchReason,
    delivery: effective.delivery,
    fileCount: rendered.files.length + (effective.delivery === 'split' ? new Set((opts.gifImages || []).map((image) => image.src)).size : 0),
    byteSize: blob.size,
    sizeWarning: effective.delivery === 'single' ? singlePageSizeWarning(blob.size) : null,
  };
}
