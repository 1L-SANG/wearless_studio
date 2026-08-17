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

   멱등: 확보한 projectId와 사진별 asset 매핑을 localStorage에 즉시 기록한다. 같은 페이지의
   재시도뿐 아니라 새로고침 뒤에도 같은 프로젝트·업로드에 합류한다.
   ============================================================= */
import { api } from '@/lib/api/index.js';
import { isUploadablePhotoMime } from '@/lib/imageTranscode.js';
import { withUploadedSrcs } from '@/lib/draftPromotionProduct.js';
import { createDraftSyncSingleFlight } from '@/lib/draftSyncSingleFlight.js';
import { draftPromotionSession } from '@/lib/draftPromotionSession.js';
import { promoteCustomMatch, stripLocalCustomMatch } from '@/lib/customMatchPromotion.js';

async function runDraftSync(draft, { projectId: existing } = {}) {
  // 서버 createProject 는 명시적 Idempotency-Key 를 지원하지 않는다. 프로젝트를 만든 즉시
  // localStorage 에 기록해 새로고침 뒤에도 같은 행으로 합류한다.
  const persisted = draftPromotionSession.read();
  const projectId = existing ?? persisted.projectId ?? (await api.createProject()).id;
  draftPromotionSession.rememberProject(projectId);

  try {
    // 성공한 사진별 asset 매핑도 즉시 기록한다. 중간 실패·새로고침 뒤 재시도는 이미 올라간
    // 사진을 재사용해 중복 R2 객체를 만들지 않는다.
    // 올릴 수 없는 mime 은 여기서 걸러 낸다. 그대로 보내면 서버가 400 으로 거부하고, 그
    // 메시지("지원하지 않는 이미지 형식입니다")가 CTA 토스트로 그대로 떠서 셀러는 자기가 올린
    // jpg 가 거부됐다고 읽는다 — 사진 한 장 때문에 확정 전체가 막히는 건 더 나쁘다.
    // (2026-08-17 사고. 상류에서 이미 걸러지지만 승격은 마지막 문지기라 여기도 본다.)
    const uploadable = (draft.photos ?? []).filter((p) => isUploadablePhotoMime(p?.mime));
    const unsupported = (draft.photos ?? []).length - uploadable.length;
    if (unsupported) {
      console.warn(`[promote] 업로드할 수 없는 사진 ${unsupported}장을 건너뜁니다.`);
    }
    const pairs = await Promise.all(
      uploadable.map(async (p) => {
        const cached = draftPromotionSession.read().assets?.[p.imageId];
        if (cached?.assetId && cached?.url) return [p.imageId, cached];
        const uploaded = await api.uploadPhoto(projectId, p);
        draftPromotionSession.rememberAsset(p.imageId, uploaded);
        return [p.imageId, uploaded];
      }),
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

    // draft 커스텀 매칭(내 옷)은 로컬 blob 아이템이라 analysis payload 로는 승격이 안 된다 —
    // payload 에선 걷어내고(죽은 objectURL 방지) 아래에서 실서버 등록으로 따로 승격한다.
    // 같은 탭이면 메모리 저장소가 정본이고, OAuth 리다이렉트로 돌아온 경우엔 그게 비어 있어
    // IndexedDB draft 에 실려 온 blob 이 유일한 소스다(2026-08-15 전수조사 — 비로그인 셀러의
    // 내 옷이 확정에서 조용히 사라지던 경로).
    const customDraft = api.getCustomMatchDraft?.() ?? draft.customMatch ?? null;
    if (analysis) analysis = stripLocalCustomMatch(analysis);

    await api.saveProduct(projectId, product);
    if (analysis) {
      await api.saveAnalysis(projectId, analysis);
    }
    await api.patchProject(projectId, {
      composeMode: draft.composeMode === 'extended' ? 'extended' : 'basic',
    });

    // 내 옷 승격 — blob 재업로드 → custom-match-item 등록(서버가 누끼 잡을 건다).
    // 결과를 위로 돌려준다: 셀러는 확정 직전까지 '내 옷' 카드를 보고 있었으므로, 등록만
    // 실패했다면 화면이 그 사실을 알려야 한다(확정 자체는 막지 않는다 — fail-open 유지).
    const customMatch = await promoteCustomMatch(api, projectId, customDraft);

    return { projectId, customMatch };
  } catch (err) {
    err.projectId = projectId; // 재시도 시 이 projectId로 호출 → 프로젝트 중복 방지
    throw err;
  }
}

// OAuth 복귀 effect와 입력 화면 재시도가 같은 탭에서 겹치면 같은 draft revision은 하나의
// 요청을 공유한다. 타임아웃 뒤 사용자가 draft를 고쳤다면 이전 요청이 끝난 다음 같은 projectId에
// 최신 revision을 다시 저장한다. 부분 실패 때 확보한 projectId도 보존해 재시도 create를 막는다.
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
  const reset = draftSyncFlight.reset();
  if (reset) draftPromotionSession.clear();
  return reset;
}

export function retryDraftPromotion(projectId) {
  return draftSyncFlight.retryFrom(projectId);
}
