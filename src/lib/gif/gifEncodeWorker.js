import * as gifenc from 'gifenc';
import { colorBoost, ditherFrame } from './gifPalette.js';

// Node resolves the CJS entry; Vite resolves the ESM entry.
const { GIFEncoder, quantize } = gifenc.default?.quantize ? gifenc.default : gifenc;

// This exact encoder is also used by the Node tests, without a worker.
export function createFrameEncoder({ width, height, delayMs, colors = 256 }) {
  if (![width, height].every((value) => Number.isInteger(value) && value > 0 && value <= 65535)
    || !Number.isFinite(delayMs) || delayMs <= 0 || !Number.isInteger(colors) || colors < 2 || colors > 256) {
    throw new Error('Invalid GIF encoder settings');
  }
  const gif = GIFEncoder();
  let count = 0;
  return {
    addFrame(rgba, frameDelay = delayMs) {
      if (!Number.isFinite(frameDelay) || frameDelay <= 0) throw new Error('Invalid GIF frame delay');
      if (rgba.length !== width * height * 4) throw new Error('Invalid GIF frame size');
      const palette = quantize(colorBoost(rgba), colors, { format: 'rgb565' });
      const indexed = ditherFrame(rgba, width, height, palette);
      gif.writeFrame(indexed, width, height, {
        palette, delay: frameDelay, repeat: 0, dispose: 1,
        colorDepth: Math.max(2, Math.ceil(Math.log2(palette.length))),
      });
      count += 1;
    },
    finish() {
      if (!count) throw new Error('No GIF frames');
      gif.finish();
      return gif.bytes();
    },
  };
}

export function encodeGifFrames({ frames, ...settings }) {
  const encoder = createFrameEncoder(settings);
  frames.forEach((rgba) => encoder.addFrame(rgba));
  return encoder.finish();
}

if (typeof WorkerGlobalScope !== 'undefined' && globalThis instanceof WorkerGlobalScope) {
  let encoder;
  globalThis.onmessage = ({ data }) => {
    try {
      if (data.type === 'start') {
        encoder = createFrameEncoder(data.settings);
      } else if (data.type === 'frame') {
        encoder.addFrame(new Uint8ClampedArray(data.buffer), data.delayMs);
      } else if (data.type === 'finish') {
        const bytes = encoder.finish();
        encoder = null;
        globalThis.postMessage({ type: 'result', buffer: bytes.buffer }, [bytes.buffer]);
        return;
      } else {
        throw new Error('Unknown GIF worker message');
      }
      globalThis.postMessage({ type: 'ready' });
    } catch (error) {
      encoder = null;
      globalThis.postMessage({ type: 'error', message: error.message });
    }
  };
}
