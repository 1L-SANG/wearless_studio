/* =============================================================
   mock/api.js — the ONLY data access screens are allowed to use.
   (ported from handoff/contracts/api.js; window.* → ES module)
   Every function is async (returns a Promise) and mimics network
   latency, progress, and optional failure. Replace the bodies with
   real fetch() calls later; keep the signatures and shapes.
   ============================================================= */
import { DB, reseedDraft } from '@/mock/db.js';
import { Placeholder } from '@/mock/placeholders.js';

const clone = (x) => JSON.parse(JSON.stringify(x));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

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
  /* ---- session ---- */
  // re-seed the per-creation draft (mannequins/storyboard/analysis/…) so a new
  // creation never inherits the previous session's mutations. Sync + local.
  resetDraft() { reseedDraft(); },

  /* ---- reference / catalogs ---- */
  async getAccount() { await wait(120); return clone(DB.account); },
  async getCatalogs() { await wait(80); return clone(DB.catalogs); },

  /* ---- product input ---- */
  async getProduct() { await wait(160); return clone(DB.product); },
  async saveProduct(patch) { await wait(200); Object.assign(DB.product, patch); return clone(DB.product); },

  /* ---- AI analysis (6) — 30s-feel progress, can fail ---- */
  async analyzeProduct({ onProgress, forceError = false } = {}) {
    await runJob({ duration: 2800, onProgress });
    if (forceError) throw new Error('분석 서버에 일시적인 문제가 발생했어요.');
    const a = clone(DB.analysis);
    // 실측은 AI가 추정하지 않는다 — 사용자가 직접 입력하도록 빈칸으로 둔다.
    a.measurements = (a.measurements || []).map((m) => ({ ...m, value: null }));
    return a;
  },
  async saveAnalysis(patch) { await wait(180); Object.assign(DB.analysis, patch); return clone(DB.analysis); },
  async draftWashCare() {
    await wait(900);
    return '찬물 단독 손세탁 권장 · 표백제 사용 금지 · 그늘에 뉘어 건조';
  },

  /* ---- mannequin (7) ---- */
  async getMatchClothing() { await wait(120); return clone(DB.matchClothing); },
  async getMannequins() { await wait(140); return clone(DB.mannequins); },
  async generateMannequins({ onProgress } = {}) {
    await runJob({ duration: 3000, stall: true, onProgress });
    return clone(DB.mannequins);
  },
  async adjustMannequin({ baseId, fit, length, match, onProgress } = {}) {
    await runJob({ duration: 1800, onProgress });
    const base = DB.mannequins.find((m) => m.id === baseId) || DB.mannequins[0];
    const sameCand = DB.mannequins.filter((m) => m.candidate === base.candidate);
    // 매칭 의류 변경분도 결과 컷에 반영 (캡션으로 노출) — 변경 없으면 base 유지
    const matchLabel = match && (match.fit || match.length)
      ? `${match.name} ${[match.length === 'short' ? '숏기장' : match.length === 'long' ? '롱기장' : '',
          match.fit === 'slim' ? '슬림' : match.fit === 'loose' ? '여유' : ''].filter(Boolean).join(' ')}`.trim()
      : (base.matchLabel || '');
    const next = { ...clone(base), id: base.candidate + '-' + sameCand.length, version: sameCand.length,
      fitLabel: fit || base.fitLabel, lengthLabel: length || base.lengthLabel, matchLabel, selected: false,
      src: Placeholder.photo(base.id + Date.now(), 'mannequin') };
    DB.mannequins.push(next);
    return clone(next);
  },
  async regenerateMannequins({ onProgress } = {}) {
    await runJob({ duration: 2200, onProgress });
    ['A', 'B'].forEach((c) => {
      const n = DB.mannequins.filter((m) => m.candidate === c).length;
      DB.mannequins.push({ id: c + '-' + n, candidate: c, version: n,
        src: Placeholder.photo(c + n + Date.now(), 'mannequin'),
        fitLabel: '정핏', lengthLabel: '원본 기장', selected: false });
    });
    return clone(DB.mannequins);
  },

  /* ---- storyboard (9) ---- */
  async getStoryboard(/* mode */) { await wait(160); return clone(DB.storyboard); },
  async saveStoryboard(blocks) { await wait(150); DB.storyboard = clone(blocks); return clone(DB.storyboard); },

  /* ---- generation waiting (10) ---- */
  async generateDetailPage({ onProgress, onStep } = {}) {
    const steps = DB.genSteps.map((s) => ({ ...s, status: 'idle' }));
    const per = 100 / steps.length;
    for (let i = 0; i < steps.length; i++) {
      steps[i].status = 'running'; onStep && onStep(clone(steps));
      await runJob({ duration: 700, onProgress: (p) => onProgress && onProgress(Math.round(i * per + (p / 100) * per)) });
      steps[i].status = 'done'; onStep && onStep(clone(steps));
    }
    onProgress && onProgress(100);
    return clone(DB.editorBlocks);
  },

  /* ---- editor (11) ---- */
  async getEditorBlocks() { await wait(180); return clone(DB.editorBlocks); },
  async getWardrobe() { await wait(160); return clone(DB.wardrobe); },
  async generateImage({ group = '색상 1', onProgress } = {}) {
    await runJob({ duration: 2400, onProgress });
    const img = { id: DB.uid('w'), src: Placeholder.any('gen' + Date.now()), ai: true };
    (DB.wardrobe[group] = DB.wardrobe[group] || []).push(img);
    return clone(img);
  },
  // a fresh "any image" for the + insert flow
  async pickAnyImage() { await wait(120); return Placeholder.any('pick' + Date.now()); },
  async download(/* optionId */) { await wait(800); return { ok: true }; },

  /* ---- library (0) ---- */
  async getLibrary({ forceEmpty = false, forceError = false } = {}) {
    await wait(420);
    if (forceError) throw new Error('보관함을 불러오지 못했어요.');
    return forceEmpty ? [] : clone(DB.library);
  },
};

export default api;
