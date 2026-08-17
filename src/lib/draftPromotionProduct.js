/* 확정 승격이 서버에 저장할 상품을 만든다 (draftSync 의 순수 부분).

   업로드 결과로 `images[].id` 를 **서버 asset id 로 치환**한다 — 서버(mannequin.base_color_images·
   분석 워커·컷·상세페이지)가 사진을 asset id 로 링크하므로 로컬 uid 를 남기면 사진을 못 찾는다.

   그리고 **올라가지 못한 로컬 사진은 상품에서 뺀다**. 남겨 두면 서버 product 에 브라우저 전용
   주소(blob:)나 데모 플레이스홀더(data:)가 asset id 인 척 저장되고, 이후 생성이 그 id 로 자산을
   찾다 실패한다(no_product_images). 이미 서버에 있는 사진(자산 URL)과 asset id 만 있는 참조는
   그대로 둔다 — 계약상 유효하고, 지우면 사진이 사라진다. (2026-08-17 사고) */

import { isPlaceholderPhotoSrc } from './imageTranscode.js';

const isLocalOnly = (src) => {
  const value = String(src || '');
  return value.startsWith('blob:') || isPlaceholderPhotoSrc(value);
};

export function withUploadedSrcs(product, uploadByImageId) {
  return {
    ...product,
    colors: (product.colors ?? []).map((c) => ({
      ...c,
      images: (c.images ?? []).flatMap((im) => {
        const up = uploadByImageId[im.id];
        if (up) return [{ ...im, id: up.assetId, src: up.url }];
        return isLocalOnly(im.src) ? [] : [im];
      }),
    })),
  };
}
