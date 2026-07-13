/* =============================================================
   httpAdapter — FastAPI 실서버 구현 (plan §8, Phase 1+에서 함수
   단위로 채운다). 여기 구현된 함수만 http 모드에서 mock 을 대체하고,
   나머지는 mock 이 계속 담당한다 (부분 스왑).
   시그니처·반환 형태는 mock/api.js(계약 §6)와 동일해야 한다.
   ============================================================= */
import { supabase } from '@/lib/supabase.js';
import { DB } from '@/mock/db.js';
import { LIMITS } from '@/lib/limits.js';
import { recommendLegacyMatchClothing } from '@/mock/matchingRecommendation.js';
import { defaultAnalysisShape, defaultStoryboard } from '@/lib/api/shapes.js';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';
const LONG_IMAGE_JOB_TIMEOUT_MS = 15 * 60 * 1000;
const DEFAULT_JOB_TIMEOUT_MESSAGE = '작업이 지연되고 있어요. 잠시 후 다시 시도해 주세요.';
const MANNEQUIN_JOB_TIMEOUT_MESSAGE = '마네킹컷 생성이 예상보다 오래 걸리고 있어요. 잠시 후 다시 확인해 주세요.';
const MANNEQUIN_ADJUST_JOB_TIMEOUT_MESSAGE = '마네킹컷 조정이 예상보다 오래 걸리고 있어요. 잠시 후 다시 확인해 주세요.';

// 서버는 에셋 이미지를 안정 앱 URL `/v1/assets/{id}/file`(상대경로)로 반환한다. 프론트는 다른
// 도메인(Vercel)에서 서빙되므로 <img src> 가 그대로 쓰면 프론트 도메인에 붙어 404 가 난다.
// 모든 응답이 지나는 http() 초크포인트에서 재귀로 절대화한다(API 도메인 프리픽스).
// vary 요청이 src 를 서버로 되돌려보내도 워커의 _ASSET_FILE_RE 는 search(비앵커)라 절대 URL 도 파싱된다.
function absolutizeAssetUrls(v) {
  if (typeof v === 'string') {
    return v.startsWith('/v1/assets/') ? `${BASE_URL}${v}` : v;
  }
  if (Array.isArray(v)) return v.map(absolutizeAssetUrls);
  if (v && typeof v === 'object') {
    const out = {};
    for (const k of Object.keys(v)) out[k] = absolutizeAssetUrls(v[k]);
    return out;
  }
  return v;
}

// 공용 fetch 헬퍼 — Supabase 세션의 access_token 을 Bearer 로 주입 (plan §9).
// 에러 봉투 { error: { code, message } } 의 한국어 message 를 그대로 throw (계약 §6).
export async function http(path, { method = 'GET', body } = {}) {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) {
    // http 모드에 mock 폴백은 없다 — 무세션이면 전 호출이 401 폭탄이 되므로 요청 전에 명확히 실패시킨다.
    console.error(`API no-session ${path}`);
    throw new Error('로그인이 필요해요. 로그인 후 다시 시도해 주세요.');
  }

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
    let code;
    try {
      const payload = await res.json();
      if (payload?.error?.message) message = payload.error.message;
      if (payload?.error?.code) code = payload.error.code;
    } catch { /* 비 JSON 응답 — 기본 메시지 유지 */ }
    console.error(`API ${res.status} ${path}`); // 기술 세부는 콘솔로만
    // status·code 를 에러에 실어 호출부가 분기할 수 있게 한다(예: 409 라이선스 차단 → 블로킹 패널).
    // message 는 그대로라 기존 catch(e.message) 는 영향 없음(하위호환).
    const err = new Error(message);
    err.status = res.status;
    if (code) err.code = code;
    throw err;
  }
  return absolutizeAssetUrls(await res.json());
}

// job 폴링 어댑터 — job형 API(202 {jobId})를 mock 의 onProgress 콜백 계약으로 변환.
// GET /v1/jobs/{id} 를 폴링해 progress 를 전달하고, done 이면 result, error 면 한국어 message throw.
// SSE 대신 폴링(마네킹 경로와 동일 GET 재사용, plan §7). 무과금 분석엔 stall 로직 불필요.
async function pollJob(
  jobId,
  { onProgress, intervalMs = 1200, timeoutMs = 90000, timeoutMessage = DEFAULT_JOB_TIMEOUT_MESSAGE } = {},
) {
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
    if (Date.now() - start > timeoutMs) throw new Error(timeoutMessage);
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

// ---- 매칭 의류 / analysis (US-4) --------------------------------------------
// 서버는 GET /analysis 라우트가 없고 PATCH 가 REPLACE(payload=excluded.payload) 라, 전체 analysis 를
// 이 모듈에 캐시해 delta 를 머지한 full payload 로 저장한다(delta 만 보내면 다른 필드가 유실됨).
// analyzeProduct 가 seed, saveAnalysis 가 갱신. 페이지 세션 동안 유지(하드 새로고침 시 리셋 → 재분석).
// getMatchClothing 도 이 캐시를 읽어 화면 전환(분석→마네킹) 간 매칭 선택을 이월한다.
// projectId 로 스코프 — 보관함에서 다른 프로젝트를 열어도 이전 프로젝트의 매칭이 새지 않게 한다.
let analysisCache = { projectId: null, analysis: null };  // { projectId, analysis }
const cachedAnalysisFor = (projectId) =>
  (analysisCache.projectId === projectId ? analysisCache.analysis : null);

// '새 제작' 진입 시 이전 프로젝트의 analysis 캐시를 비운다(beginProject 가 호출). 프로젝트 스코프가
// 이미 교차 유출을 막지만, 명시적 리셋으로 stale 참조 해제 + defense-in-depth (F1).
export function resetAnalysisCache() {
  analysisCache = { projectId: null, analysis: null };
}

const isMatchRefresh = (patch) =>
  ['clothingType', 'targetGenders', 'styleTags'].some((k) => k in patch);

// match-candidates(실 매칭 아이템) 조회 → [{id,name,gender,thumb,imageUrl,thumbnailUrl,selected:false}].
// clothingType 은 필수 쿼리 — analysis 우선, 없으면 서버 product 에서. gender/styleTags 는 반복 파라미터.
async function fetchMatchCandidates(projectId, analysis) {
  const clothingType = analysis?.clothingType
    || (await http(`/v1/projects/${projectId}/product`))?.clothingType || 'top';
  const qs = new URLSearchParams();
  qs.set('clothingType', clothingType);
  (analysis?.targetGenders || []).forEach((g) => qs.append('gender', g));
  (analysis?.styleTags || []).forEach((t) => qs.append('styleTags', t));
  return http(`/v1/projects/${projectId}/analysis/match-candidates?${qs.toString()}`);
}

// match-candidate → 레거시 matchClothing 아이템. selOrder 있으면 selected(계약 §6 shape 단일 소스).
const toMatchItem = (it, selOrder) => ({
  id: it.id, name: it.name, gender: it.gender,
  thumb: it.thumb, imageUrl: it.imageUrl, thumbnailUrl: it.thumbnailUrl,
  selected: selOrder != null, ...(selOrder != null ? { selOrder } : {}),
});

// 추천 재계산(로그인·서버 project): 이전 선택을 유효 범위에서 유지, 없으면 상위 N 기본 선택(mock 계약 동일).
async function recommendMatchHttp(projectId, analysis, current) {
  const items = await fetchMatchCandidates(projectId, analysis);
  const prev = (current || []).filter((m) => m.selected)
    .sort((a, b) => (a.selOrder || 0) - (b.selOrder || 0)).map((m) => m.id);
  const valid = prev.filter((id) => items.some((it) => it.id === id)).slice(0, LIMITS.matchClothingMax);
  const chosen = valid.length ? valid : items.slice(0, LIMITS.matchClothingMax).map((it) => it.id);
  return items.map((it) => {
    const idx = chosen.indexOf(it.id);
    return toMatchItem(it, idx >= 0 ? idx + 1 : null);
  });
}

// 선택 토글 머지 — id 단위 selected/selOrder 반영 후 1..max 재부여(mock 정규화와 동일 규칙).
function mergeMatchSelection(currentMatch, matchPatch) {
  const patchById = new Map(matchPatch.map((m) => [m.id, m]));
  const merged = (currentMatch || []).map((m) => {
    const p = patchById.get(m.id);
    if (!p) return m;
    return { ...m, selected: !!p.selected, selOrder: p.selected ? p.selOrder : undefined };
  });
  const ranked = merged.filter((m) => m.selected)
    .sort((a, b) => (a.selOrder || 99) - (b.selOrder || 99)).slice(0, LIMITS.matchClothingMax);
  const orderById = new Map(ranked.map((m, i) => [m.id, i + 1]));
  return merged.map((m) => orderById.has(m.id)
    ? { ...m, selected: true, selOrder: orderById.get(m.id) }
    : { ...m, selected: false, selOrder: undefined });
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
    // getProduct·마네킹·콘티·에디터가 모두 http 로 스왑됨(US-2~4) → mock 미러 불필요, 서버가 단일 소스.
    return http(`/v1/projects/${projectId}/product`, { method: 'PATCH', body: patch });
  },
  // AG-01 상품 분석 — POST /analyze(job) → 폴링 → analysis payload.
  // 반환 shape 은 계약 §6 와 동일해야 한다 — AnalysisForm 이 a.models/.matchClothing/.sellingPoints 등을
  // 무가드로 읽으므로. AI 가 산출하지 못하는 필드(models·selectedModelId·측정 구조 등)는 클라 소유
  // 기본 shape(shapes.defaultAnalysisShape)를 베이스로 두고 AI 산출 필드만 덮어써 shape 를 보존한다.
  // (과거엔 mock db.js 의 DB.analysis 를 베이스로 썼으나 mock 결합을 끊고 클라 상수로 대체.)
  async analyzeProduct(projectId, { onProgress } = {}) {
    const { jobId } = await http(`/v1/projects/${projectId}/analyze`, { method: 'POST' });
    // 폴링 상한은 provider 순차 폴백 최악경로(2 × analysis_timeout_seconds=60s = 120s)보다 넉넉히
    // 잡는다 — 짧으면 정상 폴백(gpt→gemini)이 완료 전에 실패 토스트가 뜨는데 job은 뒤늦게 성공한다.
    const result = await pollJob(jobId, {
      onProgress,
      timeoutMs: 180000,
      timeoutMessage: '분석이 지연되고 있어요. 잠시 후 다시 시도해 주세요.',
    });
    const ai = (result && result.data) || {};
    const base = defaultAnalysisShape();  // 클라 소유 기본 shape(models·selectedModelId·측정 구조 등)
    const merged = {
      ...base,
      clothingType: ai.clothingType ?? null,
      subCategory: ai.subCategory ?? null,
      targetGenders: ai.targetGenders ?? [],
      fit: ai.fit ?? null,
      // AI 는 확신 없으면 materials 를 비운다(피복 오탐 방지). 빈값이면 자주 쓰는 소재 2개를 편집용
      // 기본값으로 미리 채워 셀러가 바로 수정·확정하게 한다(최종 상세페이지 copywriter 가 이 값을 사용).
      materials: (ai.materials && ai.materials.length)
        ? ai.materials
        : [{ name: '면', ratio: 60 }, { name: '폴리에스터', ratio: 40 }],
      aiSuggestedPoints: ai.aiSuggestedPoints ?? [],
      suggestedName: ai.suggestedName ?? base.suggestedName,
      styleTags: ai.styleTags ?? [],
      swatchSuggestions: ai.swatchSuggestions ?? [],
      sellingPoints: [],  // 셀러는 빈 상태로 시작 — AI 제안(aiSuggestedPoints)은 폼이 자동으로 채운다
    };
    // 실측은 AI 미산출 → 기본 shape(defaultAnalysisShape)이 이미 value:null (사용자 직접 입력, PRD §6.5).
    // 매칭 의류 후보 시드 — 서버 matching_items 실 후보(top-N 기본 선택, mock 계약 §6 동일 shape).
    // defaultAnalysisShape 는 matchClothing:[] 라 여기서 채우지 않으면 분석 페이지 매칭 그리드가
    // 비어 보인다(과거 mock base 시절엔 가짜 목이 채워줬음). 실패는 비치명 — 빈 목록 유지.
    try {
      merged.matchClothing = await recommendMatchHttp(projectId, merged, []);
    } catch { /* 후보 조회 실패 — 분석 자체는 진행 */ }
    analysisCache = { projectId, analysis: merged };   // US-4: full-payload 머지 + 매칭 선택 이월 seed(프로젝트 스코프)
    return merged;
  },
  // ---- 상세페이지 (PL-4) — 콘티·에디터는 서버 소유. detail_page job 이 저장 콘티를 읽는다 ----
  async getStoryboard(projectId) {
    const saved = await http(`/v1/projects/${projectId}/storyboard`);
    if (Array.isArray(saved) && saved.length) return saved;
    // 첫 진입 — 서버에 저장 콘티 없음(GET 은 빈 []). 원 기본 콘티(7컷, d2fb3ee:
    // 후킹/셀링포인트/스타일링×2/호리존×2/제품컷)를 빌드해 서버에 시드한다.
    // detail_page job 이 '저장된 콘티'를 읽으므로 저장까지 해야 생성에 반영된다.
    const product = await http(`/v1/projects/${projectId}/product`);
    const blocks = defaultStoryboard(product?.colors || []);
    try {
      await http(`/v1/projects/${projectId}/storyboard`, { method: 'PUT', body: blocks });
    } catch { /* 시드 저장 실패 — 보드는 뜨게 두고, 편집/생성 시 자동저장이 다시 시도 */ }
    return blocks;
  },
  async saveStoryboard(projectId, blocks) {
    return http(`/v1/projects/${projectId}/storyboard`, { method: 'PUT', body: blocks });
  },
  async getEditorBlocks(projectId) {
    return http(`/v1/projects/${projectId}/editor-blocks`);
  },
  async saveEditorBlocks(projectId, blocks) {
    await http(`/v1/projects/${projectId}/editor-blocks`, { method: 'PUT', body: blocks });
  },
  // AG-06 컷 + AG-02/03 카피 → M-02 조립. 완료 재호출은 서버가 기존 결과 반환(무차감).
  async generateDetailPage(projectId, { onProgress } = {}) {
    const res = await http(`/v1/projects/${projectId}/detail-page:generate`, { method: 'POST' });
    if (res.data) return { data: res.data, credits: res.credits };  // 완료 재호출(202 아님) — 새 잡 없음
    const result = await pollJob(res.jobId, {
      onProgress,
      timeoutMs: 300000,
      timeoutMessage: '상세페이지 생성이 예상보다 오래 걸리고 있어요. 잠시 후 다시 확인해 주세요.',
    });
    // jobId 를 함께 반환 — 완료 후 정산 영수증(GET /jobs/{jobId}/settlement, payment_id=job:{jobId})을 조회한다.
    return { data: result.data, credits: result.credits, jobId: res.jobId };
  },
  // 프로젝트 단건 조회 (계약 §6) — {id,status,title,composeMode,copywriting,
  // selectedMannequinId,adjustCount,createdAt,updatedAt}. projectId 필수:
  // store.loadProject 가 argless 로 부르던 과거 경로(mock 가짜 project 오염 → 404)는
  // useAppStore 에서 제거됐다. 방어적으로 pid 없으면 서버 호출 없이 null.
  async getProject(projectId) {
    if (!projectId) return null;
    return http(`/v1/projects/${projectId}`);
  },
  // 상품 조회 (계약 §3.1) — {id,projectId,name,clothingType,colors[],measurements[],
  // measurementsUnknown,uploadComplete}. colors 는 프론트-소유 JSONB(saveProduct 가 저장한 isBase·images shape).
  // projectId 없으면(비로그인, 또는 입력단계 — 서버 project 생성은 submit 로 이연) 서버 product 가 없으므로
  // 클라 seed 템플릿(DB.product)을 반환한다. ProductInput 이 colors[0](isBase 포함)을 새 색상 템플릿으로 쓰므로
  // 빈 colors 를 주면 앵글 슬롯(앞/뒤/디테일/착용)이 사라진다 — mock 과 동일 계약 유지.
  async getProduct(projectId) {
    if (!projectId) return JSON.parse(JSON.stringify(DB.product));
    return http(`/v1/projects/${projectId}/product`);
  },
  // 분석 저장 (계약 §3.2) — 서버 PATCH 는 REPLACE 라 캐시에 delta 를 머지한 full payload 를 보낸다
  // (delta 만 보내면 다른 analysis 필드가 유실). 매칭 추천 갱신·선택 토글을 반영해 반환 matchClothing 을
  // 콜러(AnalysisForm)가 읽는다. projectId 없으면(비로그인 공개 분석) 서버 쓰기 없이 추천만 계산.
  async saveAnalysis(projectId, patch) {
    const { matchClothing: matchPatch, ...rest } = patch;
    let cached = cachedAnalysisFor(projectId);
    if (!cached && projectId) {   // 하드 새로고침 후에도 persist 되도록 저장분 1회 하이드레이션(getMatchClothing 동일)
      const saved = await http(`/v1/projects/${projectId}/analysis`);
      if (saved && Object.keys(saved).length > 1) {   // {projectId} 만 있으면 미저장 — 스킵
        analysisCache = { projectId, analysis: saved };
        cached = saved;
      }
    }
    const base = cached ? { ...cached } : {};
    Object.assign(base, rest);
    if (isMatchRefresh(patch)) {
      base.matchClothing = projectId
        ? await recommendMatchHttp(projectId, base, base.matchClothing)
        : recommendLegacyMatchClothing({
          clothingType: base.clothingType, targetGenders: base.targetGenders,
          styleTags: base.styleTags, current: base.matchClothing,
        });
    }
    if (Array.isArray(matchPatch)) {
      base.matchClothing = mergeMatchSelection(base.matchClothing || [], matchPatch);
    }
    analysisCache = { projectId, analysis: base };
    // 서버 PATCH 는 REPLACE — full base(analyze 가 seed 한 캐시)일 때만 지속한다. 캐시가 없는(비정상)
    // 상태에서 delta 만으로 덮어쓰면 서버의 더 완전한 analysis 를 유실하므로 그 경우 persist 를 건너뛴다(F3).
    if (projectId && cached) {
      await http(`/v1/projects/${projectId}/analysis`, { method: 'PATCH', body: base });
    }
    return base;
  },
  // 저장된 분석 payload 조회 (계약 §3.2) — 하드 새로고침 후 매칭 선택 등 복원용. {projectId, ...payload}.
  async getAnalysis(projectId) {
    if (!projectId) return {};
    return http(`/v1/projects/${projectId}/analysis`);
  },
  // 세탁 관리법 AI 초안 (동기·무과금) — 서버가 상품 종류·소재로 짧은 문구 생성. bare string 반환(mock 동일).
  // projectId 없으면(비로그인) 서버 project 가 없으니 클라 기본 문구로 폴백.
  async draftWashCare(projectId) {
    if (!projectId) return '찬물 단독 손세탁 권장 · 표백제 사용 금지 · 그늘에 뉘어 건조';
    const res = await http(`/v1/projects/${projectId}/wash-care:draft`, { method: 'POST' });
    return res.text;
  },
  // 매칭 후보 (계약 §6) — 같은 프로젝트의 이월 선택(analysisCache)을 우선. 캐시 미스(하드 새로고침)면
  // GET /analysis 로 저장분을 1회 하이드레이션해 선택 복원. 그래도 없으면 서버 후보 + 상위 N 기본선택.
  async getMatchClothing(projectId) {
    let cached = cachedAnalysisFor(projectId);
    if (!cached && projectId) {
      const saved = await http(`/v1/projects/${projectId}/analysis`);
      if (saved && Object.keys(saved).length > 1) {   // {projectId} 만 있으면 미저장 — 스킵
        analysisCache = { projectId, analysis: saved };
        cached = saved;
      }
    }
    if (cached?.matchClothing?.length) return cached.matchClothing;
    if (!projectId) return [];
    const items = await fetchMatchCandidates(projectId, cached);
    return items.map((it, i) => toMatchItem(it, i < LIMITS.matchClothingMax ? i + 1 : null));
  },
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
  // ---- 마네킹 (PRD §7) — generate/getMannequins/adjust 는 배포된 라우트로 실배선 ----
  // 마네킹 컷 목록 (계약 §6) — [{id,src,candidate,version,baseFit,fitAdjust,lengthAdjust,matchAdjust}].
  async getMannequins(projectId) {
    if (!projectId) return [];
    return http(`/v1/projects/${projectId}/mannequins`);
  },
  // 최초 A/B 후보 생성 — 202{jobId}→폴링, 또는 완료 존재 시 200{data,credits}(무차감 재호출).
  // 크레딧: mannequinGenerate. 진행 중 재호출은 서버가 활성 job 에 합류(1회만 차감).
  async generateMannequins(projectId, { onProgress } = {}) {
    const res = await http(`/v1/projects/${projectId}/mannequins:generate`, { method: 'POST' });
    if (res.data) return { data: res.data, credits: res.credits };  // 완료 재호출(200 캐시)
    // 마네킹 A/B 합성은 무거운 image job — 폴링 상한을 넉넉히(짧으면 정상 job 완료 전 실패 토스트).
    const result = await pollJob(res.jobId, {
      onProgress,
      timeoutMs: LONG_IMAGE_JOB_TIMEOUT_MS,
      timeoutMessage: MANNEQUIN_JOB_TIMEOUT_MESSAGE,
    });
    return { data: result.data, credits: result.credits };
  },
  // @deprecated (2026-07) AG-05 폐기 — fitProfile 재생성(regenerateMannequin)으로 통합.
  // 서버 :adjust 는 항상 410 Gone(잡 미생성). 화면 어디서도 호출하지 않으며 계약 §6 잔재로만 남김.
  async adjustMannequin(projectId, { baseId, fitAdjust, lengthAdjust, matchAdjust, onProgress } = {}) {
    const res = await http(`/v1/projects/${projectId}/mannequins:adjust`, {
      method: 'POST', body: { baseId, fitAdjust, lengthAdjust, matchAdjust },
    });
    if (res.data) return { data: res.data, credits: res.credits };
    const result = await pollJob(res.jobId, {
      onProgress,
      timeoutMs: LONG_IMAGE_JOB_TIMEOUT_MS,
      timeoutMessage: MANNEQUIN_ADJUST_JOB_TIMEOUT_MESSAGE,
    });
    return { data: result.data, credits: result.credits };
  },
  // fit-profile 재생성 — 완료 캐시 없이 매 호출이 새 A/B 버전을 만든다(서버 :regenerate, finalize 가 max(version)+1).
  // 크레딧: mannequinGenerate. generate 미러(202 job → 폴링). 재생성은 캐시 200 경로가 없어 항상 job.
  async regenerateMannequin(projectId, { fitProfile, onProgress } = {}) {
    const res = await http(`/v1/projects/${projectId}/mannequins:regenerate`, {
      method: 'POST', body: { fitProfile },
    });
    if (res.data) return { data: res.data, credits: res.credits };
    const result = await pollJob(res.jobId, {
      onProgress,
      timeoutMs: LONG_IMAGE_JOB_TIMEOUT_MS,
      timeoutMessage: MANNEQUIN_JOB_TIMEOUT_MESSAGE,
    });
    // 잡 결과 data 는 "이번에 새로 만든 컷"만(finalize candidates) — 계약(mock cutsEnvelope)은
    // 전체 버전 목록이므로 재조회로 정합한다(버전 스트립이 이전 버전 히스토리를 유지).
    const cuts = await http(`/v1/projects/${projectId}/mannequins`);
    return { data: { cuts }, credits: result.credits };
  },
  // 에디터 Wardrobe(의류 탭, 계약 §3.6) — Record<colorId|'misc', WardrobeImage[]>.
  async getWardrobe(projectId) {
    return http(`/v1/projects/${projectId}/wardrobe`);
  },
  // '내 사진' 무드 레퍼런스 — 파일 선택 → R2 업로드 → {assetId, url}. 취소 시 null.
  // 서버 컷 생성이 assetId 로 이미지를 첨부하므로(refAssetIds), objectURL 이 아니라 업로드가 필수.
  async pickRefImage(projectId) {
    const file = await new Promise((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.onchange = () => resolve(input.files && input.files[0] ? input.files[0] : null);
      input.oncancel = () => resolve(null);
      input.click();
    });
    if (!file) return null;
    const { assetId, url } = await uploadPhoto(projectId, {
      filename: file.name, mime: file.type || 'image/jpeg', blob: file,
    });
    return { assetId, url };
  },
  // AG-06(mode:'new')/AG-07(mode:'vary') — req = NewCutRequest | VaryRequest (계약 §6).
  // 완료 재호출 없음(매 호출이 새 이미지 생성, mock과 동일 계약) — onProgress는 body에서 제외.
  async generateImage(projectId, req = {}) {
    const { onProgress, ...body } = req;
    const res = await http(`/v1/projects/${projectId}/editor:generate-image`, {
      method: 'POST', body,
    });
    if (res.data) return { data: res.data, credits: res.credits };
    const result = await pollJob(res.jobId, { onProgress });
    return { data: result.data, credits: result.credits };
  },
};
