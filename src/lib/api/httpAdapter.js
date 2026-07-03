/* =============================================================
   httpAdapter — FastAPI 실서버 구현 (plan §8, Phase 1+에서 함수
   단위로 채운다). 여기 구현된 함수만 http 모드에서 mock 을 대체하고,
   나머지는 mock 이 계속 담당한다 (부분 스왑).
   시그니처·반환 형태는 mock/api.js(계약 §6)와 동일해야 한다.
   ============================================================= */
import { supabase } from '@/lib/supabase.js';
import { mockAdapter } from './mockAdapter.js';   // getCatalogs — 카탈로그는 아직 mock 소유
import { buildEditorBlocksFromStoryboard } from '@/mock/db.js';  // §7 과도기 — 조립기는 아직 프론트 소유

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

// 공용 fetch 헬퍼 — Supabase 세션의 access_token 을 Bearer 로 주입 (plan §9).
// 에러 봉투 { error: { code, message } } 의 한국어 message 를 그대로 throw (계약 §6).
export async function http(path, { method = 'GET', body, headers } = {}) {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers || {}),
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

/* ---- PL-1 분석 공용 헬퍼 (pl1_analysis_agent_spec §7.3·§7.4) ---- */

// 입력·분석 단계는 미로그인 허용이 제품 결정(로그인은 마네킹부터 — PRD §5·ProductInput 주석).
// 서버 호출은 Bearer 필수이므로, 세션이 없으면 PL-1 스왑 세트는 mock 으로 위임해
// 익명 입력 흐름(로컬 objectURL + draft 브리지)을 기존 그대로 보존한다.
// 비로그인 입력의 백엔드 동기화는 Option B 보류 결정에 따름 (반쪽 스왑 사고 방지).
async function hasSession() {
  const { data } = await supabase.auth.getSession();
  if (!data.session && import.meta.env.DEV) {
    // 개발 중 "구현 안 된 느낌" 오인 방지 — 비로그인 mock 위임은 제품 규칙상 정상이지만
    // 테스트 중엔 알아채기 어렵다 (2026-07-03 로컬 검증에서 실제 혼동 발생)
    console.warn('[api] http 모드지만 로그인 전 — 입력·분석은 로컬(mock) 데이터로 동작 중입니다.');
  }
  return !!data.session;
}

const saveAnalysisQueues = new Map();   // projectId → 직렬화 체인 (saveAnalysis 주석 참조)

// job 폴링 → onProgress 콜백 변환. SSE(EventSource)는 Bearer 헤더 불가 → MVP 폴링,
// fetch-stream SSE 는 P1 훅(spec §12-1). 이후 마네킹 스왑에서도 재사용.
async function followJob(jobId, { onProgress, intervalMs = 1000, timeoutMs = 300000 } = {}) {
  const t0 = Date.now();
  for (;;) {
    const job = await http(`/v1/jobs/${jobId}`);
    onProgress?.(Math.max(0, Math.min(100, job.progress ?? 0)));
    if (job.status === 'done') return job.result;   // { data, credits, creditsCharged }
    if (job.status === 'error') throw new Error(job.errorMessage || '작업에 실패했어요. 다시 시도해 주세요.');
    if (Date.now() - t0 > timeoutMs) throw new Error('작업이 너무 오래 걸려요. 잠시 후 다시 시도해 주세요.');
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// 서버 Analysis(계약 §3.2) → 현행 AnalysisForm 이 읽는 legacy shape 합성.
// 화면은 그대로, 갭은 어댑터가 흡수 (Phase 7 폼 리팩토링 때 제거 — TODO.md).
let catalogsPromise = null;   // mock getCatalogs 는 호출마다 지연+클론 — 모듈 캐시로 1회만
async function adaptAnalysis(data) {
  catalogsPromise = catalogsPromise || mockAdapter.getCatalogs();
  const catalogs = await catalogsPromise;
  const selectedIds = new Map((data.matchSelections || []).map((s, i) => [s.clothingId, i + 1]));
  return {
    ...data,                                        // 계약 필드 전부 (spec §3.5)
    models: catalogs.models,                        // 표시 카탈로그 (mock 소유)
    matchClothing: (data.matchCandidates || []).map((c) => ({
      ...c,
      selected: selectedIds.has(c.id),
      ...(selectedIds.has(c.id) ? { selOrder: selectedIds.get(c.id) } : {}),
    })),
    measurements: (catalogs.measurementSchema[data.clothingType] || [])
      .map((k) => ({ key: k, value: null, unit: 'cm' })),   // 항상 null — 계약 §3.1
    measurementsUnknown: false,
    washCare: '',                                   // 레거시 필드 (폼 참조만)
  };
}

export const httpAdapter = {
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
  // ---- PL-1 분석 스왑 세트 (pl1 spec §7 — 아래는 한 세트, 부분 스왑 금지) ----
  // 서버가 저장된 product 를 읽어 분석하므로 uploadAsset·get/saveProduct 가 함께 http 여야 한다.
  // 전 함수 공통: 세션 없으면 mock 위임 (hasSession 주석 참조 — 익명 입력 흐름 보존).

  // store.loadProject 전용 '현재 프로젝트' — 서버엔 싱글턴이 없으므로 최근 프로젝트,
  // 없으면 생성. mock 출신 로컬 projectId 가 서버 경로로 흘러 404 나는 반쪽 스왑 차단.
  async getCurrentProject() {
    if (!(await hasSession())) return mockAdapter.getCurrentProject();
    const library = await http('/v1/projects?view=library');   // updated_at desc
    if (library.length) return http(`/v1/projects/${library[0].id}`);
    return http('/v1/projects', { method: 'POST' });
  },
  // presigned PUT 3단계 업로드 (§7.1). 반환 = ImageAsset 핵심 필드 { id, src }.
  async uploadAsset(file, opts = {}) {
    if (!(await hasSession())) return mockAdapter.uploadAsset(file, opts);
    const { projectId } = opts;
    const { assetId, uploadUrl } = await http('/v1/assets/upload-url', {
      method: 'POST',
      body: { filename: file.name, mime: file.type, size: file.size, projectId },
    });
    const put = await fetch(uploadUrl, {
      method: 'PUT', headers: { 'Content-Type': file.type }, body: file,
    });
    if (!put.ok) throw new Error('이미지 업로드에 실패했어요. 다시 시도해 주세요.');
    const asset = await http(`/v1/assets/${assetId}/complete`, {
      method: 'POST', body: { projectId, mime: file.type, filename: file.name },
    });
    return { id: asset.id, src: asset.url };
  },
  async getProduct(projectId) {
    if (!(await hasSession())) return mockAdapter.getProduct(projectId);
    return http(`/v1/projects/${projectId}/product`);
  },
  async saveProduct(projectId, patch) {
    if (!(await hasSession())) return mockAdapter.saveProduct(projectId, patch);
    // 서버 ProductPatch 화이트리스트 외 키(id 등)는 서버가 무시한다.
    return http(`/v1/projects/${projectId}/product`, { method: 'PATCH', body: patch });
  },
  // PL-1 분석 — 202 { jobId } 면 폴링, 200 { data } 면 기존 분석(재분석 없음, spec §5.1).
  async analyzeProduct(projectId, { onProgress } = {}) {
    if (!(await hasSession())) return mockAdapter.analyzeProduct(projectId, { onProgress });
    const res = await http(`/v1/projects/${projectId}/analysis:analyze`, { method: 'POST' });
    if (!res.jobId) {
      onProgress?.(100);
      return adaptAnalysis(res.data);
    }
    const envelope = await followJob(res.jobId, { onProgress });
    return adaptAnalysis(envelope.data);
  },
  async getAnalysis(projectId) {
    if (!(await hasSession())) throw new Error('분석 결과가 아직 없습니다.');
    return adaptAnalysis(await http(`/v1/projects/${projectId}/analysis`));
  },
  // 폼의 legacy patch 를 소유자별로 라우팅·변환 (mock saveAnalysis 의 스마트 머지와 동작 동등).
  // 프로젝트별 직렬화 — read-modify-write(④)가 연타 편집과 경합해 서로 덮어쓰지 않게 한다
  // (mock 은 단일 틱 원자라 http 전용 문제).
  async saveAnalysis(projectId, patch) {
    if (!(await hasSession())) return mockAdapter.saveAnalysis(projectId, patch);
    const prev = saveAnalysisQueues.get(projectId) || Promise.resolve();
    const run = prev.catch(() => {}).then(() => this._saveAnalysisNow(projectId, patch));
    saveAnalysisQueues.set(projectId, run);
    return run;
  },
  async _saveAnalysisNow(projectId, patch) {
    const p = { ...patch };
    // ① Product 소유 필드 → PATCH /product (계약 §3.1)
    const productFields = {};
    for (const k of ['clothingType', 'measurements', 'measurementsUnknown']) {
      if (k in p) { productFields[k] = p[k]; delete p[k]; }
    }
    if (Object.keys(productFields).length) {
      await http(`/v1/projects/${projectId}/product`, { method: 'PATCH', body: productFields });
    }
    // ② legacy matchClothing 선택 patch → 계약형 matchSelections (선택 유실 방지)
    if (p.matchClothing) {
      p.matchSelections = p.matchClothing
        .filter((c) => c.selected)
        .sort((a, b) => (a.selOrder || 0) - (b.selOrder || 0))
        .slice(0, 2)
        .map((c, i) => ({ clothingId: c.id, role: i === 0 ? 'main' : 'sub' }));
      delete p.matchClothing;
    }
    // ③ 표시 전용 legacy 필드 strip — payload 오염 방지
    for (const k of ['models', 'washCare']) delete p[k];
    if (Object.keys(p).length) {
      await http(`/v1/projects/${projectId}/analysis`, { method: 'PATCH', body: p });
    }
    // ④ 의류 종류·성별 변경 → 매칭 후보 재계산 (mock 스마트 머지 동등 — 기존 라우트 재사용).
    //    기존 선택 중 새 후보군에 살아남은 것은 보존하고, 전부 사라졌을 때만 상위 2개 기본
    //    선택으로 폴백한다 (mock recommendLegacyMatchClothing의 validSelected 규칙).
    //    판정은 키 존재 기준(mock shouldRefreshMatchClothing 동일) — null 해제도 갱신 대상.
    if ('clothingType' in productFields || 'targetGenders' in patch) {
      const cur = await http(`/v1/projects/${projectId}/analysis`);
      const q = new URLSearchParams({ clothingType: productFields.clothingType || cur.clothingType || 'top' });
      (cur.targetGenders || []).forEach((g) => q.append('gender', g));
      const candidates = await http(`/v1/projects/${projectId}/analysis/match-candidates?${q}`);
      const candidateIds = new Set(candidates.map((c) => c.id));
      const kept = (cur.matchSelections || [])
        .filter((s) => candidateIds.has(s.clothingId))
        .slice(0, 2);
      const effective = kept.length ? kept : candidates.slice(0, 2)
        .map((c) => ({ clothingId: c.id }));
      const selections = effective
        .map((s, i) => ({ clothingId: s.clothingId, role: i === 0 ? 'main' : 'sub' }));
      await http(`/v1/projects/${projectId}/analysis`, {
        method: 'PATCH', body: { matchCandidates: candidates, matchSelections: selections } });
    }
    return this.getAnalysis(projectId);   // 폼이 쓰는 최종 형태 반환 (mock 계약 동일)
  },

  /* ---- 컷 생성 실배선 (ADR-0004 — kind='editor_image' job) ----
     · generateImage(new): 에디터 '새 컷 추가' = 컷 단위 실생성·재생성 루프의 실체.
     · generateDetailPage: 콘티의 AI 블록을 순차 실생성 후 로컬 조립 — 콘티·에디터 상태는
       아직 프론트(mock 소유, §7 과도기)라 조립 결과를 mock 브리지에 주입한다.
     · vary(현재 컷 변형)는 서버 미구현 — mock 폴백 유지 (부분 스왑 원칙). */
  async generateImage(projectId, req = {}) {
    if (req.mode === 'vary') return mockAdapter.generateImage(projectId, req);
    const { jobId } = await http(`/v1/projects/${projectId}/cuts:generate`, {
      method: 'POST', body: _cutSpec(req),
      headers: { 'Idempotency-Key': _idem() },        // 네트워크 재시도의 이중 잡·이중 차감 방지
    });
    const envelope = await followJob(jobId);          // 실패 시 throw — 화면이 잡아 재시도 안내
    return { data: envelope.data, credits: envelope.credits };
  },

  async getWardrobe(projectId) {
    const rows = await http(`/v1/projects/${projectId}/wardrobe`);
    const grouped = {};                                // 화면 계약(§3.6) = colorId(없으면 'misc') 그룹 맵
    for (const im of rows) (grouped[im.colorId || 'misc'] ||= []).push(im);
    return grouped;
  },

  async generateDetailPage(projectId, { onProgress, onStep } = {}) {
    const [blocks, product, project] = await Promise.all([
      mockAdapter.getStoryboard(projectId), mockAdapter.getProduct(projectId), mockAdapter.getProject(projectId),
    ]);
    const steps = GEN_STEPS.map((s) => ({ ...s, status: 'idle' }));
    const setStep = (key, status) => {
      const st = steps.find((s) => s.key === key); if (st) st.status = status;
      onStep && onStep(steps.map((s) => ({ ...s })));
    };
    const stepFor = (ct) => ct === 'product' ? 'product' : ct === 'horizon' ? 'horizon' : 'styling'; // mirror→styling (ADR-0004)
    setStep('info', 'done'); setStep('prep', 'done'); onProgress && onProgress(5);

    const aiBlocks = blocks.filter((b) => b.source !== 'mine' && b.cutType);
    const srcByBlock = {}; const generated = []; let credits = null;
    for (let i = 0; i < aiBlocks.length; i++) {
      const b = aiBlocks[i];
      setStep(stepFor(b.cutType), 'running');
      onProgress && onProgress(Math.round(5 + (i / Math.max(1, aiBlocks.length)) * 85));
      try {
        const { jobId } = await http(`/v1/projects/${projectId}/cuts:generate`, {
          method: 'POST', body: _cutSpec(b),
          headers: { 'Idempotency-Key': _idem() },
        });
        const envelope = await followJob(jobId);
        const img = envelope && envelope.data;
        if (img) { srcByBlock[b.id] = img.src; generated.push(img); }
        if (envelope && envelope.credits != null) credits = envelope.credits;
      } catch {
        // 실패 컷은 건너뛴다 — 에디터의 컷 단위 재생성 루프가 안전망 (ADR-0004).
        // 실패 잡은 서버가 예약을 해제하므로 미차감.
      }
    }
    // 전부 실패 = 빈 상세페이지 — done으로 오염시키지 않고 중단 (화면이 잡아 콘티로 되돌림)
    if (aiBlocks.length && generated.length === 0) {
      throw new Error('컷 생성에 모두 실패했어요. 잠시 후 다시 시도해 주세요.');
    }
    ['styling', 'horizon', 'product'].forEach((k) => setStep(k, 'done'));
    setStep('copy', 'done'); setStep('assemble', 'running'); onProgress && onProgress(95);

    // 조립은 mock 조립기 재사용 — 대표 이미지(첫 image 요소)만 실 생성 결과로 교체 (블록 index = 콘티 순서)
    const editorBlocks = buildEditorBlocksFromStoryboard(blocks, product, project.copywriting);
    blocks.forEach((b, idx) => {
      const src = srcByBlock[b.id]; if (!src) return;
      const imgEl = ((editorBlocks[idx] || {}).elements || []).find((e) => e.type === 'image');
      if (imgEl) imgEl.src = src;
    });
    await mockAdapter.commitGeneratedDetailPage(projectId, { editorBlocks, wardrobe: generated });
    setStep('assemble', 'done'); onProgress && onProgress(100);
    if (credits == null) credits = (await http('/v1/me/account')).credits;
    return { data: editorBlocks, credits };
  },

  // 마네킹 등 job형 플로우(generate→adjust→regenerate)는 같은 컷 상태를 공유하므로
  // 부분 스왑 금지 — 백엔드가 generate+adjust+regenerate를 다 갖추고 draft sync(A-3)가
  // 돼야 통째로 swap. 그 전까지 마네킹은 mock 유지(혼합 시 http 모드 깨짐).
};

// 콘티 블록/NewCutRequest → 서버 CutGenerateRequest. refImages는 아직 로컬 objectURL이라
// 미전송(refAssetIds 빈 배열) — 무드 레퍼런스 실배선은 assets 업로드 경로를 붙일 때 (TODO).
function _cutSpec(b) {
  return {
    cutType: b.cutType,
    direction: b.direction ?? null,
    shot: b.shot ?? null,
    colorId: b.colorId ?? null,
    pose: b.pose || 'auto',
    faceExposure: b.faceExposure ?? null,
    matchIds: b.matchIds || [],
    refAssetIds: [],
    exampleId: b.exampleId ?? null,
    spaceGroupId: b.spaceGroupId ?? null,
    spaceVariation: b.spaceVariation ?? null,
  };
}

// 요청 1건당 고유 멱등 키 — 같은 논리적 시도의 네트워크 중복만 합류시킨다 (서버 §6 ①)
function _idem() {
  return (globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID() : `idem-${Date.now()}-${Math.random()}`;
}

// 생성 진행 표시용 단계 목록 — mock DB.genSteps와 동일 라벨 (표시 전용)
const GEN_STEPS = [
  { key: 'info', label: '상품 정보 정리' }, { key: 'prep', label: '이미지 생성 준비' },
  { key: 'styling', label: '스타일링컷 생성' }, { key: 'horizon', label: '호리존컷 생성' },
  { key: 'product', label: '제품컷 생성' }, { key: 'copy', label: '카피라이팅 적용' },
  { key: 'assemble', label: '상세페이지 조립' },
];
