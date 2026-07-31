/* =============================================================
   lib/imageTranscode — 아이폰 HEIC 사진을 업로드 가능한 JPEG 로 바꾼다.

   왜 클라이언트에서 하나: 업로드는 presigned PUT 으로 **R2 에 직행**해서 서버가 바이트를
   만지는 지점이 없다. 게다가 브라우저(크롬·파이어폭스)는 HEIC 를 디코드하지 못해
   미리보기 <img> 가 빈 칸으로 뜨고, 서버 화이트리스트(r2.MIME_EXT)도 image/heic 을 막는다.
   다운스트림(Pillow·Gemini)도 HEIC 를 못 읽으므로 어차피 어딘가에서 JPEG 로 바꿔야 하는데,
   가장 이른 지점에서 한 번 바꾸는 게 제일 싸다.

   호출부는 ProductInput(상품 사진)·ModelFaceUpload(얼굴) 두 곳이며, 변환 결과의 objectURL 을
   그대로 쓰면 미리보기·draft 저장·업로드가 전부 JPEG 로 흐른다(다운스트림이 objectURL 을
   fetch().blob() 으로 되살리는 구조).
   ============================================================= */

// 긴 변 상한. 마네킹 생성 출력이 2K(≈2500px)라 그 이상 넣어도 결과가 좋아지지 않는다.
// 서버는 업로드 원본을 리사이즈 없이 그대로 Gemini 로 보내므로(InlineImage=원본 바이트),
// 큰 사진은 생성 요청마다 비용·지연으로 전가된다. 4000px 는 2K 출력의 1.6배로 여유가 있다.
export const MAX_EDGE = 4000;
export const JPEG_QUALITY = 0.85;

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

/* 긴 변이 MAX_EDGE 를 넘으면 캔버스로 축소해 다시 JPEG 로 인코딩한다.
   createImageBitmap 은 EXIF 회전을 적용해준다(imageOrientation:'from-image') — 아이폰 사진이
   눕는 사고를 여기서 막는다. 축소가 필요 없으면 원본 blob 을 그대로 돌려준다. */
async function downscaleJpeg(blob) {
  let bitmap;
  try {
    bitmap = await createImageBitmap(blob, { imageOrientation: 'from-image' });
  } catch {
    return blob; // 디코드 불가 시 원본 유지 — 업로드 자체를 막지 않는다
  }
  const { width, height } = bitmap;
  const longest = Math.max(width, height);
  if (longest <= MAX_EDGE) { bitmap.close?.(); return blob; }
  const k = MAX_EDGE / longest;
  const w = Math.round(width * k), h = Math.round(height * k);
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  canvas.getContext('2d').drawImage(bitmap, 0, 0, w, h);
  bitmap.close?.();
  const out = await new Promise((res) => canvas.toBlob(res, 'image/jpeg', JPEG_QUALITY));
  return out || blob;
}

/* 파일 하나를 업로드 가능한 형태로 정규화한다. → File
   HEIC 가 아니면 축소만 검토하고, HEIC 면 JPEG 로 변환한 뒤 축소한다.
   변환 실패는 **그 파일만** 실패시킨다(throw) — 호출부가 나머지 사진은 계속 진행한다. */
export async function toUploadableImage(file) {
  const heic = await isHeic(file);
  if (!heic) {
    const shrunk = await downscaleJpeg(file);
    if (shrunk === file) return file;
    return new File([shrunk], renameExt(file.name, 'jpg'), { type: 'image/jpeg', lastModified: file.lastModified });
  }
  // libheif(wasm)는 무겁다 — HEIC 를 실제로 만났을 때만 동적 로드해 초기 번들에서 뺀다.
  const { heicTo } = await import('heic-to');
  const converted = await heicTo({ blob: file, type: 'image/jpeg', quality: JPEG_QUALITY });
  const shrunk = await downscaleJpeg(converted);
  return new File([shrunk], renameExt(file.name, 'jpg'), {
    type: 'image/jpeg', lastModified: file.lastModified,
  });
}

export function renameExt(name, ext) {
  const base = (name || 'photo').replace(/\.[^.]+$/, '');
  return `${base}.${ext}`;
}

/* 여러 장을 한 번에. 실패한 파일은 건너뛰고 이유를 함께 돌려준다 —
   한 장이 깨졌다고 나머지 선택까지 버리면 셀러가 처음부터 다시 골라야 한다. */
export async function toUploadableImages(files) {
  const ok = [], failed = [];
  for (const f of files) {
    try {
      ok.push(await toUploadableImage(f));
    } catch (e) {
      failed.push({ name: f?.name || '이미지', reason: e?.message || String(e) });
    }
  }
  return { files: ok, failed };
}
