import { parseGIF, decompressFrames } from 'gifuct-js';
import { MAX_UPLOAD_BYTES } from '../imageTranscode.js';
import { encodeWithWorker, runLadder } from './encodeGif.js';
import { GIF_HARD_BYTES, planLadder } from './gifLadder.js';

export function validateGifAttachment(file, width, height) {
  if (file?.type !== 'image/gif' && !/\.gif$/i.test(file?.name || '')) return 'GIF 파일만 올릴 수 있어요(.gif)';
  if (width > 4000 || height > 4000) return '4,000px 이하 GIF만 올릴 수 있어요';
  if (file.size > MAX_UPLOAD_BYTES) return 'GIF는 25MB 이하만 올릴 수 있어요';
  return null;
}

function checkAbort(signal) {
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
}
const frameDelay = frame => (frame.gce?.delay || 10) * 10;

export async function inspectGifAttachment(file, { signal } = {}) {
  // Read only the logical screen header before checking the byte limit.
  const header = new Uint8Array(await file.slice(0, 10).arrayBuffer());
  checkAbort(signal);
  const width = header[6] | header[7] << 8, height = header[8] | header[9] << 8;
  const error = validateGifAttachment(file, width, height);
  if (error) throw new Error(error);
  if (!/^GIF8[79]a$/.test(new TextDecoder().decode(header.slice(0, 6))) || !width || !height) {
    throw new Error('GIF 파일을 읽지 못했어요. 다른 파일을 선택해 주세요');
  }
  const parsed = parseGIF(await file.arrayBuffer());
  checkAbort(signal);
  const frames = parsed.frames.filter(frame => frame.image);
  if (!frames.length || frames.some(frame => {
    const d = frame.image.descriptor;
    return !d.width || !d.height || d.left + d.width > width || d.top + d.height > height;
  })) throw new Error('GIF 파일을 읽지 못했어요. 다른 파일을 선택해 주세요');
  return { parsed, blob: file.type === 'image/gif' ? file : new Blob([file], { type: 'image/gif' }), width, height, frames: frames.length,
    delayMs: frames.reduce((total, frame) => total + frameDelay(frame), 0) / frames.length, bytes: file.size };
}

export function gifFrameSchedule(parsed) {
  let time = 0;
  const timeline = [];
  for (const [index, frame] of parsed.frames.filter(f => f.image).entries()) {
    if (time >= 4000) break;
    const delayMs = Math.min(frameDelay(frame), 4000 - time);
    timeline.push({ index, time, delayMs });
    time += delayMs;
  }
  if (timeline.length <= 40 && timeline.every(frame => frame.delayMs >= 100)) return timeline;
  const sampled = [];
  let cursor = 0;
  for (let at = 0; at < time; at += 100) {
    while (cursor + 1 < timeline.length && timeline[cursor + 1].time <= at) cursor++;
    const index = timeline[cursor].index, delayMs = Math.min(100, time - at);
    if (sampled.at(-1)?.index === index) sampled.at(-1).delayMs += delayMs;
    else sampled.push({ index, time: at, delayMs });
  }
  return sampled;
}

// gifuct must decode LZW indices at source size. Only one indexed patch exists at
// a time; RGBA composition is allocated at the reduced size before decompression.
export function* composeGifFrames(parsed, schedule, { maxWidth = 800, scale = 1, signal } = {}) {
  const sourceW = parsed.lsd.width, sourceH = parsed.lsd.height;
  const ratio = Math.min(1, maxWidth / sourceW) * scale;
  const width = Math.max(1, Math.round(sourceW * ratio)), height = Math.max(1, Math.round(sourceH * ratio));
  const rgba = new Uint8ClampedArray(width * height * 4);
  const rawFrames = parsed.frames.filter(f => f.image);
  const background = parsed.gct?.[parsed.lsd.backgroundColorIndex] || [255, 255, 255];
  const fill = (dims, transparent) => {
    for (let y = 0; y < height; y++) {
      const sy = Math.min(sourceH - 1, Math.floor((y + 0.5) * sourceH / height));
      if (sy < dims.top || sy >= dims.top + dims.height) continue;
      for (let x = 0; x < width; x++) {
        const sx = Math.min(sourceW - 1, Math.floor((x + 0.5) * sourceW / width));
        if (sx < dims.left || sx >= dims.left + dims.width) continue;
        rgba.set(transparent ? [0, 0, 0, 0] : [...background, 255], (y * width + x) * 4);
      }
    }
  };
  fill({ left: 0, top: 0, width: sourceW, height: sourceH }, rawFrames[0]?.gce?.extras.transparentColorGiven);
  let next = 0;
  for (let index = 0; index < rawFrames.length && next < schedule.length; index++) {
    checkAbort(signal);
    const [frame] = decompressFrames({ ...parsed, frames: [rawFrames[index]] }, false);
    const restore = frame.disposalType === 3 ? rgba.slice() : null;
    const d = frame.dims;
    for (let y = 0; y < height; y++) {
      const sy = Math.floor((y + 0.5) * sourceH / height) - d.top;
      if (sy < 0 || sy >= d.height) continue;
      for (let x = 0; x < width; x++) {
        const sx = Math.floor((x + 0.5) * sourceW / width) - d.left;
        if (sx < 0 || sx >= d.width) continue;
        const color = frame.pixels[sy * d.width + sx];
        if (color === frame.transparentIndex) continue;
        rgba.set([...(frame.colorTable[color] || [0, 0, 0]), 255], (y * width + x) * 4);
      }
    }
    while (schedule[next]?.index === index) {
      const output = rgba.slice();
      for (let p = 0; p < output.length; p += 4) if (!output[p + 3]) output.set([255, 255, 255, 255], p);
      next++;
      yield output;
    }
    if (frame.disposalType === 2) fill(d, frame.transparentIndex !== undefined);
    if (restore) rgba.set(restore);
  }
}

export async function gifPoster(rgba, width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  try {
    const ctx = canvas.getContext('2d');
    ctx.putImageData(new ImageData(rgba, width, height), 0, 0);
    return await new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob)
      : reject(new Error('첫 컷을 만들지 못했어요')), 'image/jpeg', 0.92));
  } finally { canvas.width = 0; canvas.height = 0; }
}

export async function prepareGifAttachment(info, { signal } = {}) {
  checkAbort(signal);
  const first = composeGifFrames(info.parsed, [{ index: 0 }], { maxWidth: info.width, signal }).next().value;
  const jpgBlob = await gifPoster(first, info.width, info.height);
  checkAbort(signal);
  const { parsed, ...result } = info;
  return { ...result, jpgBlob };
}

export async function reduceGifAttachment(info, { signal, onProgress } = {}) {
  const timeline = gifFrameSchedule(info.parsed);
  const durationMs = timeline.at(-1).time + timeline.at(-1).delayMs;
  const steps = planLadder('attachment');
  const baseWidth = Math.min(800, info.width), baseHeight = info.height * baseWidth / info.width;
  onProgress?.(0);
  const result = await runLadder('attachment', async (step, stepIndex) => {
    const delayMs = 1000 / step.fps;
    const frameCount = Math.ceil(Math.min(durationMs, (step.duration ?? Infinity) * 1000) * step.fps / 1000);
    const schedule = [];
    let cursor = 0;
    for (let frame = 0; frame < frameCount; frame++) {
      const time = frame * delayMs;
      // Equal distances keep the earlier frame; repeated frames preserve holds.
      while (cursor + 1 < timeline.length && time > (timeline[cursor].time + timeline[cursor + 1].time) / 2) cursor++;
      schedule.push({ index: timeline[cursor].index, time, delayMs });
    }
    const width = Math.max(1, Math.round(baseWidth * step.scale)), height = Math.max(1, Math.round(baseHeight * step.scale));
    const iterator = composeGifFrames(info.parsed, schedule, { scale: step.scale, signal });
    let jpgBlob;
    try {
      const bytes = await encodeWithWorker({ width, height, frames: schedule.length, colors: step.colors,
        delayMs, signal,
        onProgress: fraction => onProgress?.(Math.floor((stepIndex + fraction) / steps.length * 99)),
        readFrame: async index => {
          const rgba = iterator.next().value;
          if (index === 0) jpgBlob = await gifPoster(rgba, width, height);
          return rgba;
        },
      });
      return { blob: new Blob([bytes], { type: 'image/gif' }), jpgBlob, width, height,
        frames: schedule.length, delayMs };
    } finally { iterator.return(); }
  }, { signal });
  if (result.bytes > GIF_HARD_BYTES) throw new Error('줄여도 5MB를 넘어요. 더 짧은 GIF를 올려 주세요');
  onProgress?.(100);
  return result;
}
