import test from 'node:test';
import assert from 'node:assert/strict';
import {
  collectExportImageProbes,
  rasterizeExportSvg,
} from '../../src/features/editor/editorExportRaster.js';

// 회전 없는 사진과 직사각형 덮개에 필요한 브라우저 좌표 경계만 모사한다.
class TestMatrix {
  constructor(values = [1, 0, 0, 1, 0, 0]) { [this.a, this.b, this.c, this.d, this.x, this.y] = values; }
  translate(x, y) {
    return this.multiply(new TestMatrix([1, 0, 0, 1, x, y]));
  }
  multiply(o) { return new TestMatrix([
    this.a * o.a + this.c * o.b, this.b * o.a + this.d * o.b,
    this.a * o.c + this.c * o.d, this.b * o.c + this.d * o.d,
    this.a * o.x + this.c * o.y + this.x, this.b * o.x + this.d * o.y + this.y,
  ]); }
  inverse() {
    const det = this.a * this.d - this.b * this.c;
    return new TestMatrix([this.d / det, -this.b / det, -this.c / det, this.a / det,
      (this.c * this.y - this.d * this.x) / det, (this.b * this.x - this.a * this.y) / det]);
  }
  transformPoint(p) { return { x: p.x * this.a + p.y * this.c + this.x, y: p.x * this.b + p.y * this.d + this.y }; }
}

function node({ parent = null, className = '', css = {}, width = 1000, height = 500 } = {}) {
  const classes = className.split(' ');
  const result = {
    tagName: 'DIV',
    className,
    classList: { contains: (name) => classes.includes(name) },
    children: [],
    parentElement: parent,
    offsetParent: parent,
    offsetLeft: 0,
    offsetTop: 0,
    offsetWidth: width,
    offsetHeight: height,
    clientWidth: width,
    clientHeight: height,
    css: {
      opacity: '1', display: 'block', visibility: 'visible',
      overflowX: 'visible', overflowY: 'visible',
      transform: 'none', transformOrigin: '0px 0px',
      borderTopLeftRadius: '0px', borderRadius: '0px',
      backgroundColor: 'rgba(0, 0, 0, 0)', objectFit: 'cover',
      ...css,
    },
    contains(candidate) {
      return candidate === this || this.children.some((child) => child.contains(candidate));
    },
    getBoundingClientRect() { return { left: 0, top: 0, right: width, bottom: height, width, height }; },
    querySelectorAll(selector) {
      const descendants = this.children.flatMap((child) => [child, ...child.querySelectorAll('*')]);
      if (selector === '*') return descendants;
      if (selector === '.el') return descendants.filter((child) => child.classList.contains('el'));
      if (selector === 'img' || selector === 'img[src]') return descendants.filter((child) => child.tagName === 'IMG');
      if (selector === 'svg') return descendants.filter((child) => child.tagName === 'svg');
      return [];
    },
    closest(selector) {
      if (selector === '.el' && this.classList.contains('el')) return this;
      return this.parentElement?.closest(selector) || null;
    },
  };
  parent?.children.push(result);
  return result;
}

function fixture({ imageOpacity = 1, overlayOpacity } = {}) {
  const root = node({ css: { backgroundColor: 'rgb(255, 255, 255)' } });
  const frame = node({ parent: root, className: 'el', css: { opacity: String(imageOpacity) } });
  const image = node({ parent: frame });
  Object.assign(image, { tagName: 'IMG', naturalWidth: 1000, naturalHeight: 500, decode: async () => {} });
  if (overlayOpacity !== undefined) {
    const overlay = node({ parent: root, className: 'el el-shape', css: { opacity: String(overlayOpacity) } });
    // 실제 사각형 도형은 .el 안쪽 div의 배경으로 채운다.
    node({ parent: overlay, css: { backgroundColor: 'rgb(200, 200, 202)' } });
  }
  return root;
}

function overlappingPhotos() {
  const root = fixture();
  root.children[0].children[0].sourceRgba = [40, 70, 110, 255];
  const upperFrame = node({ parent: root, className: 'el', width: 200 });
  const upperImage = node({ parent: upperFrame, width: 200 });
  Object.assign(upperImage, {
    tagName: 'IMG', naturalWidth: 200, naturalHeight: 500,
    sourceRgba: [200, 121, 50, 255], decode: async () => {},
  });
  return root;
}

function installBrowserBoundary(t, sourceRgba = [40, 70, 110, 255]) {
  const prior = new Map(['DOMMatrix', 'DOMPoint', 'getComputedStyle', 'document', 'Image', 'XMLSerializer']
    .map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  globalThis.DOMMatrix = TestMatrix;
  globalThis.DOMPoint = class { constructor(x, y) { this.x = x; this.y = y; } };
  globalThis.getComputedStyle = (element) => element.css;
  // 기본 원본은 RGB 40, 70, 110의 단색이며 CSS 합성 전 투명도를 포함해 읽는다.
  globalThis.document = {
    createElement() {
      return {
        width: 0, height: 0,
        getContext() {
          let sourcePixel = sourceRgba;
          return {
            clearRect() {},
            drawImage(image, x, y) { sourcePixel = image.sampleRgba?.(x, y) || image.sourceRgba || sourceRgba; },
            getImageData: () => ({ data: new Uint8ClampedArray(sourcePixel) }),
          };
        },
      };
    },
  };
  t.after(() => {
    for (const [name, descriptor] of prior) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });
}

// SVG 디코드 이후 실제 브라우저가 내놓는 픽셀만 외부 경계에서 주입한다.
function rasterBoundary(rgba) {
  let allocated;
  return {
    deps: {
      createCanvas(width, height) {
        allocated = {
          width, height,
          getContext() {
            return {
              clearRect() {}, fillRect() {}, drawImage() {},
              getImageData: (x, y) => ({ data: new Uint8ClampedArray(typeof rgba === 'function' ? rgba(x, y) : rgba) }),
            };
          },
        };
        return allocated;
      },
      loadImage: async () => ({}),
      waitForPaint: async () => {},
    },
    canvas: () => allocated,
  };
}

async function raster(root, boundary, probeDeps = {}) {
  const probes = await collectExportImageProbes(root, { ratio: 0.86, height: 500 }, probeDeps);
  return rasterizeExportSvg('검사용 SVG', { width: 860, height: 430, probes }, boundary.deps);
}

test('CSS 불투명도 97% 사진이 빠지면 다운로드 캔버스를 반환하지 않는다', async (t) => {
  installBrowserBoundary(t);
  const boundary = rasterBoundary([255, 255, 255, 255]);
  await assert.rejects(raster(fixture({ imageOpacity: 0.97 }), boundary), /사진.*빠져|사진.*확인/);
  assert.deepEqual([boundary.canvas().width, boundary.canvas().height], [0, 0]);
});

test('45% 직사각형 덮개 아래 사진이 빠지면 다운로드 캔버스를 반환하지 않는다', async (t) => {
  installBrowserBoundary(t);
  // 흰 바탕에 RGB 200, 200, 202 덮개만 45%로 합성한 픽셀이다.
  const boundary = rasterBoundary([230, 230, 231, 255]);
  await assert.rejects(raster(fixture({ overlayOpacity: 0.45 }), boundary), /사진.*빠져|사진.*확인/);
  assert.deepEqual([boundary.canvas().width, boundary.canvas().height], [0, 0]);
});

test('불투명도 97% 사진이 실제로 그려졌으면 정상 캔버스를 반환한다', async (t) => {
  installBrowserBoundary(t);
  // RGB 40, 70, 110 사진을 흰 바탕 위에 97%로 합성한 값이다.
  const canvas = await raster(fixture({ imageOpacity: 0.97 }), rasterBoundary([46, 76, 114, 255]));
  assert.deepEqual([canvas.width, canvas.height], [860, 430]);
});

test('45% 덮개 아래 사진이 실제로 그려졌으면 정상 캔버스를 반환한다', async (t) => {
  installBrowserBoundary(t);
  // 사진 위에 RGB 200, 200, 202 덮개를 45%로 합성한 값이다.
  const canvas = await raster(fixture({ overlayOpacity: 0.45 }), rasterBoundary([112, 129, 151, 255]));
  assert.deepEqual([canvas.width, canvas.height], [860, 430]);
});

test('완전히 불투명한 직사각형에 가려진 사진은 누락 실패로 오인하지 않는다', async (t) => {
  installBrowserBoundary(t);
  const canvas = await raster(fixture({ overlayOpacity: 1 }), rasterBoundary([200, 200, 202, 255]));
  assert.deepEqual([canvas.width, canvas.height], [860, 430]);
});

test('원본도 반투명한 70% 사진이 빠지면 다운로드 캔버스를 반환하지 않는다', async (t) => {
  installBrowserBoundary(t, [40, 70, 110, 128]);
  const boundary = rasterBoundary([255, 255, 255, 255]);
  await assert.rejects(raster(fixture({ imageOpacity: 0.7 }), boundary), /사진.*빠져|사진.*확인/);
  assert.deepEqual([boundary.canvas().width, boundary.canvas().height], [0, 0]);
});

test('원본도 반투명한 70% 사진이 실제로 그려졌으면 정상 캔버스를 반환한다', async (t) => {
  installBrowserBoundary(t, [40, 70, 110, 128]);
  // 원본 알파 128과 CSS 불투명도 70%를 흰 바탕에 함께 합성한 값이다.
  const canvas = await raster(fixture({ imageOpacity: 0.7 }), rasterBoundary([179, 190, 204, 255]));
  assert.deepEqual([canvas.width, canvas.height], [860, 430]);
});

test('앞쪽 사진만 보이고 아래 사진이 빠졌으면 다운로드 캔버스를 반환하지 않는다', async (t) => {
  installBrowserBoundary(t);
  // 폭 200px 앞쪽 사진은 860px 출력에서 왼쪽 172px만 차지한다.
  const boundary = rasterBoundary((x) => x < 172 ? [200, 121, 50, 255] : [255, 255, 255, 255]);
  await assert.rejects(raster(overlappingPhotos(), boundary), /사진.*빠져|사진.*확인/);
  assert.deepEqual([boundary.canvas().width, boundary.canvas().height], [0, 0]);
});

test('겹친 앞쪽 사진과 아래 사진이 모두 그려졌으면 정상 캔버스를 반환한다', async (t) => {
  installBrowserBoundary(t);
  const boundary = rasterBoundary((x) => x < 172 ? [200, 121, 50, 255] : [40, 70, 110, 255]);
  const canvas = await raster(overlappingPhotos(), boundary);
  assert.deepEqual([canvas.width, canvas.height], [860, 430]);
});

function translucentPhotoStack() {
  const root = fixture();
  const frame = node({ parent: root, className: 'el', css: { opacity: '0.5' } });
  const image = node({ parent: frame });
  Object.assign(image, { tagName: 'IMG', naturalWidth: 1000, naturalHeight: 500,
    sourceRgba: [200, 120, 50, 255], decode: async () => {} });
  return root;
}

test('반투명 사진 아래의 사진 색까지 합성된 정상 출력은 허용한다', async (t) => {
  installBrowserBoundary(t);
  const canvas = await raster(translucentPhotoStack(), rasterBoundary([120, 95, 80, 255]));
  assert.deepEqual([canvas.width, canvas.height], [860, 430]);
});

for (const [name, actual] of [['아래 사진', [228, 188, 153, 255]], ['위 사진', [40, 70, 110, 255]]]) {
  test(`반투명 사진이 겹쳐 있어도 ${name}이 빠지면 저장을 막는다`, async (t) => {
    installBrowserBoundary(t);
    await assert.rejects(raster(translucentPhotoStack(), rasterBoundary(actual)), /사진.*빠져/);
  });
}

function svgOverlay() {
  const root = fixture();
  const layer = node({ parent: root, className: 'el el-speech-bubble' });
  const svg = node({ parent: layer });
  svg.tagName = 'svg';
  return root;
}

test('불투명 SVG 말풍선에 가려진 사진은 정상 출력으로 허용한다', async (t) => {
  installBrowserBoundary(t);
  const canvas = await raster(svgOverlay(), rasterBoundary([200, 200, 202, 255]), {
    // 브라우저의 SVG fill 래스터 경계만 대체한다. 가림 판정과 합성은 실제 코드를 쓴다.
    createSvgSampler: async () => () => [200, 200, 202, 255],
  });
  assert.deepEqual([canvas.width, canvas.height], [860, 430]);
});

test('반투명 SVG 말풍선 아래 사진이 보이면 정상 출력을 허용한다', async (t) => {
  installBrowserBoundary(t);
  const canvas = await raster(svgOverlay(), rasterBoundary([120, 135, 156, 255]), {
    createSvgSampler: async () => () => [200, 200, 202, 128],
  });
  assert.deepEqual([canvas.width, canvas.height], [860, 430]);
});

test('SVG의 투명한 부분에 보여야 하는 사진이 빠지면 저장을 막는다', async (t) => {
  installBrowserBoundary(t);
  await assert.rejects(raster(svgOverlay(),
    rasterBoundary((x) => x < 430 ? [200, 200, 202, 255] : [255, 255, 255, 255]), {
      createSvgSampler: async () => (point) => point.x < 500 ? [200, 200, 202, 255] : [0, 0, 0, 0],
    }), /사진.*빠져/);
});

test('SVGMatrix에 transformPoint가 없는 브라우저에서도 실제 SVG 표본 경로를 쓴다', async (t) => {
  installBrowserBoundary(t);
  const root = svgOverlay();
  const svg = root.querySelectorAll('svg')[0];
  svg.viewBox = { baseVal: { x: 0, y: 0, width: 1000, height: 500 } };
  const matrix = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };
  svg.getScreenCTM = () => ({ ...matrix, inverse: () => matrix });
  svg.cloneNode = () => ({ style: {}, setAttribute() {}, removeAttribute() {} });
  globalThis.XMLSerializer = class { serializeToString() { return '<svg/>'; } };
  globalThis.Image = class {
    sourceRgba = [200, 200, 202, 255];
    set src(_value) { queueMicrotask(() => this.onload()); }
    async decode() {}
  };
  const canvas = await raster(root, rasterBoundary([200, 200, 202, 255]));
  assert.deepEqual([canvas.width, canvas.height], [860, 430]);
});

test('확대한 SVG의 고정 두께 테두리 밖에서 보이는 사진은 누락 검사를 유지한다', async (t) => {
  installBrowserBoundary(t);
  const root = node({ height: 1000, css: { backgroundColor: 'rgb(255,255,255)' } });
  const frame = node({ parent: root, className: 'el', width: 100, height: 100 });
  frame.offsetLeft = 150; frame.offsetTop = 150;
  const image = node({ parent: frame, width: 100, height: 100 });
  Object.assign(image, { tagName: 'IMG', naturalWidth: 100, naturalHeight: 100, decode: async () => {} });
  frame.getBoundingClientRect = image.getBoundingClientRect = () => ({ left: 150, top: 150, right: 250, bottom: 250 });
  const layer = node({ parent: root, className: 'el el-shape', height: 1000 });
  const svg = node({ parent: layer, height: 1000 });
  svg.tagName = 'svg'; svg.viewBox = { baseVal: { x: 0, y: 0, width: 100, height: 100 } };
  svg.preserveAspectRatio = { baseVal: { align: 1, meetOrSlice: 1 } };
  svg.getScreenCTM = () => ({ a: 10, b: 0, c: 0, d: 10, e: 0, f: 0 });
  const attributes = {};
  svg.cloneNode = () => ({ style: {}, setAttribute(k, v) { attributes[k] = v; }, removeAttribute() {} });
  globalThis.XMLSerializer = class { serializeToString() { return '<svg/>'; } };
  globalThis.Image = class {
    // 마름모 왼쪽 위 변은 x+y=폭/2. non-scaling-stroke의 반폭은 항상 20px다.
    sampleRgba(x, y) { return Math.abs(x + y - Number(attributes.width) / 2) / Math.SQRT2 <= 20
      ? [0, 0, 0, 255] : [0, 0, 0, 0]; }
    set src(_value) { queueMicrotask(() => this.onload()); }
    async decode() {}
  };
  await assert.rejects(raster(root, rasterBoundary([255, 255, 255, 255])), /사진.*빠져/);
  assert.equal(Number(attributes.width), 1000);
});
