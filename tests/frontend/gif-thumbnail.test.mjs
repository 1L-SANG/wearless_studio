import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { planLadder, shouldStopLadder, presetSize, coverCrop, GIF_HARD_BYTES, GIF_SOFT_BYTES } from '../../src/lib/gif/gifLadder.js';
import { colorBoost, ditherFrame } from '../../src/lib/gif/gifPalette.js';

const root = new URL('../../', import.meta.url);
const source = (path) => readFileSync(new URL(path, root), 'utf8');

test('사진 사다리는 설계서의 단계와 순서를 그대로 따른다', () => {
  assert.deepEqual(planLadder('photo'), [
    { colors: 256, scale: 1 }, { colors: 192, scale: 1 },
    { colors: 256, scale: 0.8 }, { colors: 128, scale: 0.8 }, { colors: 128, scale: 0.64 },
  ]);
  assert.throws(() => planLadder('other'), /mode/);
  const steps = planLadder('photo');
  steps[0].colors = 1;
  assert.equal(planLadder('photo')[0].colors, 256, '호출끼리 설정을 공유하지 않는다');
});

test('소프트 목표는 축소 전에 멈추고 하드 상한 실패는 마지막 단계까지 간다', () => {
  assert.equal(GIF_SOFT_BYTES, 1_500_000);
  assert.equal(GIF_HARD_BYTES, 5_000_000);
  for (const mode of ['photo', 'attachment']) {
    const hardStart = 2;
    const last = planLadder(mode).length - 1;
    assert.equal(shouldStopLadder(mode, 0, GIF_SOFT_BYTES), true);
    assert.equal(shouldStopLadder(mode, 0, GIF_SOFT_BYTES + 1), false);
    for (let index = 0; index < last; index += 1) {
      assert.equal(shouldStopLadder(mode, index, GIF_HARD_BYTES), index >= hardStart);
      assert.equal(shouldStopLadder(mode, index, GIF_HARD_BYTES + 1), false);
    }
    assert.equal(shouldStopLadder(mode, last, GIF_HARD_BYTES), true);
    assert.equal(shouldStopLadder(mode, last, GIF_HARD_BYTES + 1), true);
  }
});

test('세 규격은 정확한 크기와 가운데 cover 크롭을 쓴다', () => {
  for (const [id, width, height] of [['ably', 640, 768], ['cafe24', 800, 1067], ['square', 800, 800]]) {
    assert.deepEqual(presetSize(id), { width, height });
    const crop = coverCrop(1000, 2400, width, height);
    assert.equal(crop.sx, 0);
    assert.equal(crop.sw, 1000);
    assert.equal(crop.sh, 1000 * height / width);
    assert.equal(crop.sy, (2400 - crop.sh) / 2);
    assert.equal(crop.width, width);
    assert.equal(crop.height, height);
  }
  assert.deepEqual(coverCrop(2400, 1000, 800, 800), { sx: 700, sy: 0, sw: 1000, sh: 1000, width: 800, height: 800 });
  assert.deepEqual(presetSize('cafe24', 0.8), { width: 640, height: 854 });
  assert.throws(() => coverCrop(0, 1, 800, 800), /size/);
});

function randomBytes(seed) {
  let state = seed;
  return () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state >>> 24; };
}

// Parse blocks, not byte-pattern matches inside compressed image data.
function parseGif(bytes) {
  const u16 = (at) => bytes[at] | (bytes[at + 1] << 8);
  assert.equal(new TextDecoder().decode(bytes.slice(0, 6)), 'GIF89a');
  const width = u16(6), height = u16(8);
  let at = 13;
  const globalPalette = bytes[10] & 128 ? 2 ** ((bytes[10] & 7) + 1) : 0;
  at += globalPalette * 3;
  let loop = null, delay = null;
  const frames = [];
  const blocks = () => {
    const data = [];
    while (bytes[at]) {
      const count = bytes[at++];
      assert.ok(at + count <= bytes.length);
      data.push(...bytes.slice(at, at + count));
      at += count;
    }
    at += 1;
    return data;
  };
  while (bytes[at] !== 0x3b) {
    assert.ok(at < bytes.length, 'GIF 종료 바이트가 있어야 한다');
    const type = bytes[at++];
    if (type === 0x21) {
      const label = bytes[at++];
      const data = blocks();
      if (label === 0xf9) delay = data[1] | data[2] << 8;
      if (label === 0xff) {
        assert.equal(new TextDecoder().decode(new Uint8Array(data.slice(0, 11))), 'NETSCAPE2.0');
        loop = data[12] | data[13] << 8;
      }
    } else {
      assert.equal(type, 0x2c);
      const frame = { width: u16(at + 4), height: u16(at + 6), delay, localPalette: !!(bytes[at + 8] & 128) };
      const packed = bytes[at + 8];
      at += 9;
      if (packed & 128) at += 3 * 2 ** ((packed & 7) + 1);
      frame.codeSize = bytes[at++];
      frame.compressed = blocks();
      assert.ok(frame.compressed.length > 0);
      frames.push(frame);
    }
  }
  assert.equal(at, bytes.length - 1);
  return { width, height, loop, frames };
}

function decodeFrame(frame) {
  const clear = 1 << frame.codeSize, end = clear + 1;
  const prefix = new Uint16Array(4096), suffix = new Uint8Array(4096), stack = new Uint8Array(4097);
  const pixels = new Uint8Array(frame.width * frame.height);
  let bit = 0, size = frame.codeSize + 1, next = end + 1, previous = -1, first = 0, written = 0;
  while (bit + size <= frame.compressed.length * 8) {
    let code = 0;
    for (let i = 0; i < size; i += 1) code |= ((frame.compressed[(bit + i) >> 3] >> ((bit + i) & 7)) & 1) << i;
    bit += size;
    if (code === clear) { size = frame.codeSize + 1; next = end + 1; previous = -1; continue; }
    if (code === end) { assert.equal(written, pixels.length); return pixels; }
    assert.ok(code <= next && code < 4096);
    const incoming = code;
    let count = 0;
    if (code === next) { assert.ok(previous >= 0); stack[count++] = first; code = previous; }
    while (code >= clear) { assert.ok(count < 4096); stack[count++] = suffix[code]; code = prefix[code]; }
    first = code;
    stack[count++] = first;
    while (count) { assert.ok(written < pixels.length); pixels[written++] = stack[--count]; }
    if (previous >= 0 && next < 4096) {
      prefix[next] = previous; suffix[next] = first; next += 1;
      if (next === 1 << size && size < 12) size += 1;
    }
    previous = incoming;
  }
  assert.fail('LZW 종료 코드가 있어야 한다');
}

test('노이즈가 있는 800×1067 사진 6장을 실제 GIF89a·무한 반복·150cs·5MB 이하로 인코딩한다', async () => {
  const { encodeGifFrames } = await import('../../src/lib/gif/gifEncodeWorker.js');
  const width = 800, height = 1067;
  const frames = Array.from({ length: 6 }, (_, index) => {
    const rgba = new Uint8ClampedArray(width * height * 4);
    const random = randomBytes(100 + index);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const at = (y * width + x) * 4;
        rgba[at] = 95 + Math.floor(x * 95 / width) + random() % 24;
        rgba[at + 1] = 50 + Math.floor(y * 85 / height) + random() % 24;
        rgba[at + 2] = 35 + random() % 24;
        rgba[at + 3] = 255;
      }
    }
    return rgba;
  });
  const bytes = encodeGifFrames({ frames, width, height, delayMs: 1500, colors: 256 });
  const gif = parseGif(bytes);
  assert.equal(gif.width, 800);
  assert.equal(gif.height, 1067);
  assert.equal(gif.frames.length, 6);
  assert.equal(gif.loop, 0);
  assert.ok(gif.frames.every((frame) => frame.delay === 150 && frame.width === width && frame.height === height));
  assert.ok(gif.frames.slice(1).every((frame) => frame.localPalette), '프레임마다 자기 팔레트를 쓴다');
  gif.frames.forEach((frame) => assert.equal(decodeFrame(frame).length, width * height));
  assert.ok(bytes.byteLength <= GIF_HARD_BYTES, `${bytes.byteLength} bytes`);
});

function lab([r, g, b]) {
  const linear = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  [r, g, b] = [r, g, b].map(linear);
  const f = (v) => v > 216 / 24389 ? Math.cbrt(v) : v * 24389 / 27 / 116 + 16 / 116;
  const x = f((0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047);
  const y = f(0.2126729 * r + 0.7151522 * g + 0.072175 * b);
  const z = f((0.0193339 * r + 0.119192 * g + 0.9503041 * b) / 1.08883);
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)];
}

function redError(rgba, pixels, palette) {
  return pixels.reduce((sum, index) => {
    const rgb = Array.from(rgba.slice(index * 4, index * 4 + 3));
    const nearest = palette.reduce((best, color) => {
      const error = color.slice(0, 3).reduce((total, value, ch) => total + (value - rgb[ch]) ** 2, 0);
      return error < best.error ? { color, error } : best;
    }, { error: Infinity });
    const original = lab(rgb), reduced = lab(nearest.color);
    return sum + Math.hypot(...original.map((value, ch) => value - reduced[ch]));
  }, 0) / pixels.length;
}

test('0.2% 붉은 영역의 평균 Lab 색 오차는 256색 boost 팔레트에서 커지지 않는다', async () => {
  const { quantize } = (await import('gifenc')).default;
  const count = 100_000;
  const rgba = new Uint8ClampedArray(count * 4);
  const random = randomBytes(1729);
  const red = [];
  for (let i = 0; i < count; i += 1) {
    const rgb = i < count * 0.002
      ? [155 + random() % 70, 40 + random() % 30, 35 + random() % 20]
      : [165 + random() % 65, 100 + random() % 50, 75 + random() % 25];
    rgba.set([...rgb, 255], i * 4);
    if (i < count * 0.002) red.push(i);
  }
  assert.equal(red.length / count, 0.002);
  const plain = quantize(rgba, 256, { format: 'rgb565' });
  const boosted = quantize(colorBoost(rgba), 256, { format: 'rgb565' });
  assert.equal(plain.length, 256);
  assert.equal(boosted.length, 256);
  const baseline = redError(rgba, red, plain), error = redError(rgba, red, boosted);
  assert.ok(error <= baseline + 1e-7, `boost ΔE=${error}, baseline ΔE=${baseline}`);
});

test('colorBoost는 피부·붉은 픽셀만 세 번 더 넣고 원본은 바꾸지 않는다', () => {
  const rgba = new Uint8ClampedArray([180, 120, 90, 255, 160, 60, 65, 255, 20, 50, 180, 255]);
  const original = rgba.slice();
  const boosted = colorBoost(rgba);
  assert.deepEqual(rgba, original);
  assert.equal(boosted.length, 9 * 4);
  for (const rgb of [[180, 120, 90], [160, 60, 65], [20, 50, 180]]) {
    let matches = 0;
    for (let i = 0; i < boosted.length; i += 4) if (rgb.every((v, ch) => boosted[i + ch] === v)) matches += 1;
    assert.equal(matches, rgb[0] === 20 ? 1 : 4);
  }
});

test('Floyd–Steinberg는 평면색을 보존하고 경계 밖으로 오차를 보내지 않는다', () => {
  const rgba = new Uint8ClampedArray([0, 0, 0, 255, 255, 255, 255, 255, 128, 128, 128, 255, 128, 128, 128, 255]);
  const copy = rgba.slice();
  assert.deepEqual(Array.from(ditherFrame(rgba, 2, 2, [[0, 0, 0], [255, 255, 255]])), [0, 1, 1, 0]);
  assert.deepEqual(rgba, copy);
});

test('실제 blob 바이트로 사다리를 실행하고 최초 통과 설정을 돌려준다', async () => {
  const { runLadder } = await import('../../src/lib/gif/encodeGif.js');
  const calls = [];
  const result = await runLadder('photo', async (step, index) => {
    calls.push(index);
    return { blob: new Blob([new Uint8Array(index === 0 ? 2_000_000 : 1_400_000)]), bytes: 1, width: 640, height: 768, frames: 6, delayMs: 1500 };
  });
  assert.deepEqual(calls, [0, 1]);
  assert.equal(result.bytes, 1_400_000, '호출자가 보고한 숫자가 아닌 실제 용량');
  assert.equal(result.colors, 192);
  assert.equal(result.scale, 1);
  assert.equal(result.stepIndex, 1);
  const overflowCalls = [];
  const overflow = await runLadder('photo', async (_step, index) => {
    overflowCalls.push(index);
    return { blob: new Blob([new Uint8Array(GIF_HARD_BYTES + 1)]) };
  });
  assert.deepEqual(overflowCalls, [0, 1, 2, 3, 4]);
  assert.equal(overflow.stepIndex, 4);
  assert.equal(overflow.bytes, GIF_HARD_BYTES + 1);
});


test('이미지는 crossOrigin을 src보다 먼저 지정하고 자산 /file만 /bytes로 바꾼다', async () => {
  const { loadGifImage } = await import('../../src/lib/gif/encodeGif.js');
  const original = globalThis.Image;
  const assignments = [];
  class ImageFixture extends EventTarget {
    naturalWidth = 800;
    naturalHeight = 1000;
    set crossOrigin(value) { assignments.push(['cors', value]); }
    set src(value) { assignments.push(['src', value]); queueMicrotask(() => this.dispatchEvent(new Event('load'))); }
  }
  globalThis.Image = ImageFixture;
  try {
    await loadGifImage('/v1/assets/styling-1/file');
    assert.deepEqual(assignments, [['cors', 'anonymous'], ['src', '/v1/assets/styling-1/bytes?e=1']]);
    assignments.length = 0;
    await loadGifImage('/assets/mock-styling.jpg');
    assert.equal(assignments[1][1], '/assets/mock-styling.jpg');
  } finally { if (original) globalThis.Image = original; else delete globalThis.Image; }
});

test('워커 래퍼는 프레임을 순서대로 보내고 실제 GIF와 진행률을 받으며 워커를 닫는다', async () => {
  const { encodeWithWorker } = await import('../../src/lib/gif/encodeGif.js');
  const { createFrameEncoder } = await import('../../src/lib/gif/gifEncodeWorker.js');
  let encoder, terminated = false;
  const messages = [], progress = [];
  const worker = {
    postMessage(data, transfer) {
      messages.push(data.type);
      let response;
      if (data.type === 'start') { encoder = createFrameEncoder(data.settings); response = { type: 'ready' }; }
      if (data.type === 'frame') {
        assert.deepEqual(transfer, [data.buffer]);
        encoder.addFrame(new Uint8ClampedArray(data.buffer));
        response = { type: 'ready' };
      }
      if (data.type === 'finish') response = { type: 'result', buffer: encoder.finish().buffer };
      queueMicrotask(() => this.onmessage({ data: response }));
    },
    terminate() { terminated = true; },
  };
  const bytes = await encodeWithWorker({ width: 2, height: 2, frames: 2, delayMs: 1500, colors: 256,
    readFrame: async (i) => new Uint8ClampedArray(Array.from({ length: 4 }, () => i ? [200, 30, 60, 255] : [60, 120, 180, 255]).flat()),
    onProgress: (value) => progress.push(value),
  }, () => worker);
  assert.deepEqual(messages, ['start', 'frame', 'frame', 'finish']);
  assert.deepEqual(progress, [0.5, 1]);
  assert.equal(parseGif(bytes).frames.length, 2);
  assert.equal(terminated, true);
});

test('워커 실패와 중단에도 대기를 해제하고 terminate 한다', async () => {
  const { encodeWithWorker } = await import('../../src/lib/gif/encodeGif.js');
  const options = { width: 1, height: 1, frames: 2, colors: 256, delayMs: 100, readFrame: async () => new Uint8ClampedArray([0, 0, 0, 255]) };
  let terminated = false;
  const worker = { postMessage() { queueMicrotask(() => this.onmessage({ data: { type: 'error', message: 'encoding failed' } })); }, terminate() { terminated = true; } };
  await assert.rejects(encodeWithWorker(options, () => worker), /encoding failed/);
  assert.equal(terminated, true);
  const controller = new AbortController();
  terminated = false;
  const blocked = { postMessage() { queueMicrotask(() => controller.abort()); }, terminate() { terminated = true; } };
  await assert.rejects(encodeWithWorker({ ...options, signal: controller.signal }, () => blocked), { name: 'AbortError' });
  assert.equal(terminated, true);
});

async function panelHarness() {
  const { createServer } = await import('vite');
  const key = `__gifPanel${Math.random().toString(36).slice(2)}`;
  const runtime = { hooks: [], cursor: 0, effects: [], dirty: false,
    encodeCalls: [], thumbnails: [], saved: [], result: { blob: new Blob(['GIF89a'], { type: 'image/gif' }), jpgBlob: new Blob(['jpg'], { type: 'image/jpeg' }), width: 640, height: 768, frames: 2, bytes: 6 },
  };
  runtime.encodeGif = async (options) => { runtime.encodeCalls.push(options); options.onProgress(42); return runtime.result; };
  runtime.attachment = await import('../../src/lib/gif/gifAttachment.js');
  globalThis[key] = runtime;
  const access = `globalThis[${JSON.stringify(key)}]`;
  const vite = await createServer({ configFile: false, root: root.pathname, logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] }, esbuild: { jsx: 'automatic' },
    ssr: { noExternal: true, external: ['gifuct-js'] },
    resolve: { alias: { '@': `${root.pathname}src` } },
    plugins: [{ name: 'gif-panel-test', enforce: 'pre',
      resolveId(id) {
        if (id === 'react') return '\0gif-react';
        if (id === 'react/jsx-runtime' || id === 'react/jsx-dev-runtime') return '\0gif-jsx';
        if (id.endsWith('/components/ui.jsx')) return '\0gif-ui';
        if (id.endsWith('/gif/encodeGif.js')) return '\0gif-encode';
        if (id.endsWith('/gif/gifAttachment.js')) return '\0gif-attachment';
        if (id.endsWith('/lib/imageCdn.js')) return '\0gif-thumbnails';
      },
      load(id) {
        if (id === '\0gif-jsx') return `export const Fragment = 'fragment'; export function jsx(type, props) { return typeof type === 'function' ? type(props) : { type, props }; } export const jsxs = jsx; export const jsxDEV = jsx;`;
        if (id === '\0gif-ui') return `import { jsx } from 'react/jsx-runtime'; export function Button({ children, variant = 'ghost', block, ...props }) { return jsx('button', { ...props, className: 'btn btn-' + variant + (block ? ' btn-block' : ''), children }); } export function Icon() { return null; }`;
        if (id === '\0gif-encode') return `const r = ${access}; export const encodeGif = o => r.encodeGif(o);`;
        if (id === '\0gif-attachment') return `const r = ${access}; export const inspectGifAttachment = (...a) => r.attachment.inspectGifAttachment(...a); export const prepareGifAttachment = (...a) => r.attachment.prepareGifAttachment(...a); export const reduceGifAttachment = (...a) => r.attachment.reduceGifAttachment(...a);`;
        if (id === '\0gif-thumbnails') return `const r = ${access}; export const thumbUrl = (src, width) => { r.thumbnails.push({ src, width }); return /^https?:/.test(src) ? 'https://images.test/width=' + width + '/' + src : src; };`;
        if (id === '\0gif-react') return `const r = ${access};
          const same = (a,b) => a && b && a.length === b.length && a.every((v,i) => Object.is(v,b[i]));
          export function useState(initial) { const index = r.cursor++; const slot = r.hooks[index] ||= { value: typeof initial === 'function' ? initial() : initial }; return [slot.value, value => { const next = typeof value === 'function' ? value(slot.value) : value; if (!Object.is(next, slot.value)) { slot.value = next; r.dirty = true; } }]; }
          export function useRef(initial) { return (r.hooks[r.cursor++] ||= { current: initial }); }
          export function useMemo(fn, deps) { const index = r.cursor++; const slot = r.hooks[index]; if (!slot || !same(slot.deps, deps)) r.hooks[index] = { deps, value: fn() }; return r.hooks[index].value; }
          export const useCallback = (fn, deps) => useMemo(() => fn, deps);
          export function useEffect(fn, deps) { const index = r.cursor++; const slot = r.hooks[index]; if (!slot || !same(slot.deps, deps)) { r.hooks[index] = { deps, cleanup: slot?.cleanup }; r.effects.push(() => { slot?.cleanup?.(); r.hooks[index].cleanup = fn(); }); } }`;
      },
    }],
  });
  const { GifThumbnailPanel } = await vite.ssrLoadModule('/src/features/editor/GifThumbnailPanel.jsx');
  const wardrobe = { navy: [
    { id: 'flat', src: '/assets/flat.jpg', cutType: 'horizon' },
    { id: 'style', src: '/assets/style.jpg', cutType: 'styling' },
    ...Array.from({ length: 8 }, (_, i) => ({ id: `photo${i}`, src: `/assets/photo${i}.jpg` })),
    { id: 'loading', loading: true },
  ] };
  const props = { wardrobe, onSaveGif: async (result) => { runtime.saved.push(result); }, detailColorOpts: [{ id: 'navy', label: '네이비' }] };
  return { runtime, props,
    render() {
      let tree;
      for (let i = 0; i < 12; i += 1) {
        runtime.cursor = 0; runtime.dirty = false; runtime.effects = [];
        tree = GifThumbnailPanel(props);
        runtime.effects.forEach((fn) => fn());
        if (!runtime.dirty) return tree;
      }
      assert.fail('패널이 무한 재렌더링된다');
    },
    async close() { runtime.hooks.forEach((slot) => slot?.cleanup?.()); await vite.close(); delete globalThis[key]; },
  };
}

function nodes(tree, predicate) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap((node) => nodes(node, predicate));
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)];
}
function textOf(tree) {
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree);
  if (!tree || typeof tree !== 'object') return '';
  if (Array.isArray(tree)) return tree.map(textOf).join('');
  return textOf(tree.props?.children);
}
const buttonNamed = (tree, label) => nodes(tree, (node) => node.type === 'button' && textOf(node) === label)[0];
const photoButtons = (tree) => nodes(tree, (node) => node.type === 'button' && node.props['aria-pressed'] !== undefined && nodes(node, (child) => child.type === 'img').length);


test('패널은 8장 한도를 지키고 실패·용량 경고·설정 변경을 처리한다', async () => {
  const harness = await panelHarness();
  try {
    buttonNamed(harness.render(), 'GIF 생성하기').props.onClick();
    let tree;
    for (let i = 0; i < 8; i += 1) { tree = harness.render(); photoButtons(tree)[i].props.onClick(); }
    tree = harness.render();
    assert.equal(photoButtons(tree)[8].props.disabled, true);
    harness.runtime.encodeGif = async () => { throw new Error('사진을 읽지 못했어요'); };
    await buttonNamed(tree, 'GIF 만들기').props.onClick();
    tree = harness.render();
    assert.equal(textOf(nodes(tree, (node) => node.props.role === 'alert')[0]), '사진을 읽지 못했어요');
    assert.equal(buttonNamed(tree, 'GIF 만들기').props.disabled, false);
    harness.runtime.encodeGif = async () => ({ ...harness.runtime.result, bytes: GIF_HARD_BYTES + 1 });
    await buttonNamed(tree, 'GIF 만들기').props.onClick();
    tree = harness.render();
    assert.ok(textOf(tree).includes('용량이 커서 담지 못했어요. 사진 수를 줄여 주세요'));
    assert.equal(harness.runtime.saved.length, 0);
    photoButtons(tree)[0].props.onClick();
    tree = harness.render();
    assert.equal(nodes(tree, (node) => node.type === 'a' && node.props.download).length, 0);
  } finally { await harness.close(); }
});


function photoPointerArea(h) {
  const area = nodes(h.render(), n => n.props['aria-label'] === '보관함 사진')[0];
  const tiles = photoButtons(h.render()).map(button => {
    const tile = { dataset: { gifPhotoId: button.props['data-gif-photo-id'] },
      closest: () => tile, setPointerCapture() {}, hasPointerCapture: () => true, releasePointerCapture() {} };
    return tile;
  });
  const container = { contains: tile => tiles.includes(tile),
    ownerDocument: { elementFromPoint: x => tiles[Math.floor(x / 100)] || null },
    setPointerCapture() {}, hasPointerCapture: () => true, releasePointerCapture() {} };
  return { area, event: (index, extra = {}) => ({ pointerId: 1, pointerType: 'mouse', button: 0, buttons: 1,
    clientX: index * 100 + 10, clientY: 10, currentTarget: container, target: tiles[index] || container,
    preventDefault() {}, ...extra }) };
}

test('사진 영역에서 드래그하면 지나간 세 타일을 순서대로 선택하고 뒤따르는 클릭은 무시한다', async () => {
  const h = await panelHarness();
  try {
    buttonNamed(h.render(), 'GIF 생성하기').props.onClick();
    const { area, event } = photoPointerArea(h);
    assert.equal(typeof area.props.onPointerDown, 'function');
    area.props.onPointerDown(event(-1));
    for (const index of [2, 0, 1, 2]) area.props.onPointerMove(event(index));
    area.props.onPointerUp(event(2, { buttons: 0 }));
    let prevented = false, stopped = false;
    area.props.onClickCapture({ detail: 1, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
    assert.ok(prevented && stopped);
    const photos = photoButtons(h.render());
    assert.match(photos[2].props['aria-label'], /선택 순서 1/);
    assert.match(photos[0].props['aria-label'], /선택 순서 2/);
    assert.match(photos[1].props['aria-label'], /선택 순서 3/);
    await buttonNamed(h.render(), 'GIF 만들기').props.onClick();
    assert.deepEqual(h.runtime.encodeCalls[0].sources, ['/assets/photo0.jpg', '/assets/style.jpg', '/assets/flat.jpg']);
  } finally { await h.close(); }
});

test('타일에서 시작한 드래그는 기존 선택을 보존하고 8장까지만 추가하며 취소 후 멈춘다', async () => {
  const h = await panelHarness();
  try {
    buttonNamed(h.render(), 'GIF 생성하기').props.onClick();
    photoButtons(h.render())[1].props.onClick();
    const { area, event } = photoPointerArea(h);
    assert.equal(typeof area.props.onPointerDown, 'function');
    area.props.onPointerDown(event(0));
    for (let i = 1; i < 10; i++) area.props.onPointerMove(event(i));
    assert.equal(photoButtons(h.render()).filter(n => n.props['aria-pressed']).length, 8);
    assert.match(photoButtons(h.render())[1].props['aria-label'], /선택 순서 1/);
    assert.match(photoButtons(h.render())[0].props['aria-label'], /선택 순서 2/);
    area.props.onPointerCancel(event(9));
    photoButtons(h.render())[0].props.onClick();
    area.props.onPointerMove(event(8));
    assert.equal(photoButtons(h.render()).filter(n => n.props['aria-pressed']).length, 7);
    assert.equal(photoButtons(h.render())[8].props['aria-pressed'], false);
  } finally { await h.close(); }
});

test('움직이지 않은 마우스 클릭은 선택과 해제를 유지하고 캡처 해제 뒤 드래그는 멈춘다', async () => {
  const h = await panelHarness();
  try {
    buttonNamed(h.render(), 'GIF 생성하기').props.onClick();
    const { area, event } = photoPointerArea(h);
    assert.equal(typeof area.props.onPointerDown, 'function');
    for (const selected of [true, false]) {
      area.props.onPointerDown(event(0));
      area.props.onPointerUp(event(0, { buttons: 0 }));
      area.props.onClickCapture({ detail: 1, preventDefault() { assert.fail('클릭은 유지한다'); } });
      photoButtons(h.render())[0].props.onClick();
      assert.equal(photoButtons(h.render())[0].props['aria-pressed'], selected);
    }
    area.props.onPointerDown(event(0));
    area.props.onLostPointerCapture(event(0));
    area.props.onPointerMove(event(1));
    assert.equal(photoButtons(h.render()).filter(n => n.props['aria-pressed']).length, 0);
  } finally { await h.close(); }
});

test('GIF 제작은 의류 안에서 열고 기존 스타일 규칙을 지킨다', () => {
  const editor = source('src/features/editor/Editor.jsx');
  const tools = editor.slice(editor.indexOf('const TOOLS = ['), editor.indexOf('const panelTitle ='));
  assert.doesNotMatch(tools, /id: 'gif'/);
  const panels = source('src/features/editor/EditorPanels.jsx');
  assert.match(panels, /UnderlineTabs/);
  assert.match(panels, /GifThumbnailPanel/);
  const panel = source('src/features/editor/GifThumbnailPanel.jsx');
  const css = source('src/features/editor/GifThumbnailPanel.module.css');
  assert.doesNotMatch(panel, /style=|—/);
  assert.doesNotMatch(css, /word-break|#[0-9a-fA-F]{3,8}\b/);
  assert.match(css, /text-wrap: balance/);
  assert.match(panel, /modal-actions/);
});

test('실제 워커 메시지 경로도 모듈에서 인코딩하고 전송한 RGBA 버퍼를 분리한다', async () => {
  const { Worker: NodeWorker } = await import('node:worker_threads');
  const { encodeWithWorker } = await import('../../src/lib/gif/encodeGif.js');
  const workerUrl = new URL('../../src/lib/gif/gifEncodeWorker.js', import.meta.url).href;
  const native = new NodeWorker(`
    import { parentPort } from 'node:worker_threads';
    globalThis.WorkerGlobalScope = class { static [Symbol.hasInstance](value) { return value === globalThis; } };
    globalThis.postMessage = (data, transfer) => parentPort.postMessage(data, transfer);
    await import(${JSON.stringify(workerUrl)});
    parentPort.on('message', data => globalThis.onmessage({ data }));
  `, { eval: true, type: 'module' });
  const rgbaFrames = [];
  const adapter = {
    postMessage: (...args) => native.postMessage(...args),
    terminate() { this.stopped = native.terminate(); },
  };
  native.on('message', (data) => adapter.onmessage({ data }));
  native.on('error', (error) => adapter.onerror(error));
  try {
    const bytes = await encodeWithWorker({ width: 3, height: 2, frames: 2, delayMs: 1500, colors: 256,
      readFrame: async (i) => {
        const rgba = new Uint8ClampedArray(Array.from({ length: 6 }, () => i ? [240, 80, 60, 255] : [30, 60, 240, 255]).flat());
        rgbaFrames.push(rgba);
        return rgba;
      },
    }, () => adapter);
    assert.equal(parseGif(bytes).frames.length, 2);
    assert.ok(rgbaFrames.every((rgba) => rgba.buffer.byteLength === 0), '전송한 프레임을 메인 스레드에 중복 보관하지 않는다');
  } finally { await (adapter.stopped || native.terminate()); }
});

test('축소한 최종 GIF와 첫 컷 JPG는 같은 크기와 선택 선두 사진을 쓴다', async () => {
  const { encodeGif } = await import('../../src/lib/gif/encodeGif.js');
  const original = { Image: globalThis.Image, document: globalThis.document, Worker: globalThis.Worker };
  const settings = [], jpgs = [];
  let lastDrawn;
  class ImageFixture extends EventTarget {
    naturalWidth = 1000;
    naturalHeight = 2400;
    set src(value) { this.url = value; queueMicrotask(() => this.dispatchEvent(new Event('load'))); }
  }
  const canvas = { width: 0, height: 0,
    getContext: () => ({ fillRect() {}, drawImage(image) { lastDrawn = image.url; }, getImageData: () => ({ data: new Uint8ClampedArray(canvas.width * canvas.height * 4) }) }),
    toBlob(callback, mime, quality) { jpgs.push({ mime, quality }); callback(new Blob([`${canvas.width}×${canvas.height}:${lastDrawn}`], { type: mime })); },
  };
  globalThis.Image = ImageFixture;
  globalThis.document = { createElement: () => canvas };
  globalThis.Worker = class {
    constructor(url, options) { assert.ok(url.href.endsWith('/gifEncodeWorker.js')); assert.equal(options.type, 'module'); }
    postMessage(data) {
      if (data.type === 'start') settings.push(data.settings);
      const response = data.type === 'finish'
        ? { type: 'result', buffer: new ArrayBuffer(settings.length <= 2 ? GIF_HARD_BYTES + 1 : 1000) }
        : { type: 'ready' };
      queueMicrotask(() => this.onmessage({ data: response }));
    }
    terminate() {}
  };
  try {
    const progress = [];
    const result = await encodeGif({ sources: ['/assets/first.jpg', '/assets/second.jpg'], preset: 'cafe24', onProgress: (value) => progress.push(value) });
    assert.equal(result.stepIndex, 2);
    assert.equal(result.colors, 256);
    assert.equal(result.width, 640);
    assert.equal(result.height, 854);
    assert.equal(result.bytes, 1000);
    assert.equal(result.blob.type, 'image/gif');
    assert.equal(await result.jpgBlob.text(), '640×854:/assets/first.jpg');
    assert.ok(jpgs.every(({ mime, quality }) => mime === 'image/jpeg' && quality === 0.92));
    assert.equal(progress.at(-1), 100);
    assert.ok(progress.every((value, index) => index === 0 || value >= progress[index - 1]));
    assert.equal(canvas.width, 0);
    assert.equal(canvas.height, 0);
  } finally { for (const [key, value] of Object.entries(original)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } }
});

test('원본 사진이 바뀌면 진행 중 인코딩을 취소하고 늦은 결과를 미리보기에 쓰지 않는다', async () => {
  const harness = await panelHarness();
  try {
    buttonNamed(harness.render(), 'GIF 생성하기').props.onClick();
    let tree = harness.render();
    photoButtons(tree)[0].props.onClick();
    tree = harness.render();
    photoButtons(tree)[1].props.onClick();
    tree = harness.render();
    let resolve;
    harness.runtime.encodeGif = (options) => {
      harness.runtime.encodeCalls.push(options);
      options.onProgress(42);
      return new Promise((finish) => { resolve = finish; });
    };
    const building = buttonNamed(tree, 'GIF 만들기').props.onClick();
    tree = harness.render();
    assert.equal(buttonNamed(tree, '만드는 중 42%').props.disabled, true);
    assert.ok(photoButtons(tree).every((node) => node.props.disabled));
    assert.ok(nodes(tree, (node) => node.type === 'fieldset').every((node) => node.props.disabled));
    harness.props.wardrobe = { navy: harness.props.wardrobe.navy.map((photo) => photo.id === 'style' ? { ...photo, src: '/assets/new-style.jpg' } : photo) };
    tree = harness.render();
    assert.equal(harness.runtime.encodeCalls[0].signal.aborted, true);
    resolve(harness.runtime.result);
    await building;
    tree = harness.render();
    assert.equal(nodes(tree, (node) => node.type === 'a' && node.props.download).length, 0);
    assert.equal(buttonNamed(tree, 'GIF 만들기').props.disabled, false);
    harness.props.wardrobe = { navy: [] };
    tree = harness.render();
    assert.equal(buttonNamed(tree, 'GIF 만들기').props.disabled, true);
  } finally { await harness.close(); }
});



test('소프트 미달이면 첫 하드 통과본의 GIF·JPG·설정을 보존하고 128색을 시도하지 않는다', async () => {
  const { runLadder } = await import('../../src/lib/gif/encodeGif.js');
  for (const mode of ['photo', 'attachment']) {
    const first = { blob: new Blob([new Uint8Array(2_000_000)]), jpgBlob: new Blob(['first JPG']), width: 800, height: 1067, frames: mode === 'photo' ? 6 : 30, delayMs: mode === 'photo' ? 1500 : 100 };
    const calls = [];
    const result = await runLadder(mode, async (step, index) => {
      calls.push({ index, ...step });
      return index === 0 ? first : { ...first, blob: new Blob([new Uint8Array(1_800_000)]), jpgBlob: new Blob(['second JPG']), frames: 24, delayMs: 125 };
    });
    assert.deepEqual(calls.map(({ index }) => index), [0, 1]);
    assert.ok(calls.every(({ colors, scale }) => colors > 128 && scale === 1));
    assert.equal(result.stepIndex, 0);
    assert.equal(result.blob, first.blob);
    assert.equal(result.jpgBlob, first.jpgBlob);
    assert.equal(result.bytes, 2_000_000);
    assert.equal(result.width, first.width);
    assert.equal(result.height, first.height);
    assert.equal(result.frames, first.frames);
    assert.equal(result.delayMs, first.delayMs);
    assert.equal(result.colors, 256);
    assert.equal(result.scale, 1);
  }
});

test('색 보정본이 상한을 넘거나 두 번째 단계가 첫 통과본이어도 하드 통과 결과를 보존한다', async () => {
  const { runLadder } = await import('../../src/lib/gif/encodeGif.js');
  for (const [sizes, winner] of [[[5_000_000, 5_200_000], 0], [[5_200_000, 2_000_000], 1]]) {
    const calls = [];
    const result = await runLadder('photo', async (_step, index) => {
      calls.push(index);
      return { blob: new Blob([new Uint8Array(sizes[index])]) };
    });
    assert.deepEqual(calls, [0, 1]);
    assert.equal(result.stepIndex, winner);
    assert.equal(result.bytes, sizes[winner]);
    assert.equal(result.colors, winner ? 192 : 256);
  }
});



test('사진 타일은 240px 썸네일을 쓰고 GIF 인코더에는 원본 사진을 보낸다', async () => {
  const harness = await panelHarness();
  buttonNamed(harness.render(), 'GIF 생성하기').props.onClick();
  const original = 'https://cdn.example.com/styling.jpg';
  harness.props.wardrobe = { navy: harness.props.wardrobe.navy.map((photo) => photo.id === 'style' ? { ...photo, src: original } : photo) };
  try {
    let tree = harness.render();
    const image = nodes(photoButtons(tree)[0], (node) => node.type === 'img')[0];
    assert.equal(image.props.src, `https://images.test/width=240/${original}`);
    assert.ok(harness.runtime.thumbnails.some(({ src, width }) => src === original && width === 240));
    photoButtons(tree)[0].props.onClick();
    tree = harness.render();
    photoButtons(tree)[1].props.onClick();
    tree = harness.render();
    await buttonNamed(tree, 'GIF 만들기').props.onClick();
    assert.deepEqual(harness.runtime.encodeCalls[0].sources, [original, '/assets/flat.jpg']);
  } finally { await harness.close(); }
});


test('원본 규격은 첫 재료 비율의 짝수 높이와 1200 상한을 지킨다', () => {
  assert.deepEqual(presetSize('original', 1, { width: 800, height: 1200 }), { width: 640, height: 960 });
  assert.deepEqual(presetSize('original', 1, { width: 640, height: 901 }), { width: 640, height: 902 });
  assert.deepEqual(presetSize('original', 1, { width: 100, height: 1000 }), { width: 640, height: 1200 });
  assert.deepEqual(presetSize('original', 0.8, { width: 800, height: 1200 }), { width: 512, height: 768 });
  assert.throws(() => presetSize('original', 1, { width: 0, height: 10 }));
  assert.deepEqual(coverCrop(1000, 2000, 640, 768, { anchorY: 0.08 }),
    { sx: 0, sy: 64, sw: 1000, sh: 1200, width: 640, height: 768 });
  assert.equal(coverCrop(2400, 1000, 800, 800, { anchorY: 0.08 }).sx, 700);
});



async function gifApiHarness() {
  const { createServer } = await import('vite');
  const vite = await createServer({ configFile: false, root: root.pathname, logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': `${root.pathname}src` } },
  });
  const { mockAdapter } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js');
  return { api: mockAdapter, close: () => vite.close() };
}
const gifResult = () => ({ blob: new Blob(['GIF89a-original'], { type: 'image/gif' }),
  jpgBlob: new Blob(['first-frame'], { type: 'image/jpeg' }), width: 640, height: 960, frames: 4, delayMs: 1500 });

test('목 저장은 GIF와 JPG 원본 blob을 한 항목으로 보관하고 프로젝트별로 조회와 삭제한다', async () => {
  const h = await gifApiHarness();
  try {
    assert.equal(typeof h.api.saveWardrobeGif, 'function');
    const result = gifResult();
    const preview = URL.createObjectURL(result.blob);
    const image = await h.api.saveWardrobeGif('gif-project', result);
    URL.revokeObjectURL(preview);
    assert.equal(image.kind, 'gif');
    assert.notEqual(image.src, preview);
    assert.deepEqual(image.gifMeta, { width: 640, height: 960, frames: 4, delayMs: 1500, bytes: 15 });
    assert.equal(await (await fetch(image.src)).text(), 'GIF89a-original');
    assert.equal(await (await fetch(image.posterSrc)).text(), 'first-frame');
    assert.ok((await h.api.getWardrobe('gif-project')).misc.some(item => item.id === image.id));
    assert.ok(!(await h.api.getWardrobe('another-project')).misc.some(item => item.id === image.id));
    await h.api.deleteWardrobeGif('gif-project', image.id);
    assert.ok(!(await h.api.getWardrobe('gif-project')).misc.some(item => item.id === image.id));
  } finally { await h.close(); }
});

test('GIF 저장은 정확히 5MB인 원본을 허용한다', async () => {
  const { uploadWardrobeGif } = await import('../../src/lib/api/gifWardrobe.js');
  const image = await uploadWardrobeGif('p', { ...gifResult(), blob: new Blob([new Uint8Array(5_000_000)], { type: 'image/gif' }) },
    async (_id, photo) => ({ assetId: photo.filename, url: '/' + photo.filename }));
  assert.equal(image.gifMeta.bytes, 5_000_000);
  assert.equal(image.src, '/thumbnail.gif');
  assert.equal(image.posterSrc, '/thumbnail.jpg');
});

test('GIF 저장은 5MB 초과와 poster 업로드 실패 시 항목을 만들지 않는다', async () => {
  const { uploadWardrobeGif } = await import('../../src/lib/api/gifWardrobe.js');
  let uploads = 0;
  await assert.rejects(uploadWardrobeGif('p', { ...gifResult(), blob: new Blob([new Uint8Array(5_000_001)], { type: 'image/gif' }) },
    async () => { uploads++; }), /5MB/);
  assert.equal(uploads, 0);
  await assert.rejects(uploadWardrobeGif('p', gifResult(), async (_id, photo) => {
    uploads++; if (photo.mime === 'image/jpeg') throw new Error('poster failed');
    return { assetId: 'g', url: '/g.gif' };
  }), /poster failed/);
  assert.equal(uploads, 2);
});

test('GIF 메타데이터는 블록과 프레임에 남고 일반 사진 교체와 비우기에서는 제거된다', async () => {
  const { buildImageBlock, encodeWardrobeImage, decodeWardrobeImage } = await import('../../src/features/editor/editorLibrary.js');
  const { applySlotFillToInfo, carrySlotImages } = await import('../../src/features/editor/presets/infoPresets.js');
  const { replaceCanvasAiImage } = await import('../../src/features/editor/canvasAiApply.js');
  const { removeSelectedElements: deleteSelection } = await import('../../src/features/editor/editorSelection.js');
  const { mergeEditorImagesIntoWardrobe, isWardrobeImageUsed } = await import('../../src/features/editor/editorWardrobe.js');
  const image = { id: 'g', src: '/g.gif', posterSrc: '/g.jpg', kind: 'gif', gifMeta: { width: 640, height: 960, frames: 4, delayMs: 1500, bytes: 100 }, width: 640, height: 960, userUploaded: true, wardrobeGroup: 'misc' };
  let id = 0;
  const block = buildImageBlock(image, () => String(++id));
  const element = block.elements[0];
  assert.equal(element.posterSrc, '/g.jpg');
  assert.deepEqual(element.gifMeta, image.gifMeta);
  assert.equal(decodeWardrobeImage(encodeWardrobeImage(image)).kind, 'gif');
  const slotBlock = { ...block, elements: [{ ...element, frameSlot: true }] };
  const filled = applySlotFillToInfo(slotBlock, element.id, image);
  assert.equal(filled.elements[0].kind, 'gif');
  const replaced = applySlotFillToInfo(filled, element.id, { src: '/photo.jpg' });
  assert.ok(!replaced.elements[0].posterSrc && !replaced.elements[0].gifMeta && replaced.elements[0].kind !== 'gif');
  const removed = deleteSelection([filled], [element.id]);
  assert.ok(!removed[0].elements[0].gifMeta);
  const swapped = replaceCanvasAiImage([filled], { blockId: block.id, elementId: element.id, sourceSrc: '/g.gif' }, { src: '/normal.jpg' });
  assert.ok(!swapped[0].elements[0].gifMeta);
  const carried = carrySlotImages(filled.elements, { ...block, elements: [{ ...element, src: '/g.gif', posterSrc: undefined, gifMeta: undefined, kind: undefined }] });
  assert.equal(carried.elements[0].posterSrc, '/g.jpg');
  const restored = mergeEditorImagesIntoWardrobe({ blocks: [filled] }).misc[0];
  assert.equal(restored.kind, 'gif');
  assert.equal(restored.width, 640);
  assert.equal(isWardrobeImageUsed([{ elements: [{ type: 'image', src: '/g.jpg' }] }], image), true);
});

test('GIF 정지 전환은 원본 요소를 건드리지 않고 AI 메뉴에서 GIF를 제외한다', async () => {
  const { gifStillPatch, collectGifImages } = await import('../../src/lib/gif/gifMedia.js');
  const { buildCanvasAiMenu } = await import('../../src/features/editor/canvasAiActions.js');
  const element = { id: 'g', type: 'image', kind: 'gif', src: '/g.gif', posterSrc: '/g.jpg', gifMeta: { frames: 4 } };
  const still = { ...element, ...gifStillPatch(element) };
  assert.equal(still.src, '/g.jpg'); assert.ok(!still.gifMeta); assert.notEqual(still.kind, 'gif');
  assert.equal(element.src, '/g.gif');
  assert.equal(buildCanvasAiMenu({ element }).kind, 'unsupported');
  assert.deepEqual(collectGifImages([{ elements: [element, { ...element, id: 'copy' }, { ...element, src: '/hidden.gif', hidden: true }, still] }]).map(i => i.src), ['/g.gif']);
});

test('내보내기 복제본만 poster로 바꾸고 ZIP에는 중복 없는 GIF 원본 바이트를 담는다', async () => {
  const { replaceGifPosters, appendGifFiles } = await import('../../src/features/editor/editorExport.js');
  const attrs = new Map([['src', '/g.gif'], ['data-gif-poster', '/first.jpg'], ['srcset', '/g-thumb.jpg']]);
  const img = { getAttribute: k => attrs.get(k), setAttribute: (k,v) => attrs.set(k,v), removeAttribute: k => attrs.delete(k) };
  replaceGifPosters({ querySelectorAll: () => [img] });
  assert.equal(attrs.get('src'), '/first.jpg'); assert.equal(attrs.has('srcset'), false);
  const JSZip = (await import('jszip')).default;
  const zip = new JSZip(); zip.file('detail_01.jpg', 'signed-jpg');
  const url = URL.createObjectURL(gifResult().blob);
  try {
    await appendGifFiles(zip, [{ src: url }, { src: url }]);
    const loaded = await JSZip.loadAsync(await zip.generateAsync({ type: 'uint8array' }));
    assert.deepEqual(Object.keys(loaded.files), ['detail_01.jpg', 'gif_01.gif']);
    assert.equal(await loaded.file('gif_01.gif').async('string'), 'GIF89a-original');
    assert.equal(await loaded.file('detail_01.jpg').async('string'), 'signed-jpg');
  } finally { URL.revokeObjectURL(url); }
});


test('정지 이미지 ZIP만 공증한 뒤 GIF를 추가하며 서명된 정지 바이트를 보존한다', async () => {
  const { packageStorefrontZip } = await import('../../src/features/editor/editorExport.js');
  const JSZip = (await import('jszip')).default;
  const gifUrl = URL.createObjectURL(gifResult().blob);
  try {
    assert.equal(typeof packageStorefrontZip, 'function');
    const result = await packageStorefrontZip([new Blob(['image'])], 'jpeg', {
      provenance: { projectId: 'project' }, gifImages: [{ src: gifUrl }],
    }, async (blob, options) => {
      assert.deepEqual(options, { projectId: 'project', kind: 'zip' });
      const sent = await JSZip.loadAsync(await blob.arrayBuffer());
      assert.deepEqual(Object.keys(sent.files), ['detail_01.jpg']);
      sent.file('detail_01.jpg', 'signed-image');
      return { blob: new Blob([await sent.generateAsync({ type: 'uint8array' })]), verifyUrl: '/verify', warning: null };
    });
    const received = await JSZip.loadAsync(await result.blob.arrayBuffer());
    assert.equal(await received.file('detail_01.jpg').async('string'), 'signed-image');
    assert.equal(await received.file('gif_01.gif').async('string'), 'GIF89a-original');
    assert.equal(result.verifyUrl, '/verify');
  } finally { URL.revokeObjectURL(gifUrl); }
});

test('GIF를 정지 컷으로 바꾸면 정보 블록의 재편집 원본도 JPG로 바뀌고 크롭은 남는다', async () => {
  const { convertGifToStill } = await import('../../src/features/editor/presets/infoPresets.js');
  assert.equal(typeof convertGifToStill, 'function');
  const element = { id: 'slot', type: 'image', kind: 'gif', src: '/g.gif', posterSrc: '/g.jpg', gifMeta: { frames: 4 },
    userUploaded: true, wardrobeGroup: 'misc', frameSlot: true, crop: { ox: 10, oy: 20, iw: 300, ih: 400 } };
  const block = { id: 'b', kind: 'info', infoType: 'benefit_copy', info: { items: [{ src: '/g.gif' }] }, elements: [element] };
  const next = convertGifToStill(block, 'slot');
  assert.equal(next.info.items[0].src, '/g.jpg');
  assert.equal(next.elements[0].src, '/g.jpg');
  assert.deepEqual(next.elements[0].crop, element.crop);
  assert.equal(block.elements[0].src, '/g.gif', 'Undo가 쓸 이전 스냅샷을 보존한다');
});

test('로컬 GIF 목록은 재조회와 삭제를 보존하고 저장소 오류를 숨기지 않는다', async () => {
  const { readWardrobeGifs, writeWardrobeGifs } = await import('../../src/lib/api/gifWardrobe.js');
  const data = new Map(); const storage = { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) };
  const image = { id: 'g', kind: 'gif', src: '/g.gif', posterSrc: '/g.jpg', gifMeta: { frames: 2 } };
  writeWardrobeGifs('p1', [image], storage);
  assert.deepEqual(readWardrobeGifs('p1', storage), [image]);
  assert.deepEqual(readWardrobeGifs('p2', storage), []);
  writeWardrobeGifs('p1', [], storage);
  assert.deepEqual(readWardrobeGifs('p1', storage), []);
  assert.throws(() => writeWardrobeGifs('p1', [image], { setItem() { throw new Error('quota'); } }), /저장/);
});


test('GIF 선택은 AI 자산 조회와 생성 요청을 실행하지 않는다', async () => {
  const { canvasAiHookHarness } = await import('./helpers/editorCanvasAiHarness.mjs');
  const calls = [];
  const element = { id: 'g', type: 'image', kind: 'gif', src: '/g.gif', posterSrc: '/g.jpg', gifMeta: { frames: 4 } };
  const h = canvasAiHookHarness({ blocks: [{ id: 'b', elements: [element] }], selectedId: 'g', api: {
    getEditorAssetInfo: async () => { calls.push('info'); return { items: {} }; },
    generateImage: async () => { calls.push('generate'); },
  } });
  assert.equal(h.hook.menu.kind, 'unsupported');
  assert.equal(h.start('similar'), null);
  assert.deepEqual(calls, []);
});

test('의류 밑줄 탭은 이미지 삽입과 GIF 선택 모드를 분리하고 인스펙터는 전환 버튼을 연결한다', async () => {
  const React = (await import('react')).default;
  const { createServer } = await import('vite');
  const vite = await createServer({ configFile: false, root: root.pathname, logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] }, esbuild: { jsx: 'automatic' },
    resolve: { alias: { '@': `${root.pathname}src` } },
  });
  const dispatcher = React.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentDispatcher;
  const previous = dispatcher.current;
  const slots = []; let cursor = 0;
  dispatcher.current = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useMemo: fn => fn(),
  };
  const render = (component, props) => { cursor = 0; return component(props); };
  try {
    const { WardrobePanel, ImagePanel } = await vite.ssrLoadModule('/src/features/editor/EditorPanels.jsx');
    const gif = { id: 'g', type: 'image', kind: 'gif', src: '/g.gif', posterSrc: '/g.jpg', gifMeta: { width: 640, height: 960, frames: 4, bytes: 400000 }, w: 200, h: 300 };
    const onInsert = () => {};
    const props = { wardrobe: { misc: [{ id: 'p', src: '/photo.jpg' }, gif] }, onInsert };
    let tree = render(WardrobePanel, props);
    let tabs = nodes(tree, n => n.type?.name === 'UnderlineTabs')[0];
    assert.equal(tabs.props.value, 'image');
    let library = nodes(tree, n => n.type?.name === 'WardrobeImagePanel')[0];
    assert.deepEqual(library.props.wardrobe.misc.map(i => i.id), ['p']);
    assert.equal(library.props.onInsert, onInsert);
    tabs.props.onChange('gif');
    tree = render(WardrobePanel, props);
    assert.equal(nodes(tree, n => n.type?.name === 'WardrobeImagePanel').length, 0);
    assert.equal(nodes(tree, n => n.type?.name === 'GifThumbnailPanel').length, 1);
    tabs = nodes(tree, n => n.type?.name === 'UnderlineTabs')[0];
    tabs.props.onChange('image');
    assert.equal(nodes(render(WardrobePanel, props), n => n.type?.name === 'WardrobeImagePanel').length, 1);
    const calls = [];
    tree = render(ImagePanel, { el: gif, onChange() {}, onLayer() {}, onCrop() {}, onDownloadGif: image => calls.push(['download', image.src]), onConvertGif: image => calls.push(['still', image.posterSrc]) });
    const buttons = nodes(tree, n => n.type?.name === 'Button');
    assert.ok(!buttons.some(n => ['GIF 받기', '정지 컷으로 바꾸기'].includes(textOf(n))));
    buttonNamed(tree, '첫 장을 정지 이미지로 사용').props.onClick();
    assert.deepEqual(calls, [['still', '/g.jpg']]);
    assert.ok(textOf(tree).includes('4장 · 0.4MB'));
    const details = nodes(tree, n => n.type === 'details')[0];
    assert.ok(details);
    assert.ok(!details.props.open);
    assert.ok(textOf(details).includes('캔버스 모양'));
    assert.ok(textOf(details).includes('모양을 바꿔도 GIF 원본은 그대로예요'));
    assert.equal(nodes(tree, n => n.type?.name === 'LayerRow').length, 1);
    tree = render(ImagePanel, { el: { ...gif, kind: null, gifMeta: null, src: '/g.jpg' }, onChange() {}, onLayer() {} });
    assert.ok(!textOf(tree).includes('정지 컷으로 바꾸기'));
  } finally { dispatcher.current = previous; await vite.close(); }
});


test('HTTP 의류 저장은 기존 presigned PUT과 complete를 두 번 거쳐 안정 URL만 보관한다', async () => {
  const { createServer } = await import('vite');
  const originalFetch = globalThis.fetch;
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const values = new Map();
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: key => values.get(key), setItem: (key, value) => values.set(key, value),
  } });
  const vite = await createServer({ configFile: false, root: root.pathname, logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] }, resolve: { alias: { '@': `${root.pathname}src` } },
    define: { 'import.meta.env.VITE_API_BASE_URL': '"https://api.test"' },
    plugins: [{ name: 'gif-http-session', enforce: 'pre',
      resolveId(id) { if (id === '@/lib/supabase.js' || id.endsWith('/src/lib/supabase.js')) return '\0gif-http-session'; },
      load(id) { if (id === '\0gif-http-session') return 'export const supabase = { auth: { getSession: async () => ({ data: { session: { access_token: "test-token", user: { id: "test-user" } } } }) } };'; },
    }],
  });
  try {
    const { httpAdapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js');
    const calls = []; let asset = 0; let failPoster = false;
    globalThis.fetch = async (url, options = {}) => {
      calls.push({ url, method: options.method || 'GET', body: options.body, headers: new Headers(options.headers) });
      if (String(url).endsWith('/upload-url')) {
        asset++;
        return Response.json({ assetId: `asset-${asset}`, uploadUrl: `https://storage.test/${asset}` });
      }
      if (String(url).startsWith('https://storage.test/')) {
        if (failPoster && options.headers['Content-Type'] === 'image/jpeg') return new Response('', { status: 503 });
        return new Response('');
      }
      if (String(url).endsWith('/complete')) return Response.json({ url: 'https://temporary.test/expired' });
      if (String(url).endsWith('/wardrobe')) return Response.json({ misc: [{ id: 'photo', src: '/photo.jpg' }] });
      assert.fail(`unexpected request: ${url}`);
    };
    const image = await httpAdapter.saveWardrobeGif('project', gifResult());
    assert.deepEqual(calls.map(c => c.method), ['POST', 'PUT', 'POST', 'POST', 'PUT', 'POST']);
    assert.deepEqual(calls.filter(c => c.method === 'PUT').map(c => c.headers.get('content-type')), ['image/gif', 'image/jpeg']);
    assert.equal(await calls[1].body.text(), 'GIF89a-original');
    assert.equal(await calls[4].body.text(), 'first-frame');
    assert.equal(JSON.parse(calls[0].body).projectId, 'project');
    assert.match(image.src, /api.test\/v1\/assets\/asset-1\/file/);
    assert.match(image.posterSrc, /api.test\/v1\/assets\/asset-2\/file/);
    const restored = await httpAdapter.getWardrobe('project');
    assert.equal(restored.misc[0].src, image.src);
    assert.equal(restored.misc[0].kind, 'gif');
    assert.deepEqual((await httpAdapter.getWardrobe('other')).misc.map(i => i.id), ['photo']);
    failPoster = true;
    await assert.rejects(httpAdapter.saveWardrobeGif('project', gifResult()));
    assert.equal((await httpAdapter.getWardrobe('project')).misc.filter(i => i.kind === 'gif').length, 1);
    await httpAdapter.deleteWardrobeGif('project', image.id);
    assert.deepEqual((await httpAdapter.getWardrobe('project')).misc.map(i => i.id), ['photo']);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage); else delete globalThis.localStorage;
    await vite.close();
  }
});

test('다운로드 창은 ZIP 포함 안내와 단일 이미지의 GIF 별도 받기를 구분한다', async () => {
  const React = (await import('react')).default;
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { createServer } = await import('vite');
  const vite = await createServer({ configFile: false, root: root.pathname, logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] }, esbuild: { jsx: 'automatic' },
    resolve: { alias: { '@': `${root.pathname}src` } },
  });
  try {
    const { EditorDownloadModal } = await vite.ssrLoadModule('/src/features/editor/EditorDownloadModal.jsx');
    const props = { settings: { delivery: 'split' }, gifCount: 2,
      summary: { width: 860, height: 2000, fileCount: 1, approxBytes: 1000 }, onDownloadGifs() {} };
    let html = renderToStaticMarkup(React.createElement(EditorDownloadModal, props));
    assert.match(html, /움직이는 GIF 2개는 별도 파일로 같이 담아요/);
    assert.doesNotMatch(html, /GIF 따로 받기/);
    html = renderToStaticMarkup(React.createElement(EditorDownloadModal, { ...props, settings: { delivery: 'single' } }));
    assert.match(html, /GIF 따로 받기/);
    html = renderToStaticMarkup(React.createElement(EditorDownloadModal, { ...props, gifCount: 0 }));
    assert.doesNotMatch(html, /움직이는 GIF/);
  } finally { await vite.close(); }
});


test('정보 카드에서 GIF를 새로 고르거나 앞 카드를 지워도 파일 주소로 GIF 정보를 복원한다', async () => {
  const { carrySlotImages, buildInfoBlock } = await import('../../src/features/editor/presets/infoPresets.js');
  const { isGifImage, collectGifImages } = await import('../../src/lib/gif/gifMedia.js');
  const image = { id: 'g', src: '/saved.gif', kind: 'gif', posterSrc: '/first.jpg', userUploaded: true, wardrobeGroup: 'misc',
    gifMeta: { width: 640, height: 960, frames: 4, delayMs: 1500, bytes: 100 } };
  const build = () => buildInfoBlock('model_info', { models: [{ name: '모델', height: 170, weight: 50, src: '/saved.gif' }] }, {});
  const newBlock = carrySlotImages([], build(), [image]);
  const inserted = newBlock.elements.find(el => el.type === 'image');
  assert.equal(isGifImage(inserted), true);
  assert.equal(inserted.posterSrc, '/first.jpg');
  assert.equal(collectGifImages([newBlock]).length, 1);
  const reordered = carrySlotImages([
    { id: 'old1', type: 'image', src: '/plain.jpg' },
    { ...image, id: 'old2', type: 'image' },
  ], build());
  assert.equal(isGifImage(reordered.elements.find(el => el.type === 'image')), true);
  const changed = carrySlotImages([{ id: 'old', type: 'image', src: '/plain.jpg' }], build(), [image]);
  assert.equal(changed.elements.find(el => el.type === 'image').posterSrc, '/first.jpg');
});


test('GIF 생성하기는 선택 순서를 인코딩하고 자동 저장한 뒤 보관함으로 복귀한다', async () => {
  const h = await panelHarness();
  try {
    let tree = h.render();
    assert.equal(photoButtons(tree).length, 0);
    assert.ok(textOf(tree).includes('보관한 GIF'));
    assert.ok(buttonNamed(tree, '＋ GIF 첨부'));
    buttonNamed(tree, 'GIF 생성하기').props.onClick();
    tree = h.render();
    const photos = photoButtons(tree);
    assert.equal(nodes(photos[0], n => n.type === 'img')[0].props.src, '/assets/style.jpg');
    photos[1].props.onClick();
    photoButtons(h.render())[0].props.onClick();
    await buttonNamed(h.render(), 'GIF 만들기').props.onClick();
    assert.deepEqual(h.runtime.encodeCalls[0].sources, ['/assets/flat.jpg', '/assets/style.jpg']);
    assert.equal(h.runtime.saved.length, 1);
    assert.ok(buttonNamed(h.render(), 'GIF 생성하기'));
    assert.ok(!textOf(h.render()).includes('의류에 저장'));
    assert.equal(nodes(h.render(), n => n.props['aria-label'] === 'GIF 결과').length, 0);
  } finally { await h.close(); }
});

test('자동 저장 실패는 만들기에 남아 오류를 표시하며 중복 실행을 막는다', async () => {
  const h = await panelHarness();
  try {
    buttonNamed(h.render(), 'GIF 생성하기').props.onClick();
    photoButtons(h.render())[0].props.onClick();
    photoButtons(h.render())[1].props.onClick();
    let reject; let count = 0;
    h.props.onSaveGif = () => { count++; return new Promise((_, no) => { reject = no; }); };
    const click = buttonNamed(h.render(), 'GIF 만들기').props.onClick;
    const pending = click(); await new Promise(resolve => setImmediate(resolve));
    await click(); assert.equal(count, 1);
    reject(new Error('저장 실패')); await pending;
    assert.ok(buttonNamed(h.render(), 'GIF 만들기'));
    assert.ok(textOf(h.render()).includes('저장 실패'));
    h.props.onSaveGif = async () => { count++; };
    await buttonNamed(h.render(), 'GIF 만들기').props.onClick();
    assert.ok(buttonNamed(h.render(), 'GIF 생성하기'));
    assert.equal(count, 2);
  } finally { await h.close(); }
});

test('cropLossRatio는 비율 일치, 25% 경계, 긴 세로와 가로 사진을 계산한다', async () => {
  const { cropLossRatio } = await import('../../src/lib/gif/gifLadder.js');
  assert.equal(typeof cropLossRatio, 'function');
  assert.equal(cropLossRatio(100, 100, 800, 800), 0);
  assert.equal(cropLossRatio(100, 100, 300, 400), 0.25);
  assert.ok(cropLossRatio(100, 100, 299, 400) > 0.25);
  assert.equal(cropLossRatio(100, 200, 800, 800), 0.5);
  assert.equal(cropLossRatio(200, 100, 800, 800), 0.5);
  assert.throws(() => cropLossRatio(0, 100, 800, 800));
});

test('GIF 첨부 검사는 형식, 4000px, 25MB 순서로 실패하며 경계값은 받는다', async () => {
  const { validateGifAttachment } = await import('../../src/lib/gif/gifAttachment.js');
  assert.match(validateGifAttachment({ type: 'image/png', name: 'a.png', size: 30_000_000 }, 4001, 4001), /GIF 파일만/);
  assert.match(validateGifAttachment({ type: 'image/gif', size: 30_000_000 }, 4001, 100), /4,000px/);
  assert.match(validateGifAttachment({ type: 'image/gif', size: 25 * 1024 * 1024 + 1 }, 4000, 4000), /25MB/);
  assert.equal(validateGifAttachment({ type: '', name: 'A.GIF', size: 25 * 1024 * 1024 }, 4000, 4000), null);
});

test('gifuct 프레임은 dispose 2와 3을 합성하고 원본 delay로 4초까지 다시 인코딩한다', async () => {
  const { GIFEncoder } = (await import('gifenc')).default;
  const { inspectGifAttachment, gifFrameSchedule, composeGifFrames } = await import('../../src/lib/gif/gifAttachment.js');
  const { createFrameEncoder } = await import('../../src/lib/gif/gifEncodeWorker.js');
  const { runLadder } = await import('../../src/lib/gif/encodeGif.js');
  const encoder = GIFEncoder(); const palette = [[255,255,255], [255,0,0], [0,0,255]];
  encoder.writeFrame(new Uint8Array([1,1,1,1]), 2, 2, { palette, delay: 1500, dispose: 1 });
  encoder.writeFrame(new Uint8Array([2,0,0,0]), 2, 2, { palette, delay: 500, dispose: 3, transparent: true, transparentIndex: 0 });
  encoder.writeFrame(new Uint8Array([0,0,0,2]), 2, 2, { palette, delay: 1500, dispose: 2, transparent: true, transparentIndex: 0 });
  encoder.writeFrame(new Uint8Array([0,2,0,0]), 2, 2, { palette, delay: 1500, dispose: 1, transparent: true, transparentIndex: 0 });
  encoder.finish();
  const input = new Blob([encoder.bytes()], { type: 'image/gif' });
  const info = await inspectGifAttachment(input);
  assert.equal(info.frames, 4); assert.equal(info.delayMs, 1250);
  const schedule = gifFrameSchedule(info.parsed);
  assert.deepEqual(schedule.map(f => f.delayMs), [1500, 500, 1500, 500]);
  const frames = [...composeGifFrames(info.parsed, schedule)];
  assert.deepEqual([...frames[1].slice(0,4)], [0,0,255,255]);
  assert.deepEqual([...frames[2].slice(0,4)], [255,0,0,255], 'dispose 3 restores red');
  assert.deepEqual([...frames[3].slice(0,4)], [255,255,255,255], 'dispose 2 clears previous patch');
  const result = await runLadder('photo', async step => {
    const out = createFrameEncoder({ width: 2, height: 2, delayMs: 1500, colors: step.colors });
    frames.forEach((rgba, i) => out.addFrame(rgba, schedule[i].delayMs));
    return { blob: new Blob([out.finish()], { type: 'image/gif' }) };
  });
  assert.ok(result.bytes <= GIF_HARD_BYTES);
  assert.deepEqual(parseGif(new Uint8Array(await result.blob.arrayBuffer())).frames.map(f => f.delay), [150,50,150,50]);
});

test('블록 ZIP은 공증한 PNG와 GIF 원본을 담고 원본만 받기는 단일 GIF를 바로 받는다', async () => {
  const { packageBlockDownload, downloadBlockGifs } = await import('../../src/features/editor/editorExport.js');
  assert.equal(typeof packageBlockDownload, 'function');
  const JSZip = (await import('jszip')).default;
  const originalFetch = globalThis.fetch, originalDocument = globalThis.document;
  const downloads = [];
  const originalTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (...args) => { const timer = originalTimeout(...args); timer.unref(); return timer; };
  globalThis.fetch = async () => new Response(new Blob(['GIF89a-original'], { type: 'image/gif' }));
  globalThis.document = { body: { appendChild() {} }, createElement: () => ({ click() { downloads.push(this.download); }, remove() {} }) };
  try {
    const image = { src: '/a.gif' };
    const result = await packageBlockDownload(new Blob(['png']), 'block.png', { provenance: { projectId: 'p' }, gifImages: [image, image] }, async (blob, options) => {
      assert.equal(options.kind, 'block_png'); assert.equal(await blob.text(), 'png');
      return { blob: new Blob(['signed png']), verifyUrl: 'verify' };
    });
    const zip = await JSZip.loadAsync(await result.blob.arrayBuffer());
    assert.deepEqual(Object.keys(zip.files), ['block.png', 'gif_01.gif']);
    assert.equal(await zip.file('block.png').async('string'), 'signed png');
    assert.equal(await zip.file('gif_01.gif').async('string'), 'GIF89a-original');
    assert.equal(result.verifyUrl, 'verify');
    await downloadBlockGifs([image, image]);
    assert.deepEqual(downloads, ['thumbnail.gif']);
    await downloadBlockGifs([image, { src: '/b.gif' }]);
    assert.deepEqual(downloads, ['thumbnail.gif', 'gifs.zip']);
  } finally { globalThis.fetch = originalFetch; globalThis.document = originalDocument; globalThis.setTimeout = originalTimeout; }
});

test('확장자로 허용한 GIF도 저장용 blob은 image/gif이며 원본 바이트를 보존한다', async () => {
  const { GIFEncoder } = (await import('gifenc')).default;
  const { inspectGifAttachment } = await import('../../src/lib/gif/gifAttachment.js');
  const encoder = GIFEncoder();
  encoder.writeFrame(new Uint8Array([0]), 1, 1, { palette: [[0,0,0], [255,255,255]], delay: 200 }); encoder.finish();
  const file = new File([encoder.bytes()], 'from-browser.GIF', { type: 'application/octet-stream' });
  const info = await inspectGifAttachment(file);
  assert.equal(info.blob.type, 'image/gif');
  assert.deepEqual(new Uint8Array(await info.blob.arrayBuffer()), encoder.bytes());
});

test('점선 사진 타일은 빈 색상 묶음에도 있고 업로드를 그 색상으로 전달한다', async () => {
  const React = (await import('react')).default;
  const { createServer } = await import('vite');
  const vite = await createServer({ configFile: false, root: root.pathname, logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] },
    esbuild: { jsx: 'automatic' }, resolve: { alias: { '@': `${root.pathname}src` } } });
  const dispatcher = React.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentDispatcher;
  const previous = dispatcher.current;
  dispatcher.current = { useState: initial => [initial, () => {}], useMemo: fn => fn() };
  try {
    const { WardrobePanel } = await vite.ssrLoadModule('/src/features/editor/EditorPanels.jsx');
    const calls = [];
    const props = { wardrobe: {}, colorOpts: [{ id: 'navy', label: '네이비' }, { id: 'red', label: '빨강' }], uploading: { red: 2 }, onUpload: group => calls.push(group) };
    const library = nodes(WardrobePanel(props), n => n.type?.name === 'WardrobeImagePanel')[0];
    let tree = library.type(library.props);
    const groups = nodes(tree, n => n.props.className?.startsWith('wardrobe-group'));
    assert.equal(groups.length, 2);
    buttonNamed(groups[0], '＋ 사진 추가').props.onClick();
    assert.deepEqual(calls, ['navy']);
    assert.equal(nodes(groups[0], n => n.type?.name === 'UploadPendingTile').length, 0);
    assert.equal(nodes(groups[1], n => n.type?.name === 'UploadPendingTile').length, 2);
    tree = library.type({ ...props, colorOpts: [], uploading: {} });
    assert.equal(nodes(tree, n => n.props.className?.startsWith('wardrobe-group')).length, 1);
    assert.ok(textOf(tree).includes('기타'));
    buttonNamed(tree, '＋ 사진 추가').props.onClick();
    assert.deepEqual(calls, ['navy', 'misc']);
  } finally { dispatcher.current = previous; await vite.close(); }
});

test('한 장을 점선 타일로 올려도 해당 색상에만 저장하고 캔버스에 넣지 않는다', async () => {
  const editor = source('src/features/editor/Editor.jsx');
  const calls = []; let wardrobe = {}; let uploading = {};
  const handlers = editor.slice(editor.indexOf('  const uploadEditorImage ='), editor.indexOf('  // fresh ='));
  const bindings = {
    clientEvents: { getAccountGeneration: () => 1, bindGeneration() {} }, looksLikeImageFile: () => true,
    toUploadableImage: async f => f, getUploadValidationError: () => null, readImageDimensions: async () => ({ width: 300, height: 400 }),
    api: { uploadPhoto: async () => { assert.equal(uploading.navy, 1); return { assetId: 'new', url: '/new.jpg' }; } }, projectId: 'p', uid: () => 'new',
    patchImageImport() {}, finishImageImport() {}, waitForImageSource: async () => {},
    setWardrobe: fn => { wardrobe = fn(wardrobe); }, setWardrobeUploadLoading: fn => { uploading = fn(uploading); },
    wardrobeInsert: () => calls.push('insert'), insertImage: () => calls.push('insert'),
    pickEditorImageFile: async () => [new File(['x'], 'x.jpg', { type: 'image/jpeg' })],
    ...(await import('../../src/features/editor/editorUpload.js')), toast: { push() {} },
  };
  const pick = new Function(...Object.keys(bindings), `${handlers}; return pickAndInsertImage;`)(...Object.values(bindings));
  await pick('navy');
  assert.deepEqual(Object.keys(wardrobe), ['navy']);
  assert.equal(wardrobe.navy[0].wardrobeGroup, 'navy');
  assert.deepEqual(calls, []); assert.equal(uploading.navy, 0);
});

test('많이 잘리는 배지는 선택 사진의 25% 초과에만 표시하고 제작을 막지 않는다', async () => {
  const h = await panelHarness();
  try {
    h.props.wardrobe = { navy: [{ id: 'a', src: '/a.jpg', width: 100, height: 100 }, { id: 'b', src: '/b.jpg', width: 100, height: 300 }] };
    buttonNamed(h.render(), 'GIF 생성하기').props.onClick();
    buttonNamed(h.render(), '1:1').props.onClick();
    photoButtons(h.render())[0].props.onClick(); photoButtons(h.render())[1].props.onClick();
    assert.ok(!textOf(photoButtons(h.render())[0]).includes('많이 잘려요'));
    assert.ok(textOf(photoButtons(h.render())[1]).includes('많이 잘려요'));
    assert.equal(buttonNamed(h.render(), 'GIF 만들기').props.disabled, false);
  } finally { await h.close(); }
});

test('큰 GIF는 확인 뒤 선택한 축소 또는 원본 저장 경로만 실행한다', async () => {
  for (const shrink of [true, false]) {
    const h = await panelHarness();
    try {
      const calls = []; const info = { bytes: GIF_HARD_BYTES + 1 };
      h.runtime.attachment = {
        inspectGifAttachment: async () => info,
        prepareGifAttachment: async value => { assert.equal(value, info); calls.push('original'); return h.runtime.result; },
        reduceGifAttachment: async value => { assert.equal(value, info); calls.push('reduce'); return h.runtime.result; },
      };
      h.props.onSaveGif = async (_result, options) => calls.push(options.allowOversize);
      const input = nodes(h.render(), n => n.type === 'input')[0];
      assert.equal(input.props.accept, 'image/gif,.gif');
      await input.props.onChange({ target: { files: [new Blob()], value: 'file' } });
      assert.deepEqual(calls, []);
      assert.ok(textOf(h.render()).includes('5MB를 넘어요. 용량을 줄여서 담을까요?'));
      await buttonNamed(h.render(), shrink ? '줄여서 담기' : '그대로 담기').props.onClick();
      assert.deepEqual(calls, shrink ? ['reduce', false] : ['original', true]);
      assert.ok(!textOf(h.render()).includes('용량을 줄여서 담을까요'));
    } finally { await h.close(); }
  }
});

test('실제 GIF 축소 경로는 800px RGBA, 10fps와 4초 상한으로 디코드 후 워커 재인코딩한다', async () => {
  const { GIFEncoder } = (await import('gifenc')).default;
  const { inspectGifAttachment, reduceGifAttachment } = await import('../../src/lib/gif/gifAttachment.js');
  const { createFrameEncoder } = await import('../../src/lib/gif/gifEncodeWorker.js');
  const gif = GIFEncoder();
  for (let i = 0; i < 90; i++) gif.writeFrame(new Uint8Array(1000 * 4).fill(i % 2), 1000, 4, { palette: [[220,40,70],[40,80,180]], delay: 50 });
  gif.finish();
  const info = await inspectGifAttachment(new Blob([gif.bytes()], { type: 'image/gif' }));
  const originals = { Worker: globalThis.Worker, document: globalThis.document, ImageData: globalThis.ImageData };
  const settings = [], delays = []; let terminated = 0;
  globalThis.Worker = class {
    postMessage(data) {
      if (data.type === 'start') { settings.push(data.settings); this.encoder = createFrameEncoder(data.settings); }
      if (data.type === 'frame') { delays.push(data.delayMs); this.encoder.addFrame(new Uint8ClampedArray(data.buffer), data.delayMs); }
      const reply = data.type === 'finish' ? { type: 'result', buffer: this.encoder.finish().buffer } : { type: 'ready' };
      queueMicrotask(() => this.onmessage({ data: reply }));
    }
    terminate() { terminated++; }
  };
  globalThis.ImageData = class { constructor(rgba, width, height) { assert.equal(rgba.length, width * height * 4); } };
  globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => ({ putImageData() {} }),
    toBlob(callback, type) { callback(new Blob([`${this.width}x${this.height}`], { type })); } }) };
  try {
    const progress = [];
    const result = await reduceGifAttachment(info, { onProgress: v => progress.push(v) });
    assert.equal(result.width, 800); assert.equal(result.height, 3); assert.equal(result.frames, 40);
    assert.equal(result.delayMs, 100); assert.equal(await result.jpgBlob.text(), '800x3');
    assert.ok(result.bytes <= GIF_HARD_BYTES); assert.equal(result.bytes, result.blob.size);
    const decoded = parseGif(new Uint8Array(await result.blob.arrayBuffer()));
    assert.equal(decoded.frames.length, 40); assert.ok(decoded.frames.every(f => f.delay === 10));
    assert.ok(settings.every(s => s.width <= 800)); assert.ok(delays.every(d => d === 100));
    assert.equal(terminated, settings.length); assert.equal(progress.at(-1), 100);
  } finally { for (const [key, value] of Object.entries(originals)) if (value === undefined) delete globalThis[key]; else globalThis[key] = value; }
});

test('캔버스에서 복원한 색상 업로드 사진은 원래 색상과 사용자 업로드 표시를 유지한다', async () => {
  const { mergeEditorImagesIntoWardrobe } = await import('../../src/features/editor/editorWardrobe.js');
  const result = mergeEditorImagesIntoWardrobe({ colorIds: ['navy'], blocks: [{ elements: [
    { id: 'photo', type: 'image', src: '/portrait.jpg', w: 400, h: 400, userUploaded: true, wardrobeGroup: 'navy' },
  ] }] });
  assert.equal(result.navy?.[0]?.wardrobeGroup, 'navy');
  assert.equal(result.navy[0].userUploaded, true);
  assert.ok(!result.navy[0].generated);
});

test('사진이 로드되면 캔버스에서 온 잘못된 치수 대신 실제 사진 비율로 잘림을 경고한다', async () => {
  const h = await panelHarness();
  try {
    h.props.wardrobe = { navy: [{ id: 'a', src: '/a.jpg', width: 400, height: 400 }, { id: 'b', src: '/b.jpg', width: 100, height: 100 }] };
    buttonNamed(h.render(), 'GIF 생성하기').props.onClick(); buttonNamed(h.render(), '1:1').props.onClick();
    photoButtons(h.render())[0].props.onClick(); photoButtons(h.render())[1].props.onClick();
    const image = nodes(photoButtons(h.render())[0], n => n.type === 'img')[0];
    image.props.onLoad({ currentTarget: { naturalWidth: 240, naturalHeight: 480 } });
    assert.ok(textOf(photoButtons(h.render())[0]).includes('많이 잘려요'));
  } finally { await h.close(); }
});

test('GIF 블록 다운로드 아이콘은 기존 아이콘 모양의 메뉴에서 ZIP과 원본 동작을 구분한다', async () => {
  const { createServer } = await import('vite');
  const vite = await createServer({ configFile: false, root: root.pathname, logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] },
    esbuild: { jsx: 'automatic' }, resolve: { alias: { '@': `${root.pathname}src` } } });
  try {
    const { BlockDownloadButton } = await vite.ssrLoadModule('/src/features/editor/BlockDownloadButton.jsx');
    const calls = []; const onDownload = (...args) => calls.push(args);
    const plain = { elements: [] };
    const button = BlockDownloadButton({ block: plain, index: 1, onDownload });
    assert.equal(button.type.name, 'IconButton'); button.props.onClick();
    const gif = { id: 'g', type: 'image', kind: 'gif', src: '/a.gif', posterSrc: '/a.jpg', gifMeta: {} };
    const block = { elements: [gif] };
    const menu = BlockDownloadButton({ block, index: 2, onDownload });
    assert.equal(nodes(menu, n => n.type === 'summary')[0].props.className, 'iconbtn');
    const details = { open: true };
    const event = { currentTarget: { closest: () => details } };
    buttonNamed(menu, '블록 이미지와 GIF 받기 (ZIP)').props.onClick(event);
    assert.equal(details.open, false);
    buttonNamed(menu, 'GIF 원본만 받기').props.onClick(event);
    assert.deepEqual(calls, [[plain, 1], [block, 2, 'zip'], [block, 2, 'gifs']]);
  } finally { await vite.close(); }
});

test('첨부 사다리는 색상·fps·폭·길이 순서로 줄인다', () => {
  assert.deepEqual(planLadder('attachment'), [
    { colors: 256, scale: 1, fps: 10 }, { colors: 192, scale: 1, fps: 10 },
    { colors: 256, scale: 1, fps: 8 }, { colors: 128, scale: 1, fps: 8 },
    { colors: 128, scale: 0.8, fps: 8 }, { colors: 128, scale: 0.8, fps: 6 },
    { colors: 128, scale: 0.64, fps: 8, duration: 2 }, { colors: 128, scale: 0.64, fps: 6, duration: 2 },
  ]);
});

// Node has no canvas or browser Worker. Keep the production GIF encoder and
// decoder real; only adapt those browser boundaries, optionally forcing overflow.
async function withAttachmentRuntime(run, forceOverflow = () => false) {
  const { createFrameEncoder } = await import('../../src/lib/gif/gifEncodeWorker.js');
  const originals = { Worker: globalThis.Worker, document: globalThis.document, ImageData: globalThis.ImageData };
  const attempts = [];
  globalThis.Worker = class {
    postMessage(data) {
      if (data.type === 'start') {
        this.attempt = { ...data.settings, delays: [], pixels: [] };
        attempts.push(this.attempt);
        this.encoder = createFrameEncoder(data.settings);
      }
      if (data.type === 'frame') {
        const rgba = new Uint8ClampedArray(data.buffer);
        this.attempt.delays.push(data.delayMs); this.attempt.pixels.push(rgba[0]);
        this.encoder.addFrame(rgba, data.delayMs);
      }
      let reply = { type: 'ready' };
      if (data.type === 'finish') {
        const bytes = this.encoder.finish();
        this.attempt.bytes = bytes.length;
        reply = { type: 'result', buffer: forceOverflow(attempts.length - 1) ? new ArrayBuffer(GIF_HARD_BYTES + 1) : bytes.buffer };
      }
      queueMicrotask(() => this.onmessage({ data: reply }));
    }
    terminate() {}
  };
  globalThis.ImageData = class { constructor(rgba, width, height) { assert.equal(rgba.length, width * height * 4); } };
  globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => ({ putImageData() {} }),
    toBlob(callback, type) { callback(new Blob([`${this.width}x${this.height}`], { type })); } }) };
  try { return await run(attempts); }
  finally { for (const [key, value] of Object.entries(originals)) if (value === undefined) delete globalThis[key]; else globalThis[key] = value; }
}

test('첨부 축소는 가까운 프레임을 반복 샘플링하고 마지막 두 단계에서 2초로 자른다', async () => {
  const { GIFEncoder } = (await import('gifenc')).default;
  const { inspectGifAttachment, reduceGifAttachment } = await import('../../src/lib/gif/gifAttachment.js');
  const gif = GIFEncoder();
  const palette = [[20,20,20], [80,80,80], [140,140,140], [200,200,200]];
  for (const [index, delay] of [200, 300, 500, 2000].entries()) {
    gif.writeFrame(new Uint8Array(16).fill(index), 4, 4, { palette, delay });
  }
  gif.finish();
  const info = await inspectGifAttachment(new Blob([gif.bytes()], { type: 'image/gif' }));
  for (const winner of [6, 7, 8]) await withAttachmentRuntime(async attempts => {
    const progress = [];
    const pending = reduceGifAttachment(info, { onProgress: v => progress.push(v) });
    if (winner === 8) {
      await assert.rejects(pending, { message: '줄여도 5MB를 넘어요. 더 짧은 GIF를 올려 주세요' });
      assert.equal(attempts.length, 8);
    } else {
      const result = await pending;
      assert.equal(result.stepIndex, winner);
      assert.equal(result.frames, winner === 6 ? 16 : 12);
      assert.equal(result.delayMs, 1000 / (winner === 6 ? 8 : 6));
      assert.equal(result.frames * result.delayMs, 2000);
      assert.equal(progress.at(-1), 100);
    }
    assert.deepEqual(attempts[0].pixels.slice(0, 6), [20, 20, 80, 80, 140, 140]);
    assert.deepEqual(attempts[2].pixels.slice(0, 5), [20, 80, 80, 140, 140]);
    assert.deepEqual(attempts.map(a => a.delays.length), winner === 6 ? [30,30,24,24,24,18,16] : [30,30,24,24,24,18,16,12]);
    assert.ok(attempts.every(a => a.delays.every(delay => delay === a.delayMs)));
    assert.ok(progress.every((v, i) => v >= 0 && v <= 100 && (i === 0 || v >= progress[i - 1])));
  }, index => index < winner);
});

test('800×1196·30장·약 11MB 노이즈 첨부 GIF를 실제로 5MB 이하로 줄이고 통과 단계를 기록한다', async t => {
  const { GIFEncoder } = (await import('gifenc')).default;
  const { inspectGifAttachment, reduceGifAttachment } = await import('../../src/lib/gif/gifAttachment.js');
  const width = 800, height = 1196, gif = GIFEncoder();
  const palette = Array.from({ length: 256 }, (_, i) => [i, i * 53 % 256, i * 107 % 256]);
  const random = randomBytes(1729);
  for (let frame = 0; frame < 30; frame++) {
    const pixels = new Uint8Array(width * height);
    for (let y = 0; y < height; y += 5) for (let x = 0; x < width; x += 5) {
      const color = random();
      for (let dy = 0; dy < 5 && y + dy < height; dy++) pixels.fill(color, (y + dy) * width + x, (y + dy) * width + x + 5);
    }
    gif.writeFrame(pixels, width, height, { palette, delay: 100 });
  }
  gif.finish();
  const info = await inspectGifAttachment(new Blob([gif.bytes()], { type: 'image/gif' }));
  assert.ok(info.bytes >= 10_000_000 && info.bytes <= 12_000_000, `fixture bytes=${info.bytes}`);
  assert.equal(info.frames, 30); assert.equal(info.delayMs, 100);
  await withAttachmentRuntime(async attempts => {
    const result = await reduceGifAttachment(info);
    t.diagnostic(JSON.stringify({ inputBytes: info.bytes, outputBytes: result.bytes, stepIndex: result.stepIndex,
      width: result.width, height: result.height, fps: 1000 / result.delayMs, duration: result.frames * result.delayMs / 1000,
      attempts: attempts.map(a => a.bytes) }));
    assert.ok(result.bytes <= GIF_HARD_BYTES, `output bytes=${result.bytes}`);
    assert.equal(result.bytes, result.blob.size);
    assert.ok(result.stepIndex >= 2 && result.stepIndex <= 7);
    assert.equal(result.stepIndex, attempts.length - 1);
    assert.ok(attempts.slice(0, -1).every(a => a.bytes > GIF_HARD_BYTES));
    const decoded = parseGif(new Uint8Array(await result.blob.arrayBuffer()));
    assert.equal(decoded.width, result.width); assert.equal(decoded.height, result.height);
    assert.equal(decoded.frames.length, result.frames);
    assert.equal(await result.jpgBlob.text(), `${result.width}x${result.height}`);
  });
});

test('줄이는 중 진행률을 보이고 실패 뒤 원본 그대로 담기를 유지한다', async () => {
  const h = await panelHarness();
  try {
    let rejectReduction;
    h.runtime.attachment = {
      inspectGifAttachment: async () => ({ bytes: GIF_HARD_BYTES + 1 }),
      reduceGifAttachment: (_info, { onProgress }) => {
        onProgress(42);
        return new Promise((_resolve, reject) => { rejectReduction = reject; });
      },
      prepareGifAttachment: async () => h.runtime.result,
    };
    await nodes(h.render(), n => n.type === 'input')[0].props.onChange({ target: { files: [new Blob()], value: 'file' } });
    const pending = buttonNamed(h.render(), '줄여서 담기').props.onClick();
    const during = h.render();
    rejectReduction(new Error('줄여도 5MB를 넘어요. 더 짧은 GIF를 올려 주세요'));
    await pending;
    assert.equal(buttonNamed(during, '줄이는 중 42%')?.props.disabled, true);
    assert.equal(textOf(nodes(h.render(), n => n.props.role === 'alert')[0]), '줄여도 5MB를 넘어요. 더 짧은 GIF를 올려 주세요');
    assert.equal(buttonNamed(h.render(), '그대로 담기').props.disabled, false);
    await buttonNamed(h.render(), '그대로 담기').props.onClick();
    assert.equal(h.runtime.saved.length, 1);
  } finally { await h.close(); }
});
