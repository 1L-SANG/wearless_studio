import { toBytesUrl } from '../../features/editor/editorExport.js';
import { coverCrop, planLadder, presetSize, shouldStopLadder, GIF_HARD_BYTES, GIF_SOFT_BYTES, GIF_PHOTO_DELAY_MS } from './gifLadder.js';

export const VIDEO_FORMAT_MESSAGE = 'MP4(H.264)로 바꿔 올려 주세요';

function checkAbort(signal) {
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
}

function waitForMedia(element, event, init, signal, message) {
  return new Promise((resolve, reject) => {
    let timer;
    const cleanup = () => {
      clearTimeout(timer);
      element.removeEventListener(event, ready);
      element.removeEventListener('error', failed);
      signal?.removeEventListener('abort', aborted);
    };
    const ready = () => { cleanup(); resolve(element); };
    const failed = () => { cleanup(); reject(new Error(message)); };
    const aborted = () => { cleanup(); reject(new DOMException('Aborted', 'AbortError')); };
    if (signal?.aborted) { aborted(); return; }
    element.addEventListener(event, ready, { once: true });
    element.addEventListener('error', failed, { once: true });
    signal?.addEventListener('abort', aborted, { once: true });
    timer = setTimeout(failed, 30_000);
    try { init(); } catch (error) { cleanup(); reject(error); }
  });
}

export async function loadGifImage(src, { signal } = {}) {
  checkAbort(signal);
  const image = new Image();
  image.crossOrigin = 'anonymous';
  await waitForMedia(image, 'load', () => { image.src = toBytesUrl(src); }, signal, '사진을 읽지 못했어요. 다시 선택해 주세요');
  if (!image.naturalWidth || !image.naturalHeight) throw new Error('사진을 읽지 못했어요. 다시 선택해 주세요');
  return image;
}

export function releaseGifVideo(video) {
  if (!video) return;
  video.pause();
  video.removeAttribute('src');
  video.load();
}

export async function loadGifVideo(src, { signal } = {}) {
  checkAbort(signal);
  const video = document.createElement('video');
  video.preload = 'auto';
  video.muted = true;
  video.playsInline = true;
  try {
    await waitForMedia(video, 'loadeddata', () => { video.src = src; video.load(); }, signal, VIDEO_FORMAT_MESSAGE);
    if (!Number.isFinite(video.duration) || !video.videoWidth || !video.videoHeight) throw new Error(VIDEO_FORMAT_MESSAGE);
    if (video.duration < 1) throw new Error('1초 이상 영상을 올려 주세요');
    return video;
  } catch (error) {
    releaseGifVideo(video);
    throw error;
  }
}

export function videoFrameTimes(start, duration, videoDuration, fps) {
  if (![start, duration, videoDuration, fps].every(Number.isFinite) || duration <= 0 || videoDuration <= 0 || fps <= 0) {
    throw new Error('Invalid GIF video range');
  }
  const length = Math.min(duration, videoDuration);
  const from = Math.max(0, Math.min(start, videoDuration - length));
  return Array.from({ length: Math.ceil(length * fps) }, (_, index) => from + index / fps);
}

export async function seekGifVideo(video, time, { signal } = {}) {
  checkAbort(signal);
  if (Math.abs(video.currentTime - time) < 0.00001 && video.readyState >= 2 && !video.seeking) return;
  await waitForMedia(video, 'seeked', () => { video.currentTime = time; }, signal, VIDEO_FORMAT_MESSAGE);
}

export async function runLadder(mode, encodeAttempt, { signal } = {}) {
  const steps = planLadder(mode);
  let firstHardResult;
  for (let stepIndex = 0; stepIndex < steps.length; stepIndex += 1) {
    checkAbort(signal);
    const step = steps[stepIndex];
    const encoded = await encodeAttempt(step, stepIndex);
    checkAbort(signal);
    const result = { ...encoded, bytes: encoded.blob.size, colors: step.colors, scale: step.scale, stepIndex };
    if (!firstHardResult && result.bytes <= GIF_HARD_BYTES) firstHardResult = result;
    // Soft attempts end before 128-color or reduced-size steps in both modes.
    if (shouldStopLadder(mode, stepIndex, result.bytes) || (stepIndex === 1 && firstHardResult)) {
      return result.bytes <= GIF_SOFT_BYTES ? result : firstHardResult || result;
    }
  }
}

export async function encodeWithWorker({ width, height, frames, delayMs, colors, readFrame, onProgress, signal },
  createWorker = () => new Worker(new URL('./gifEncodeWorker.js', import.meta.url), { type: 'module' })) {
  checkAbort(signal);
  const worker = createWorker();
  let pending, failure;
  const fail = (error) => { failure = error; pending?.reject(error); };
  const abort = () => fail(new DOMException('Aborted', 'AbortError'));
  worker.onmessage = ({ data }) => {
    if (data.type === 'error') fail(new Error(data.message || 'GIF를 만들지 못했어요. 다시 시도해 주세요'));
    else pending?.resolve(data);
  };
  worker.onerror = (event) => {
    event.preventDefault?.();
    fail(new Error('GIF를 만들지 못했어요. 다시 시도해 주세요'));
  };
  worker.onmessageerror = () => fail(new Error('GIF를 만들지 못했어요. 다시 시도해 주세요'));
  signal?.addEventListener('abort', abort, { once: true });
  const request = (data, transfer = []) => new Promise((resolve, reject) => {
    if (failure) { reject(failure); return; }
    const timer = setTimeout(() => fail(new Error('GIF를 만들지 못했어요. 다시 시도해 주세요')), 30_000);
    const settle = (handler) => (value) => { clearTimeout(timer); pending = null; handler(value); };
    pending = { resolve: settle(resolve), reject: settle(reject) };
    try { worker.postMessage(data, transfer); } catch (error) { fail(error); }
  });
  try {
    await request({ type: 'start', settings: { width, height, delayMs, colors } });
    for (let index = 0; index < frames; index += 1) {
      checkAbort(signal);
      const rgba = await readFrame(index);
      checkAbort(signal);
      const buffer = rgba.byteOffset === 0 && rgba.byteLength === rgba.buffer.byteLength ? rgba.buffer : rgba.slice().buffer;
      await request({ type: 'frame', buffer }, [buffer]);
      onProgress?.((index + 1) / frames);
    }
    const result = await request({ type: 'finish' });
    return new Uint8Array(result.buffer);
  } finally {
    signal?.removeEventListener('abort', abort);
    worker.terminate();
  }
}

function firstFrameJpg(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('첫 컷을 만들지 못했어요. 다시 시도해 주세요')), 'image/jpeg', 0.92);
  });
}

export async function encodeGif({ mode = 'photo', sources = [], video, preset = 'original',
  start = 0, duration = 3, onProgress, signal }) {
  checkAbort(signal);
  if (mode === 'photo' && (sources.length < 2 || sources.length > 8)) throw new Error('사진을 2~8장 선택해 주세요');
  if (mode === 'video' && !video) throw new Error('영상을 선택해 주세요');
  const steps = planLadder(mode);
  onProgress?.(0);
  const images = mode === 'photo' ? await Promise.all(sources.map((src) => loadGifImage(src, { signal }))) : [];
  const sourceSize = mode === 'photo'
    ? { width: images[0].naturalWidth, height: images[0].naturalHeight }
    : { width: video.videoWidth, height: video.videoHeight };
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('이 브라우저에서 GIF를 만들지 못했어요');
  try {
    const result = await runLadder(mode, async (step, stepIndex) => {
      const { width, height } = presetSize(preset, step.scale, sourceSize);
      canvas.width = width;
      canvas.height = height;
      const times = mode === 'video' ? videoFrameTimes(start, Math.min(duration, step.duration ?? duration), video.duration, step.fps) : [];
      const frames = mode === 'photo' ? images.length : times.length;
      const frameDelay = mode === 'photo' ? GIF_PHOTO_DELAY_MS : 1000 / step.fps;
      let jpgBlob;
      const bytes = await encodeWithWorker({ width, height, frames, delayMs: frameDelay, colors: step.colors, signal,
        onProgress: (fraction) => onProgress?.(Math.floor((stepIndex + fraction) / steps.length * 99)),
        readFrame: async (index) => {
          checkAbort(signal);
          if (mode === 'video') await seekGifVideo(video, times[index], { signal });
          const image = mode === 'photo' ? images[index] : video;
          const crop = coverCrop(image.naturalWidth || image.videoWidth, image.naturalHeight || image.videoHeight, width, height, { anchorY: 0.08 });
          context.fillStyle = 'white';
          context.fillRect(0, 0, width, height);
          context.drawImage(image, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, width, height);
          if (index === 0) jpgBlob = await firstFrameJpg(canvas);
          return context.getImageData(0, 0, width, height).data;
        },
      });
      return { blob: new Blob([bytes], { type: 'image/gif' }), jpgBlob, width, height, frames, delayMs: frameDelay };
    }, { signal });
    onProgress?.(100);
    return result;
  } catch (error) {
    if (error.name === 'SecurityError') throw new Error('사진을 읽지 못했어요. 다시 선택해 주세요');
    throw error;
  } finally {
    canvas.width = 0;
    canvas.height = 0;
  }
}
