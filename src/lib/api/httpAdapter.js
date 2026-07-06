/* =============================================================
   httpAdapter — FastAPI 실서버 구현 (plan §8, Phase 1+에서 함수
   단위로 채운다). 여기 구현된 함수만 http 모드에서 mock 을 대체하고,
   나머지는 mock 이 계속 담당한다 (부분 스왑).
   시그니처·반환 형태는 mock/api.js(계약 §6)와 동일해야 한다.
   ============================================================= */
import { supabase } from '@/lib/supabase.js';
import { DB } from '@/mock/db.js';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

// 공용 fetch 헬퍼 — Supabase 세션의 access_token 을 Bearer 로 주입 (plan §9).
// 에러 봉투 { error: { code, message } } 의 한국어 message 를 그대로 throw (계약 §6).
export async function http(path, { method = 'GET', body } = {}) {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!res.ok) {
    // 계약 §6: 사용자에게 그대로 보여줄 한국어 message. envelope 없으면 한국어 기본값.
    let message = '요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.';
    try {
      const payload = await res.json();
      if (payload?.error?.message) message = payload.error.message;
    } catch { /* 비 JSON 응답 — 기본 메시지 유지 */ }
    console.error(`API ${res.status} ${path}`); // 기술 세부는 콘솔로만
    throw new Error(message);
  }
  return res.json();
}

// job 폴링 어댑터 — job형 API(202 {jobId})를 mock 의 onProgress 콜백 계약으로 변환.
// GET /v1/jobs/{id} 를 폴링해 progress 를 전달하고, done 이면 result, error 면 한국어 message throw.
// SSE 대신 폴링(마네킹 경로와 동일 GET 재사용, plan §7). 무과금 분석엔 stall 로직 불필요.
async function pollJob(jobId, { onProgress, intervalMs = 1200, timeoutMs = 90000 } = {}) {
  const start = Date.now();
  let last = -1;
  for (;;) {
    const job = await http(`/v1/jobs/${jobId}`);
    if (typeof job.progress === 'number' && job.progress !== last) {
      last = job.progress;
      onProgress && onProgress(job.progress);
    }
    if (job.status === 'done') { onProgress && onProgress(100); return job.result; }
    if (job.status === 'error') throw new Error(job.errorMessage || '작업에 실패했어요.');
    if (Date.now() - start > timeoutMs) throw new Error('분석이 지연되고 있어요. 잠시 후 다시 시도해 주세요.');
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// 사진 1장 업로드 — presigned URL 3콜(발급→R2 PUT→complete). {assetId, url} 반환.
// 로그인 후 draft 동기화(draftSync)도 이 함수를 재사용 (단일 업로드 계약, 서버 §3).
// 서명 PUT은 Bearer 안 씀(서명 자체가 인증).
export async function uploadPhoto(projectId, { filename, mime, blob }) {
  const { assetId, uploadUrl } = await http('/v1/assets/upload-url', {
    method: 'POST', body: { filename, mime, size: blob.size, projectId },
  });
  const put = await fetch(uploadUrl, { method: 'PUT', headers: { 'Content-Type': mime }, body: blob });
  if (!put.ok) throw new Error('사진 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
  const asset = await http(`/v1/assets/${assetId}/complete`, {
    method: 'POST', body: { projectId, mime, filename },
  });
  return { assetId, url: asset.url };
}

export const httpAdapter = {
  // 상품 사진(blob)을 R2에 업로드하고 images[].id 를 **서버 asset id 로 치환**한다.
  // 서버(mannequin.base_color_images·분석 워커)는 colors[].images[].id 를 asset id 로 링크하므로,
  // 로컬 uid('img') 를 그대로 두면 서버가 사진을 못 찾는다(no_product_images). src 도 R2 URL 로 갱신.
  // 이미 업로드된(blob: 아님) 이미지는 건너뛴다. projectId 없으면(비로그인 공개) 업로드 불가 → 그대로 반환.
  async uploadProductPhotos(projectId, product) {
    if (!projectId) return product;
    const colors = await Promise.all((product.colors ?? []).map(async (c) => {
      const images = await Promise.all((c.images ?? []).map(async (im) => {
        if (!im.src || !im.src.startsWith('blob:')) return im;
        const blob = await fetch(im.src).then((r) => r.blob());
        // im.type 은 파일 감지 실패 시 'image'(잘못된 MIME)일 수 있다(filesToMetas 폴백) —
        // '/' 가 있는 진짜 MIME 일 때만 쓰고, 아니면 blob.type / jpeg 로. (upload-url 400 방지)
        const mime = (im.type && im.type.includes('/')) ? im.type : (blob.type || 'image/jpeg');
        const { assetId, url } = await uploadPhoto(projectId, {
          filename: im.name || 'photo', mime, blob,
        });
        return { ...im, id: assetId, src: url };
      }));
      return { ...c, images };
    }));
    return { ...product, colors };
  },
  async saveProduct(projectId, patch) {
    const row = await http(`/v1/projects/${projectId}/product`, { method: 'PATCH', body: patch });
    // 부분 스왑 브릿지(codex 리뷰 P2): getProduct·마네킹·콘티·에디터는 아직 mock 폴백이라
    // DB.product 를 읽는다. 실 saveProduct 가 mock 미러를 안 건드리면 하위 단계가 seed 를 읽으므로,
    // 그 단계들이 http 로 함께 스왑되기 전까지 mock 미러도 같은 patch 로 갱신한다.
    Object.assign(DB.product, patch);
    if (patch.name != null) DB.project.title = patch.name;
    return row;
  },
  // AG-01 상품 분석 — POST /analyze(job) → 폴링 → analysis payload.
  // 반환 shape 은 mock(계약 §6)과 **동일해야 한다** — AnalysisForm 이 a.models/.matchClothing/
  // .sellingPoints 등을 무가드로 읽으므로. 부분 스왑 단계라 models·matchClothing·selectedModelId·
  // washCare 등은 아직 mock 소유 → mock 분석 shape 를 베이스로 AI 산출 필드만 덮어써 shape 를 보존한다.
  async analyzeProduct(projectId, { onProgress } = {}) {
    const { jobId } = await http(`/v1/projects/${projectId}/analyze`, { method: 'POST' });
    // 폴링 상한은 provider 순차 폴백 최악경로(2 × analysis_timeout_seconds=60s = 120s)보다 넉넉히
    // 잡는다 — 짧으면 정상 폴백(gpt→gemini)이 완료 전에 실패 토스트가 뜨는데 job은 뒤늦게 성공한다.
    const result = await pollJob(jobId, { onProgress, timeoutMs: 180000 });
    const ai = (result && result.data) || {};
    const base = JSON.parse(JSON.stringify(DB.analysis));  // 전체 shape(models·matchClothing·… 포함)
    const merged = {
      ...base,
      clothingType: ai.clothingType ?? null,
      subCategory: ai.subCategory ?? null,
      targetGenders: ai.targetGenders ?? [],
      fit: ai.fit ?? null,
      materials: ai.materials ?? [],
      aiSuggestedPoints: ai.aiSuggestedPoints ?? [],
      suggestedName: ai.suggestedName ?? base.suggestedName,
      styleTags: ai.styleTags ?? [],
      swatchSuggestions: ai.swatchSuggestions ?? [],
      sellingPoints: [],  // 셀러는 빈 상태로 시작 — AI 제안(aiSuggestedPoints)은 폼이 자동으로 채운다
    };
    // 실측은 AI 미산출 → 값 비움(사용자 직접 입력, PRD §6.5). mock analyzeProduct 와 동일 처리.
    merged.measurements = (base.measurements || []).map((m) => ({ ...m, value: null }));
    return merged;
  },
  // ---- 상세페이지 (PL-4) — 콘티·에디터는 서버 소유. detail_page job 이 저장 콘티를 읽는다 ----
  async getStoryboard(projectId) {
    return http(`/v1/projects/${projectId}/storyboard`);
  },
  async saveStoryboard(projectId, blocks) {
    const out = await http(`/v1/projects/${projectId}/storyboard`, { method: 'PUT', body: blocks });
    DB.storyboard = blocks;  // mock 미러(부분 스왑 브릿지 — mock 폴백 읽기 정합)
    return out;
  },
  async getEditorBlocks(projectId) {
    return http(`/v1/projects/${projectId}/editor-blocks`);
  },
  async saveEditorBlocks(projectId, blocks) {
    await http(`/v1/projects/${projectId}/editor-blocks`, { method: 'PUT', body: blocks });
    DB.editorBlocks = blocks;
  },
  // AG-06 컷 + AG-02/03 카피 → M-02 조립. 완료 재호출은 서버가 기존 결과 반환(무차감).
  async generateDetailPage(projectId, { onProgress } = {}) {
    const res = await http(`/v1/projects/${projectId}/detail-page:generate`, { method: 'POST' });
    if (res.data) return { data: res.data, credits: res.credits };  // 완료 재호출(202 아님)
    const result = await pollJob(res.jobId, { onProgress, timeoutMs: 300000 });
    DB.editorBlocks = result.data; DB.project.status = 'done';  // mock 미러
    return { data: result.data, credits: result.credits };
  },
  // Phase 1-B 읽기·CRUD 스왑 (계약 §6 시그니처 동일). 미구현 함수는 mock 폴백.
  // getProject 는 store 가 projectId 없이 호출(api.getProject()) → 시그니처 정리 후
  // 플로우 단계에서 스왑. 지금 스왑하면 깨지므로 mock 유지.
  async getAccount() {
    return http('/v1/me/account');
  },
  async getLibrary() {
    // mock 의 { forceEmpty, forceError } 옵션은 실서버에선 무의미 — 무시.
    return http('/v1/projects?view=library');
  },
  async createProject() {
    return http('/v1/projects', { method: 'POST' });
  },
  async patchProject(projectId, patch) {
    return http(`/v1/projects/${projectId}`, { method: 'PATCH', body: patch });
  },
  // 크레딧 표시 페이지 (계약 §6) — 조회 전용. 구매·환불 UI는 PG 단계.
  async getPricingPlans() {
    return http('/v1/pricing-plans');
  },
  async getCreditHistory() {
    return http('/v1/credits/history');
  },
  async getCreditSources() {
    return http('/v1/credits/sources');
  },
  // 마네킹 등 job형 플로우(generate→adjust→regenerate)는 같은 컷 상태를 공유하므로
  // 부분 스왑 금지 — 백엔드가 generate+adjust+regenerate를 다 갖추고 draft sync(A-3)가
  // 돼야 통째로 swap. 그 전까지 마네킹은 mock 유지(혼합 시 http 모드 깨짐).
};
