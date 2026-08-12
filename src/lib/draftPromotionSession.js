const PROMOTION_KEY = 'wl_draftPromotion';

function safeStorage(storage) {
  return storage || globalThis.localStorage || null;
}

export function createDraftPromotionSession({ storage = null } = {}) {
  const read = () => {
    try {
      return JSON.parse(safeStorage(storage)?.getItem(PROMOTION_KEY) || 'null') || {};
    } catch {
      return {};
    }
  };
  const write = (value) => {
    try { safeStorage(storage)?.setItem(PROMOTION_KEY, JSON.stringify(value)); } catch { /* 현재 페이지는 single-flight가 보호 */ }
  };

  return {
    read,
    rememberProject(projectId) {
      if (!projectId) return;
      write({ ...read(), projectId });
    },
    rememberAsset(imageId, asset) {
      if (!imageId || !asset?.assetId) return;
      const current = read();
      write({
        ...current,
        assets: { ...(current.assets || {}), [imageId]: asset },
      });
    },
    clear() {
      try { safeStorage(storage)?.removeItem(PROMOTION_KEY); } catch { /* noop */ }
    },
  };
}

export const draftPromotionSession = createDraftPromotionSession();

export function clearDraftPromotionSession() {
  draftPromotionSession.clear();
}
