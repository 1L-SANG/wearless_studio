/* =============================================================
   mock/api.js — the ONLY data access screens are allowed to use.
   Signatures/shapes follow documents/common_data_contract.md §6.
   Every function is async (returns a Promise) and mimics network
   latency, progress, and optional failure. Replace the bodies with
   real fetch() calls later; keep the signatures and shapes.

   mock 단순화: 동시에 1개 프로젝트만 존재하므로 projectId 인자는
   받되 무시한다 — 화면은 계약대로 항상 넘긴다.

   크레딧 봉투 (계약 §6): 크레딧을 소모하는 API 는 { data, credits }
   를 반환한다. credits = 차감 후 잔액 — 화면은 store.syncCredits 로
   반영한다. 차감은 여기(서버 역할)가 책임지고, 프론트 선차감 금지.
   ============================================================= */
import { DB, reseedDraft, buildEditorBlocksFromStoryboard, buildStoryboard } from '@/mock/db.js';
import { Placeholder } from '@/mock/placeholders.js';
import { recommendLegacyMatchClothing } from '@/mock/matchingRecommendation.js';
import { CREDIT_COSTS, LIMITS } from '@/lib/limits.js';
import { uid } from '@/lib/ids.js';

const clone = (x) => JSON.parse(JSON.stringify(x));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const touch = () => { DB.project.updatedAt = new Date().toISOString(); };
const spend = (n) => { DB.account.credits = Math.max(0, DB.account.credits - n); return DB.account.credits; };
const shouldRefreshMatchClothing = (patch) => ['clothingType', 'targetGenders', 'styleTags'].some((key) => key in patch);
const cutsEnvelope = () => ({ cuts: clone(DB.mannequins) });
const syncSelectedCut = (cutId) => {
  const selected = DB.mannequins.find((m) => m.id === cutId) || DB.mannequins[0] || null;
  DB.project.selectedMannequinId = selected?.id || null;
  DB.mannequins = DB.mannequins.map((m) => ({ ...m, isSelected: !!selected && m.id === selected.id }));
  return selected;
};
const makeMannequinCut = (version) => ({
  id: uid('mq'),
  version,
  imageUrl: Placeholder.photo(`mq${version}_${Date.now()}`, 'mannequin'),
  isSelected: true,
  createdAt: new Date().toISOString(),
});

/* 진행 중인 유료 job 레지스트리 — 같은 job 의 중복 시작 요청(StrictMode 이중
   실행, 생성 중 이탈 후 재진입)은 새 작업을 만들지 않고 기존 job 에 합류시켜
   1회만 차감되게 한다. 실서버의 job 레코드 dedup 에 대응 (계약 §6). */
const inflight = { mannequins: null, detailPage: null };
function joinable(slot, start) {
  if (!inflight[slot]) {
    const listeners = [];
    const job = { listeners };
    job.promise = start(listeners).finally(() => { if (inflight[slot] === job) inflight[slot] = null; });
    inflight[slot] = job;
  }
  return inflight[slot];
}

// generic long-running job: ticks progress 0..100, resolves with result
function runJob({ duration = 2600, onProgress, result, stall = false }) {
  return new Promise((resolve) => {
    const start = performance.now();
    const id = setInterval(() => {
      const elapsed = performance.now() - start;
      let pct = Math.min(100, Math.round((elapsed / duration) * 100));
      if (stall && pct >= 95 && elapsed < duration + 1200) pct = 95; // PRD §7.2 stall at 95%
      onProgress && onProgress(pct);
      if (pct >= 100) { clearInterval(id); resolve(typeof result === 'function' ? result() : result); }
    }, 60);
  });
}

export const api = {
  /* ---- project (계약 §2, ADR-0001) ---- */
  // 새 제작 시작 — draft 전체를 재시드해 이전 세션의 변형이 새 생성에 새지 않게 한다.
  // 진행 중이던 이전 프로젝트의 job 에 새 프로젝트가 합류하지 않도록 레지스트리도 비운다.
  async createProject() {
    reseedDraft();
    inflight.mannequins = null; inflight.detailPage = null;
    await wait(80); return clone(DB.project);
  },
  async getProject(/* projectId */) { await wait(60); return clone(DB.project); },
  async patchProject(_projectId, patch) {
    await wait(60);
    if ('composeMode' in patch && !['basic', 'extended'].includes(patch.composeMode)) {
      throw new Error("composeMode는 basic 또는 extended여야 합니다.");
    }
    const modeChanged = patch.composeMode && patch.composeMode !== DB.project.composeMode;
    Object.assign(DB.project, patch); touch();
    if ('selectedMannequinId' in patch) syncSelectedCut(patch.selectedMannequinId);
    if ('fitProfile' in patch) DB.analysis.fitProfile = clone(patch.fitProfile);
    // 사진 양 변경 시, 사용자가 콘티를 손대기 전이면 기본 콘티를 새 모드로 재구성 (PRD §7.7)
    if (modeChanged && !DB.storyboardDirty) DB.storyboard = buildStoryboard(DB.project.composeMode, DB.product.colors);
    return clone(DB.project);
  },

  /* ---- reference / catalogs ---- */
  async getAccount() { await wait(120); return clone(DB.account); },
  async getCatalogs() { await wait(80); return clone(DB.catalogs); },

  /* ---- credits (표시 전용; 실서버는 httpAdapter. 계약 §6) ---- */
  async getPricingPlans() {
    await wait(80);
    return [
      { id: 'm-basic', code: 'basic', kind: 'subscription', name: 'Basic', credits: 200, price: 19900, billingPeriod: 'monthly', sortOrder: 1 },
      { id: 'm-plus', code: 'plus', kind: 'subscription', name: 'Plus', credits: 600, price: 49900, billingPeriod: 'monthly', sortOrder: 2 },
      { id: 'm-seller', code: 'seller', kind: 'subscription', name: 'Seller', credits: 1400, price: 99900, billingPeriod: 'monthly', sortOrder: 3 },
      { id: 'm-tb', code: 'topup_basic', kind: 'topup', name: '크레딧 200', credits: 200, price: 19900, billingPeriod: 'once', sortOrder: 11 },
      { id: 'm-tp', code: 'topup_plus', kind: 'topup', name: '크레딧 600', credits: 600, price: 49900, billingPeriod: 'once', sortOrder: 12 },
      { id: 'm-ts', code: 'topup_seller', kind: 'topup', name: '크레딧 1400', credits: 1400, price: 99900, billingPeriod: 'once', sortOrder: 13 },
    ];
  },
  async getCreditHistory() {
    await wait(120);
    const now = Date.now();
    // 정합 시나리오: 200 충전 → 마네킹 생성 2회(각 -2) = 196 (account.credits 와 일치)
    return [
      { id: 'l2', projectId: 'p1', jobId: 'j2', actionKey: 'mannequinGenerate', delta: -CREDIT_COSTS.mannequinGenerate, balanceAfter: 196, availableAfter: 196, createdAt: new Date(now - 32e5).toISOString() },
      { id: 'l1', projectId: 'p1', jobId: 'j1', actionKey: 'mannequinGenerate', delta: -CREDIT_COSTS.mannequinGenerate, balanceAfter: 198, availableAfter: 198, createdAt: new Date(now - 36e5).toISOString() },
      { id: 'l0', projectId: null, jobId: null, actionKey: 'grant_subscription', delta: 200, balanceAfter: 200, availableAfter: 200, createdAt: new Date(now - 40e5).toISOString() },
    ];
  },
  async getCreditSources() {
    await wait(100);
    return [
      { id: 's1', sourceType: 'subscription', status: 'active', initialCredits: 200, remainingCredits: 196, periodEnd: new Date(Date.now() + 25 * 864e5).toISOString(), planId: 'm-basic', createdAt: new Date(Date.now() - 40e5).toISOString() },
    ];
  },

  // store.loadProject 전용 '현재 프로젝트' (pl1 spec §7 — http 어댑터가 최근/생성 의미를
  // 구현하기 위한 과도기 함수. mock 은 싱글턴이라 getProject 와 동일).
  async getCurrentProject() { return this.getProject(); },

  /* ---- product input ---- */
  async getProduct(/* projectId */) { await wait(160); return clone(DB.product); },
  // mock 은 실제 업로드가 없다 — 상품을 그대로 돌려준다(계약: http 는 사진을 R2에 올리고
  // images[].id 를 asset id 로 치환). 화면 submit 은 mock/http 동일 호출로 동작.
  async uploadProductPhotos(_projectId, product) { await wait(40); return clone(product); },
  async saveProduct(_projectId, patch) {
    await wait(200);
    Object.assign(DB.product, patch);
    if (patch.name != null) { DB.project.title = patch.name; touch(); }
    return clone(DB.product);
  },

  // 실서비스 계약 uploadAsset(file, { projectId }) 의 mock 대행 (계약 §6 · pl1 spec §7.1)
  // — 업로드 없이 objectURL 로 표시만 지원한다. id 는 http 모드에서 asset row id 가 된다.
  async uploadAsset(file /*, { projectId } */) {
    await wait(120);
    return { id: uid('img'), src: URL.createObjectURL(file) };
  },

  /* ---- AI analysis (PRD §6) — 30s-feel progress, can fail ---- */
  async analyzeProduct(_projectId, { onProgress, forceError = false } = {}) {
    await runJob({ duration: 2800, onProgress });
    if (forceError) throw new Error('분석 서버에 일시적인 문제가 발생했어요.');
    const a = clone(DB.analysis);
    // 실측은 AI가 추정하지 않는다 — 사용자가 직접 입력하도록 빈칸으로 둔다.
    a.measurements = (a.measurements || []).map((m) => ({ ...m, value: null }));
    return a;
  },
  async getAnalysis(/* projectId */) { await wait(120); return clone(DB.analysis); },
  async saveAnalysis(_projectId, patch) {
    await wait(180);
    // 매칭 후보 목록은 서버(추천)가 소유 — matchClothing patch 는 통째로 덮지 않고
    // 아래에서 "선택 상태만" id 단위로 머지한다. 의류 종류 전환 직후 도착하는 묵은
    // 클라 스냅샷이 갱신된 후보 목록을 되살리는 레이스 차단 (stale save 방어).
    const { matchClothing: matchPatch, ...rest } = patch;
    Object.assign(DB.analysis, rest);
    if ('fitProfile' in rest) DB.project.fitProfile = clone(rest.fitProfile);
    if (shouldRefreshMatchClothing(patch)) {
      DB.analysis.matchClothing = recommendLegacyMatchClothing({
        clothingType: DB.analysis.clothingType,
        targetGenders: DB.analysis.targetGenders,
        styleTags: DB.analysis.styleTags,
        current: DB.analysis.matchClothing,
      });
    }
    if (Array.isArray(matchPatch)) {
      const patchById = new Map(matchPatch.map((m) => [m.id, m]));
      const merged = (DB.analysis.matchClothing || []).map((m) => {
        const p = patchById.get(m.id);
        if (!p) return m;                                  // 클라가 모르는 새 후보 — 서버 상태 유지
        return { ...m, selected: !!p.selected, selOrder: p.selected ? p.selOrder : undefined };
      });
      // selOrder 정규화 — 머지로 생길 수 있는 중복/초과를 1..matchClothingMax 로 재부여
      const ranked = merged.filter((m) => m.selected)
        .sort((a, b) => (a.selOrder || 99) - (b.selOrder || 99))
        .slice(0, LIMITS.matchClothingMax);
      const orderById = new Map(ranked.map((m, i) => [m.id, i + 1]));
      DB.analysis.matchClothing = merged.map((m) => orderById.has(m.id)
        ? { ...m, selected: true, selOrder: orderById.get(m.id) }
        : { ...m, selected: false, selOrder: undefined });
    }
    // Product 소유 필드(계약 §3.1)는 product 에도 반영 — 사이즈 안내 등
    // 하위 단계는 product 를 읽는다. (소유권 일원화 전까지의 동기화 규칙)
    const owned = {};
    ['clothingType', 'measurements', 'measurementsUnknown'].forEach((k) => { if (k in patch) owned[k] = patch[k]; });
    if (Object.keys(owned).length) Object.assign(DB.product, clone(owned));
    return clone(DB.analysis);
  },
  async draftWashCare(/* projectId */) {
    await wait(900);
    return '찬물 단독 손세탁 권장 · 표백제 사용 금지 · 그늘에 뉘어 건조';
  },

  /* ---- mannequin (PRD §7) ---- */
  async getMatchClothing(/* projectId */) {
    await wait(120);
    return clone((DB.analysis?.matchClothing?.length ? DB.analysis.matchClothing : DB.matchClothing));
  },
  async getMannequins(/* projectId */) {
    await wait(140);
    syncSelectedCut(DB.project.selectedMannequinId);
    return clone(DB.mannequins);
  },
  async selectMannequin(_projectId, cutId) {
    await wait(80);
    const selected = syncSelectedCut(cutId);
    touch();
    return clone(selected);
  },
  // 최초 진입 시 단일 v0 컷을 생성한다. 크레딧: mannequinGenerate (계약 §6).
  // 진행 중에 다시 호출되면(이중 mount·재진입) 기존 job 에 합류한다 — 1회만 차감.
  // 이미 컷이 있으면(완료 후 재호출) 재실행·재차감 없이 기존 결과를 반환한다.
  async generateMannequins(_projectId, { onProgress } = {}) {
    if (DB.mannequins.length) {
      await wait(140);
      onProgress && onProgress(100);
      syncSelectedCut(DB.project.selectedMannequinId);
      return { data: cutsEnvelope(), credits: DB.account.credits };
    }
    const job = joinable('mannequins', (listeners) => (async () => {
      const ownerId = DB.project.id;   // job 도중 새 프로젝트로 리시드되면 결과를 버린다
      // 실서버 체감(25~60s)에 근접시켜 로딩 시퀀스(인트로 3.5s+루프)가 보이게 한다
      await runJob({ duration: 9000, stall: true, onProgress: (p) => listeners.forEach((f) => f(p)) });
      if (DB.project.id !== ownerId) return { data: { cuts: [] }, credits: DB.account.credits };
      if (!DB.mannequins.length) {
        DB.mannequins.push(makeMannequinCut(0));
        syncSelectedCut(DB.mannequins[0].id);
      }
      touch();
      return { data: cutsEnvelope(), credits: spend(CREDIT_COSTS.mannequinGenerate) };
    })());
    if (onProgress) job.listeners.push(onProgress);
    return job.promise;
  },
  async regenerateMannequin(_projectId, { fitProfile, onProgress } = {}) {
    await runJob({ duration: 2200, onProgress });
    if (fitProfile) {
      DB.project.fitProfile = clone(fitProfile);
      DB.analysis.fitProfile = clone(fitProfile);
    }
    const prevMax = DB.mannequins.reduce((max, cut) => Math.max(max, cut.version ?? -1), -1);
    const next = makeMannequinCut(prevMax + 1);
    DB.mannequins = DB.mannequins.map((m) => ({ ...m, isSelected: false }));
    DB.mannequins.push(next);
    syncSelectedCut(next.id);
    touch();
    return { data: cutsEnvelope(), credits: spend(CREDIT_COSTS.mannequinGenerate) };
  },

  /* ---- storyboard (PRD §8) ---- */
  async getStoryboard(/* projectId */) { await wait(160); return clone(DB.storyboard); },
  async saveStoryboard(_projectId, blocks) { await wait(150); DB.storyboard = clone(blocks); DB.storyboardDirty = true; touch(); return clone(DB.storyboard); },

  /* ---- generation waiting (PRD §9) ----
     입력은 전부 서버 상태(저장된 콘티 + project 선택값)에서 읽는다 (계약 §6).
     크레딧: storyboardPerCut × AI 컷 수 — 내 이미지 블록은 생성 작업이 없어 제외.
     진행 중 재호출은 기존 job 에 합류한다 — 1회만 생성·차감.
     이미 완료(status='done')면 재생성·재차감 없이 기존 결과를 반환한다. */
  async generateDetailPage(_projectId, { onProgress, onStep } = {}) {
    if (DB.project.status === 'done') {
      await wait(160);
      onProgress && onProgress(100);
      return { data: clone(DB.editorBlocks), credits: DB.account.credits };
    }
    const job = joinable('detailPage', (listeners) => (async () => {
      const ownerId = DB.project.id;
      const emitStep = (steps) => listeners.forEach((l) => l.onStep && l.onStep(clone(steps)));
      const emitProgress = (p) => listeners.forEach((l) => l.onProgress && l.onProgress(p));
      DB.project.status = 'generating'; touch();
      const steps = DB.genSteps.map((s) => ({ ...s, status: 'idle' }));
      const per = 100 / steps.length;
      for (let i = 0; i < steps.length; i++) {
        steps[i].status = 'running'; emitStep(steps);
        await runJob({ duration: 700, onProgress: (p) => emitProgress(Math.round(i * per + (p / 100) * per)) });
        steps[i].status = 'done'; emitStep(steps);
      }
      emitProgress(100);
      if (DB.project.id !== ownerId) return { data: [], credits: DB.account.credits };
      DB.editorBlocks = buildEditorBlocksFromStoryboard(DB.storyboard, DB.product, DB.project.copywriting);
      DB.project.status = 'done'; touch();
      const aiCuts = DB.storyboard.filter((b) => b.source !== 'mine').length;
      return { data: clone(DB.editorBlocks), credits: spend(CREDIT_COSTS.storyboardPerCut * aiCuts) };
    })());
    job.listeners.push({ onProgress, onStep });
    return job.promise;
  },

  /* ---- editor (PRD §10) ---- */
  async getEditorBlocks(/* projectId */) { await wait(180); return clone(DB.editorBlocks); },
  // 에디터 상태 영속화 (계약 §6) — 저장 버튼·자동 저장이 호출. 세션 내 재진입 시 편집 유지.
  async saveEditorBlocks(_projectId, blocks) { await wait(200); DB.editorBlocks = clone(blocks); touch(); },
  async getWardrobe(/* projectId */) { await wait(160); return clone(DB.wardrobe); },
  // §7 과도기 브리지 — http 모드 generateDetailPage가 실생성 결과를 mock 소유 상태(에디터 블록·의류탭)에 주입.
  // 콘티·에디터 저장이 서버로 넘어가면(Phase 7) 제거된다.
  async commitGeneratedDetailPage(_projectId, { editorBlocks, wardrobe } = {}) {
    if (editorBlocks) DB.editorBlocks = clone(editorBlocks);
    for (const im of wardrobe || []) {
      const g = im.colorId || 'misc';
      (DB.wardrobe[g] ||= []).push({ ...im, ai: true });
    }
    DB.project.status = 'done'; touch();
    return true;
  },
  // req = NewCutRequest { mode:'new', colorId, cutType, direction?, shot?, modelId? }
  //     | VaryRequest   { mode:'vary', source:{src,cutType}, changes[], refBg? }  (계약 §6)
  async generateImage(_projectId, req = {}) {
    await runJob({ duration: 2400, onProgress: req.onProgress });
    const isVary = req.mode === 'vary';
    const group = isVary ? 'misc' : (req.colorId || 'misc');
    // cutType 은 생성 시점에 기록되는 메타데이터 — 이후 '현재 이미지 수정'이 옵션 세트를 고르는 기준
    const cutType = isVary ? ((req.source && req.source.cutType) || 'styling') : (req.cutType || null);
    const img = { id: uid('w'), src: Placeholder.any('gen' + Date.now()), ai: true, ...(cutType ? { cutType } : {}) };
    (DB.wardrobe[group] = DB.wardrobe[group] || []).push(img);
    return { data: clone(img), credits: spend(CREDIT_COSTS.editorImage) };
  },
  // a fresh "any image" — mock 전용 헬퍼 (실서비스 계약은 uploadAsset(file))
  async pickAnyImage() { await wait(120); return Placeholder.any('pick' + Date.now()); },
  // '내 사진' 무드 레퍼런스 1장 — 실서비스는 파일 선택→업로드→{assetId, url}. mock 은 플레이스홀더+가짜 id.
  async pickRefImage(/* projectId */) { await wait(120); return { assetId: uid('ref'), url: Placeholder.any('pick' + Date.now()) }; },
  async download(/* projectId, format */) { await wait(800); return { ok: true }; },

  /* ---- library (PRD §4) ---- */
  async getLibrary({ forceEmpty = false, forceError = false } = {}) {
    await wait(420);
    if (forceError) throw new Error('보관함을 불러오지 못했어요.');
    return forceEmpty ? [] : clone(DB.library);
  },
};

export default api;
