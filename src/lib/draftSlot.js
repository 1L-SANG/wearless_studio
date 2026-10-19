import { baseProductPhoto, currentDraftVisuals } from './draftVisuals.js';
import { stripFeatureClientFields } from './featureRun.js';
import { clientApiFetch, readClientResponse } from './api/clientRequest.js';
import { isPlaceholderPhotoSrc } from './imageTranscode.js';
import { seoulClock } from './datetime.js';
import { fetchPhotoBytes } from './photoBytes.js';

const TOKEN_KEY = 'wl_draftSlotToken';
const SYNCED_AT_KEY = 'wl_draftSlotSyncedAt';
const SERVER_SYNCED_AT_KEY = 'wl_draftSlotServerSyncedAt';
const SYNCED_USER_KEY = 'wl_draftSlotSyncedUser';
const TAB_OWNER_KEY = 'wl_draftSlotOwnerTab';
// '새로 시작' 시 서버 슬롯 삭제가 서버 장애로 실패하면 결정 시각을 남기고 나중에 정리한다.
const PENDING_REMOVE_KEY = 'wl_draftSlotPendingRemove';
const TAB_CHANNEL_NAME = 'wl_draftSlotTabs';
const DEFAULT_DEBOUNCE_MS = 500;
const PHOTO_RETRY_MS = 2000;
// 서버 draft_asset_reclaimer.py의 7일 회수까지 6일의 여유를 남긴다.
const UNSYNCED_UPLOAD_MAX_AGE_MS = 24 * 60 * 60 * 1000;
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

export function getDraftSlotDeviceLabel(ua = globalThis.navigator?.userAgent || '', maxTouchPoints = globalThis.navigator?.maxTouchPoints || 0) {
  const device = /iPhone/.test(ua) ? 'iPhone'
    : /iPad/.test(ua) || (/Macintosh/.test(ua) && maxTouchPoints > 1) ? 'iPad'
      : /Android/.test(ua) || (/Linux/.test(ua) && !/CrOS/.test(ua) && maxTouchPoints > 1) ? (/Mobile/.test(ua) ? 'Android' : 'Android 태블릿')
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
  return seoulClock(date);
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
  now = null,
} = {}) {
  // 기본 매개변수 자리에 옵셔널 호출(?.())을 두면 esbuild(safari14 타깃)가
  // 임시변수를 스코프 밖에서 참조하는 코드를 만들어 프로덕션 번들이 부팅에서
  // 죽는다(ReferenceError). 반드시 함수 본문에서 채울 것.
  documentId = documentId
    ?? (globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2));
  now = now ?? (() => Date.now());
  let api = adapter;
  let visualRefs = {};
  let customMatch = null;
  const visualListeners = new Set();
  let visualsEnabled = false;
  const visualPrepares = new Map();
  const cancelVisualPreparations = () => {
    for (const controller of visualPrepares.values()) controller.abort();
    visualPrepares.clear();
  };
  const notifyVisuals = () => visualListeners.forEach((listener) => listener());
  let token = readStorage(storage, TOKEN_KEY);
  let syncedAt = readStorage(storage, SYNCED_AT_KEY);
  let serverSyncedAt = readStorage(storage, SERVER_SYNCED_AT_KEY);
  let userId;
  let syncedUserId = readStorage(storage, SYNCED_USER_KEY);
  let pendingSnapshot = null;
  let pendingIdentity = null;
  let latestSnapshot = null;
  let snapshotVersion = 0;
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
  let entryWritesPaused = false;
  let lockMeta = null;
  let slotWritePending = false;
  let stagedPayload = null;
  let requestEpoch = 0;
  let identityEpoch = 0;
  let removeStorageListener = () => {};
  let removePagehideListener = () => {};
  let tabChannel = null;
  let probeTimer = null;
  // 생존 확인(450ms) 동안 들어온 편집 — 죽은 탭이었다면 인수 직후 되살려 첫 편집이
  // 서버 슬롯에서 영영 빠지는 일을 막는다. 산 탭이 확인되면 버리는 게 맞다(단일 작성자).
  let probeDroppedSnapshot = null;
  const uploads = new Map();
  const retainedAssets = new Set();
  let remotePhotosGone = false;
  const missingPhotos = (product) => (product?.colors || []).flatMap((color) => color.images || [])
    .filter((image) => remotePhotosGone && !image.src?.startsWith('blob:') && !isPlaceholderPhotoSrc(image.src));
  const clearGoneUploads = () => {
    for (const upload of uploads.values()) clearTimer(upload.retryTimer);
    uploads.clear();
    remotePhotosGone = true;
    setSyncedAt(null);
    setServerSyncedAt(null);
  };
  const saves = new Set();
  const finishSave = (save, status, photoCount = 0, error) => {
    saves.delete(save);
    const result = { status, photoCount, ...(save.missingPhotoCount ? { missingPhotoCount: save.missingPhotoCount } : {}) };
    if (error) Object.defineProperty(result, 'error', { value: error });
    save.resolve(result);
  };
  const finishSaves = (status, error) => {
    for (const save of saves) finishSave(save, status, 0, error);
  };

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
    syncedUserId = serverSyncedAt ? userId || null : null;
    writeStorage(storage, SYNCED_USER_KEY, syncedUserId);
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
    const expectedUserId = userId;
    const identity = identityEpoch;
    const stillValid = () => requestEpoch === epoch && userId === expectedUserId
      && identityEpoch === identity && getPendingRemoval() === decidedAtRaw;
    const options = { expectedUserId, isCurrent: stillValid };
    const slot = await api.getDraftSlot?.(token, options);
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
    const grabbed = await api.takeoverDraftSlot?.({ deviceLabel: getDraftSlotDeviceLabel() }, options);
    if (!stillValid()) return;
    if (grabbed?.token) await api.deleteDraftSlot(grabbed.token, options);
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
    finishSaves('locked');
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
    clearGoneUploads();
    finishSaves('gone');
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
    if (upload.everSynced || retainedAssets.has(upload.assetId)) return;
    if (upload.pendingPuts) { upload.discardRequested = true; return; }
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
    // 원본(blob:) 을 못 읽은 건 재시도로 안 풀린다 — 폐기된 주소는 영원히 ERR_FILE_NOT_FOUND 다.
    // 승격 실패 복구가 폐기된 사진을 담은 초안을 되살리자 2초마다 무한 재시도했다(2026-09-26).
    // 재시도는 업로드(네트워크·서버) 실패에만 건다.
    let sourceUnreadable = false;
    const uploadIdentity = identityEpoch;
    const uploadUser = userId;
    const uploadIsCurrent = () => uploadIdentity === identityEpoch && uploadUser === userId;
    record.promise = clientApiFetch(image.src)
      .then((response) => readClientResponse(response, 'blob'))
      .catch((error) => { sourceUnreadable = true; throw error; })
      .then((blob) => uploadIsCurrent() ? api.uploadDraftSlotPhoto({
        filename: image.name || `${image.id}.jpg`,
        mime: (image.type && image.type.includes('/')) ? image.type : (blob.type || 'image/jpeg'),
        blob,
        purpose: 'draft_slot',
      }, { expectedUserId: uploadUser, isCurrent: uploadIsCurrent }) : null)
      .then(async (uploaded) => {
        if (!uploaded) return;
        if (uploads.get(image.id) !== record || !shouldKeepUpload(image.id) || locked) {
          await discardUploaded(uploaded);
          if (uploads.get(image.id) === record) uploads.delete(image.id);
          return;
        }
        uploads.set(image.id, { status: 'uploaded', image, ...uploaded, uploadedAt: now() });
        if (latestSnapshot) queue(latestSnapshot);
      })
      .catch((error) => {
        if (uploads.get(image.id) !== record) return;
        if (!shouldKeepUpload(image.id) || locked) {
          uploads.delete(image.id);
          return;
        }
        const failed = uploads.get(image.id) || {};
        failed.status = 'failed';
        failed.error = error;
        failed.retryTimer = sourceUnreadable
          ? null
          : setTimer(() => startPhotoUpload(image), PHOTO_RETRY_MS);
        uploads.set(image.id, failed);
        for (const save of saves) {
          if (save.images.some((required) => required.id === image.id && required.src === image.src)) {
            finishSave(save, 'failed', 0, error);
          }
        }
      })
      .finally(notifyPhotosPending);
  };

  const syncPhotos = (product) => {
    if (!api?.uploadDraftSlotPhoto || locked) return;
    const liveIds = imageIds(product);
    for (const [imageId, upload] of uploads) {
      const stale = upload.status === 'uploaded'
        && now() - upload.uploadedAt > UNSYNCED_UPLOAD_MAX_AGE_MS;
      const live = (product?.colors || []).flatMap((color) => color.images || []).find((image) => image.id === imageId);
      if (liveIds.has(imageId) && !stale && upload.image?.src === live?.src) continue;
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
          if (!image.src?.startsWith('blob:')) return remotePhotosGone ? [] : [image];
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
    const serializedVisualRefs = { ...visualRefs };
    const currentFront = baseProductPhoto(snapshot.product);
    if (serializedVisualRefs.product) {
      if (serializedVisualRefs.product.imageId === currentFront?.id && serializedVisualRefs.product.src === currentFront?.src) {
        serializedVisualRefs.product = { ...serializedVisualRefs.product,
          imageId: baseProductPhoto(product)?.id, src: baseProductPhoto(product)?.src };
      } else delete serializedVisualRefs.product;
    }
    return {
      payload: {
        product,
        analysis: stripFeatureClientFields(snapshot.analysis) || null,
        composeMode: snapshot.composeMode === 'extended' ? 'extended' : 'basic',
        ...(customMatch ? { customMatch } : {}),
        ...(Object.keys(serializedVisualRefs).length ? { draftVisuals: serializedVisualRefs } : {}),
      },
      photosPending,
      includedUploadIds,
    };
  };

  const finishSaved = (snapshot, version, payload, photosPending) => {
    if (photosPending) return;
    const images = (snapshot.product.colors || []).flatMap((color) => color.images || []);
    const savedImages = (payload.product.colors || []).flatMap((color) => color.images || []);
    for (const save of saves) {
      // 호출 전 PUT 응답이나 사진이 빠진 후속 스냅샷으로 완료 처리하지 않는다.
      if (version < save.version) continue;
      const complete = save.images.every((required) => {
        if (!images.some((image) => image.id === required.id && image.src === required.src)) return false;
        if (!required.src?.startsWith('blob:')) {
          return savedImages.some((image) => image.id === required.id && image.src === required.src);
        }
        const upload = uploads.get(required.id);
        return upload?.status === 'synced' && upload.image.src === required.src
          && savedImages.some((image) => image.id === upload.assetId && image.src === upload.url);
      });
      if (complete) finishSave(save, 'saved', savedImages.length);
    }
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
    if (locked || entryWritesPaused || putRetryTimer) return;
    const delay = Math.min(PHOTO_RETRY_MS * (2 ** putRetryAttempt), PUT_RETRY_MAX_MS);
    putRetryAttempt += 1;
    putRetryTimer = setTimer(() => {
      putRetryTimer = null;
      void commit().catch(() => {});
    }, delay);
  };

  const put = async (snapshot, version, identity) => {
    const current = () => identity.userId === userId && identity.epoch === identityEpoch
      && identity.requestEpoch === requestEpoch && !entryWritesPaused;
    if (!current() || !api?.putDraftSlot || locked || !snapshot?.product) return null;
    if (!claimTab()) return null;
    slotWritePending = true;
    notifyPhotosPending();
    try {
      try {
        // '새로 시작' 때 서버 장애로 못 지운 옛 슬롯이 있으면 새 저장 전에 마저 정리한다.
        if (getPendingRemoval()) await resolvePendingRemoval();
        if (!current()) return null;
        syncPhotos(snapshot.product);
        const { payload, photosPending, includedUploadIds } = payloadFor(snapshot);
        const includedUploads = new Map(includedUploadIds.map((id) => [id, uploads.get(id)]));
        for (const upload of includedUploads.values()) upload.pendingPuts = (upload.pendingPuts || 0) + 1;
        const requestToken = token;
        let result;
        try {
          result = await api.putDraftSlot({
            payload,
            token: requestToken,
            deviceLabel: getDraftSlotDeviceLabel(),
            photosPending,
          }, { expectedUserId: identity.userId, isCurrent: current });
          // 늦은 응답이어도 성공한 PUT의 사진은 서버 보존 기간을 따른다.
          for (const upload of includedUploads.values()) {
            upload.everSynced = true;
            retainedAssets.add(upload.assetId);
          }
        } catch (error) {
          // 응답을 못 받은 요청도 서버가 사진을 참조했을 수 있어 7일 회수에 맡긴다.
          if (!error?.requestNotSent) {
            for (const upload of includedUploads.values()) retainedAssets.add(upload.assetId);
          }
          throw error;
        } finally {
          for (const upload of includedUploads.values()) {
            upload.pendingPuts -= 1;
            if (upload.discardRequested) void discardUploaded(upload);
          }
        }
        if (!current()) return null;
        // 요청 중 다른 탭이 작업권을 가져갔다면 늦은 옛 응답으로 최신 토큰을 되돌리지 않는다.
        const storedToken = readStorage(storage, TOKEN_KEY);
        const responseIsCurrent = current()
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
            if (upload && upload === includedUploads.get(imageId)) {
              upload.status = 'synced';
            }
          }
          finishSaved(snapshot, version, payload, photosPending);
        }
        return result;
      } catch (error) {
        if (current()) {
          const handled = handleConflict(error);
          if (!handled) {
            finishSaves('failed', error);
            pendingSnapshot = latestSnapshot || snapshot;
            pendingIdentity = identity;
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
    if (!pendingSnapshot || locked || entryWritesPaused) return putChain;
    const snapshot = pendingSnapshot;
    const version = snapshotVersion;
    const identity = pendingIdentity;
    pendingSnapshot = null;
    pendingIdentity = null;
    putChain = putChain.catch(() => null).then(() => put(snapshot, version, identity));
    notifyPhotosPending();
    return putChain;
  };

  function queue(snapshot) {
    if (!snapshot?.product) return;
    if (entryWritesPaused) return;
    if (locked) {
      if (probeTimer != null) probeDroppedSnapshot = snapshot;
      return;
    }
    latestSnapshot = snapshot;
    snapshotVersion += 1;
    pendingSnapshot = snapshot;
    pendingIdentity = { userId, epoch: identityEpoch, requestEpoch };
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
    setVisualsEnabled(expectedUserId, enabled) {
      if (!userId || expectedUserId !== userId) return;
      const next = enabled === true;
      if (visualsEnabled === next) return;
      visualsEnabled = next;
      if (!next) cancelVisualPreparations();
      notifyVisuals();
    },
    isVisualsEnabled() { return Boolean(userId) && visualsEnabled; },
    adoptVisualHandoff(payload, restoredProduct) {
      if (!userId) return;
      if (payload?.customMatch?.sources?.length) customMatch = payload.customMatch;
      const saved = payload?.visuals || payload?.draftVisuals;
      if (!saved) return;
      const photo = baseProductPhoto(restoredProduct);
      const original = baseProductPhoto(payload.product);
      const front = saved.product;
      if (front?.jobId && photo && original?.id === front.imageId && photo.id === original.id) {
        const sourceUrl = front.sourceUrl || (!photo.src?.startsWith('blob:') ? photo.src : null);
        if (sourceUrl && front.sourceAssetIds?.[0]) {
          visualRefs.product = { ...front, imageId: photo.id, src: photo.src, sourceUrl };
          if (photo.src?.startsWith('blob:')) {
            uploads.set(photo.id, { status: 'synced', image: photo, assetId: front.sourceAssetIds[0],
              url: sourceUrl, everSynced: true });
            retainedAssets.add(front.sourceAssetIds[0]);
          }
        }
      }
      const custom = saved.custom;
      const sources = custom?.sources || payload.customMatch?.sources;
      if (custom?.jobId && sources?.length) {
        visualRefs.custom = { ...custom, sources };
        customMatch = { localId: custom.localId, sources };
      }
      notifyVisuals();
    },
    onVisuals(listener) { visualListeners.add(listener); return () => visualListeners.delete(listener); },
    getVisualHandoff(product = latestSnapshot?.product, customId = customMatch?.localId) {
      const photo = baseProductPhoto(product);
      return {
        ...(visualRefs.product && visualRefs.product.imageId === photo?.id && visualRefs.product?.src === photo?.src
          ? { product: { ...visualRefs.product } } : {}),
        ...(visualRefs.custom && visualRefs.custom.localId === customId ? { custom: { ...visualRefs.custom } } : {}),
      };
    },
    getVisualSources(product = latestSnapshot?.product, customId = customMatch?.localId) {
      const photo = baseProductPhoto(product);
      const upload = uploads.get(photo?.id);
      const assetId = upload && upload.image?.src === photo?.src ? upload.assetId
        : (!photo?.src?.startsWith('blob:') ? photo?.id : null);
      return {
        ...(assetId ? { product: { sourceAssetIds: [assetId] } } : {}),
        ...(customMatch?.localId === customId && customMatch?.sources?.length
          ? { custom: { localId: customId, sourceAssetIds: customMatch.sources.map((source) => source.id) } } : {}),
      };
    },
    recoverVisualHandoff(result, product, customId) {
      const current = currentDraftVisuals(result, this.getVisualSources(product, customId));
      const photo = baseProductPhoto(product);
      let changed = false;
      if (current.product?.jobId && (visualRefs.product?.jobId !== current.product.jobId
          || visualRefs.product?.imageId !== photo.id || visualRefs.product?.src !== photo.src)) {
        visualRefs.product = { jobId: current.product.jobId, imageId: photo.id, src: photo.src,
          sourceAssetIds: current.product.sourceAssetIds, sourceUrl: uploads.get(photo.id)?.url || photo.src };
        changed = true;
      }
      if (current.custom?.jobId && (visualRefs.custom?.jobId !== current.custom.jobId
          || visualRefs.custom?.localId !== customId)) {
        visualRefs.custom = { jobId: current.custom.jobId, localId: customId,
          sourceAssetIds: current.custom.sourceAssetIds, sources: customMatch.sources };
        changed = true;
      }
      if (changed) {
        if (latestSnapshot) queue(latestSnapshot);
        notifyVisuals();
      }
      return current;
    },
    settleVisualPreparations({ timeoutMs = 3000, signal } = {}) {
      if (!visualPrepares.size) return Promise.resolve();
      return new Promise((resolve) => {
        let timer;
        const finish = (cancel = false) => {
          clearTimer(timer);
          signal?.removeEventListener('abort', abort);
          if (cancel) cancelVisualPreparations();
          resolve();
        };
        const abort = () => finish(true);
        if (signal?.aborted) { abort(); return; }
        signal?.addEventListener('abort', abort, { once: true });
        timer = setTimer(abort, timeoutMs);
        void Promise.allSettled([...visualPrepares.keys()]).then(() => finish());
      });
    },
    prepareProductVisuals(product) {
      if (!this.isVisualsEnabled()) return Promise.resolve();
      const controller = new AbortController();
      const task = this.runProductVisualPreparation(product, controller.signal);
      visualPrepares.set(task, controller);
      void task.finally(() => visualPrepares.delete(task)).catch(() => {});
      return task;
    },
    async runProductVisualPreparation(product, signal) {
      if (!this.isVisualsEnabled() || locked || !api?.prepareDraftVisuals) return;
      const epoch = identityEpoch;
      const photo = baseProductPhoto(product);
      if (!photo) return;
      if (visualRefs.product?.jobId && visualRefs.product.imageId === photo.id && visualRefs.product.src === photo.src) return;
      const current = () => epoch === identityEpoch && !locked && visualsEnabled && !signal?.aborted
        && baseProductPhoto(latestSnapshot?.product)?.id === photo.id
        && baseProductPhoto(latestSnapshot?.product)?.src === photo.src;
      if (!latestSnapshot) queue({ product });
      syncPhotos(product);
      // Wait only for Front. Other colour uploads never delay analysis or this request.
      await uploads.get(photo.id)?.promise;
      if (!current()) return;
      const upload = uploads.get(photo.id);
      const assetId = upload?.assetId || (!photo.src?.startsWith('blob:') ? photo.id : null);
      if (!assetId) return;
      await this.flush();
      if (!current() || !token) return;
      const binding = { imageId: photo.id, src: photo.src, sourceAssetIds: [assetId], sourceUrl: upload?.url || photo.src };
      const result = await api.prepareDraftVisuals(token, { role: 'product', sourceAssetIds: [assetId] },
        { expectedUserId: userId, isCurrent: current, signal });
      if (!current() || !result?.jobId) return;
      visualRefs.product = { ...binding, jobId: result?.jobId };
      if (latestSnapshot) queue(latestSnapshot);
      notifyVisuals();
    },
    prepareCustomVisuals(draft) {
      if (!this.isVisualsEnabled()) return Promise.resolve();
      const controller = new AbortController();
      const task = this.runCustomVisualPreparation(draft, controller.signal);
      visualPrepares.set(task, controller);
      void task.finally(() => visualPrepares.delete(task)).catch(() => {});
      return task;
    },
    async runCustomVisualPreparation(draft, signal) {
      if (!this.isVisualsEnabled() || locked || !draft?.uploads?.length || !api?.prepareDraftVisuals) return;
      if (visualRefs.custom?.jobId && visualRefs.custom.localId === draft.localId) return;
      const epoch = identityEpoch;
      const current = () => epoch === identityEpoch && !locked && visualsEnabled && !signal?.aborted
        && latestSnapshot?.analysis?.matchClothing?.some((item) => item.isCustom && item.id === draft.localId);
      await this.flush();
      if (!current() || !token) return;
      const expectedUserId = userId;
      const uploaded = await Promise.all(draft.uploads.map(async (up) => api.uploadDraftSlotPhoto({ ...up,
        blob: up.blob || await fetchPhotoBytes(up.src, { signal }) }, { expectedUserId, isCurrent: current, signal })));
      if (!current()) {
        await Promise.allSettled(uploaded.map((up) => api.discardDraftSlotPhoto?.(up.assetId)));
        return;
      }
      customMatch = { localId: draft.localId, selected: draft.selected,
        sources: uploaded.map((up) => ({ id: up.assetId, src: up.url })) };
      queue(latestSnapshot);
      await this.flush();
      if (!current()) return;
      const binding = { localId: draft.localId, sourceAssetIds: uploaded.map((up) => up.assetId) };
      const result = await api.prepareDraftVisuals(token, { role: 'custom', ...binding },
        { expectedUserId, isCurrent: current, signal });
      if (!current() || !result?.jobId) return;
      visualRefs.custom = { ...binding, sources: customMatch.sources, jobId: result?.jobId };
      queue(latestSnapshot);
      notifyVisuals();
    },
    clearCustomVisuals() {
      if (!customMatch && !visualRefs.custom) return;
      customMatch = null; delete visualRefs.custom;
      if (latestSnapshot) queue(latestSnapshot);
      notifyVisuals();
    },
    configure(nextAdapter) { api = nextAdapter; },
    getDiagnosticState() {
      return { photo_pending_count: Math.min(1000, [...uploads.values()].filter((upload) => upload.status !== 'synced').length),
        draft_slot_status: locked ? 'lost' : token ? 'owned' : userId === undefined ? 'unknown' : 'none' };
    },
    setUserId(value) {
      if ((userId !== undefined && userId !== value) || (syncedUserId && syncedUserId !== value)) this.resetIdentity();
      else if (userId !== value) this.discard();
      userId = value;
    },
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
    saveAll(snapshot) {
      return new Promise((resolve) => {
        const save = {
          resolve,
          version: snapshotVersion + 1,
          images: (snapshot?.product?.colors || []).flatMap((color) => color.images || [])
            .filter((image) => !isPlaceholderPhotoSrc(image.src) && (!remotePhotosGone || image.src?.startsWith('blob:'))),
          missingPhotoCount: snapshot?.missingPhotoCount || missingPhotos(snapshot?.product).length,
        };
        saves.add(save);
        queue(snapshot);
        if (entryWritesPaused) { finishSave(save, 'failed'); return; }
        if (locked) {
          finishSave(save, goneLocked ? 'gone' : 'locked');
          return;
        }
        for (const image of save.images) {
          const upload = uploads.get(image.id);
          // 명시적 재시도는 대기 타이머를 건너뛰되, 읽을 수 없는 원본은 다시 시도하지 않는다.
          if (image.src?.startsWith('blob:') && upload?.status === 'failed' && upload.retryTimer != null) {
            startPhotoUpload(image);
          }
        }
        if (!snapshot?.product || !api?.putDraftSlot || save.images.some((image) => (
          image.src?.startsWith('blob:') && uploads.get(image.id)?.status === 'failed'
        ))) finishSave(save, 'failed', 0, save.images.map((image) => uploads.get(image.id)?.error).find(Boolean));
      });
    },
    retryFailedUploads() {
      if (locked) return;
      for (const upload of uploads.values()) {
        if (upload.status === 'failed' && upload.retryTimer != null) startPhotoUpload(upload.image);
      }
    },
    syncPhotos,
    async flush() { await commit(); return putChain; },
    discard() {
      cancelVisualPreparations();
      requestEpoch += 1;
      clearTimer(timer);
      clearTimer(putRetryTimer);
      timer = null;
      putRetryTimer = null;
      pendingSnapshot = null;
      pendingIdentity = null;
      latestSnapshot = null;
      probeDroppedSnapshot = null;
      notifyPhotosPending();
    },
    pauseEntryWrites() {
      if (entryWritesPaused) return;
      entryWritesPaused = true;
      this.discard();
      finishSaves('failed');
    },
    resumeEntryWrites() { entryWritesPaused = false; },
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
      cancelVisualPreparations();
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
      const epoch = identityEpoch;
      const expectedUserId = userId;
      const current = () => epoch === identityEpoch && expectedUserId === userId;
      const slot = api?.getDraftSlot ? await api.getDraftSlot(token, { full, expectedUserId, isCurrent: current }) : null;
      return current() ? slot : null;
    },
    async checkOwnership() {
      const epoch = identityEpoch;
      const slot = await this.get();
      if (epoch !== identityEpoch) return null;
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
    async takeover({ isCurrent = () => true } = {}) {
      const epoch = identityEpoch;
      const request = requestEpoch;
      const expectedUserId = userId;
      const current = () => epoch === identityEpoch && request === requestEpoch
        && expectedUserId === userId && isCurrent();
      if (!current()) return null;
      const result = await api?.takeoverDraftSlot?.({ deviceLabel: getDraftSlotDeviceLabel() }, { expectedUserId, isCurrent: current });
      if (!current()) return null;
      requestEpoch += 1;
      putChain = putChain.catch(() => null);
      claimTab({ force: true });
      // 이어서 쓰기로 한 슬롯이다 — 과거의 '새로 시작' 지연 삭제 의사는 여기서 무효가 된다.
      setPendingRemoval(null);
      if (!result) {
        // 서버에 슬롯이 없다(204). 낡은 토큰을 남겨두면 다음 저장이 옛 토큰으로 409를 맞아
        // '다른 곳에서 정리됐어요' 잠금이 오발된다 — 비워서 새 슬롯 생성(token=null)으로 가게 한다.
        setToken(null);
        clearGoneUploads();
        finishSaves('gone');
        clearLock();
        if (latestSnapshot) queue(latestSnapshot);
        return null;
      }
      remotePhotosGone = false;
      visualRefs = result.payload?.draftVisuals || {};
      customMatch = result.payload?.customMatch || null;
      notifyVisuals();
      setToken(result.token);
      setServerSyncedAt(result.meta?.updatedAt);
      clearLock();
      return result;
    },
    restartAfterGone() {
      if (!goneLocked) return false;
      clearGoneUploads();
      requestEpoch += 1;
      putChain = putChain.catch(() => null);
      setToken(null);
      claimTab({ force: true });
      clearLock();
      return true;
    },
    async recoverGonePhotos(snapshot) {
      const epoch = identityEpoch;
      let missingPhotoCount = 0;
      const colors = await Promise.all((snapshot.product?.colors || []).map(async (color) => ({
        ...color,
        images: (await Promise.all((color.images || []).map(async (image) => {
          if (image.src?.startsWith('blob:') || isPlaceholderPhotoSrc(image.src)) return image;
          try {
            const blob = await fetchPhotoBytes(image.src);
            if (epoch !== identityEpoch) return null;
            return { ...image, src: URL.createObjectURL(blob), type: blob.type };
          } catch { missingPhotoCount += 1; return null; }
        }))).filter(Boolean),
      })));
      if (epoch !== identityEpoch) return null;
      return { ...snapshot, product: { ...snapshot.product, colors }, missingPhotoCount };
    },
    async removeForNewFlow({ isCurrent = () => true } = {}) {
      const epoch = identityEpoch;
      const current = () => epoch === identityEpoch && isCurrent();
      // 새 제작/처음부터 다시는 사용자의 명시적 폐기 의사다. 먼저 작업권을 인수한 뒤 같은
      // 토큰으로 DELETE한다. 확정 승격은 이 메서드를 쓰지 않아 작업권 상실을 우회하지 않는다.
      try {
        await this.takeover({ isCurrent: current });
        if (!current()) return false;
        await this.remove({ isCurrent: current });
        if (!current()) return false;
        setPendingRemoval(null);
        return true;
      } catch (error) {
        if (!current()) return false;
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
        this.resetIdentity({ sameUser: true });
        setPendingRemoval(new Date().toISOString());
        return false;
      }
    },
    async remove({ isCurrent = () => true, forPromotion = false } = {}) {
      const epoch = identityEpoch;
      const expectedUserId = userId;
      const current = () => epoch === identityEpoch && expectedUserId === userId && isCurrent();
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
        if (!current()) return;
        await waitForUploads();
        if (!current()) return;
        if (api?.deleteDraftSlot) await api.deleteDraftSlot(deleteToken, { expectedUserId, isCurrent: current, forPromotion });
        if (!current()) return;
      } catch (error) {
        if (!current()) return;
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
      visualRefs = {}; customMatch = null; cancelVisualPreparations(); notifyVisuals();
      await Promise.allSettled(uploaded.map(discardUploaded));
      if (!current()) return;
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
    resetIdentity({ sameUser = false } = {}) {
      if (!sameUser) identityEpoch += 1;
      if (!sameUser) visualsEnabled = false;
      visualRefs = {}; customMatch = null; cancelVisualPreparations(); notifyVisuals();
      stagedPayload = null;
      entryWritesPaused = false;
      remotePhotosGone = false;
      requestEpoch += 1;
      putChain = putChain.catch(() => null);
      this.discard();
      finishSaves('failed');
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
    hasStaged() { return Boolean(stagedPayload?.payload); },
    consumeStaged() {
      const payload = stagedPayload;
      stagedPayload = null;
      return payload;
    },
    hasUnsyncedChanges(localUpdatedAt) { return Boolean(localUpdatedAt && localUpdatedAt !== syncedAt); },
    hasPendingRemoval() { return Boolean(getPendingRemoval()); },
    continueWithoutSlotCheck() {
      if (token) return false;
      this.discard();
      setPendingRemoval(null);
      return true;
    },
    async retryPendingRemoval() {
      try { await resolvePendingRemoval(); } catch { /* 서버 복구 뒤 다음 저장 전에 재시도 */ }
    },
    getToken() { return token; },
    getSyncedAt() { return syncedAt; },
    getServerSyncedAt() { return serverSyncedAt; },
    getIdentityEpoch() { return identityEpoch; },
    getUserId() { return userId || null; },
    getSyncedUserId() { return syncedUserId; },
    hasOtherTabOwner() {
      const owner = readStorage(storage, TAB_OWNER_KEY);
      return Boolean(owner && owner !== documentId);
    },
    isLocked() { return locked; },
  };
}

export const draftSlot = createDraftSlotSync();
