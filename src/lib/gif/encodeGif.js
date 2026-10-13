import { toBytesUrl } from '../../features/editor/editorExport.js';
import { coverCrop, planLadder, presetSize, shouldStopLadder, GIF_HARD_BYTES, GIF_SOFT_BYTES, GIF_PHOTO_DELAY_MS } from './gifLadder.js';

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
    // Soft attempts end before 128-color or reduced-size steps for photos.
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
    await request({ type: 'start', settings: { width, height, delayMs: Array.isArray(delayMs) ? delayMs[0] : delayMs, colors } });
    for (let index = 0; index < frames; index += 1) {
      checkAbort(signal);
      const rgba = await readFrame(index);
      checkAbort(signal);
      const buffer = rgba.byteOffset === 0 && rgba.byteLength === rgba.buffer.byteLength ? rgba.buffer : rgba.slice().buffer;
      await request({ type: 'frame', buffer, delayMs: Array.isArray(delayMs) ? delayMs[index] : delayMs }, [buffer]);
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

export async function encodeGif({ sources = [], preset = 'original', onProgress, signal }) {
  checkAbort(signal);
  if (sources.length < 2 || sources.length > 8) throw new Error('사진을 2~8장 선택해 주세요');
  const steps = planLadder('photo');
  onProgress?.(0);
  const images = await Promise.all(sources.map((src) => loadGifImage(src, { signal })));
  const sourceSize = { width: images[0].naturalWidth, height: images[0].naturalHeight };
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('이 브라우저에서 GIF를 만들지 못했어요');
  try {
    const result = await runLadder('photo', async (step, stepIndex) => {
      const { width, height } = presetSize(preset, step.scale, sourceSize);
      canvas.width = width;
      canvas.height = height;
      let jpgBlob;
      const bytes = await encodeWithWorker({ width, height, frames: images.length, delayMs: GIF_PHOTO_DELAY_MS, colors: step.colors, signal,
        onProgress: (fraction) => onProgress?.(Math.floor((stepIndex + fraction) / steps.length * 99)),
        readFrame: async (index) => {
          checkAbort(signal);
          const image = images[index];
          const crop = coverCrop(image.naturalWidth, image.naturalHeight, width, height, { anchorY: 0.08 });
          context.fillStyle = 'white';
          context.fillRect(0, 0, width, height);
          context.drawImage(image, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, width, height);
          if (index === 0) jpgBlob = await firstFrameJpg(canvas);
          return context.getImageData(0, 0, width, height).data;
        },
      });
      return { blob: new Blob([bytes], { type: 'image/gif' }), jpgBlob, width, height, frames: images.length, delayMs: GIF_PHOTO_DELAY_MS };
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
