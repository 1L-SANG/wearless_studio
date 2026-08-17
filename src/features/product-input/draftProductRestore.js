/* 저장된 draft(로컬 IndexedDB · 서버 임시저장)를 화면 상품으로 되살린다.

   되살릴 수 있는 사진만 남긴다. 판정은 두 가지다:
     ① photos[] 에 blob 이 있으면 새 objectURL 을 만들어 붙인다 — 정상 복원.
     ② blob 이 없으면 저장된 src 를 쓰되, **업로드된 자산 URL 일 때만** 인정한다.
        `blob:` 은 그 페이지에서만 살아 있던 주소라 이미 죽었고, `data:` 는 목 데모의 SVG
        플레이스홀더다. 둘 다 "정상 사진인 척" 복원되면 화면에는 깨진/데모 이미지가 뜨고,
        확정 단계에서 서버가 업로드를 거부해 진행이 막힌다(2026-08-17 사고: 분석 페이지에
        데모 실루엣이 보이고 CTA 가 "지원하지 않는 이미지 형식입니다"로 죽었다).

   순수 함수로 둔 이유: 이 판정이 곧 셀러가 사진을 잃는 경계라 화면 없이 테스트해야 한다. */

import { isPlaceholderPhotoSrc, isUploadablePhotoMime } from '../../lib/imageTranscode.js';

export function restoreDraftProduct(draft, { createObjectUrl } = {}) {
  if (!draft?.product) return null;
  const makeUrl = createObjectUrl || ((blob) => URL.createObjectURL(blob));
  const urlById = {};
  for (const photo of draft.photos || []) {
    // 저장된 mime 도 본다 — 배포 전 옛 번들은 데모 플레이스홀더(SVG)까지 blob 으로 담았고,
    // blob 을 objectURL 로 감싸면 src 판정만으로는 걸러지지 않는다(2026-08-17 사고).
    // mime 이 아예 없는 레코드는 판정 근거가 없으므로 버리지 않는다(정상 사진 유실 방지).
    if (photo.mime && !isUploadablePhotoMime(photo.mime)) continue;
    try { urlById[photo.imageId] = makeUrl(photo.blob); } catch { /* skip */ }
  }
  return {
    ...draft.product,
    colors: (draft.product.colors || []).map((color) => ({
      ...color,
      images: (color.images || []).flatMap((image) => {
        const restored = urlById[image.id];
        if (restored) return [{ ...image, src: restored }];
        // blob 이 없는 사진 — 서버 자산 참조만 그대로 쓸 수 있다. `blob:` 은 그 페이지에서만
        // 살아 있던 주소라 이미 죽었고, `data:` 는 데모 플레이스홀더다. src 가 아예 없는 항목은
        // 계약상 유효한 asset id 참조이므로 지우지 않는다.
        const dead = String(image.src || '').startsWith('blob:') || isPlaceholderPhotoSrc(image.src);
        return dead ? [] : [image];
      }),
    })),
  };
}
