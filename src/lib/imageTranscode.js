/* =============================================================
   lib/imageTranscode — 아이폰 HEIC 사진을 업로드 가능한 JPEG 로 바꾼다.

   왜 클라이언트에서 하나: 업로드는 presigned PUT 으로 **R2 에 직행**해서 서버가 바이트를
   만지는 지점이 없다. 게다가 브라우저(크롬·파이어폭스)는 HEIC 를 디코드하지 못해
   미리보기 <img> 가 빈 칸으로 뜨고, 서버 화이트리스트(r2.MIME_EXT)도 image/heic 을 막는다.
   다운스트림(Pillow·Gemini)도 HEIC 를 못 읽으므로 어딘가에서 JPEG 로 바꿔야 하는데,
   가장 이른 지점에서 한 번 바꾸는 게 제일 싸다.

   호출부는 ProductInput(상품 사진)·ModelFaceUpload(얼굴)·커스텀 매칭 업로드이며, 변환 결과의 objectURL 을
   그대로 쓰면 미리보기·draft 저장·업로드가 전부 JPEG 로 흐른다(다운스트림이 objectURL 을
   fetch().blob() 으로 되살리는 구조).
   ============================================================= */

// 긴 변 상한. 마네킹 생성 출력이 2K(≈2500px)라 그 이상 넣어도 결과가 좋아지지 않는다.
// 서버는 업로드 원본을 리사이즈 없이 그대로 Gemini 로 보내므로(InlineImage=원본 바이트),
// 큰 사진은 생성 요청마다 비용·지연으로 전가된다. 4000px 는 2K 출력의 1.6배로 여유가 있다.
export const MAX_EDGE = 4000;
export const JPEG_QUALITY = 0.85;
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export const UPLOAD_IMAGE_MIME_TYPES = new Set([
  'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/avif',
]);
const IMAGE_PIPELINE_TIMEOUT_MS = 10000;

const IMAGE_EXT = /\.(jpe?g|png|webp|gif|avif|heic|heif|hif)$/i;

// iOS/일부 브라우저는 HEIC File.type 을 비워서 준다. 선택 단계에서는 확장자까지 보고,
// 실제 HEIC 여부는 아래 매직바이트 검사로 확정한다.
export const looksLikeImageFile = (file) => (
  file?.type ? file.type.startsWith('image/') : IMAGE_EXT.test(file?.name || '')
);

// 서버의 presigned upload 문지기(r2.MIME_EXT/routes.MAX_UPLOAD_BYTES)와 같은 규칙.
// 반드시 HEIC 변환·축소가 끝난 File 에 적용한다.
export const getUploadValidationError = (file) => {
  if (!UPLOAD_IMAGE_MIME_TYPES.has((file?.type || '').toLowerCase())) return 'unsupported_type';
  if (!file?.size || file.size > MAX_UPLOAD_BYTES) return 'file_too_large';
  return null;
};

/* HEIC 판별은 **확장자·MIME 이 아니라 매직바이트**로 한다.
   iOS/일부 브라우저가 File.type 을 빈 문자열로 주고, AirDrop·iCloud 로 받은 파일은 확장자가
   .HEIC/.heif/.hif 로 제각각이다. ISO-BMFF 박스의 ftyp 브랜드를 직접 본다. */
const HEIC_BRANDS = new Set([
  'heic', 'heix', 'heim', 'heis', 'hevc', 'hevx', 'hevm', 'hevs', 'mif1', 'msf1', 'heif',
]);

export async function isHeic(file) {
  try {
    const head = new Uint8Array(await file.slice(0, 16).arrayBuffer());
    if (head.length < 12) return false;
    // 4..8 = 'ftyp', 8..12 = major brand
    const tag = String.fromCharCode(head[4], head[5], head[6], head[7]);
    if (tag !== 'ftyp') return false;
    const brand = String.fromCharCode(head[8], head[9], head[10], head[11]).toLowerCase();
    return HEIC_BRANDS.has(brand);
  } catch {
    return false; // 읽기 실패는 "HEIC 아님"으로 — 기존 경로(그대로 업로드)를 막지 않는다
  }
}

export function scaledImageDimensions(width, height, { maxEdge = MAX_EDGE, minEdge = 0 } = {}) {
  const longest = Math.max(width, height);
  const shortest = Math.min(width, height);
  if (longest <= maxEdge || longest <= 0 || shortest <= 0) return { width, height };

  // 커스텀 매칭처럼 다운스트림 최소변 게이트가 있으면, 축소 때문에 통과 원본이 400px 밑으로
  // 내려가지 않도록 긴 변 상한보다 최소변을 우선한다(극단적인 파노라마는 1600px를 넘을 수 있음).
  const scale = Math.min(1, Math.max(maxEdge / longest, minEdge / shortest));
  return {
    width: Math.round(width * scale),
    height: Math.round(height * scale),
  };
}

async function withTimeout(promise, { ms, message } = {}) {
  if (!ms || ms <= 0) return promise;
  let timeoutId;
  const timeout = new Promise((_, reject) => {
    timeoutId = setTimeout(() => {
      reject(new Error(message || `이미지 처리에 ${ms}ms가 초과됐습니다.`));
    }, ms);
  });
  try {
    return await Promise.race([promise.finally(() => clearTimeout(timeoutId)), timeout]);
  } finally {
    clearTimeout(timeoutId);
  }
}

async function bitmapToJpeg(bitmap, options) {
  const { jpegQuality = JPEG_QUALITY, timeoutMs = IMAGE_PIPELINE_TIMEOUT_MS } = options || {};
  const size = scaledImageDimensions(bitmap.width, bitmap.height, options);
  const canvas = document.createElement('canvas');
  canvas.width = size.width;
  canvas.height = size.height;
  const context = canvas.getContext('2d');
  if (!context) {
    throw new Error('이미지 처리용 캔버스 컨텍스트를 만들지 못했어요.');
  }
  context.drawImage(bitmap, 0, 0, size.width, size.height);
  bitmap.close?.();
  return withTimeout(new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('이미지를 JPEG로 인코딩하지 못했어요.'));
    }, 'image/jpeg', jpegQuality);
  }), {
    ms: timeoutMs,
    message: `이미지 인코딩이 ${timeoutMs}ms 내에 끝나지 않았습니다.`,
  });
}

/* 긴 변이 maxEdge 를 넘으면 캔버스로 축소해 다시 JPEG 로 인코딩한다.
   createImageBitmap 은 EXIF 회전을 적용해준다(imageOrientation:'from-image') — 아이폰 사진이
   눕는 사고를 여기서 막는다. 축소/재인코딩이 필요 없으면 원본 blob 을 그대로 돌려준다. */
async function downscaleJpeg(blob, options) {
  let bitmap;
  const timeoutMs = options?.timeoutMs ?? IMAGE_PIPELINE_TIMEOUT_MS;
  try {
    bitmap = await withTimeout(createImageBitmap(blob, { imageOrientation: 'from-image' }), {
      ms: timeoutMs,
      message: `이미지를 디코드하는 데 ${timeoutMs}ms가 초과됐습니다.`,
    });
  } catch (error) {
    if (String(error?.message || '').includes('초과')) {
      throw error;
    }
    return blob; // 디코드 불가 시 원본 유지 — 업로드 자체를 막지 않는다
  }
  const size = scaledImageDimensions(bitmap.width, bitmap.height, options);
  if (!options.forceJpeg && size.width === bitmap.width && size.height === bitmap.height) {
    bitmap.close?.();
    return blob;
  }
  const out = await bitmapToJpeg(bitmap, options);
  return out || blob;
}

/* 파일 하나를 업로드 가능한 형태로 정규화한다. → File
   HEIC 가 아니면 축소/재인코딩을 검토하고, HEIC 면 bitmap에서 목표 JPEG를 바로 만든다.
   변환 실패는 **그 파일만** 실패시킨다(throw) — 호출부가 나머지 사진은 계속 진행한다. */
export async function toUploadableImage(file, options = {}) {
  const heic = await isHeic(file);
  if (!heic) {
    const shrunk = await downscaleJpeg(file, options);
    if (shrunk === file) return file;
    return new File([shrunk], renameExt(file.name, 'jpg'), { type: 'image/jpeg', lastModified: file.lastModified });
  }
  // libheif(wasm)는 무겁다 — HEIC 를 실제로 만났을 때만 동적 로드해 초기 번들에서 뺀다.
  const importTimeout = options?.timeoutMs ?? IMAGE_PIPELINE_TIMEOUT_MS;
  const { heicTo } = await withTimeout(import('heic-to'), {
    ms: importTimeout,
    message: 'HEIC 디코딩 모듈을 불러오지 못해 진행을 멈췄어요.',
  });
  // bitmap으로 한 번만 디코드한 뒤 목표 크기 JPEG를 바로 만든다. 전체 크기 JPEG 중간본을
  // 만들고 다시 디코드·재인코딩하던 기존 2단계를 피한다.
  const bitmap = await withTimeout(heicTo({
    blob: file, type: 'bitmap', options: { imageOrientation: 'from-image' },
  }), {
    ms: importTimeout,
    message: 'HEIC 디코딩이 너무 오래 걸려 진행을 멈췄어요.',
  });
  const converted = await bitmapToJpeg(bitmap, options);
  if (!converted) throw new Error('HEIC 사진을 JPEG로 바꾸지 못했어요.');
  return new File([converted], renameExt(file.name, 'jpg'), {
    type: 'image/jpeg', lastModified: file.lastModified,
  });
}

export function renameExt(name, ext) {
  const base = (name || 'photo').replace(/\.[^.]+$/, '');
  return `${base}.${ext}`;
}

/* 여러 장을 한 번에. 실패한 파일은 건너뛰고 이유를 함께 돌려준다 —
   한 장이 깨졌다고 나머지 선택까지 버리면 셀러가 처음부터 다시 골라야 한다. */
export async function toUploadableImages(files, options = {}, { signal } = {}) {
  const ok = [], failed = [];
  for (const f of files) {
    if (signal?.aborted) break;
    let lastError;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const converted = await toUploadableImage(f, options);
        if (signal?.aborted) break;
        ok.push(converted);
        lastError = null;
        break;
      } catch (e) {
        lastError = e;
        if (signal?.aborted) break;
      }
    }
    if (signal?.aborted) break;
    if (lastError) {
      console.warn('이미지 변환을 두 번 시도했지만 실패했습니다.', {
        name: f?.name || '이미지', error: lastError,
      });
      failed.push({ name: f?.name || '이미지', reason: lastError?.message || String(lastError) });
    }
  }
  return { files: ok, failed };
}
