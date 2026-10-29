import { registrationActive } from './mallRegisterDraft.js';

const copy = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
const unresolved = (view) => registrationActive(view) || ['unknown', 'duplicate_suspected'].includes(view?.outcome) || view?.setupStatus === 'needs_attention';
const viewItems = (views) => views.map((registration) => ({ mall: registration.mall, status: 'accepted', jobId: registration.jobId, registration }));
const storage = () => { try { return globalThis.sessionStorage; } catch { return null; } };
const storageKey = (id) => `wl-mall-register:${id}`;

export function createMallRegisterSession({ api, createId, isCurrent, onChange }) {
  let projectId, restored = false, batch = null, frozen = null, pending = null, startedAt = Date.now(), keptItems = [];
  const resumeKeys = new Map();
  const assertCurrent = () => { if (!isCurrent()) throw new Error('계정이나 상품이 바뀌었어요. 다시 열어 주세요'); };
  const emit = () => { assertCurrent(); onChange?.(copy(batch)); return copy(batch); };
  const remember = () => {
    assertCurrent();
    try {
      if (frozen || batch?.batchId) storage()?.setItem(storageKey(projectId), JSON.stringify({ request: frozen, batchId: batch?.batchId, keptItems }));
      else storage()?.removeItem(storageKey(projectId));
    } catch { /* Server history still restores unresolved jobs without local storage. */ }
  };
  const merge = (next) => ({ ...next, items: [...keptItems.filter((old) => !next.items.some((item) => item.mall === old.mall)), ...next.items] });
  const apply = (next) => { assertCurrent(); batch = merge(next); frozen = null; remember(); return emit(); };
  const find = (jobId) => batch?.items.find((item) => item.jobId === jobId)?.registration;
  const replaceView = (view) => {
    assertCurrent();
    keptItems = keptItems.map((item) => item.jobId === view.jobId ? { ...item, registration: view } : item);
    batch = { ...batch, timedOut: false, items: batch.items.map((item) => item.jobId === view.jobId ? { ...item, registration: view } : item) };
    remember(); return emit();
  };
  const send = async (draft) => {
    assertCurrent();
    if (!restored) throw new Error('이전 등록 내역을 먼저 확인해 주세요');
    if (!frozen) {
      if (batch?.items.some((item) => draft.selectedMalls.includes(item.mall)
        && (unresolved(item.registration) || item.registration?.outcome === 'created'))) throw new Error('이전 등록 결과를 먼저 확인해 주세요');
      frozen = { batchId: createId(), draft: copy(draft) }; remember();
    }
    const request = copy(frozen);
    try { return apply(await api.createMallRegistrationBatch(request)); }
    catch (error) {
      assertCurrent();
      // A definitive rejection has no receipt. Network/server failures keep the exact request.
      if (error.status >= 400 && error.status < 500) { frozen = null; remember(); }
      throw error;
    }
  };
  return {
    getState: () => ({ batch: copy(batch), pendingRequest: Boolean(frozen), restored }),
    async restore(id) {
      assertCurrent(); projectId = id; restored = false;
      const history = await api.listMallRegistrations(id); assertCurrent();
      const recoveries = [...new Map(Object.values(history.recoveries || {}).flat().map((view) => [view.jobId, view])).values()];
      let saved;
      try { saved = JSON.parse(storage()?.getItem(storageKey(id)) || 'null'); } catch { saved = null; }
      if (saved?.batchId || saved?.request) {
        keptItems = saved.keptItems || []; frozen = saved.request || null;
        try { batch = await api.getMallRegistrationBatch(saved.batchId || frozen.batchId); assertCurrent(); frozen = null; }
        catch (error) {
          assertCurrent();
          if (error.status !== 404 || !frozen) throw error;
          batch = { batchId: frozen.batchId, items: [], receiptUnconfirmed: true };
        }
      } else batch = recoveries.length ? { batchId: null, items: viewItems(recoveries), restored: true } : null;
      // Retained jobs belong to earlier receipts and must be refreshed independently.
      keptItems = await Promise.all(keptItems.map(async (item) => item.jobId && unresolved(item.registration)
        ? { ...item, registration: await api.getMallRegistration(item.jobId) } : item));
      assertCurrent();
      if (batch) {
        const receiptJobs = new Set(batch.items.map((item) => item.jobId));
        batch = merge(batch);
        for (const view of recoveries) {
          const index = batch.items.findIndex((item) => item.jobId === view.jobId);
          if (index >= 0) batch.items[index] = { ...batch.items[index], registration: view };
          else batch.items.push(...viewItems([view]));
        }
        if (batch.batchId) keptItems = batch.items.filter((item) => item.jobId && !receiptJobs.has(item.jobId));
      }
      restored = true; startedAt = Date.now(); remember(); return emit();
    },
    submit(draft) {
      if (pending) return pending;
      pending = send(draft).finally(() => { pending = null; }); return pending;
    },
    async refresh({ now = Date.now() } = {}) {
      assertCurrent();
      if (!batch || batch.receiptUnconfirmed) return emit();
      if (now - startedAt >= 600000) { batch = { ...batch, timedOut: true }; return emit(); }
      if (batch.batchId) {
        const [next, retained] = await Promise.all([
          api.getMallRegistrationBatch(batch.batchId),
          Promise.all(keptItems.map(async (item) => item.jobId && unresolved(item.registration)
            ? { ...item, registration: await api.getMallRegistration(item.jobId) } : item)),
        ]);
        assertCurrent(); keptItems = retained; return apply(next);
      }
      const items = await Promise.all(batch.items.map(async (item) => item.jobId ? { ...item, registration: await api.getMallRegistration(item.jobId) } : item));
      assertCurrent(); batch = { ...batch, items }; return emit();
    },
    async verify(jobId) {
      assertCurrent();
      const view = find(jobId);
      if (!['unknown', 'duplicate_suspected'].includes(view?.outcome) || view.retryAction !== 'verify') throw new Error('등록 여부를 확인할 수 없는 상태예요');
      return replaceView(await api.verifyMallRegistration(jobId));
    },
    async resume(jobId) {
      assertCurrent(); const view = find(jobId);
      if (view?.outcome !== 'created' || view.retryAction !== 'resume') throw new Error('설정을 이어 할 수 없는 상태예요');
      if (!resumeKeys.has(jobId)) resumeKeys.set(jobId, createId());
      const result = await api.resumeMallRegistration(jobId, { requestKey: resumeKeys.get(jobId) });
      assertCurrent(); startedAt = Date.now(); return replaceView(result);
    },
    async retryFailed(mall, draft) {
      assertCurrent();
      const item = batch?.items.find((entry) => entry.mall === mall);
      if (!item || (item.status !== 'blocked' && !(item.registration?.outcome === 'failed' && item.registration.retryAction === 'new_request'))
        || item.existingJobId) throw new Error('올리지 못한 몰만 다시 시도할 수 있어요');
      keptItems = copy(batch.items.filter((entry) => entry.mall !== mall));
      frozen = null; startedAt = Date.now();
      return this.submit({ ...draft, selectedMalls: [mall], confirmedSourcesByMall: Object.fromEntries(Object.entries(draft.confirmedSourcesByMall || {}).filter(([key]) => key === mall)) });
    },
    startNew() {
      assertCurrent();
      if (frozen || batch?.items.some((item) => unresolved(item.registration))) throw new Error('이전 등록 결과를 먼저 확인해 주세요');
      batch = null; keptItems = []; remember(); return emit();
    },
  };
}
