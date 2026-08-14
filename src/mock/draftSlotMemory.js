const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));

export function createDraftSlotMemory({
  tokenFactory = () => Math.random().toString(36).slice(2),
  now = () => new Date().toISOString(),
} = {}) {
  let slot = null;

  const metaFor = (payload, deviceLabel, photosPending) => {
    const photoCount = (payload?.product?.colors || []).reduce(
      (count, color) => count + (color.images || []).length,
      0,
    );
    return {
      updatedAt: now(),
      deviceLabel,
      photoCount,
      photosPending: Boolean(photosPending),
      // 서버 _draft_slot_meta 와 같은 기준 — 빈 슬롯(팬텀)은 진입 카드에서 숨긴다.
      hasContent: Boolean(
        photoCount > 0
        || (payload?.product?.name || '').trim()
        || payload?.analysis,
      ),
    };
  };

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
        const error = new Error('저장해 둔 내용이 다른 곳에서 정리돼 이 화면의 저장을 멈췄어요.');
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
      // 서버와 동일: 이어받기는 슬롯을 계속 쓰겠다는 의사 — updatedAt 갱신으로 지연 삭제에서 보호
      slot.meta = { ...slot.meta, updatedAt: now() };
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
