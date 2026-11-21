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
import { toSvg } from 'html-to-image';
import { waitForEditedContrast } from './editorContrastPending.js';
import { getPageFontEmbedCSS } from './editorExportFonts.js';
import { bakeExportBackdrops, collectExportImageProbes, rasterizeBackdropImage, rasterizeExportSvg } from './editorExportRaster.js';
import { hasBackdropBlur } from './editorImageBlur.js';
import { isGifImage } from '../../lib/gif/gifMedia.js';
import JSZip from 'jszip';
import { toBytesUrl, isAssetBytesUrl } from './editorAssetBytes.js';
export { toBytesUrl, isAssetBytesUrl } from './editorAssetBytes.js';
import { notarize } from './publicationNotarize.js';
import { CHROME_SELECTOR, exportStillImageSource } from './editorExportContent.js';

export const EXPORT_WIDTH = 1000; // .ed-canvas 설계 폭 — 캡처는 화면 scale 과 무관하게 이 폭 기준
const PIXEL_RATIO = 2; // 상세페이지 업로드 기준 2000px — 쇼핑몰 권장폭(860~1280)을 여유 있게 커버
export const MAX_CANVAS_DIM = 30000; // 캔버스 한 변 한계(32767) 아래 여유 — 넘으면 긴 PNG 대신 ZIP 안내
export const MAX_CAPTURE_DIM = 16384; // html-to-image가 이 길이를 넘으면 자동 축소한다
export const MAX_CANVAS_AREA = 16_777_216; // Safari가 빈 캔버스로 돌려줄 수 있는 총 픽셀 상한
export const MAX_SINGLE_PAGE_HEIGHT = 20000;
export const MAX_SPLIT_HEIGHT = 3000;
export const MAX_PUBLICATION_PIECES = 40;
export const PUBLICATION_PIECE_LIMIT_MESSAGE = '조각이 40장을 넘어 붙여넣을 코드를 만들 수 없어요. 이미지로 받아 주세요';
export const MAX_JPEG_BYTES = 3 * 1024 * 1024;
export const JPEG_QUALITY = 0.9;
export const ORIGINAL_PNG_MAX_HEIGHT = Math.min(
  MAX_CAPTURE_DIM, MAX_CANVAS_DIM, Math.floor(MAX_CANVAS_AREA / (EXPORT_WIDTH * PIXEL_RATIO)),
);
export const EXPORT_SETTINGS_KEY = 'wearless.editor.export.v1';
export const CUSTOM_EXPORT_MIN_WIDTH = 640;
export const CUSTOM_EXPORT_MAX_WIDTH = 2000;
export const CUSTOM_EXPORT_WIDTH_ERROR = '640~2,000px 사이로 넣어 주세요';

export const EXPORT_PRESETS = Object.freeze([
  { id: 'smartstore', width: 860, label: '스마트스토어' },
  { id: 'coupang', width: 780, label: '쿠팡' },
  { id: 'cafe24', width: 800, label: '카페24' },
  { id: 'high', width: 1290, label: '고화질', desc: '휴대폰에서 더 선명, 파일이 큼' },
  { id: 'original', width: 2000, label: '원본 PNG', original: true },
  { id: 'custom', label: '직접 입력' },
]);

export const DEFAULT_EXPORT_SETTINGS = Object.freeze({
  preset: 'smartstore',
  format: 'jpeg',
  delivery: 'split',
});

const EXPORT_FORMATS = new Set(['jpeg', 'png']);
const EXPORT_DELIVERIES = new Set(['split', 'single']);
const CUT_SEARCH_WINDOW = 400;

/* ---- 순수 헬퍼 (테스트 대상) ---- */

export function exportPreset(presetId) {
  return EXPORT_PRESETS.find((preset) => preset.id === presetId) || EXPORT_PRESETS[0];
}

function validCustomExportWidth(width) {
  return Number.isInteger(width) && width >= CUSTOM_EXPORT_MIN_WIDTH && width <= CUSTOM_EXPORT_MAX_WIDTH;
}

export function exportWidth({ preset, customWidth }) {
  if (preset === 'custom') return validCustomExportWidth(customWidth) ? customWidth : null;
  return exportPreset(preset).width;
}

export function exportScaleForPreset(preset, customWidth) {
  const width = exportWidth({ preset, customWidth });
  return width === null ? null : width / EXPORT_WIDTH;
}

export function normalizeExportSettings(value) {
  const { id: preset, original } = exportPreset(value?.preset);
  return {
    preset,
    format: original ? 'png' : (EXPORT_FORMATS.has(value?.format) ? value.format : DEFAULT_EXPORT_SETTINGS.format),
    delivery: EXPORT_DELIVERIES.has(value?.delivery) ? value.delivery : DEFAULT_EXPORT_SETTINGS.delivery,
    ...(validCustomExportWidth(value?.customWidth) ? { customWidth: value.customWidth } : {}),
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
    if (!raw) return { ...DEFAULT_EXPORT_SETTINGS };
    const settings = normalizeExportSettings(JSON.parse(raw));
    return exportWidth(settings) === null ? { ...DEFAULT_EXPORT_SETTINGS } : settings;
  } catch {
    return { ...DEFAULT_EXPORT_SETTINGS };
  }
}

export function writeExportSettings(storage, settings) {
  try {
    const target = resolveExportStorage(storage);
    if (!target) return false;
    const normalized = normalizeExportSettings(settings);
    if (exportWidth(normalized) === null) return false;
    target.setItem(EXPORT_SETTINGS_KEY, JSON.stringify(normalized));
    return true;
  } catch {
    return false;
  }
}

export function exportPixelHeight(height, width) {
  const cssHeight = Number(height);
  return Number.isFinite(cssHeight) ? Math.max(0, Math.floor(cssHeight * width / EXPORT_WIDTH)) : 0;
}

const SHORT_SECTION_HEIGHT = 700;
const sumHeights = (slices) => slices.reduce((sum, slice) => sum + slice.height, 0);

function makePiece(slices) {
  const joined = [];
  let height = 0;
  for (const slice of slices) {
    height += slice.height;
    const last = joined.at(-1);
    if (last?.blockIndex === slice.blockIndex) {
      joined[joined.length - 1] = { ...last, height: last.height + slice.height };
    } else joined.push(slice);
  }
  return { height, slices: joined, blank: joined.every((slice) => slice.blank) };
}

function splitBlock(blockIndex, height, blank, maxHeight) {
  const count = Math.ceil(height / maxHeight);
  let y = 0;
  return Array.from({ length: count }, (_, index) => {
    const end = Math.ceil((index + 1) * height / count);
    const slice = { blockIndex, y, height: end - y, blank };
    y = end;
    return slice;
  });
}

function collectExportSections(blocks, heights, maxHeight) {
  const runs = [];
  blocks.forEach((block, index) => {
    const key = block.pageSectionKey;
    let run = runs.at(-1);
    if (!key || run?.key !== key) {
      run = { key, slices: [] };
      runs.push(run);
    }
    run.slices.push(...splitBlock(index, heights[index], block.blank, maxHeight));
  });

  const sections = [];
  let gap = [];
  for (const { slices } of runs) {
    const first = slices.findIndex((slice) => !slice.blank);
    if (first === -1) {
      gap.push(...slices);
      continue;
    }
    const end = slices.findLastIndex((slice) => !slice.blank) + 1;
    let content = slices.slice(first, end);
    const height = sumHeights(slices);
    const short = height < SHORT_SECTION_HEIGHT && height <= maxHeight;
    if (short) {
      // Keep a short section together even if its neighboring long run is rebalanced later.
      content = content.map((slice) => ({ ...slice, shortSection: sections.length }));
    }
    sections.push({ gap: [...gap, ...slices.slice(0, first)], content, short });
    gap = slices.slice(end);
  }
  return { sections, trailingGap: gap };
}

function bundleSectionSlices(slices, maxHeight) {
  const bundles = [];
  for (let start = 0; start < slices.length;) {
    let end = start + 1;
    const shortSection = slices[start].shortSection;
    if (shortSection !== undefined) {
      while (slices[end]?.shortSection === shortSection) end += 1;
    } else if (slices[start].blank) {
      while (end < slices.length && slices[end].blank) end += 1;
    }
    const bundle = makePiece(slices.slice(start, end));
    if (bundle.height <= maxHeight) bundles.push(bundle);
    else bundles.push(...slices.slice(start, end).map((slice) => makePiece([slice])));
    start = end;
  }
  return bundles;
}

function partitionSection(slices, maxHeight, { leading, trailing } = {}) {
  const bundles = bundleSectionSlices(slices, maxHeight);
  if (leading) bundles[0] = makePiece([...leading, ...bundles[0].slices]);
  if (trailing) bundles[bundles.length - 1] = makePiece([...bundles.at(-1).slices, ...trailing]);
  if (bundles.some(({ height }) => height > maxHeight)) return null;
  const order = ['blankCount', 'count', 'gapsAtEnd', 'squaredHeights'];
  const better = (candidate, current) => {
    const difference = current && order.find((key) => candidate[key] !== current[key]);
    return !difference || candidate[difference] < current[difference];
  };
  const best = [{ blankCount: 0, count: 0, gapsAtEnd: 0, squaredHeights: 0 }];
  // Each prefix uses as few blank-only files as possible, then as few files as
  // possible. For a fixed file count, minimizing squared heights balances them.
  for (let end = 1; end <= bundles.length; end += 1) {
    let height = 0;
    let blank = true;
    for (let start = end - 1; start >= 0; start -= 1) {
      height += bundles[start].height;
      if (height > maxHeight) break;
      blank &&= bundles[start].blank;
      const prefix = best[start];
      const candidate = {
        start, blankCount: prefix.blankCount + Number(blank), count: prefix.count + 1,
        gapsAtEnd: prefix.gapsAtEnd + Number(!blank && end < bundles.length && bundles[end - 1].blank),
        squaredHeights: prefix.squaredHeights + height * height,
      };
      if (better(candidate, best[end])) best[end] = candidate;
    }
  }
  const pieces = [];
  for (let end = bundles.length; end > 0;) {
    const { start } = best[end];
    pieces.push(makePiece(bundles.slice(start, end).flatMap((bundle) => bundle.slices)));
    end = start;
  }
  return pieces.reverse();
}

function takeGapEdge(gap, budget, fromEnd, blockHeights, maxHeight) {
  const taken = [];
  while (gap.length && budget > 0) {
    const index = fromEnd ? gap.length - 1 : 0;
    const slice = gap[index];
    if (slice.height <= budget) {
      taken.push(gap.splice(index, 1)[0]);
      budget -= slice.height;
    } else {
      // Only a block that was already taller than the limit can be cut here.
      if (blockHeights[slice.blockIndex] > maxHeight) {
        const remainder = slice.height - budget;
        taken.push({ ...slice, y: fromEnd ? slice.y + remainder : slice.y, height: budget });
        gap[index] = { ...slice, y: fromEnd ? slice.y : slice.y + budget, height: remainder };
      }
      break;
    }
  }
  return fromEnd ? taken.reverse() : taken;
}

function shareOversizeGap(remaining, previous, following, heights, maxHeight) {
  const suffix = following ? takeGapEdge(remaining, maxHeight - following.height, true, heights, maxHeight) : [];
  const prefix = previous ? takeGapEdge(remaining, maxHeight - previous.height, false, heights, maxHeight) : [];
  return {
    previous: prefix.length ? makePiece([...previous.slices, ...prefix]) : previous,
    following: suffix.length ? makePiece([...suffix, ...following.slices]) : following,
  };
}

// A long section's balanced pieces may leave part of an oversized gap alone.
// Try the available space on both sides without moving any content rows.
function absorbBlankPieces(pieces, heights, maxHeight) {
  const result = [];
  for (let index = 0; index < pieces.length;) {
    if (!pieces[index].blank) {
      result.push(pieces[index++]);
      continue;
    }
    let end = index + 1;
    while (pieces[end]?.blank) end += 1;
    const previous = result.at(-1);
    const following = pieces[end];
    const blanks = pieces.slice(index, end);
    index = end;
    if (!previous || !following) {
      result.push(...blanks);
      continue;
    }
    const previousEnd = previous.slices.findLastIndex((slice) => !slice.blank) + 1;
    const followingStart = following.slices.findIndex((slice) => !slice.blank);
    const gap = [
      ...previous.slices.slice(previousEnd), ...blanks.flatMap((piece) => piece.slices),
      ...following.slices.slice(0, followingStart),
    ];
    if (sumHeights(gap) <= maxHeight) {
      result.push(...blanks);
      continue;
    }
    const shared = shareOversizeGap(gap, makePiece(previous.slices.slice(0, previousEnd)),
      makePiece(following.slices.slice(followingStart)), heights, maxHeight);
    const leftover = partitionSection(gap, maxHeight);
    if (leftover.length <= blanks.length && sumHeights(leftover) < sumHeights(blanks)) {
      result[result.length - 1] = shared.previous;
      result.push(...leftover);
      pieces[end] = shared.following;
    } else result.push(...blanks);
  }
  return result;
}

function rebalanceWithTrailingSections(pieces, sections, maxHeight) {
  for (const [index, { content }] of sections.entries()) {
    if (sumHeights(content) <= maxHeight) continue;
    let after = index + 1;
    while (sections[after]?.short) after += 1;

    const firstBlock = content[0].blockIndex;
    const lastBlock = content.at(-1).blockIndex;
    const start = pieces.findIndex(({ slices }) => slices.at(-1).blockIndex >= firstBlock);
    const nextBlock = sections[after]?.content[0].blockIndex;
    const end = nextBlock === undefined ? pieces.length
      : pieces.findIndex(({ slices }) => slices.at(-1).blockIndex >= nextBlock);
    const current = pieces.slice(start, end);
    const slices = current.flatMap((piece) => piece.slices);

    // Keep the gaps where the normal placement rules put them. In particular,
    // a gap carried by the next regular section is outside this range.
    const leading = slices.filter((slice) => slice.blockIndex < firstBlock);
    const trailing = slices.filter((slice) => slice.blockIndex > lastBlock);
    if (!trailing.length) continue;
    const section = slices.slice(leading.length, slices.length - trailing.length);
    const balanced = partitionSection(section, maxHeight, { leading, trailing });
    if (!balanced || balanced.length > current.length) continue;
    if (balanced.filter((piece) => piece.blank).length > current.filter((piece) => piece.blank).length) continue;
    pieces.splice(start, current.length, ...balanced);
  }
  return pieces;
}

function planSplitPieces(blocks, heights, maxHeight) {
  const { sections, trailingGap } = collectExportSections(blocks, heights, maxHeight);
  const pieces = [];
  let previousLongStart = null;
  let atStart = true;

  function placeGap(gap, next) {
    if (!gap.length) return;
    const height = sumHeights(gap);
    const previous = pieces.at(-1);
    const following = next[0];
    if (following && height + following.height <= maxHeight) {
      next[0] = makePiece([...gap, ...following.slices]);
    } else if (previous && previous.height + height <= maxHeight) {
      pieces[pieces.length - 1] = makePiece([...previous.slices, ...gap]);
    } else {
      // Long sections can move their internal boundaries to make room.
      if (height <= maxHeight && next.length > 1) {
        const rebalanced = partitionSection(next.flatMap((piece) => piece.slices), maxHeight, { leading: gap });
        if (rebalanced) {
          next.splice(0, next.length, ...rebalanced);
          return;
        }
      }
      if (height <= maxHeight && previousLongStart !== null) {
        const rebalanced = partitionSection(
          pieces.slice(previousLongStart).flatMap((piece) => piece.slices), maxHeight, { trailing: gap },
        );
        if (rebalanced) {
          pieces.splice(previousLongStart, pieces.length, ...rebalanced);
          return;
        }
      }
      // Only a gap exceeding the limit can be divided between its neighbors.
      let remaining = [...gap];
      if (height > maxHeight) {
        const shared = shareOversizeGap(remaining, previous, following, heights, maxHeight);
        const previousRun = previousLongStart === null ? null : pieces.slice(previousLongStart).flatMap((piece) => piece.slices);
        const followingRun = next.length > 1 ? next.flatMap((piece) => piece.slices) : null;
        const candidate = remaining.length && (previousRun || followingRun) ? [...gap] : null;
        const after = followingRun ? bundleSectionSlices(followingRun, maxHeight)[0] : following;
        const before = previousRun ? bundleSectionSlices(previousRun, maxHeight).at(-1) : previous;
        const suffix = candidate && after ? takeGapEdge(candidate, maxHeight - after.height, true, heights, maxHeight) : [];
        const prefix = candidate && before ? takeGapEdge(candidate, maxHeight - before.height, false, heights, maxHeight) : [];
        if (candidate && sumHeights(candidate) < sumHeights(remaining)) {
          // A different boundary can give the gap more space without mixing sections.
          if (previousRun) pieces.splice(previousLongStart, pieces.length, ...partitionSection(previousRun, maxHeight, { trailing: prefix }));
          else if (previous) pieces[pieces.length - 1] = makePiece([...previous.slices, ...prefix]);
          if (followingRun) next.splice(0, next.length, ...partitionSection(followingRun, maxHeight, { leading: suffix }));
          else if (following) next[0] = makePiece([...suffix, ...following.slices]);
          remaining = candidate;
        } else {
          if (previous) pieces[pieces.length - 1] = shared.previous;
          if (following) next[0] = shared.following;
        }
      }
      pieces.push(...partitionSection(remaining, maxHeight));
      if (remaining.length) previousLongStart = null;
    }
  }

  for (const { gap, content, short } of sections) {
    const section = makePiece(content);
    const long = section.height > maxHeight;
    let next = long ? partitionSection(content, maxHeight) : [section];
    placeGap(gap, next);
    const previous = pieces.at(-1);
    let joinedPrevious = false;
    if ((short || atStart) && previous && previous.height + next[0].height <= maxHeight) {
      next[0] = makePiece([...pieces.pop().slices, ...next[0].slices]);
      joinedPrevious = true;
    } else if (atStart && long && previous && !previous.blank) {
      const rebalanced = partitionSection(next.flatMap((piece) => piece.slices), maxHeight, { leading: previous.slices });
      if (rebalanced) {
        pieces.pop();
        next = rebalanced;
      }
    }
    if (long) previousLongStart = pieces.length;
    else if (!short || !joinedPrevious) previousLongStart = null;
    pieces.push(...next);
    atStart &&= short;
  }
  placeGap(trailingGap, []);
  return rebalanceWithTrailingSections(absorbBlankPieces(pieces, heights, maxHeight), sections, maxHeight);
}

function mergePiecesForPublication(pieces, maxHeight) {
  while (pieces.length > MAX_PUBLICATION_PIECES) {
    let bestIndex = -1;
    let bestHeight = Infinity;
    for (let i = 0; i + 1 < pieces.length; i += 1) {
      const height = pieces[i].height + pieces[i + 1].height;
      if (height <= maxHeight && height < bestHeight) {
        bestIndex = i;
        bestHeight = height;
      }
    }
    if (bestIndex === -1) break;
    pieces.splice(bestIndex, 2, makePiece([...pieces[bestIndex].slices, ...pieces[bestIndex + 1].slices]));
  }
  return pieces;
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

export function detailFileName(index, count, format, base = '상세페이지', preset) {
  const digits = Math.max(2, String(Math.max(1, count)).length);
  const ext = format === 'png' ? 'png' : 'jpg';
  return `${preset === 'cafe24' ? 'detail' : base}_${String(index + 1).padStart(digits, '0')}.${ext}`;
}

export function publicationKindForSingle(format) {
  return format === 'jpeg' ? 'jpeg' : 'long_png';
}

function buildExportLayout(blocks, settings, maxHeight) {
  const width = exportWidth(settings);
  if (width === null) return null;
  const ratio = width / EXPORT_WIDTH;
  const original = !!exportPreset(settings.preset).original;
  const maxPieceHeight = maxHeight ?? (original ? ORIGINAL_PNG_MAX_HEIGHT : MAX_SPLIT_HEIGHT);
  const measured = blocks.map((block) => typeof block === 'number' ? { height: block } : block);
  const scaledHeights = measured.map((block) => exportPixelHeight(block.height, width));
  const height = scaledHeights.reduce((sum, value) => sum + value, 0);
  const fallbackReason = settings.delivery === 'single' ? singlePageFallbackReason({ width, height }) : null;
  const delivery = fallbackReason ? 'split' : settings.delivery;
  const pieces = delivery === 'split' ? mergePiecesForPublication(planSplitPieces(measured, scaledHeights, maxPieceHeight), maxPieceHeight)
    : height > 0 ? [makePiece(scaledHeights.flatMap((height, index) => splitBlock(index, height, measured[index].blank, maxPieceHeight)))] : [];
  return { width, ratio, height, scaledHeights, original, delivery, maxPieceHeight, pieces, fallbackReason };
}

export function buildExportSummary(blocks, rawSettings) {
  const settings = normalizeExportSettings(rawSettings);
  const layout = buildExportLayout(blocks, settings);
  if (!layout) return null;
  const bytesPerPixel = settings.format === 'jpeg' ? 0.24 : 1.1;
  return {
    ...layout,
    pieceHeights: layout.pieces.map((piece) => piece.height),
    fileCount: layout.pieces.length,
    maxPieceHeight: layout.delivery === 'split' ? layout.maxPieceHeight : null,
    approxBytes: Math.round(layout.width * layout.height * bytesPerPixel),
    approximate: true,
  };
}

function blockSlicePlan(layout) {
  const plan = layout.scaledHeights.map(() => []);
  layout.pieces.forEach((piece, pieceIndex) => {
    for (const [sliceIndex, slice] of piece.slices.entries()) {
      const cutSlack = slice.y + slice.height < layout.scaledHeights[slice.blockIndex]
        ? layout.maxPieceHeight - layout.pieces[pieceIndex + 1].height : 0;
      plan[slice.blockIndex].push({ ...slice, pieceIndex, cutSlack, endsPiece: sliceIndex === piece.slices.length - 1 });
    }
  });
  return plan;
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
  { searchWindow = CUT_SEARCH_WINDOW, tolerance = 2 } = {},
) {
  const height = Math.floor(pixels.length / Math.max(1, width * 4));
  const target = Math.min(height, Math.max(1, Math.trunc(targetHeight)));
  const first = Math.max(1, target - searchWindow);
  for (let y = target - 1; y >= first; y -= 1) {
    if (rowIsUniform(pixels, width, y, tolerance)) return y;
  }
  return target;
}

function findUniformJpegRow(canvas, range, middle) {
  const start = Math.max(range.y + 1, middle - CUT_SEARCH_WINDOW);
  const end = Math.min(range.y + range.height - 1, middle + CUT_SEARCH_WINDOW);
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  const { data } = ctx.getImageData(0, start, canvas.width, end - start + 1);
  for (let step = 0; step <= CUT_SEARCH_WINDOW; step += 1) {
    const before = middle - step;
    const after = middle + step;
    if (before >= start && rowIsUniform(data, canvas.width, before - start, 2)) return before;
    if (after <= end && rowIsUniform(data, canvas.width, after - start, 2)) return after;
  }
  return null;
}

export async function splitOversizeJpegRanges(range, encodeRange, options = {}) {
  const { maxBytes = MAX_JPEG_BYTES, quality = JPEG_QUALITY, blockBoundaries = [], findUniformRow } = options;
  const blob = await encodeRange(range, quality);
  if (blob.size <= maxBytes) return [{ range, blob }];
  if (range.height <= 1) {
    throw new Error('JPG 조각을 3MB 이하로 만들 수 없어요. PNG로 다시 시도해 주세요.');
  }
  const end = range.y + range.height;
  const middle = range.y + Math.floor(range.height / 2);
  let boundary = null;
  for (const row of blockBoundaries) {
    if (row > range.y && row < end && (boundary === null
      || Math.abs(row - middle) < Math.abs(boundary - middle))) boundary = row;
  }
  const uniform = boundary === null ? findUniformRow?.(range, middle) : null;
  const cut = boundary ?? (uniform > range.y && uniform < end ? uniform : middle);
  const left = await splitOversizeJpegRanges({ y: range.y, height: cut - range.y }, encodeRange, options);
  const right = await splitOversizeJpegRanges({ y: cut, height: end - cut }, encodeRange, options);
  return [...left, ...right];
}

export function singlePageSizeWarning(byteSize) {
  return byteSize > 5 * 1024 * 1024
    ? '카페24 직접 등록, 쿠팡은 장당 5MB 한도라 나눠서 받기를 권해요.'
    : null;
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
  const style = node.ownerDocument?.defaultView?.getComputedStyle?.(node);
  if (style?.boxSizing === 'border-box') {
    // Hidden content can reduce the declared height below the padding that the clone still has.
    const edges = [style.paddingTop, style.paddingBottom, style.borderTopWidth, style.borderBottomWidth]
      .reduce((sum, value) => sum + (Number.parseFloat(value) || 0), 0);
    return Math.max(1, Math.round(Math.max(exportHeight, edges, Number.parseFloat(style.minHeight) || 0)));
  }
  if (!Number.isFinite(editorHeight)) return Math.round(exportHeight);
  return Math.max(1, offsetHeight - Math.round(editorHeight) + Math.round(exportHeight));
}

export function exportNodeBlock(node) {
  return {
    height: exportNodeHeight(node),
    pageSectionKey: node?.dataset?.exportSection,
    blank: node?.dataset?.exportBlank === 'true',
  };
}

const RESERVED_EXPORT_NAME = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;

export function sanitizeExportFileName(value) {
  const cleaned = String(value ?? '').normalize('NFC')
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().replace(/[ .]+$/, '');
  const base = Array.from(cleaned).slice(0, 60).join('').replace(/[ .]+$/, '');
  return RESERVED_EXPORT_NAME.test(base) ? `${base}_상세` : base || '상세페이지';
}

// The same pure projection drives preview elements and the cloned canvas.
// These DOM attributes are transient; no new editor-save fields are introduced.
export function applyExportLayout(node) {
  if (!node?.dataset?.exportLayout) return;
  const layout = JSON.parse(node.dataset.exportLayout);
  for (const element of node.querySelectorAll('[data-elid]')) {
    const patch = layout[element.dataset.elid];
    if (!patch) continue;
    for (const [key, style] of [['x', 'left'], ['y', 'top'], ['w', 'width'], ['h', 'height']]) {
      if (Number.isFinite(patch[key])) element.style[style] = `${patch[key]}px`;
    }
  }
}

export function exportFileName(productName, suffix) {
  return `${sanitizeExportFileName(productName)}${suffix ? `_${suffix}` : ''}.png`;
}

/* ---- DOM 캡처 ---- */

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
  applyExportLayout(clone);
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
    await bakeExportBackdrops(clone, pixelRatio);
    // backgroundColor 옵션을 넘기면 html-to-image 가 **캡처 대상(블록) 자체의 배경색을 그 값으로
    // 덮어쓴다**(apply-style: style.backgroundColor = options.backgroundColor). 그래서 셀러가 바꾼
    // 블록 배경이 다운로드에서 전부 흰색이 됐다(2026-09-28). 블록 배경은 그대로 캡처하고,
    // 흰 바탕 합성은 우리가 한다 — 반투명 배경(bgOpacity)도 화면처럼 흰 바탕 위에 보인다.
    const height = clone.offsetHeight;
    const width = Math.round(EXPORT_WIDTH * pixelRatio);
    const probes = await collectExportImageProbes(clone, { ratio: pixelRatio, height });
    const svg = await toSvg(clone, { width: EXPORT_WIDTH, height, fontEmbedCSS });
    onVerify?.();
    const canvas = await rasterizeExportSvg(svg, {
      width,
      height: exportPixelHeight(height, width),
      probes,
    });
    return { canvas, softFailed };
  } finally {
    dispose();
  }
}

export async function captureBlockCanvasSlices(
  blockNode,
  pixelRatio,
  fontEmbedCSS,
  maxHeight = MAX_SPLIT_HEIGHT,
  imageCache,
  onVerify,
  plannedSlices,
) {
  const width = Math.round(EXPORT_WIDTH * pixelRatio);
  maxHeight = Math.min(maxHeight, MAX_CAPTURE_DIM, MAX_CANVAS_DIM,
    Math.floor(MAX_CANVAS_AREA / width));
  const { clone, softFailed, dispose } = await prepareClone(blockNode, imageCache);
  const canvases = [];
  try {
    await bakeExportBackdrops(clone, pixelRatio);
    const totalCssHeight = clone.offsetHeight;
    const totalHeight = exportPixelHeight(totalCssHeight, width);
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

    const slices = plannedSlices ?? splitBlock(0, totalHeight, false, maxHeight);
    const expectedHeight = slices.reduce((sum, slice) => sum + slice.height, 0);
    if (totalHeight !== expectedHeight) throw new Error('브라우저 한계로 이미지 아랫부분이 잘릴 수 있어 다운로드를 중단했어요.');
    let y = 0;
    for (const slice of slices) {
      // A cut shifted up in the previous slice leaves extra rows here.
      const wantedHeight = slice.y + slice.height - y;
      if (wantedHeight > maxHeight) throw new Error('이미지를 나눌 수 없어요. 다시 시도해 주세요.');
      const cssY = y / pixelRatio;
      const cssHeight = wantedHeight / pixelRatio;
      viewport.style.height = `${cssHeight}px`;
      clone.style.top = `${-cssY}px`;
      const probes = await collectExportImageProbes(clone, { ratio: pixelRatio, top: cssY, height: cssHeight });
      const svg = await toSvg(viewport, { width: EXPORT_WIDTH, height: cssHeight, fontEmbedCSS });
      // 최종 픽셀 수를 직접 지정해 CSS 높이 왕복에서 생기는 1px 손실을 막는다.
      onVerify?.();
      let flattened = await rasterizeExportSvg(svg, {
        width, height: wantedHeight, probes,
      }); // eslint-disable-line no-await-in-loop
      if (flattened.width !== width || flattened.height !== wantedHeight) {
        releaseExportCanvas(flattened);
        throw new Error('브라우저 한계로 이미지 아랫부분이 잘릴 수 있어 다운로드를 중단했어요.');
      }
      let keepHeight = flattened.height;
      if (slice.cutSlack > 0 && y + keepHeight < totalHeight) {
        const ctx = flattened.getContext('2d');
        if (!ctx) throw new Error('이미지를 나눌 수 없어요. 다시 시도해 주세요.');
        const pixels = ctx.getImageData(0, 0, flattened.width, flattened.height).data;
        keepHeight = findUniformCutRow(pixels, flattened.width, flattened.height, {
          searchWindow: Math.min(CUT_SEARCH_WINDOW, slice.cutSlack),
        });
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
  const measureBlock = deps.measureBlock || exportNodeBlock;
  const layout = deps.layout || buildExportLayout(blockNodes.map(measureBlock), settings, deps.maxHeight);
  if (!layout) throw new Error(CUSTOM_EXPORT_WIDTH_ERROR);
  if (settings.delivery === 'single' && layout.fallbackReason) throw new Error(layout.fallbackReason);
  const { width, ratio, maxPieceHeight: maxHeight } = layout;
  const createCanvas = deps.createCanvas || createExportCanvas;
  const releaseCanvas = deps.releaseCanvas || releaseExportCanvas;
  const encodeCanvas = deps.encodeCanvas || canvasToBlob;
  const captureBlock = deps.captureBlock;
  if (!captureBlock) throw new Error('내보내기 캡처 함수를 찾지 못했어요.');
  const mime = settings.format === 'png' ? 'image/png' : 'image/jpeg';
  let softFailed = 0;
  let totalHeight = 0;
  const validateCapture = (canvases, index) => {
    const expectedHeight = layout.scaledHeights[index];
    const actualHeight = canvases.reduce((sum, canvas) => sum + canvas.height, 0);
    if (canvases.some((canvas) => canvas.width !== width || canvas.height <= 0)
      || actualHeight !== expectedHeight) {
      throw new Error('브라우저 한계로 이미지 아랫부분이 잘릴 수 있어 다운로드를 중단했어요. 나눠서 다시 시도해 주세요.');
    }
    return actualHeight;
  };

  if (settings.delivery === 'single') {
    const out = createCanvas(width, layout.height);
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
          validateCapture([captured.canvas], i);
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
  const slicePlan = blockSlicePlan(layout);
  let current = null;
  let currentHeight = 0;
  let blockBoundaries = [];

  const savePiece = async (canvas, boundaries = []) => {
    if (settings.format === 'png') {
      files.push(await encodeCanvas(canvas, mime));
      return;
    }
    const parts = await splitOversizeJpegRanges({ y: 0, height: canvas.height }, async (range, quality) => {
      if (range.height === canvas.height) return encodeCanvas(canvas, mime, quality);
      const slice = drawCanvasRange(createCanvas, canvas, range);
      try { return await encodeCanvas(slice, mime, quality); }
      finally { releaseCanvas(slice); }
    }, { blockBoundaries: boundaries, findUniformRow: (range, middle) => findUniformJpegRow(canvas, range, middle) });
    files.push(...parts.map(({ blob }) => blob));
  };

  const flush = async () => {
    const exact = drawCanvasRange(createCanvas, current, { y: 0, height: currentHeight }, width);
    try { await savePiece(exact, blockBoundaries); }
    finally { releaseCanvas(exact); }
    releaseCanvas(current);
    current = null;
    currentHeight = 0;
    blockBoundaries = [];
  };

  try {
    for (let i = 0; i < blockNodes.length; i += 1) {
      const planned = slicePlan[i];
      if (!planned.length) continue;
      deps.onProgress?.(i, blockNodes.length);
      const captured = await captureBlock(blockNodes[i], ratio, i, planned);
      const canvases = captured.canvases || [captured.canvas];
      try {
        if (canvases.length !== planned.length) throw new Error('이미지를 나눌 수 없어요. 다시 시도해 주세요.');
        totalHeight += validateCapture(canvases, i);
        softFailed += captured.softFailed;
        for (const [index, canvas] of canvases.entries()) {
          const pieceIndex = planned[index].pieceIndex;
          const piece = layout.pieces[pieceIndex];
          if (piece.slices.length === 1) {
            if (canvas.height > maxHeight) throw new Error('이미지를 나눌 수 없어요. 다시 시도해 주세요.');
            await savePiece(canvas);
            continue;
          }
          if (!current) current = createCanvas(width, maxHeight);
          const ctx = current.getContext('2d');
          if (!ctx || currentHeight + canvas.height > maxHeight) throw new Error('저장용 캔버스를 만들지 못했어요. 다시 시도해 주세요.');
          if (currentHeight > 0) blockBoundaries.push(currentHeight);
          ctx.drawImage(canvas, 0, currentHeight);
          currentHeight += canvas.height;
          if (planned[index].endsPiece) await flush();
        }
      } finally { canvases.forEach(releaseCanvas); }
    }
    deps.onProgress?.(blockNodes.length - 1, blockNodes.length, 'encode');
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

// Ordinary selections keep original bytes. Backdrop selections download the
// cropped, baked background (GIF replacements use their stable poster frame).
export async function downloadEditorPhoto(image, productName, { fetchImage = clientApiFetch, save = saveBlob,
  loadImage = loadBlobImage, createCanvas, encodeCanvas = canvasToBlob } = {}) {
  if (!image?.src) throw new Error('받을 사진을 선택해 주세요.');
  const backdrop = hasBackdropBlur(image);
  const response = await fetchImage(toBytesUrl(backdrop && isGifImage(image) ? image.posterSrc : image.src));
  const type = (response.headers.get('content-type') || '').split(';')[0];
  if (!response.ok || !type.startsWith('image/')) {
    throw attachResponseError(new Error('사진을 받지 못했어요. 다시 시도해 주세요.'), response);
  }
  let blob = await readClientResponse(response, 'blob');
  if (backdrop) {
    const decoded = await loadImage(blob);
    const canvas = rasterizeBackdropImage(decoded, image, PIXEL_RATIO, { createCanvas });
    try { blob = await encodeCanvas(canvas, 'image/png'); }
    finally { releaseExportCanvas(canvas); }
  }
  const ext = backdrop ? 'png' : { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/avif': 'avif', 'image/gif': 'gif', 'image/svg+xml': 'svg' }[type] || 'png';
  const name = `${productName || 'wearless'}-${image.cutType || 'photo'}`.replace(/[\\/:*?"<>|]/g, '-');
  save(blob, `${name}.${ext}`);
}

async function loadBlobImage(blob) {
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return image;
  } finally { URL.revokeObjectURL(url); }
}

// Export clones use a fixed first frame, while the live canvas keeps playing.
export function replaceGifPosters(clone) {
  clone.querySelectorAll('img[data-gif-poster]').forEach((img) => {
    const poster = img.getAttribute('data-gif-poster');
    if (!poster) return;
    img.removeAttribute('srcset');
    img.setAttribute('src', exportStillImageSource(img.getAttribute('src'), poster));
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
  const base = sanitizeExportFileName(opts.fileName);
  // ArrayBuffer also works with JSZip outside browsers, without a FileReader shim.
  for (let index = 0; index < files.length; index += 1) {
    zip.file(detailFileName(index, files.length, format, base, opts.preset), await files[index].arrayBuffer());
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
  const fontEmbedCSS = await getPageFontEmbedCSS([blockNode]);
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
  const fontEmbedCSS = await getPageFontEmbedCSS(blockNodes);
  const heights = blockNodes.map(exportNodeHeight);
  const totalCssHeight = heights.reduce((a, b) => a + b, 0);
  const ratio = fitPixelRatio(totalCssHeight, exportScaleForPreset('original'));
  if (ratio == null) {
    throw new Error('페이지가 너무 길어 한 장으로는 저장할 수 없어요. 블록별 ZIP으로 받아주세요.');
  }
  const imageCache = new Map();
  const out = document.createElement('canvas');
  out.width = Math.round(EXPORT_WIDTH * ratio);
  // 블록별 캡처와 같은 높이로 예약해 이음새를 없앤다.
  out.height = heights.reduce((sum, height) => sum + exportPixelHeight(height, out.width), 0);
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
  fileName,
  onProgress,
  rawSettings,
  opts = {},
) {
  const settings = normalizeExportSettings(rawSettings);
  if (exportWidth(settings) === null) throw new Error(CUSTOM_EXPORT_WIDTH_ERROR);
  await blockNodes[0]?.ownerDocument?.fonts?.ready;
  await waitForEditedContrast(blockNodes);
  const layout = buildExportLayout(blockNodes.map(exportNodeBlock), settings);
  const { fallbackReason: autoSwitchReason, delivery, original, maxPieceHeight } = layout;
  const effective = { ...settings, delivery };

  if (original && delivery === 'single') {
    const result = await exportLongPng(blockNodes, fileName, onProgress, opts);
    return { ...result, autoSwitchReason, delivery: effective.delivery };
  }

  recordClientAction('download');
  const fontEmbedCSS = await getPageFontEmbedCSS(blockNodes);
  const imageCache = new Map();
  let rendered;
  try {
    rendered = await renderStorefrontFiles(blockNodes, effective, {
      onProgress, layout,
      captureBlock: effective.delivery === 'split'
        ? (node, ratio, index, plannedSlices) => captureBlockCanvasSlices(node, ratio, fontEmbedCSS,
          maxPieceHeight, imageCache,
          () => onProgress?.(index, blockNodes.length, 'verify'), plannedSlices)
        : (node, ratio, index) => captureBlockCanvas(node, ratio, fontEmbedCSS, imageCache,
          () => onProgress?.(index, blockNodes.length, 'verify')),
    });
  } finally { imageCache.clear(); }
  let blob;
  const base = sanitizeExportFileName(fileName);
  let verifyUrl = null;
  let notarizeWarning = null;

  if (effective.delivery === 'split') {
    onProgress?.(blockNodes.length - 1, blockNodes.length, 'zip');
    ({ blob, verifyUrl, notarizeWarning } = await packageStorefrontZip(rendered.files, effective.format,
      { ...opts, preset: effective.preset, fileName }));
    saveBlob(blob, `${base}.zip`);
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
    saveBlob(blob, `${base}${ext}`);
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
