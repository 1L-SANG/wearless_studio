import { isPlaceholderPhotoSrc } from './imageTranscode.js';

const TOKEN_KEY = 'wl_draftSlotToken';
const SYNCED_AT_KEY = 'wl_draftSlotSyncedAt';
const SERVER_SYNCED_AT_KEY = 'wl_draftSlotServerSyncedAt';
const TAB_OWNER_KEY = 'wl_draftSlotOwnerTab';
// '새로 시작' 시 서버 슬롯 삭제가 서버 장애로 실패하면 결정 시각을 남기고 나중에 정리한다.
const PENDING_REMOVE_KEY = 'wl_draftSlotPendingRemove';
const TAB_CHANNEL_NAME = 'wl_draftSlotTabs';
const DEFAULT_DEBOUNCE_MS = 500;
const PHOTO_RETRY_MS = 2000;
const PUT_RETRY_MAX_MS = 30000;
// 다른 탭 생존 확인(ping) 응답 대기 — 크래시로 pagehide 없이 죽은 탭의 소유권이
// localStorage 에 남아 방문마다 잠금이 뜨는 것을 막는다.
const TAB_PROBE_WAIT_MS = 450;

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
  if (!updatedAt) return '--:--';
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
  documentId = null,
  debounceMs = DEFAULT_DEBOUNCE_MS,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  // 기본 매개변수 자리에 옵셔널 호출(?.())을 두면 esbuild(safari14 타깃)가
  // 임시변수를 스코프 밖에서 참조하는 코드를 만들어 프로덕션 번들이 부팅에서
  // 죽는다(ReferenceError). 반드시 함수 본문에서 채울 것.
  documentId = documentId
    ?? (globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2));
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
  let conflictLocked = false;
  let goneLocked = false;
  let suspended = false;
  let lockMeta = null;
  let slotWritePending = false;
  let stagedPayload = null;
  let requestEpoch = 0;
  let removeStorageListener = () => {};
  let removePagehideListener = () => {};
  let tabChannel = null;
  let probeTimer = null;
  // 생존 확인(450ms) 동안 들어온 편집 — 죽은 탭이었다면 인수 직후 되살려 첫 편집이
  // 서버 슬롯에서 영영 빠지는 일을 막는다. 산 탭이 확인되면 버리는 게 맞다(단일 작성자).
  let probeDroppedSnapshot = null;
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
  const getPendingRemoval = () => readStorage(storage, PENDING_REMOVE_KEY);
  const setPendingRemoval = (value) => writeStorage(storage, PENDING_REMOVE_KEY, value);
  // '새로 시작' 때 못 지운 옛 슬롯을 마저 정리한다. 결정 이후 다른 기기가 슬롯을 새로 썼다면
  // 그쪽 작업이 우선 — 지연 삭제를 포기한다(오래된 삭제 의사가 남의 새 작업을 지우면 안 된다).
  const resolvePendingRemoval = async () => {
    const decidedAtRaw = getPendingRemoval();
    if (!decidedAtRaw || !api?.deleteDraftSlot) return;
    // 로그아웃(resetIdentity) 등으로 신원이 바뀌면 요청 사이에 epoch 이 올라간다.
    // 각 응답 후 재검증해, Alice 의 삭제 결정이 Bob 세션으로 이어져 Bob 슬롯을
    // 지우는 교차 계정 사고를 막는다. 플래그가 지워졌으면 결정 자체가 철회된 것.
    const epoch = requestEpoch;
    const stillValid = () => requestEpoch === epoch && getPendingRemoval() === decidedAtRaw;
    const slot = await api.getDraftSlot?.(token);
    if (!stillValid()) return;
    if (!slot) {
      setPendingRemoval(null);
      return;
    }
    const slotUpdatedAt = Date.parse(slot.meta?.updatedAt || '');
    const decidedAt = Date.parse(decidedAtRaw);
    if (Number.isFinite(slotUpdatedAt) && Number.isFinite(decidedAt) && slotUpdatedAt > decidedAt) {
      setPendingRemoval(null);
      return;
    }
    const grabbed = await api.takeoverDraftSlot?.();
    if (!stillValid()) return;
    if (grabbed?.token) await api.deleteDraftSlot(grabbed.token);
    if (stillValid()) setPendingRemoval(null);
  };
  const stopScheduledWrites = () => {
    clearTimer(timer);
    clearTimer(putRetryTimer);
    timer = null;
    putRetryTimer = null;
  };
  const notifyLock = (meta) => {
    lockMeta = meta;
    conflictHandler?.(meta);
  };
  const stopProbe = () => {
    clearTimer(probeTimer);
    probeTimer = null;
  };
  // silent: 잠금(쓰기 차단)은 즉시 걸되 화면 안내는 미룬다 — 상대 탭 생존 확인 중에 쓴다.
  const lockForConflict = (meta, { silent = false } = {}) => {
    requestEpoch += 1;
    locked = true;
    conflictLocked = true;
    goneLocked = false;
    suspended = false;
    stopProbe();
    stopScheduledWrites();
    if (silent) lockMeta = meta || { deviceLabel: '다른 탭 또는 기기' };
    else notifyLock(meta || { deviceLabel: '다른 탭 또는 기기' });
  };
  const lockForGoneSlot = () => {
    requestEpoch += 1;
    locked = true;
    conflictLocked = false;
    goneLocked = true;
    suspended = false;
    stopProbe();
    stopScheduledWrites();
    notifyLock({ state: 'gone' });
  };
  const clearLock = () => {
    locked = false;
    conflictLocked = false;
    goneLocked = false;
    suspended = false;
    lockMeta = null;
    stopProbe();
    conflictHandler?.(null);
  };
  const ownsTab = () => readStorage(storage, TAB_OWNER_KEY) === documentId;
  const claimTab = ({ force = false } = {}) => {
    const owner = readStorage(storage, TAB_OWNER_KEY);
    if (!force && owner && owner !== documentId) {
      lockForConflict({ state: 'other-tab', deviceLabel: '이 브라우저의 다른 탭' });
      return false;
    }
    writeStorage(storage, TAB_OWNER_KEY, documentId);
    if (!ownsTab()) {
      lockForConflict({ state: 'other-tab', deviceLabel: '이 브라우저의 다른 탭' });
      return false;
    }
    return true;
  };
  const releaseTab = () => {
    if (ownsTab()) writeStorage(storage, TAB_OWNER_KEY, null);
  };
  const handleStorage = (event) => {
    if (![TOKEN_KEY, TAB_OWNER_KEY, SYNCED_AT_KEY, SERVER_SYNCED_AT_KEY].includes(event?.key)) return;
    try {
      const target = safeStorage(storage);
      if (event.storageArea && target && event.storageArea !== target) return;
    } catch {
      return;
    }
    if (event?.key === SYNCED_AT_KEY) {
      syncedAt = event.newValue || null;
      return;
    }
    if (event?.key === SERVER_SYNCED_AT_KEY) {
      serverSyncedAt = event.newValue || null;
      return;
    }
    if (event.key === TAB_OWNER_KEY) {
      if (event.newValue && event.newValue !== documentId) {
        lockForConflict({ state: 'other-tab', deviceLabel: '이 브라우저의 다른 탭' });
      } else if (!event.newValue && conflictLocked && lockMeta?.state === 'other-tab') {
        // 상대 탭이 정상 종료(pagehide)로 소유권을 놓았다 — 조용히 이어받아 잠금을 걷는다.
        claimTab({ force: true });
        clearLock();
      }
      return;
    }
    if ((event.newValue || null) === token) return;
    // storage 이벤트는 다른 탭의 화면 내용까지 현재 탭에 반영됐다는 뜻이 아니다.
    // 새 작업권을 빌려 쓰지 않고 현재 탭을 멈춰, 서로 다른 전체 스냅샷의 last-write-wins를 막는다.
    if (event.newValue == null) lockForGoneSlot();
    else lockForConflict({ state: 'other-tab', deviceLabel: '이 브라우저의 다른 탭' });
  };
  try {
    const eventTarget = typeof storage?.addEventListener === 'function' ? storage : globalThis;
    if (typeof eventTarget?.addEventListener === 'function') {
      eventTarget.addEventListener('storage', handleStorage);
      removeStorageListener = () => eventTarget.removeEventListener?.('storage', handleStorage);
    }
  } catch { /* storage 이벤트를 쓸 수 없으면 현재 문서 수명 메모리 값만 사용 */ }
  try {
    if (typeof globalThis.addEventListener === 'function') {
      const release = () => releaseTab();
      globalThis.addEventListener('pagehide', release);
      removePagehideListener = () => globalThis.removeEventListener?.('pagehide', release);
    }
  } catch { /* pagehide 미지원 환경은 명시적 이어받기로 stale owner를 교체 */ }
  try {
    // 같은 브라우저 탭끼리의 생존 확인 채널. 크래시로 죽은 탭은 응답하지 못하므로
    // 소유권만 남은 유령 잠금을 activate()의 ping/timeout 으로 걷어낸다.
    if (typeof BroadcastChannel === 'function') {
      tabChannel = new BroadcastChannel(TAB_CHANNEL_NAME);
      tabChannel.onmessage = (event) => {
        const message = event?.data;
        if (!message || message.from === documentId) return;
        // ping 은 '지금 소유자로 기록된 탭'을 지목해 묻는다 — 지목된 탭만 응답한다.
        if (message.t === 'ping' && message.owner === documentId && ownsTab() && !locked) {
          tabChannel.postMessage({ t: 'alive', from: documentId });
          return;
        }
        if (message.t === 'alive' && probeTimer != null && conflictLocked) {
          // 상대 탭이 실제로 살아 있다 — 이제서야 화면에 잠금을 알린다.
          stopProbe();
          probeDroppedSnapshot = null;
          notifyLock(lockMeta || { state: 'other-tab', deviceLabel: '이 브라우저의 다른 탭' });
        }
      };
      tabChannel.unref?.();
    }
  } catch { tabChannel = null; }
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
          // 올릴 수 없는 src(목 데모의 SVG 플레이스홀더 등)는 임시저장에 싣지 않는다 —
          // 실려 나가면 복원된 화면의 상품 사진이 되고, 확정 업로드에서 서버가 거부한다
          // (2026-08-17 사고). blob: 이 아니라는 이유로 통과시키던 자리다.
          if (isPlaceholderPhotoSrc(image.src)) return [];
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
    if (error?.status !== 409 || error?.code !== 'token_mismatch') {
      return false;
    }
    if (error.meta == null) {
      setToken(null);
      lockForGoneSlot();
      return true;
    }
    lockForConflict(error.meta);
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
    if (!claimTab()) return null;
    syncPhotos(snapshot.product);
    const { payload, photosPending, includedUploadIds } = payloadFor(snapshot);
    slotWritePending = true;
    notifyPhotosPending();
    const epoch = requestEpoch;
    try {
      try {
        // '새로 시작' 때 서버 장애로 못 지운 옛 슬롯이 있으면 새 저장 전에 마저 정리한다.
        if (getPendingRemoval()) await resolvePendingRemoval();
        const requestToken = token;
        const result = await api.putDraftSlot({
          payload,
          token: requestToken,
          deviceLabel: getDraftSlotDeviceLabel(),
          photosPending,
        });
        // 요청 중 다른 탭이 작업권을 가져갔다면 늦은 옛 응답으로 최신 토큰을 되돌리지 않는다.
        const storedToken = readStorage(storage, TOKEN_KEY);
        const responseIsCurrent = epoch === requestEpoch
          && ownsTab()
          && token === requestToken
          && (requestToken == null ? storedToken == null : storedToken === requestToken);
        if (responseIsCurrent) {
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
        }
        return result;
      } catch (error) {
        if (epoch === requestEpoch) {
          const handled = handleConflict(error);
          if (!handled) {
            pendingSnapshot = latestSnapshot || snapshot;
            schedulePutRetry();
          }
        }
        throw error;
      }
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
    if (!snapshot?.product) return;
    if (locked) {
      if (probeTimer != null) probeDroppedSnapshot = snapshot;
      return;
    }
    latestSnapshot = snapshot;
    pendingSnapshot = snapshot;
    syncPhotos(snapshot.product);
    clearTimer(timer);
    timer = setTimer(() => { void commit().catch(() => {}); }, debounceMs);
    notifyPhotosPending();
  }

  const waitForUploads = async () => {
    const pending = [...uploads.values()].map((upload) => upload.promise).filter(Boolean);
    if (pending.length) await Promise.allSettled(pending);
  };

  return {
    configure(nextAdapter) { api = nextAdapter; },
    activate() {
      const owner = readStorage(storage, TAB_OWNER_KEY);
      if (owner && owner !== documentId && tabChannel) {
        // 다른 탭 소유권 발견 — 쓰기는 즉시 멈추되, 그 탭이 진짜 살아 있는지 먼저 물어본다.
        // 응답이 오면 그때 잠금 화면을 알리고, 없으면(크래시·강제종료 잔재) 조용히 이어받는다.
        lockForConflict({ state: 'other-tab', deviceLabel: '이 브라우저의 다른 탭' }, { silent: true });
        probeTimer = setTimer(() => {
          probeTimer = null;
          claimTab({ force: true });
          clearLock();
          if (probeDroppedSnapshot) {
            const snapshot = probeDroppedSnapshot;
            probeDroppedSnapshot = null;
            queue(snapshot);
          }
        }, TAB_PROBE_WAIT_MS);
        try { tabChannel.postMessage({ t: 'ping', from: documentId, owner }); } catch { /* 응답 없음 → timeout 경로 */ }
        return false;
      }
      return claimTab();
    },
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
      requestEpoch += 1;
      locked = true;
      conflictLocked = false;
      goneLocked = false;
      suspended = true;
      this.discard();
      for (const upload of uploads.values()) clearTimer(upload.retryTimer);
      return putChain;
    },
    resume() {
      if (!suspended) return;
      suspended = false;
      if (claimTab()) clearLock();
    },
    onConflict(handler) {
      conflictHandler = handler;
      if (locked && lockMeta) handler(lockMeta);
      return () => { if (conflictHandler === handler) conflictHandler = null; };
    },
    onPhotosPending(handler) { photosPendingHandler = handler; notifyPhotosPending(); return () => { if (photosPendingHandler === handler) photosPendingHandler = null; }; },
    dispose() {
      requestEpoch += 1;
      stopProbe();
      stopScheduledWrites();
      putChain = putChain.catch(() => null);
      removeStorageListener();
      removeStorageListener = () => {};
      removePagehideListener();
      removePagehideListener = () => {};
      try { tabChannel?.close(); } catch { /* 이미 닫힘 */ }
      tabChannel = null;
      releaseTab();
    },
    async get({ full = false } = {}) {
      return api?.getDraftSlot ? api.getDraftSlot(token, { full }) : null;
    },
    async checkOwnership() {
      const slot = await this.get();
      if (!slot && token) {
        setToken(null);
        lockForGoneSlot();
        return slot;
      }
      if (slot && token && slot.holdsToken === false) {
        lockForConflict(slot.meta);
      }
      return slot;
    },
    async takeover() {
      const result = await api?.takeoverDraftSlot?.();
      requestEpoch += 1;
      putChain = putChain.catch(() => null);
      claimTab({ force: true });
      // 이어서 쓰기로 한 슬롯이다 — 과거의 '새로 시작' 지연 삭제 의사는 여기서 무효가 된다.
      setPendingRemoval(null);
      if (!result) {
        // 서버에 슬롯이 없다(204). 낡은 토큰을 남겨두면 다음 저장이 옛 토큰으로 409를 맞아
        // '다른 곳에서 정리됐어요' 잠금이 오발된다 — 비워서 새 슬롯 생성(token=null)으로 가게 한다.
        setToken(null);
        setServerSyncedAt(null);
        clearLock();
        return null;
      }
      setToken(result.token);
      setServerSyncedAt(result.meta?.updatedAt);
      clearLock();
      return result;
    },
    restartAfterGone() {
      if (!goneLocked) return false;
      requestEpoch += 1;
      putChain = putChain.catch(() => null);
      setToken(null);
      claimTab({ force: true });
      clearLock();
      return true;
    },
    async removeForNewFlow() {
      // 새 제작/처음부터 다시는 사용자의 명시적 폐기 의사다. 먼저 작업권을 인수한 뒤 같은
      // 토큰으로 DELETE한다. 확정 승격은 이 메서드를 쓰지 않아 작업권 상실을 우회하지 않는다.
      try {
        await this.takeover();
        await this.remove();
        setPendingRemoval(null);
        return true;
      } catch (error) {
        if (error?.status === 409 && error?.code === 'token_mismatch' && error?.meta == null) {
          // 슬롯이 이미 사라졌다 — 새로 시작 관점에선 이미 정리된 것과 같다.
          setToken(null);
          setPendingRemoval(null);
          clearLock();
          return true;
        }
        if (error?.status === 409) throw error; // 다른 기기가 방금 이어받음 — 사용자에게 알린다
        // 서버·네트워크 장애로 삭제하지 못했다 — 새로 시작 자체를 막지 않는다.
        // 결정 시각을 남겨 두고(지연 삭제) 서버가 돌아오면 다음 저장 전에 마저 정리한다.
        // resetIdentity 가 플래그도 함께 지우므로(로그아웃 교차계정 보호) 초기화 뒤에 남긴다.
        this.resetIdentity();
        setPendingRemoval(new Date().toISOString());
        return false;
      }
    },
    async remove() {
      const resumeSnapshot = latestSnapshot;
      const wasLocked = locked;
      const wasConflictLocked = conflictLocked;
      const wasGoneLocked = goneLocked;
      const wasSuspended = suspended;
      const wasLockMeta = lockMeta;
      const deleteToken = token;
      locked = true;
      conflictLocked = false;
      goneLocked = false;
      suspended = false;
      this.discard();
      try {
        await putChain;
        await waitForUploads();
        if (api?.deleteDraftSlot) await api.deleteDraftSlot(deleteToken);
      } catch (error) {
        if (error?.status === 409 && error?.code === 'token_mismatch') {
          if (error.meta == null) lockForGoneSlot();
          else lockForConflict(error.meta);
          throw error;
        }
        locked = wasLocked;
        conflictLocked = wasConflictLocked;
        goneLocked = wasGoneLocked;
        suspended = wasSuspended;
        lockMeta = wasLockMeta;
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
      conflictLocked = false;
      goneLocked = false;
      suspended = false;
      lockMeta = null;
      releaseTab();
      notifyPhotosPending();
    },
    resetIdentity() {
      requestEpoch += 1;
      putChain = putChain.catch(() => null);
      this.discard();
      for (const upload of uploads.values()) clearTimer(upload.retryTimer);
      uploads.clear();
      setToken(null);
      setSyncedAt(null);
      setServerSyncedAt(null);
      // 로그아웃 등 신원 초기화 시 지연 삭제 의사도 버린다 — 다음 계정의 슬롯을 지우면 안 된다.
      setPendingRemoval(null);
      clearLock();
      releaseTab();
      notifyPhotosPending();
    },
    stage(payload) { stagedPayload = payload || null; },
    consumeStaged() {
      const payload = stagedPayload;
      stagedPayload = null;
      return payload;
    },
    hasUnsyncedChanges(localUpdatedAt) { return Boolean(localUpdatedAt && localUpdatedAt !== syncedAt); },
    hasPendingRemoval() { return Boolean(getPendingRemoval()); },
    async retryPendingRemoval() {
      try { await resolvePendingRemoval(); } catch { /* 서버 복구 뒤 다음 저장 전에 재시도 */ }
    },
    getToken() { return token; },
    getSyncedAt() { return syncedAt; },
    getServerSyncedAt() { return serverSyncedAt; },
    isLocked() { return locked; },
  };
}

export const draftSlot = createDraftSlotSync();
