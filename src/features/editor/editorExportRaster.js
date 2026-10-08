// 눈에 보이는 사진 내용이 있는데 결과 영역에 균일한 배경만 남았는지 확인한다.
// 원래 빈 블록, 흰 이미지, 투명 여백은 사진 누락으로 판정하지 않는다.
export function isBlankImageRegion(expected, actual) {
  if (actual.length < 3) return false;
  const distance = (a, b) => Math.max(...a.slice(0, 3).map((value, i) => Math.abs(value - b[i])));
  let background = actual[0];
  let largest = 0;
  for (const color of actual) {
    const count = actual.filter((other) => distance(color, other) <= 6).length;
    if (count > largest) { largest = count; background = color; }
  }
  if (largest < Math.ceil(actual.length * 0.9)) return false;
  const visible = expected.filter((color) => color[3] >= 240);
  if (!visible.length) return false;
  const mean = [0, 1, 2].map((i) => visible.reduce((sum, color) => sum + color[i], 0) / visible.length);
  if (distance(mean, background) <= 12) return false;
  const meaningful = visible.filter((color) => distance(color, background) > 12);
  return meaningful.length >= Math.max(3, Math.ceil(expected.length * 0.1));
}

function createRasterCanvas(width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  if (!canvas.getContext('2d', { willReadFrequently: true })) {
    canvas.width = 0; canvas.height = 0;
    throw new Error('저장용 캔버스를 만들지 못했어요. 다시 시도해 주세요.');
  }
  return canvas;
}

function loadDecodedSvg(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const timer = setTimeout(() => reject(new Error('사진을 읽는 데 너무 오래 걸려요. 다시 시도해 주세요.')), 30_000);
    image.onload = async () => {
      try { await image.decode(); resolve(image); }
      catch (error) { reject(error); }
      finally { clearTimeout(timer); }
    };
    image.onerror = () => { clearTimeout(timer); reject(new Error('사진을 읽지 못했어요. 다시 시도해 주세요.')); };
    image.crossOrigin = 'anonymous';
    image.src = src;
  });
}

// SVG 내부 사진의 디코드는 SVG 자체의 decode 완료보다 늦을 수 있다.
// 같은 SVG를 다시 그리되, 사진 픽셀이 나타난 결과만 반환하고 대기는 제한한다.
export async function rasterizeExportSvg(svg, { width, height, probes = [] }, deps = {}) {
  const createCanvas = deps.createCanvas || createRasterCanvas;
  const loadImage = deps.loadImage || loadDecodedSvg;
  const waitForPaint = deps.waitForPaint || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const canvas = createCanvas(width, height);
  try {
    const image = await loadImage(svg);
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('저장용 캔버스를 만들지 못했어요. 다시 시도해 주세요.');
    for (let attempt = 0; attempt < 6; attempt += 1) {
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(image, 0, 0, width, height);
      const missing = probes.filter(({ points }) => isBlankImageRegion(
        points.map((point) => point.rgba),
        points.map(({ x, y }) => [...ctx.getImageData(x, y, 1, 1).data]),
      ));
      if (!missing.length) return canvas;
      if (attempt < 5) await waitForPaint(32 * 2 ** attempt); // eslint-disable-line no-await-in-loop
    }
    throw new Error('사진이 빠져 다운로드를 중단했어요. 다시 시도해 주세요.');
  } catch (error) {
    canvas.width = 0;
    canvas.height = 0;
    throw error;
  }
}

// CSS 회전을 포함해 이미지의 좌표를 블록 좌표로 바꾼다.
function imageMatrix(node, root) {
  let matrix = new DOMMatrix();
  for (let current = node; current && current !== root; current = current.offsetParent) {
    const style = getComputedStyle(current);
    const [ox, oy] = style.transformOrigin.split(' ').map(Number.parseFloat);
    const step = new DOMMatrix().translate(current.offsetLeft, current.offsetTop)
      .translate(ox || 0, oy || 0)
      .multiply(new DOMMatrix(style.transform === 'none' ? undefined : style.transform))
      .translate(-(ox || 0), -(oy || 0));
    matrix = step.multiply(matrix);
  }
  return matrix;
}

function insideBox(point, node, root) {
  const local = imageMatrix(node, root).inverse().transformPoint(point);
  const w = node.offsetWidth, h = node.offsetHeight;
  if (local.x < 0 || local.y < 0 || local.x >= w || local.y >= h) return false;
  const corner = getComputedStyle(node).borderTopLeftRadius;
  const wantedRadius = corner.endsWith('%') ? Math.min(w, h) * Number.parseFloat(corner) / 100 : Number.parseFloat(corner);
  const radius = Math.min(wantedRadius || 0, w / 2, h / 2);
  const dx = Math.max(radius - local.x, local.x - (w - radius), 0);
  const dy = Math.max(radius - local.y, local.y - (h - radius), 0);
  return dx * dx + dy * dy <= radius * radius;
}

function rgbaFor(value = '') {
  const numbers = value.match(/[\d.]+/g)?.map(Number);
  if (!value.startsWith('rgb') || !numbers) return [0, 0, 0, 0];
  return [numbers[0], numbers[1], numbers[2], numbers[3] ?? 1];
}

function blend(color, background, opacity = 1) {
  const alpha = color[3] * opacity;
  return color.slice(0, 3).map((value, i) => value * alpha + background[i] * (1 - alpha));
}

function effectiveOpacity(node, root) {
  let opacity = 1;
  for (let current = node; current && current !== root.parentElement; current = current.parentElement) {
    const style = getComputedStyle(current);
    if (style.display === 'none' || style.visibility === 'hidden') return 0;
    opacity *= Number(style.opacity);
  }
  return opacity;
}

function imageSampler(image, root, ctx) {
  const inverse = imageMatrix(image, root).inverse();
  const style = getComputedStyle(image);
  const w = image.clientWidth, h = image.clientHeight;
  const fit = style.objectFit;
  const cover = Math.max(w / image.naturalWidth, h / image.naturalHeight);
  const contain = Math.min(w / image.naturalWidth, h / image.naturalHeight);
  const scale = fit === 'contain' ? contain : fit === 'none' ? 1 : fit === 'scale-down' ? Math.min(1, contain) : cover;
  const sx = fit === 'fill' ? w / image.naturalWidth : scale;
  const sy = fit === 'fill' ? h / image.naturalHeight : scale;
  const offsetX = (w - image.naturalWidth * sx) / 2;
  const offsetY = (h - image.naturalHeight * sy) / 2;
  const clips = [];
  for (let node = image.parentElement; node && node !== root; node = node.parentElement) {
    const css = getComputedStyle(node);
    if (css.overflowX === 'hidden' || css.overflowY === 'hidden') clips.push(node);
  }
  return (point) => {
    if (!insideBox(point, image, root) || clips.some((clip) => !insideBox(point, clip, root))) return null;
    const local = inverse.transformPoint(point);
    const x = (local.x - offsetX) / sx, y = (local.y - offsetY) / sy;
    if (x < 0 || y < 0 || x >= image.naturalWidth || y >= image.naturalHeight) return null;
    ctx.clearRect(0, 0, 1, 1);
    ctx.drawImage(image, Math.floor(x), Math.floor(y), 1, 1, 0, 0, 1, 1);
    return [...ctx.getImageData(0, 0, 1, 1).data];
  };
}

// 실제 채워진 도형과 앞쪽 사진의 픽셀만 가림으로 처리한다.
function layerPaints(layer, point, root, samplers) {
  const paints = [];
  for (const node of [layer, ...layer.children]) {
    const color = rgbaFor(getComputedStyle(node).backgroundColor);
    if (color[3] > 0 && insideBox(point, node, root)) {
      paints.push({ color, opacity: effectiveOpacity(node, root) });
    }
  }
  for (const image of layer.querySelectorAll('img[src]')) {
    const color = samplers.get(image)?.(point);
    if (color && color[3] > 0) paints.push({ color: [color[0], color[1], color[2], color[3] / 255], opacity: effectiveOpacity(image, root) });
  }
  return paints;
}

function imageBackdrop(image, root) {
  const parents = [];
  for (let node = image.parentElement; node && node !== root.parentElement; node = node.parentElement) parents.unshift(node);
  return parents.reduce((color, node) => blend(rgbaFor(getComputedStyle(node).backgroundColor), color), [255, 255, 255]);
}

export async function collectExportImageProbes(root, { ratio, top = 0, height }) {
  const probes = [];
  const rootRect = root.getBoundingClientRect();
  const layers = [...root.querySelectorAll('.el')];
  const reader = document.createElement('canvas');
  reader.width = 1; reader.height = 1;
  const ctx = reader.getContext('2d', { willReadFrequently: true });
  if (!ctx) throw new Error('사진을 확인할 수 없어요. 다시 시도해 주세요.');
  try {
    const images = [...root.querySelectorAll('img[src]')];
    try { await Promise.all(images.map((image) => image.decode())); }
    catch { throw new Error('사진을 읽지 못해 다운로드를 중단했어요. 다시 시도해 주세요.'); }
    const samplers = new Map(images.map((image) => [image, imageSampler(image, root, ctx)]));
    for (const image of images) {
      const frame = image.closest('.el');
      const opacity = effectiveOpacity(image, root);
      if (opacity === 0 || !image.naturalWidth || !image.naturalHeight) continue;
      const backdrop = imageBackdrop(image, root);
      const rect = image.getBoundingClientRect();
      let left = Math.max(0, rect.left - rootRect.left), right = Math.min(1000, rect.right - rootRect.left);
      let first = Math.max(top, rect.top - rootRect.top), last = Math.min(top + height, rect.bottom - rootRect.top);
      const clips = [];
      for (let n = image.parentElement; n && n !== root; n = n.parentElement) {
        const style = getComputedStyle(n);
        if (style.overflowX === 'hidden' || style.overflowY === 'hidden') {
          clips.push(n);
          const r = n.getBoundingClientRect();
          left = Math.max(left, r.left - rootRect.left); right = Math.min(right, r.right - rootRect.left);
          first = Math.max(first, r.top - rootRect.top); last = Math.min(last, r.bottom - rootRect.top);
        }
      }
      if ((right - left) * ratio < 4 || (last - first) * ratio < 4) continue;
      const above = frame ? layers.slice(layers.indexOf(frame) + 1).filter((layer) => !layer.contains(image)) : [];
      const points = [];
      for (let row = 1; row <= 7; row += 1) {
        for (let column = 1; column <= 7; column += 1) {
          const point = new DOMPoint(left + (right - left) * column / 8, first + (last - first) * row / 8);
          if (!insideBox(point, image, root) || clips.some((clip) => !insideBox(point, clip, root))) continue;
          const covering = above.filter((layer) => effectiveOpacity(layer, root) > 0 && insideBox(point, layer, root));
          // 글자 영역은 글꼴의 픽셀과 사진의 픽셀이 섞이므로 표본에서 제외한다.
          if (covering.some((layer) => layer.classList.contains('el-text'))) continue;
          const paints = covering.flatMap((layer) => layerPaints(layer, point, root, samplers));
          if (paints.some(({ color, opacity: layerOpacity }) => color[3] * layerOpacity >= 0.999)) continue;
          const source = samplers.get(image)(point);
          if (!source) continue;
          let expected = blend([source[0], source[1], source[2], source[3] / 255], backdrop, opacity);
          for (const paint of paints) expected = blend(paint.color, expected, paint.opacity);
          points.push({ x: Math.floor(point.x * ratio), y: Math.floor((point.y - top) * ratio),
            rgba: [...expected.map(Math.round), source[3] > 0 ? 255 : 0] });
        }
      }
      if (points.length >= 3) probes.push({ points });
    }
    return probes;
  } finally { reader.width = 0; reader.height = 0; }
}
