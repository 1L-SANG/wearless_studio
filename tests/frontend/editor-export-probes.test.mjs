import test from 'node:test';
import assert from 'node:assert/strict';
import {
  collectExportImageProbes,
  rasterizeExportSvg,
} from '../../src/features/editor/editorExportRaster.js';

// 회전 없는 사진과 직사각형 덮개에 필요한 브라우저 좌표 경계만 모사한다.
class TestMatrix {
  constructor() { this.x = 0; this.y = 0; }
  translate(x, y) {
    const result = new TestMatrix();
    result.x = this.x + x;
    result.y = this.y + y;
    return result;
  }
  multiply(other) { return this.translate(other.x, other.y); }
  inverse() {
    const result = new TestMatrix();
    result.x = -this.x;
    result.y = -this.y;
    return result;
  }
  transformPoint(point) { return { x: point.x + this.x, y: point.y + this.y }; }
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
  const prior = new Map(['DOMMatrix', 'DOMPoint', 'getComputedStyle', 'document']
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
            drawImage(image) { sourcePixel = image.sourceRgba || sourceRgba; },
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

async function raster(root, boundary) {
  const probes = await collectExportImageProbes(root, { ratio: 0.86, height: 500 });
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
