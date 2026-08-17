/* 비로그인(게스트) 입력 흐름이 mock 어댑터에서 받는 상품 템플릿을 정리한다.

   http 모드에서도 서버 projectId 가 없는 동안은 상품 조회·저장이 mock 어댑터로 간다
   (api/index.js PUBLIC_INPUT — 입력·분석은 로그인 없이 공개하는 제품 결정). mock 은 데모용
   시드 상품(골지 니트)을 들고 있고 그 사진은 SVG 플레이스홀더(data:image/svg+xml)다. 그 사진이
   "서버에 저장된 상품 사진" 자리로 들어오면 실제 셀러 사진과 구분되지 않은 채

     저장 패치 → 임시저장 페이로드 → 복원된 화면의 상품 사진 → 확정 업로드(서버 400)

   까지 흘러간다. 2026-08-17 사고가 정확히 그 경로였다: 분석 페이지 상단 요약에 데모 실루엣이
   보이고, 콘티보드로 넘어가는 CTA 가 "지원하지 않는 이미지 형식입니다"(서버가 거부한
   image/svg+xml)로 막혔다 — 셀러는 jpg 를 올렸는데도.

   자를 대상은 **올릴 수 없는 src 를 가진 사진**뿐이다. 실제 사진(blob:·업로드된 자산 URL)은
   그대로 둔다 — 사진이 사라지는 쪽이 더 큰 사고다. 색상 메타(기준색·스와치)는 템플릿의
   쓸모이므로 항상 남는다. */

import { isPlaceholderPhotoSrc } from '../imageTranscode.js';

export function withoutDemoPhotos(product) {
  const colors = product?.colors;
  if (!Array.isArray(colors)) return product;
  let changed = false;
  const next = colors.map((color) => {
    const images = color?.images;
    if (!Array.isArray(images) || !images.length) return color;
    const kept = images.filter((image) => !isPlaceholderPhotoSrc(image?.src));
    if (kept.length === images.length) return color;
    changed = true;
    return { ...color, images: kept };
  });
  return changed ? { ...product, colors: next } : product;
}
