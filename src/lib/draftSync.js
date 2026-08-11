/* =============================================================
   draftSync — 비로그인 공개 입력 → 로그인 후 실서버 동기화 (Phase 2, 해결책 A).

   로그인 에이전트가 OAuth 리다이렉트 직전 IndexedDB에 저장한 draft(상품 정보 +
   사진 blob)를, 로그인 복귀 후 복원해 이 함수에 넘긴다. 여기서 프로젝트 생성 +
   사진 R2 업로드 + 상품 저장을 묶어 실행하고 projectId를 반환한다 (백엔드 §3·§4).

   draft = {
     product,    // 상품 작업본. colors[].images[].src 는 임시(죽은 objectURL) — 업로드 후 R2 URL로 치환됨
     analysis,   // 분석 작업본 (있으면 백엔드 저장). 없으면 생략
     photos: [{ imageId, colorId, slot, blob, mime, filename }]
   }

   토큰은 http 헬퍼가 supabase 세션에서 주입한다 — **반드시 로그인 후 호출**할 것.

   멱등: 부분 실패 시 던지는 에러에 `err.projectId`를 부착한다. 호출측은 재시도 시
   `syncDraftToBackend(draft, { projectId: err.projectId })`로 호출하면 프로젝트가 중복
   생성되지 않는다(이미 올라간 사진은 재업로드되어 일부 orphan asset이 생길 수 있음 — 허용).
   ============================================================= */
import { api } from '@/lib/api/index.js';
import { createDraftSyncSingleFlight } from '@/lib/draftSyncSingleFlight.js';

// product.colors[].images[] 의 id·src 를 업로드 결과로 치환 (원본 imageId 매칭).
// **id 를 서버 asset id 로 바꾼다** — 서버(mannequin.base_color_images·분석 워커)가 이미지를
// asset id 로 링크하므로, 로컬 uid 를 남기면 사진을 못 찾는다. src 는 R2 서빙 URL.
function withUploadedSrcs(product, uploadByImageId) {
  return {
    ...product,
    colors: (product.colors ?? []).map((c) => ({
      ...c,
      images: (c.images ?? []).map((im) => {
        const up = uploadByImageId[im.id];
        return up ? { ...im, id: up.assetId, src: up.url } : im;
      }),
    })),
  };
}

async function runDraftSync(draft, { projectId: existing } = {}) {
  // 멱등: 재시도 시 호출측이 넘긴 기존 projectId 재사용(없으면 새로 생성).
  const projectId = existing ?? (await api.createProject()).id;

  try {
    // 사진 병렬 업로드 (사진당 3콜 순차 → 동시 — 로그인→마네킹 지연 완화).
    const pairs = await Promise.all(
      (draft.photos ?? []).map(async (p) => [p.imageId, await api.uploadPhoto(projectId, p)]),
    );
    const uploadByImageId = Object.fromEntries(pairs);  // imageId -> {assetId, url}

    const product = withUploadedSrcs(draft.product ?? {}, uploadByImageId);

    // 계약 §3.2/TODO §1: clothingType·measurements·measurementsUnknown 는 Product 단일 소유.
    // 분석 폼이 이들을 analysis 작업본에 둘 수 있으니(과도기) → product 로 미러(현재값 반영)하고
    // analysis payload 에선 제거한다(analysis 에 stale product 상태가 박히는 것 방지).
    let analysis = draft.analysis;
    if (analysis) {
      analysis = { ...analysis };
      for (const k of ['clothingType', 'measurements', 'measurementsUnknown']) {
        if (analysis[k] != null) product[k] = analysis[k];
        delete analysis[k];
      }
    }

    await api.saveProduct(projectId, product);
    if (analysis) {
      await api.saveAnalysis(projectId, analysis);
    }
    await api.patchProject(projectId, {
      composeMode: draft.composeMode === 'extended' ? 'extended' : 'basic',
    });

    return { projectId };
  } catch (err) {
    err.projectId = projectId; // 재시도 시 이 projectId로 호출 → 프로젝트 중복 방지
    throw err;
  }
}

// OAuth 복귀 effect와 입력 화면 재시도가 같은 탭에서 겹쳐도 하나의 요청만 실행한다.
// 타임아웃은 호출측의 기다림만 끝낼 뿐 이 Promise는 계속 살아 있으며, 성공 결과도 다음
// 호출이 재사용한다. 부분 실패 때 확보한 projectId 역시 보존해 재시도 create를 막는다.
const draftSyncFlight = createDraftSyncSingleFlight(runDraftSync);

export function syncDraftToBackend(draft, options) {
  return draftSyncFlight.sync(draft, options);
}

// 로그인 사용자와 로그인 복귀 게스트가 같은 승격 경로를 공유한다. 기존 이름은 하위호환으로
// 유지하고 새 호출부는 제품 결정의 용어(확정 시 승격)를 드러내는 이름을 쓴다.
export function promoteDraftToProject(draft, options) {
  return draftSyncFlight.sync(draft, options);
}

export function resetDraftSyncSingleFlight() {
  return draftSyncFlight.reset();
}

export function retryDraftPromotion(projectId) {
  return draftSyncFlight.retryFrom(projectId);
}
