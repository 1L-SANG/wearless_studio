import { discardPendingDraftSave, flushProductDraftSave } from '../../../src/lib/draftStore.js';
import { draftSlot } from '../../../src/lib/draftSlot.js';

// IndexedDB와 사진 읽기 경계만 제어하고 저장 큐와 진입 판정은 실제 코드로 실행한다.
export function draftStoreFixture(t, initialDraft = null) {
  let saved = initialDraft;
  let writesPaused = false;
  let writeError = null;
  const waitingWrites = [];
  const writes = [];
  const cleanup = [];
  const storage = new Map(initialDraft ? [['wl_draftPending', '1']] : []);
  const originals = ['sessionStorage', 'indexedDB'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]);
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  } });
  Object.defineProperty(globalThis, 'indexedDB', { configurable: true, value: {
    open() {
      const request = {};
      queueMicrotask(() => {
        request.result = {
          close() {},
          transaction() {
            let finishWrite = null;
            const tx = { objectStore: () => ({
              get() {
                const result = {};
                queueMicrotask(() => { result.result = saved; result.onsuccess(); });
                return result;
              },
              put(value) {
                if (writeError) throw writeError;
                finishWrite = () => { saved = value; writes.push(value); tx.oncomplete(); };
                return {};
              },
              delete() { finishWrite = () => { saved = null; tx.oncomplete(); }; return {}; },
            }) };
            queueMicrotask(() => queueMicrotask(() => {
              if (!finishWrite) tx.oncomplete?.();
              else if (writesPaused) waitingWrites.push(finishWrite);
              else finishWrite();
            }));
            return tx;
          },
        };
        request.onsuccess();
      });
      return request;
    },
  } });
  draftSlot.setUserId('seller');
  const releaseWrites = () => { writesPaused = false; waitingWrites.splice(0).forEach((finish) => finish()); };
  t.after(async () => {
    releaseWrites();
    cleanup.forEach((run) => run());
    discardPendingDraftSave();
    await flushProductDraftSave().catch(() => null);
    draftSlot.resetIdentity();
    for (const [key, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return { writes, onCleanup(run) { cleanup.push(run); }, get saved() { return saved; }, get waitingWrites() { return waitingWrites.length; },
    failWrites(error) { writeError = error; }, pauseWrites() { writesPaused = true; }, releaseWrites };
}

export const drainDraftTasks = async () => { for (let i = 0; i < 12; i += 1) await new Promise(setImmediate); };
