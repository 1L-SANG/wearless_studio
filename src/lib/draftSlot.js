const TOKEN_KEY = 'wl_draftSlotToken';
const SYNCED_AT_KEY = 'wl_draftSlotSyncedAt';
const SERVER_SYNCED_AT_KEY = 'wl_draftSlotServerSyncedAt';
const DEFAULT_DEBOUNCE_MS = 500;
const PHOTO_RETRY_MS = 2000;
const PUT_RETRY_MAX_MS = 30000;

function safeStorage(storage) {
  return storage || globalThis.localStorage || null;
}

function readStorage(storage, key) {
  try { return safeStorage(storage)?.getItem(key) || null; } catch { return null; }
}

function writeStorage(storage, key, value) {
  try {
    const target = safeStorage(storage);
    if (!target) return;
    if (value == null) target.removeItem(key);
    else target.setItem(key, value);
  } catch { /* storage 차단 시 현재 문서 수명 메모리 값만 사용 */ }
}

function browserName(ua) {
  if (/Edg\//.test(ua)) return 'Edge';
  if (/Chrome\//.test(ua) || /CriOS\//.test(ua)) return 'Chrome';
  if (/Firefox\//.test(ua) || /FxiOS\//.test(ua)) return 'Firefox';
  if (/Safari\//.test(ua)) return 'Safari';
  return '';
}

export function getDraftSlotDeviceLabel(ua = globalThis.navigator?.userAgent || '') {
  const device = /iPhone/.test(ua) ? 'iPhone'
    : /Android/.test(ua) ? 'Android'
      : /Macintosh|Mac OS X/.test(ua) ? 'Mac'
        : /Windows/.test(ua) ? 'Windows'
          : '기기';
  const browser = browserName(ua);
  return browser ? `${device} ${browser}` : device;
}

export function formatDraftRelativeTime(updatedAt, now = Date.now()) {
  const time = Date.parse(updatedAt || '');
  if (!Number.isFinite(time)) return '방금';
  const minutes = Math.max(0, Math.floor((now - time) / 60000));
  if (minutes < 1) return '방금';
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}

export function formatDraftClock(updatedAt) {
  const date = new Date(updatedAt || 0);
  if (Number.isNaN(date.getTime())) return '--:--';
  return new Intl.DateTimeFormat('ko-KR', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

export function localDraftMeta(draft) {
  if (!draft?.product) return null;
  return {
    updatedAt: draft.updatedAt || null,
    deviceLabel: getDraftSlotDeviceLabel(),
    photoCount: (draft.product.colors || []).reduce(
      (count, color) => count + (color.images || []).length,
      0,
    ),
    photosPending: false,
  };
}

function imageIds(product) {
  return new Set((product?.colors || []).flatMap(
    (color) => (color.images || []).map((image) => image.id),
  ));
}

export function createDraftSlotSync({
  adapter = null,
  storage = null,
  debounceMs = DEFAULT_DEBOUNCE_MS,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  let api = adapter;
  let token = readStorage(storage, TOKEN_KEY);
  let syncedAt = readStorage(storage, SYNCED_AT_KEY);
  let serverSyncedAt = readStorage(storage, SERVER_SYNCED_AT_KEY);
  let pendingSnapshot = null;
  let latestSnapshot = null;
  let timer = null;
  let putRetryTimer = null;
  let putRetryAttempt = 0;
  let putChain = Promise.resolve(null);
  let conflictHandler = null;
  let photosPendingHandler = null;
  let locked = false;
  let slotWritePending = false;
  let stagedPayload = null;
  const uploads = new Map();

  const setToken = (value) => {
    token = value || null;
    writeStorage(storage, TOKEN_KEY, token);
  };
  const setSyncedAt = (value) => {
    syncedAt = value || null;
    writeStorage(storage, SYNCED_AT_KEY, syncedAt);
  };
  const setServerSyncedAt = (value) => {
    serverSyncedAt = value || null;
    writeStorage(storage, SERVER_SYNCED_AT_KEY, serverSyncedAt);
  };
  const notifyPhotosPending = () => {
    const pending = slotWritePending || Boolean(pendingSnapshot)
      || [...uploads.values()].some((upload) => upload.status !== 'synced');
    photosPendingHandler?.(pending);
  };
  const currentImageIds = () => imageIds(latestSnapshot?.product);
  const shouldKeepUpload = (imageId) => currentImageIds().has(imageId);
  const discardUploaded = async (upload) => {
    if (!upload?.assetId || !api?.discardDraftSlotPhoto) return;
    try { await api.discardDraftSlotPhoto(upload.assetId); } catch { /* 서버 회수 작업이 재시도 */ }
  };

  const startPhotoUpload = (image) => {
    if (!api?.uploadDraftSlotPhoto || uploads.get(image.id)?.status === 'uploading') return;
    const record = uploads.get(image.id) || {};
    clearTimer(record.retryTimer);
    record.status = 'uploading';
    record.image = image;
    record.retryTimer = null;
    uploads.set(image.id, record);
    notifyPhotosPending();
    record.promise = fetch(image.src)
      .then((response) => response.blob())
      .then((blob) => api.uploadDraftSlotPhoto({
        filename: image.name || `${image.id}.jpg`,
        mime: (image.type && image.type.includes('/')) ? image.type : (blob.type || 'image/jpeg'),
        blob,
        purpose: 'draft_slot',
      }))
      .then(async (uploaded) => {
        if (!shouldKeepUpload(image.id) || locked) {
          await discardUploaded(uploaded);
          uploads.delete(image.id);
          return;
        }
        uploads.set(image.id, { status: 'uploaded', image, ...uploaded });
        if (latestSnapshot) queue(latestSnapshot);
      })
      .catch(() => {
        if (!shouldKeepUpload(image.id) || locked) {
          uploads.delete(image.id);
          return;
        }
        const failed = uploads.get(image.id) || {};
        failed.status = 'failed';
        failed.retryTimer = setTimer(() => startPhotoUpload(image), PHOTO_RETRY_MS);
        uploads.set(image.id, failed);
      })
      .finally(notifyPhotosPending);
  };

  const syncPhotos = (product) => {
    if (!api?.uploadDraftSlotPhoto || locked) return;
    const liveIds = imageIds(product);
    for (const [imageId, upload] of uploads) {
      if (liveIds.has(imageId)) continue;
      clearTimer(upload.retryTimer);
      uploads.delete(imageId);
      void discardUploaded(upload);
    }
    for (const color of product?.colors || []) {
      for (const image of color.images || []) {
        if (!image.src?.startsWith('blob:') || uploads.has(image.id)) continue;
        startPhotoUpload(image);
      }
    }
    notifyPhotosPending();
  };

  const payloadFor = (snapshot) => {
    let photosPending = false;
    const includedUploadIds = [];
    const product = snapshot.product ? {
      ...snapshot.product,
      colors: (snapshot.product.colors || []).map((color) => ({
        ...color,
        images: (color.images || []).flatMap((image) => {
          if (!image.src?.startsWith('blob:')) return [image];
          const upload = uploads.get(image.id);
          if (upload?.status === 'uploaded' || upload?.status === 'synced') {
            includedUploadIds.push(image.id);
            return [{ ...image, id: upload.assetId, src: upload.url }];
          }
          photosPending = true;
          return [];
        }),
      })),
    } : snapshot.product;
    return {
      payload: {
        product,
        analysis: snapshot.analysis || null,
        composeMode: snapshot.composeMode === 'extended' ? 'extended' : 'basic',
      },
      photosPending,
      includedUploadIds,
    };
  };

  const handleConflict = (error) => {
    if (error?.status !== 409 || error?.code !== 'token_mismatch') return false;
    locked = true;
    clearTimer(timer);
    clearTimer(putRetryTimer);
    timer = null;
    putRetryTimer = null;
    conflictHandler?.(error.meta || null);
    return true;
  };

  const schedulePutRetry = () => {
    if (locked || putRetryTimer) return;
    const delay = Math.min(PHOTO_RETRY_MS * (2 ** putRetryAttempt), PUT_RETRY_MAX_MS);
    putRetryAttempt += 1;
    putRetryTimer = setTimer(() => {
      putRetryTimer = null;
      void commit().catch(() => {});
    }, delay);
  };

  const put = async (snapshot) => {
    if (!api?.putDraftSlot || locked || !snapshot?.product) return null;
    syncPhotos(snapshot.product);
    const { payload, photosPending, includedUploadIds } = payloadFor(snapshot);
    slotWritePending = true;
    notifyPhotosPending();
    try {
      const result = await api.putDraftSlot({
        payload,
        token,
        deviceLabel: getDraftSlotDeviceLabel(),
        photosPending,
      });
      setToken(result?.token);
      setSyncedAt(snapshot.localUpdatedAt);
      setServerSyncedAt(result?.meta?.updatedAt);
      putRetryAttempt = 0;
      clearTimer(putRetryTimer);
      putRetryTimer = null;
      for (const imageId of includedUploadIds) {
        const upload = uploads.get(imageId);
        if (upload) uploads.set(imageId, { ...upload, status: 'synced' });
      }
      return result;
    } catch (error) {
      if (!handleConflict(error)) {
        pendingSnapshot = latestSnapshot || snapshot;
        schedulePutRetry();
      }
      throw error;
    } finally {
      slotWritePending = false;
      notifyPhotosPending();
    }
  };

  const commit = () => {
    clearTimer(timer);
    timer = null;
    if (!pendingSnapshot || locked) return putChain;
    const snapshot = pendingSnapshot;
    pendingSnapshot = null;
    putChain = putChain.catch(() => null).then(() => put(snapshot));
    notifyPhotosPending();
    return putChain;
  };

  function queue(snapshot) {
    if (!snapshot?.product || locked) return;
    latestSnapshot = snapshot;
    pendingSnapshot = snapshot;
    syncPhotos(snapshot.product);
    clearTimer(timer);
    timer = setTimer(commit, debounceMs);
    notifyPhotosPending();
  }

  const waitForUploads = async () => {
    const pending = [...uploads.values()].map((upload) => upload.promise).filter(Boolean);
    if (pending.length) await Promise.allSettled(pending);
  };

  return {
    configure(nextAdapter) { api = nextAdapter; },
    queue,
    syncPhotos,
    async flush() { await commit(); return putChain; },
    discard() {
      clearTimer(timer);
      clearTimer(putRetryTimer);
      timer = null;
      putRetryTimer = null;
      pendingSnapshot = null;
      latestSnapshot = null;
      notifyPhotosPending();
    },
    suspend() {
      locked = true;
      this.discard();
      for (const upload of uploads.values()) clearTimer(upload.retryTimer);
      return putChain;
    },
    resume() { locked = false; },
    onConflict(handler) { conflictHandler = handler; return () => { if (conflictHandler === handler) conflictHandler = null; }; },
    onPhotosPending(handler) { photosPendingHandler = handler; notifyPhotosPending(); return () => { if (photosPendingHandler === handler) photosPendingHandler = null; }; },
    async get({ full = false } = {}) {
      return api?.getDraftSlot ? api.getDraftSlot(token, { full }) : null;
    },
    async checkOwnership() {
      const slot = await this.get();
      if (slot && token && slot.holdsToken === false) {
        locked = true;
        conflictHandler?.(slot.meta || null);
      }
      return slot;
    },
    async takeover() {
      const result = await api?.takeoverDraftSlot?.();
      if (!result) return null;
      setToken(result.token);
      setServerSyncedAt(result.meta?.updatedAt);
      locked = false;
      return result;
    },
    async removeForNewFlow() {
      // 새 제작/처음부터 다시는 사용자의 명시적 폐기 의사다. 먼저 작업권을 인수한 뒤 같은
      // 토큰으로 DELETE한다. 확정 승격은 이 메서드를 쓰지 않아 작업권 상실을 우회하지 않는다.
      await this.takeover();
      return this.remove();
    },
    async remove() {
      const resumeSnapshot = latestSnapshot;
      const wasLocked = locked;
      locked = true;
      this.discard();
      try {
        await putChain;
        await waitForUploads();
        if (api?.deleteDraftSlot) await api.deleteDraftSlot(token);
      } catch (error) {
        locked = wasLocked;
        if (!locked && resumeSnapshot) queue(resumeSnapshot);
        throw error;
      }
      const uploaded = [...uploads.values()];
      for (const upload of uploaded) clearTimer(upload.retryTimer);
      uploads.clear();
      await Promise.allSettled(uploaded.map(discardUploaded));
      setToken(null);
      setSyncedAt(null);
      setServerSyncedAt(null);
      locked = false;
      notifyPhotosPending();
    },
    stage(payload) { stagedPayload = payload || null; },
    consumeStaged() {
      const payload = stagedPayload;
      stagedPayload = null;
      return payload;
    },
    hasUnsyncedChanges(localUpdatedAt) { return Boolean(localUpdatedAt && localUpdatedAt !== syncedAt); },
    getToken() { return token; },
    getSyncedAt() { return syncedAt; },
    getServerSyncedAt() { return serverSyncedAt; },
    isLocked() { return locked; },
  };
}

export const draftSlot = createDraftSlotSync();
