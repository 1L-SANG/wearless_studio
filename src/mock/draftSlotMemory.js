const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));

export function createDraftSlotMemory({
  tokenFactory = () => Math.random().toString(36).slice(2),
  now = () => new Date().toISOString(),
} = {}) {
  let slot = null;

  const metaFor = (payload, deviceLabel, photosPending) => ({
    updatedAt: now(),
    deviceLabel,
    photoCount: (payload?.product?.colors || []).reduce(
      (count, color) => count + (color.images || []).length,
      0,
    ),
    photosPending: Boolean(photosPending),
  });

  return {
    get(token, { full = false } = {}) {
      if (!slot) return null;
      const result = {
        meta: clone(slot.meta),
        holdsToken: Boolean(token) && token === slot.token,
      };
      if (full) result.payload = clone(slot.payload);
      return result;
    },
    put({ payload, token, deviceLabel, photosPending }) {
      if (!slot && token) {
        const error = new Error('이 기기의 임시저장 작업권이 만료됐어요.');
        error.status = 409;
        error.code = 'token_mismatch';
        error.meta = null;
        throw error;
      }
      if (slot && token !== slot.token) {
        const error = new Error('다른 기기에서 이어서 작업을 시작했어요.');
        error.status = 409;
        error.code = 'token_mismatch';
        error.meta = clone(slot.meta);
        throw error;
      }
      const nextToken = slot?.token || tokenFactory();
      slot = {
        token: nextToken,
        payload: clone(payload),
        meta: metaFor(payload, deviceLabel, photosPending),
      };
      return { token: nextToken, meta: clone(slot.meta) };
    },
    takeover() {
      if (!slot) return null;
      slot.token = tokenFactory();
      return clone({ token: slot.token, payload: slot.payload, meta: slot.meta });
    },
    remove(token) {
      if (slot && token !== slot.token) {
        const error = new Error('다른 기기에서 이어서 작업을 시작했어요.');
        error.status = 409;
        error.code = 'token_mismatch';
        error.meta = clone(slot.meta);
        throw error;
      }
      slot = null;
    },
    simulateConflict({ deviceLabel = 'iPhone Safari' } = {}) {
      if (!slot) return false;
      slot.token = tokenFactory();
      slot.meta = { ...slot.meta, deviceLabel, updatedAt: now() };
      return true;
    },
  };
}
