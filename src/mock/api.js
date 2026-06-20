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
import { DB, reseedDraft, buildEditorBlocksFromStoryboard } from '@/mock/db.js';
import { Placeholder } from '@/mock/placeholders.js';
import { recommendLegacyMatchClothing } from '@/mock/matchingRecommendation.js';
import { CREDIT_COSTS, LIMITS } from '@/lib/limits.js';
import { uid } from '@/lib/ids.js';

const clone = (x) => JSON.parse(JSON.stringify(x));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const touch = () => { DB.project.updatedAt = new Date().toISOString(); };
const spend = (n) => { DB.account.credits = Math.max(0, DB.account.credits - n); return DB.account.credits; };
const shouldRefreshMatchClothing = (patch) => ['clothingType', 'targetGenders', 'styleTags'].some((key) => key in patch);

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
    Object.assign(DB.project, patch); touch();
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
    // 정합 시나리오: 200 충전 → 4 사용 = 196 (account.credits 와 일치)
    return [
      { id: 'l2', projectId: 'p1', jobId: 'j2', actionKey: 'mannequinGenerate', delta: -2, balanceAfter: 196, availableAfter: 196, createdAt: new Date(now - 30e5).toISOString() },
      { id: 'l1', projectId: 'p1', jobId: 'j1', actionKey: 'mannequinGenerate', delta: -2, balanceAfter: 198, availableAfter: 198, createdAt: new Date(now - 36e5).toISOString() },
      { id: 'l0', projectId: null, jobId: null, actionKey: 'grant_subscription', delta: 200, balanceAfter: 200, availableAfter: 200, createdAt: new Date(now - 40e5).toISOString() },
    ];
  },
  async getCreditSources() {
    await wait(100);
    return [
      { id: 's1', sourceType: 'subscription', status: 'active', initialCredits: 200, remainingCredits: 196, periodEnd: new Date(Date.now() + 25 * 864e5).toISOString(), planId: 'm-basic', createdAt: new Date(Date.now() - 40e5).toISOString() },
    ];
  },

  /* ---- product input ---- */
  async getProduct(/* projectId */) { await wait(160); return clone(DB.product); },
  async saveProduct(_projectId, patch) {
    await wait(200);
    Object.assign(DB.product, patch);
    if (patch.name != null) { DB.project.title = patch.name; touch(); }
    return clone(DB.product);
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
  async saveAnalysis(_projectId, patch) {
    await wait(180);
    // 매칭 후보 목록은 서버(추천)가 소유 — matchClothing patch 는 통째로 덮지 않고
    // 아래에서 "선택 상태만" id 단위로 머지한다. 의류 종류 전환 직후 도착하는 묵은
    // 클라 스냅샷이 갱신된 후보 목록을 되살리는 레이스 차단 (stale save 방어).
    const { matchClothing: matchPatch, ...rest } = patch;
    Object.assign(DB.analysis, rest);
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
  async getMannequins(/* projectId */) { await wait(140); return clone(DB.mannequins); },
  // 최초 진입 시 A/B 후보를 생성한다. 크레딧: mannequinGenerate (계약 §6).
  // 진행 중에 다시 호출되면(이중 mount·재진입) 기존 job 에 합류한다 — 1회만 차감.
  // 이미 후보가 있으면(완료 후 재호출) 재실행·재차감 없이 기존 결과를 반환한다.
  async generateMannequins(_projectId, { onProgress } = {}) {
    if (DB.mannequins.length) {
      await wait(140);
      onProgress && onProgress(100);
      return { data: clone(DB.mannequins), credits: DB.account.credits };
    }
    const job = joinable('mannequins', (listeners) => (async () => {
      const ownerId = DB.project.id;   // job 도중 새 프로젝트로 리시드되면 결과를 버린다
      await runJob({ duration: 3000, stall: true, onProgress: (p) => listeners.forEach((f) => f(p)) });
      if (DB.project.id !== ownerId) return { data: [], credits: DB.account.credits };
      if (!DB.mannequins.length) {
        DB.mannequins.push(
          { id: 'A-0', candidate: 'A', version: 0, src: Placeholder.photo('A0', 'mannequin'), baseFit: 'regular', fitAdjust: null, lengthAdjust: null, matchAdjust: null },
          { id: 'B-0', candidate: 'B', version: 0, src: Placeholder.photo('B0', 'mannequin'), baseFit: 'slim', fitAdjust: null, lengthAdjust: null, matchAdjust: null },
        );
      }
      touch();
      return { data: clone(DB.mannequins), credits: spend(CREDIT_COSTS.mannequinGenerate) };
    })());
    if (onProgress) job.listeners.push(onProgress);
    return job.promise;
  },
  // 조정 값은 enum 토큰만 받는다: fitAdjust slimmer|looser, lengthAdjust shorter|longer.
  // '현재(변경 없음)' = 필드 생략. 라벨('슬림' 등)은 화면 표시에서만 파생한다.
  async adjustMannequin(_projectId, { baseId, fitAdjust, lengthAdjust, matchAdjust, onProgress } = {}) {
    await runJob({ duration: 1800, onProgress });
    const base = DB.mannequins.find((m) => m.id === baseId) || DB.mannequins[0];
    const sameCand = DB.mannequins.filter((m) => m.candidate === base.candidate);
    // 매칭 의류 조정을 차원별로 누적: 이번에 안 바뀐 차원은 base 값으로 폴백해서
    // 연속 조정 시 직전 차원이 사라지지 않게 (기존 동작 보존).
    const prev = base.matchAdjust;
    const nextMatch = matchAdjust
      ? {
          clothingId: matchAdjust.clothingId,
          fitAdjust: matchAdjust.fitAdjust || (prev && prev.clothingId === matchAdjust.clothingId ? prev.fitAdjust : null),
          lengthAdjust: matchAdjust.lengthAdjust || (prev && prev.clothingId === matchAdjust.clothingId ? prev.lengthAdjust : null),
        }
      : (prev ? { ...prev } : null);
    const next = {
      ...clone(base), id: base.candidate + '-' + sameCand.length, version: sameCand.length,
      fitAdjust: fitAdjust || base.fitAdjust || null,
      lengthAdjust: lengthAdjust || base.lengthAdjust || null,
      matchAdjust: nextMatch,
      src: Placeholder.photo(base.id + Date.now(), 'mannequin'),
    };
    DB.mannequins.push(next);
    DB.project.adjustCount += 1; touch();
    return { data: clone(next), credits: spend(CREDIT_COSTS.mannequinAdjust) };
  },
  async regenerateMannequins(_projectId, { onProgress } = {}) {
    await runJob({ duration: 2200, onProgress });
    ['A', 'B'].forEach((c) => {
      const n = DB.mannequins.filter((m) => m.candidate === c).length;
      DB.mannequins.push({ id: c + '-' + n, candidate: c, version: n,
        src: Placeholder.photo(c + n + Date.now(), 'mannequin'),
        baseFit: 'regular', fitAdjust: null, lengthAdjust: null, matchAdjust: null });
    });
    DB.project.adjustCount += 1; touch();
    return { data: clone(DB.mannequins), credits: spend(CREDIT_COSTS.mannequinGenerate) };
  },

  /* ---- storyboard (PRD §8) ---- */
  async getStoryboard(/* projectId */) { await wait(160); return clone(DB.storyboard); },
  async saveStoryboard(_projectId, blocks) { await wait(150); DB.storyboard = clone(blocks); touch(); return clone(DB.storyboard); },

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
  // req = NewCutRequest { mode:'new', colorId, cutType, direction?, shot?, modelId? }
  //     | VaryRequest   { mode:'vary', source:{src,cutType}, changes[], refBg? }  (계약 §6)
  async generateImage(_projectId, req = {}) {
    await runJob({ duration: 2400, onProgress: req.onProgress });
    const isVary = req.mode === 'vary';
    const group = isVary ? 'misc' : (req.colorId || 'misc');
    // cutType 은 생성 시점에 기록되는 메타데이터 — 이후 '현재 컷 변형'이 옵션 세트를 고르는 기준
    const cutType = isVary ? ((req.source && req.source.cutType) || 'styling') : (req.cutType || null);
    const img = { id: uid('w'), src: Placeholder.any('gen' + Date.now()), ai: true, ...(cutType ? { cutType } : {}) };
    (DB.wardrobe[group] = DB.wardrobe[group] || []).push(img);
    return { data: clone(img), credits: spend(CREDIT_COSTS.editorImage) };
  },
  // a fresh "any image" — mock 전용 헬퍼 (실서비스 계약은 uploadAsset(file))
  async pickAnyImage() { await wait(120); return Placeholder.any('pick' + Date.now()); },
  async download(/* projectId, format */) { await wait(800); return { ok: true }; },

  /* ---- library (PRD §4) ---- */
  async getLibrary({ forceEmpty = false, forceError = false } = {}) {
    await wait(420);
    if (forceError) throw new Error('보관함을 불러오지 못했어요.');
    return forceEmpty ? [] : clone(DB.library);
  },
};

export default api;
