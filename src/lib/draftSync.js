/* =============================================================
   draftSync — 비로그인 공개 입력 → 로그인 후 실서버 동기화 (Phase 2, 해결책 A).

   로그인 에이전트가 OAuth 리다이렉트 직전 IndexedDB에 저장한 draft(상품 정보 +
   사진 blob)를, 로그인 복귀 후 복원해 이 함수에 넘긴다. 여기서 프로젝트 생성 +
   사진 R2 업로드 + 상품 저장을 묶어 실행하고 projectId를 반환한다 (백엔드 §3·§4).

   draft = {
     product,   // 상품 작업본. colors[].images[].src 는 임시(죽은 objectURL) — 업로드 후 R2 URL로 치환됨
     photos: [{ imageId, colorId, slot, blob, mime, filename }]
   }

   토큰은 http 헬퍼가 supabase 세션에서 주입한다 — **반드시 로그인 후 호출**할 것.
   주의(MVP 한계): 부분 실패(프로젝트 생성 후 업로드 실패) 시 재호출하면 빈 프로젝트가
   하나 더 생긴다. 호출측은 실패 시 draft를 유지하고 사용자에게 재시도를 안내한다.
   ============================================================= */
import { http } from '@/lib/api/httpAdapter.js';

async function uploadPhoto(projectId, photo) {
  const { assetId, uploadUrl } = await http('/v1/assets/upload-url', {
    method: 'POST',
    body: { filename: photo.filename, mime: photo.mime, size: photo.blob.size, projectId },
  });
  // presigned URL로 R2에 직접 PUT — 서명 자체가 인증이라 http 헬퍼(Bearer) 안 씀.
  // ContentType은 upload-url 발급 때 서명된 값과 동일해야 한다.
  const put = await fetch(uploadUrl, {
    method: 'PUT',
    headers: { 'Content-Type': photo.mime },
    body: photo.blob,
  });
  if (!put.ok) throw new Error('사진 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');

  const asset = await http(`/v1/assets/${assetId}/complete`, {
    method: 'POST',
    body: { projectId, mime: photo.mime, filename: photo.filename },
  });
  return asset.url; // R2 서빙 URL (images.wearless.kr/...)
}

// product.colors[].images[].src 를 업로드된 R2 URL로 치환 (id 매칭).
function withUploadedSrcs(product, urlByImageId) {
  return {
    ...product,
    colors: (product.colors ?? []).map((c) => ({
      ...c,
      images: (c.images ?? []).map((im) => ({ ...im, src: urlByImageId[im.id] ?? im.src })),
    })),
  };
}

export async function syncDraftToBackend(draft) {
  const project = await http('/v1/projects', { method: 'POST' });
  const projectId = project.id;

  const urlByImageId = {};
  for (const photo of draft.photos ?? []) {
    urlByImageId[photo.imageId] = await uploadPhoto(projectId, photo);
  }

  const product = withUploadedSrcs(draft.product ?? {}, urlByImageId);
  await http(`/v1/projects/${projectId}/product`, { method: 'PATCH', body: product });

  return { projectId };
}
