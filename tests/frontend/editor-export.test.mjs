import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  DEFAULT_EXPORT_SETTINGS,
  EXPORT_PRESETS,
  EXPORT_WIDTH,
  buildExportSummary,
  detailFileName,
  exportNodeHeight,
  exportFileName,
  exportScaleForPreset,
  findUniformCutRow,
  fitPixelRatio,
  groupScaledBlockHeights,
  isAssetBytesUrl,
  publicationKindForSingle,
  readExportSettings,
  renderStorefrontFiles,
  singlePageSizeWarning,
  singlePageFallbackReason,
  splitCanvasRanges,
  splitOversizeJpegRanges,
  stitchLayout,
  toBytesUrl,
  writeExportSettings,
} from '../../src/features/editor/editorExport.js';

const AID = '123e4567-e89b-42d3-a456-426614174000';
const editorSource = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');

test('toBytesUrl: /file 자산 URL을 /bytes로 바꾼다 (상대·절대·비uuid id — id 검증은 서버 몫)', () => {
  // 기본은 안전한 레거시 e=1. 백엔드 선배포 뒤 VITE gate로만 e=2를 켠다.
  // 1년짜리로 캐시에 박아 둬서, 주소를 갈지 않으면 서버를 고쳐도 계속 차단된다.
  assert.equal(toBytesUrl(`/v1/assets/${AID}/file`), `/v1/assets/${AID}/bytes?e=1`);
  assert.equal(
    toBytesUrl(`https://api.wearless.app/v1/assets/${AID}/file`),
    `https://api.wearless.app/v1/assets/${AID}/bytes?e=1`,
  );
  // 프론트가 서버보다 엄격하면 어긋난다 — id 모양은 경로 수준만 본다 (리뷰 반영)
  assert.equal(toBytesUrl('/v1/assets/stable-1/file'), '/v1/assets/stable-1/bytes?e=1');
  assert.equal(toBytesUrl(`/v1/assets/${AID}/file?e=2`), `/v1/assets/${AID}/bytes?e=1`);
});

test('isAssetBytesUrl: 쿼리가 붙어도 핵심 자산으로 판정한다', () => {
  // 이 판정이 깨지면 상품컷 실패가 soft 로 강등돼 빈 이미지인 채 "저장 완료" 로 속인다.
  assert.equal(isAssetBytesUrl(`/v1/assets/${AID}/bytes?e=1`), true);
  assert.equal(isAssetBytesUrl(`/v1/assets/${AID}/bytes`), true);
  assert.equal(isAssetBytesUrl(toBytesUrl(`/v1/assets/${AID}/file`)), true);
  assert.equal(isAssetBytesUrl(`/v1/assets/${AID}/file`), false);
  assert.equal(isAssetBytesUrl('https://cdn.example.com/img.png'), false);
  assert.equal(isAssetBytesUrl(null), false);
});

test('toBytesUrl: blob·data·외부·비자산 URL은 그대로 둔다', () => {
  for (const src of [
    'blob:http://localhost:5173/abc',
    'data:image/png;base64,xyz',
    'https://cdn.example.com/img.png',
    '/assets/brand/logo.svg',
    '',
    null,
  ]) {
    assert.equal(toBytesUrl(src), src);
  }
});

test('stitchLayout: 세로 오프셋 누적 + 최대 폭', () => {
  const { width, height, offsets } = stitchLayout([
    { width: 2000, height: 800 },
    { width: 2000, height: 1200 },
    { width: 2000, height: 500 },
  ]);
  assert.equal(width, 2000);
  assert.equal(height, 2500);
  assert.deepEqual(offsets, [0, 800, 2000]);
});

test('fitPixelRatio: 한계 안에서 2 유지, 넘치면 축소, 한 장이 불가능하면 null(ZIP 안내)', () => {
  assert.equal(fitPixelRatio(5000), 2);         // 5000*2=10000 < 30000
  assert.ok(fitPixelRatio(20000) < 2);           // 20000*2=40000 > 30000 → 축소
  assert.ok(fitPixelRatio(20000) >= 1);
  assert.equal(fitPixelRatio(30001), null);      // 배율 1로도 못 담음 → 호출부가 ZIP 안내
  assert.equal(fitPixelRatio(0), 2);
});

test('원본 PNG도 큰 블록을 안전한 파일 수로 안내하고 폭 2000을 유지한다', () => {
  const summary = buildExportSummary([10000], { preset: 'original', format: 'png', delivery: 'split' });
  assert.equal(summary.width, 2000);
  assert.equal(summary.height, 20000);
  assert.equal(summary.fileCount, 3);
  assert.ok(summary.maxPieceHeight * summary.width <= 16777216);
});

test('원본 PNG는 2배율·무손실 형식으로 기존 블록 경계를 유지한다', async () => {
  const encoded = [];
  const result = await renderStorefrontFiles([{ h: 100 }, { h: 150 }],
    { preset: 'original', delivery: 'split' }, {
      measureBlock: node => node.h,
      captureBlock: async (node, ratio) => {
        assert.equal(ratio, 2);
        return { canvases: [{ width: 2000, height: node.h * ratio }], softFailed: 0 };
      },
      createCanvas: (width, height) => ({ width, height, getContext: () => ({ drawImage() {} }) }),
      releaseCanvas() {},
      encodeCanvas: async (canvas, mime, quality) => {
        encoded.push({ width: canvas.width, height: canvas.height, mime, quality });
        return new Blob(['png'], { type: mime });
      },
    });
  assert.equal(result.files.length, 2);
  assert.deepEqual(encoded, [
    { width: 2000, height: 200, mime: 'image/png', quality: undefined },
    { width: 2000, height: 300, mime: 'image/png', quality: undefined },
  ]);
});

test('긴 원본 PNG 조각은 새 축소 캔버스 없이 모든 행을 순서대로 인코딩한다', async () => {
  const heights = [8388, 8388, 3224];
  let offset = 0;
  const slices = heights.map(height => {
    const canvas = { width: 2000, height, rows: Array.from({ length: height }, (_, index) => offset + index) };
    offset += height;
    return canvas;
  });
  const files = await renderStorefrontFiles([10000], { preset: 'original', delivery: 'split' }, {
    measureBlock: height => height,
    captureBlock: async (_node, ratio) => { assert.equal(ratio, 2); return { canvases: slices, softFailed: 0 }; },
    createCanvas: () => { throw new Error('원본 PNG를 다시 그리거나 축소하면 안 된다'); },
    releaseCanvas() {},
    encodeCanvas: async (canvas, mime, quality) => {
      assert.equal(mime, 'image/png'); assert.equal(quality, undefined);
      assert.ok(canvas.width * canvas.height <= 16777216);
      return { rows: canvas.rows, width: canvas.width };
    },
  });
  assert.deepEqual(files.files.flatMap(file => file.rows), Array.from({ length: 20000 }, (_, i) => i));
  assert.ok(files.files.every(file => file.width === 2000));
});

test('쇼핑몰 폭 프리셋은 1,000px 설계 캔버스의 정확한 배율을 쓴다', () => {
  assert.deepEqual(EXPORT_PRESETS.map(({ id, width }) => [id, width]), [
    ['smartstore', 860],
    ['coupang', 780],
    ['high', 1290],
    ['original', 2000],
  ]);
  assert.equal(exportScaleForPreset('smartstore'), 0.86);
  assert.equal(exportScaleForPreset('coupang'), 0.78);
  assert.equal(exportScaleForPreset('high'), 1.29);
  assert.equal(exportScaleForPreset('original'), 2);
});

test('나눠 받기는 연속 블록을 3,000px 이하로 묶고 긴 블록은 내부 분할한다', () => {
  assert.deepEqual(groupScaledBlockHeights([1000, 2000, 4000], 0.86), [2580, 3000, 440]);
  assert.deepEqual(groupScaledBlockHeights([3500, 1000], 0.78), [2730, 780]);
  assert.ok(groupScaledBlockHeights([8000], 1.29).every((height) => height <= 3000));
});

test('파일명은 영문 detail과 숫자만 쓰고 순서 자릿수를 맞춘다', () => {
  assert.equal(detailFileName(0, 3, 'jpeg'), 'detail_01.jpg');
  assert.equal(detailFileName(1, 3, 'png'), 'detail_02.png');
  assert.equal(detailFileName(99, 120, 'jpeg'), 'detail_100.jpg');
});

test('한 장 받기는 20,000px 또는 브라우저 캔버스 한도를 넘으면 이유를 돌려준다', () => {
  assert.equal(singlePageFallbackReason({ width: 860, height: 16000 }), null);
  assert.match(singlePageFallbackReason({ width: 860, height: 20001 }), /20,000px/);
  assert.match(singlePageFallbackReason({ width: 860, height: 18000 }), /브라우저/,
    'html-to-image가 16,384px를 넘는 캔버스를 자동 축소한다');
  assert.match(singlePageFallbackReason({ width: 1290, height: 900, maxCanvasDim: 1000 }), /브라우저/);
  assert.match(singlePageFallbackReason({ width: 1290, height: 13006 }), /브라우저/,
    'Safari의 약 1,677만 픽셀 면적 한도도 지켜야 한다');
});

test('선택 기억은 잘못된 값과 localStorage 실패에서 안전한 기본값으로 돌아간다', () => {
  assert.deepEqual(DEFAULT_EXPORT_SETTINGS, { preset: 'smartstore', format: 'jpeg', delivery: 'split' });
  assert.deepEqual(readExportSettings({ getItem: () => null }), DEFAULT_EXPORT_SETTINGS);
  assert.deepEqual(readExportSettings({ getItem: () => JSON.stringify({
    preset: 'coupang', format: 'png', delivery: 'single',
  }) }), { preset: 'coupang', format: 'png', delivery: 'single' });
  assert.deepEqual(readExportSettings({ getItem: () => '{broken' }), DEFAULT_EXPORT_SETTINGS);
  assert.deepEqual(readExportSettings({ getItem: () => { throw new Error('blocked'); } }), DEFAULT_EXPORT_SETTINGS);
  assert.equal(writeExportSettings({ setItem: () => { throw new Error('full'); } }, DEFAULT_EXPORT_SETTINGS), false);
});

test('localStorage 객체 자체를 읽을 때 예외가 나도 에디터 렌더를 막지 않는다', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    get() { throw new DOMException('blocked', 'SecurityError'); },
  });
  try {
    assert.deepEqual(readExportSettings(), DEFAULT_EXPORT_SETTINGS);
    assert.equal(writeExportSettings(undefined, DEFAULT_EXPORT_SETTINGS), false);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor);
    else delete globalThis.localStorage;
  }
});

test('요약은 최종 폭과 총 세로, 예상 파일 수와 용량을 표시할 값을 만든다', () => {
  const split = buildExportSummary([1000, 2000, 4000], DEFAULT_EXPORT_SETTINGS);
  assert.deepEqual({ width: split.width, height: split.height, fileCount: split.fileCount }, {
    width: 860, height: 6020, fileCount: 3,
  });
  assert.equal(split.maxPieceHeight, 3000);
  assert.ok(split.approxBytes > 0);
  assert.equal(split.approximate, true);

  const single = buildExportSummary([1000, 2000], {
    preset: 'high', format: 'png', delivery: 'single',
  });
  assert.deepEqual({ width: single.width, height: single.height, fileCount: single.fileCount }, {
    width: 1290, height: 3870, fileCount: 1,
  });
});

test('긴 블록은 3,000px 가까이의 가로 단색 여백 줄에서 자른다', () => {
  const width = 4;
  const height = 6200;
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const at = (y * width + x) * 4;
      data[at] = (x * 30 + y) % 255;
      data[at + 1] = x * 20;
      data[at + 2] = y % 255;
      data[at + 3] = 255;
    }
  }
  for (let x = 0; x < width; x += 1) {
    const at = (2850 * width + x) * 4;
    data.set([248, 248, 248, 255], at);
  }
  const canvas = {
    width,
    height,
    getContext: () => ({
      getImageData: (_x, y, w, h) => ({
        data: data.slice(y * w * 4, (y + h) * w * 4), width: w, height: h,
      }),
    }),
  };
  assert.equal(findUniformCutRow(data.slice(0, 3000 * width * 4), width, 3000), 2850);
  const ranges = splitCanvasRanges(canvas);
  assert.equal(ranges[0].height, 2850);
  assert.equal(ranges.reduce((sum, range) => sum + range.height, 0), height);
  assert.ok(ranges.every((range) => range.height <= 3000));
});

test('긴 블록에 단색 여백 줄이 없으면 정확히 3,000px에서 자른다', () => {
  const width = 3;
  const pixels = new Uint8ClampedArray(width * 3000 * 4);
  for (let y = 0; y < 3000; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const at = (y * width + x) * 4;
      pixels.set([x * 50, y % 255, 10, 255], at);
    }
  }
  assert.equal(findUniformCutRow(pixels, width, 3000), 3000);
});

test('3MB 초과 JPG는 품질 90을 유지한 채 더 짧게 재분할한다', async () => {
  const qualities = [];
  const parts = await splitOversizeJpegRanges(
    { y: 0, height: 3000 },
    async (range, quality) => {
      qualities.push(quality);
      return { size: range.height * 4096 };
    },
  );
  assert.equal(parts.length, 4);
  assert.ok(parts.every(({ range, blob }) => range.height === 750 && blob.size <= 3 * 1024 * 1024));
  assert.ok(qualities.every((quality) => quality === 0.9));
});

test('한 장 결과가 실제 5MB를 넘으면 카페24·쿠팡 한도를 안내한다', () => {
  assert.equal(singlePageSizeWarning(5 * 1024 * 1024), null);
  assert.match(singlePageSizeWarning(5 * 1024 * 1024 + 1), /카페24 직접 등록, 쿠팡은 장당 5MB/);
});

test('FaceMarket 한 장 공증 kind는 JPG와 PNG를 구분하고 ZIP은 포맷과 무관하다', () => {
  assert.equal(publicationKindForSingle('jpeg'), 'jpeg');
  assert.equal(publicationKindForSingle('png'), 'long_png');
});

function fakeCanvas(width, height) {
  return {
    width,
    height,
    draws: [],
    getContext() {
      return {
        fillRect: () => {},
        drawImage: (...args) => this.draws.push(args),
        getImageData: (_x, _y, w, h) => {
          const data = new Uint8ClampedArray(w * h * 4);
          for (let y = 0; y < h; y += 1) {
            for (let x = 0; x < w; x += 1) {
              const at = (y * w + x) * 4;
              data.set([x % 255, y % 255, (x + y) % 255, 255], at);
            }
          }
          return { data, width: w, height: h };
        },
      };
    },
  };
}

test('실제 나눠 받기 렌더러도 블록을 묶고 긴 블록을 이어지는 JPG 조각으로 만든다', async () => {
  const heights = [10, 20, 40];
  const rendered = await renderStorefrontFiles(
    heights,
    { preset: 'smartstore', format: 'jpeg', delivery: 'split' },
    {
      maxHeight: 30,
      captureBlock: async (height) => ({ canvas: fakeCanvas(860, height), softFailed: 0 }),
      measureBlock: (height) => height / 0.86,
      createCanvas: fakeCanvas,
      encodeCanvas: async (canvas, mime, quality) => ({
        size: 100, type: mime, width: canvas.width, height: canvas.height, quality,
      }),
      releaseCanvas: () => {},
    },
  );
  assert.deepEqual(rendered.files.map((file) => file.height), [30, 30, 10]);
  assert.ok(rendered.files.every((file) => file.type === 'image/jpeg' && file.quality === 0.9));
  assert.equal(rendered.softFailed, 0);
});

test('브라우저가 블록 캔버스 아랫부분을 조용히 자르면 다운로드를 중단한다', async () => {
  await assert.rejects(
    renderStorefrontFiles(
      [100],
      { preset: 'smartstore', format: 'png', delivery: 'split' },
      {
        captureBlock: async () => ({ canvas: fakeCanvas(860, 80), softFailed: 0 }),
        measureBlock: (height) => height,
        createCanvas: fakeCanvas,
        encodeCanvas: async () => ({ size: 100 }),
        releaseCanvas: () => {},
      },
    ),
    /아랫부분이 잘릴 수 있어 다운로드를 중단/,
  );
});

test('아주 긴 단일 블록은 큰 중간 캔버스 없이 3,000px 캡처 조각들로 처리한다', async () => {
  const slices = Array.from({ length: 6 }, () => fakeCanvas(860, 3000));
  const rendered = await renderStorefrontFiles(
    [18000 / 0.86],
    { preset: 'smartstore', format: 'png', delivery: 'split' },
    {
      captureBlock: async () => ({ canvases: slices, softFailed: 0 }),
      measureBlock: (height) => height,
      createCanvas: fakeCanvas,
      encodeCanvas: async (canvas) => ({ size: 100, height: canvas.height }),
      releaseCanvas: () => {},
    },
  );
  assert.deepEqual(rendered.files.map((file) => file.height), [3000, 3000, 3000, 3000, 3000, 3000]);
  assert.equal(rendered.height, 18000);
});

test('인코딩이 실패해도 한 장과 나눠 받기 캔버스 버퍼를 모두 해제한다', async () => {
  for (const delivery of ['single', 'split']) {
    const created = [];
    const released = new Set();
    const createCanvas = (width, height) => {
      const canvas = fakeCanvas(width, height);
      created.push(canvas);
      return canvas;
    };
    const capture = createCanvas(860, 86);
    await assert.rejects(
      renderStorefrontFiles(
        [100],
        { preset: 'smartstore', format: 'png', delivery },
        {
          captureBlock: async () => ({ canvas: capture, softFailed: 0 }),
          measureBlock: (height) => height,
          createCanvas,
          encodeCanvas: async () => { throw new Error('encode failed'); },
          releaseCanvas: (canvas) => released.add(canvas),
        },
      ),
      /encode failed/,
    );
    assert.ok(created.every((canvas) => released.has(canvas)), `${delivery} 캔버스가 남았다`);
  }
});

test('exportFileName: 금지문자 제거·빈 값 기본, 캡처 폭 계약 고정', () => {
  assert.equal(exportFileName('아이보리 니트 셋업'), '아이보리 니트 셋업.png');
  assert.equal(exportFileName('a/b:c*d?e"f<g>h|i'), 'a b c d e f g h i.png');
  assert.equal(exportFileName(''), '상세페이지.png');
  assert.equal(exportFileName('니트', '블록01'), '니트_블록01.png');
  assert.equal(EXPORT_WIDTH, 1000); // .ed-canvas 설계 폭과 같아야 한다 (features.css .ed-canvas)
});


test('내보내기 캡처에서 빈 사진 자리는 빠진다 — 완성본에 회색 네모가 찍히면 안 된다', () => {
  const source = readFileSync(new URL('../../src/features/editor/editorExport.js', import.meta.url), 'utf8');
  const chrome = source.slice(source.indexOf('const CHROME_SELECTOR'), source.indexOf("].join(',')"));
  // 사진을 비워 둔 칸('＋ 여기에 사진 넣기')은 편집기 안내지 상품 페이지 내용이 아니다.
  assert.match(chrome, /'\.el-slot'/);
  assert.match(chrome, /'\.slot-add'/, '＋ 버튼도 함께 빠진다');
  // 실제로 제거하는 코드와 이어져 있어야 목록이 의미가 있다.
  assert.match(source, /clone\.querySelectorAll\(CHROME_SELECTOR\)\.forEach\(\(n\) => n\.remove\(\)\)/);
});

test('빈 텍스트 placeholder는 다운로드 DOM에서 제거되고 export 전용 높이를 쓴다', () => {
  const source = readFileSync(new URL('../../src/features/editor/editorExport.js', import.meta.url), 'utf8');
  const chrome = source.slice(source.indexOf('const CHROME_SELECTOR'), source.indexOf("].join(',')"));
  assert.match(chrome, /'\.el-empty-text'/, 'placeholder 없는 빈 텍스트도 결과물에서 빠져야 한다');
  assert.match(source, /clone\.dataset\.exportHeight/);
  assert.match(source, /clone\.style\.height = `\$\{exportHeight\}px`/);
  assert.match(editorSource, /data-export-height=\{getBlockRenderHeight\(block, \{ forExport: true \}\)\}/);
  assert.match(editorSource, /height: getBlockRenderHeight\(b, \{ forExport: true \}\)/,
    '전체화면 미리보기 높이도 다운로드와 같아야 한다');
  assert.equal(exportNodeHeight({
    offsetHeight: 826,
    style: { height: '726px' },
    dataset: { exportHeight: '660' },
  }), 760, '긴 PNG도 CSS padding은 유지하고 빈 캡션이 만든 66px만 뺀다');
});

test('내보내기 캡처는 블록 배경색을 덮어쓰지 않는다(backgroundColor 옵션 금지) — 흰 바탕은 합성으로', () => {
  const src = readFileSync(new URL('../../src/features/editor/editorExport.js', import.meta.url), 'utf8');
  const call = src.slice(src.indexOf('await toSvg(clone'), src.indexOf('await toSvg(clone') + 400);
  assert.doesNotMatch(call, /backgroundColor/, 'html-to-image backgroundColor 는 블록 배경을 흰색으로 덮는다');
  assert.match(src, /return \{ canvas, softFailed \}/);
});
