/* =============================================================
   lib/draftStore — 비로그인 입력 임시 보관 (IndexedDB).

   OAuth 풀페이지 리다이렉트로 페이지가 통째로 새로고침되면 ProductInput 의
   사진(URL.createObjectURL 로 만든 objectURL/메모리 blob)과 로컬 입력이 소실된다.
   리다이렉트 직전에 상품정보(JSON)와 사진 blob 을 IndexedDB 에 저장해 두고,
   로그인 복귀 후 복원→백엔드 sync(@/lib/draftSync) 한다.
   (sessionStorage 는 문자열만 → blob 보관 불가라 IndexedDB 필수.)

   draft = { product, photos: [{ imageId, colorId, slot, blob, mime, filename }] }
   ============================================================= */

import { normalizeAnalysisFit } from './fitAxes.js';
import { isUploadablePhotoMime } from './imageTranscode.js';
import { fetchPhotoBytes, isRemoteAssetPhoto } from './photoBytes.js';
import { draftSlot } from './draftSlot.js';

const DB_NAME = 'wearless-draft';
const DB_VERSION = 1;
const STORE = 'draft';
const KEY = 'current';
const PENDING_KEY = 'wl_draftPending'; // sessionStorage(탭 세션 한정) — 미동기화 draft 존재 표시
const DRAFT_DEBOUNCE_MS = 500;
let pendingSnapshot = null;
let pendingTimer = null;
let saveChain = Promise.resolve(null);

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function withStore(mode, run) {
  const db = await openDB();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const req = run(tx.objectStore(STORE));
      let result;
      if (req) req.onsuccess = () => { result = req.result; };
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error('draft tx aborted'));
    });
  } finally {
    db.close();
  }
}

/** ProductInput 의 product 에서 사진 blob 을 추출해 draft 를 IndexedDB 에 저장한다.
    blob 추출(fetch(objectURL))은 페이지가 살아있을 때만 가능 → 리다이렉트 직전에 호출. */
/** product 의 사진을 draft 에 담을 수 있는 형태로 모은다. {photos, cleanProduct, failed, failedIds}
 *
 * 담는 조건은 두 개다: blob 을 읽을 수 있고, **서버에 올릴 수 있는 mime** 이어야 한다.
 * 원격 자산은 응답 MIME을 사용한다. 로컬 파일은 type이 허용 목록에 없으면 blob 타입으로 고친다
 * (filesToMetas 폴백이 남긴 'image' 같은 잘못된 값이 그대로 확정 업로드까지 가면 서버가
 * 400 으로 거부하고, 셀러는 자기가 올린 jpg 가 거부됐다고 읽는다 — 2026-08-17 사고).
 * 목 데모의 SVG 플레이스홀더도 여기서 걸린다: data: 는 fetch 가 성공하므로 mime 검사만이
 * 유일한 문지기다.
 *
 * fetchBlob 주입은 테스트용 — 화면·IndexedDB 없이 이 판정만 검증한다. */

/** 사진 1장을 blob 으로. CORS 로 막히면 캐시를 건너뛰고 한 번만 다시 받는다.
 *
 * 이미 올라간 사진의 src 는 R2 공개 URL(images.wearless.kr)이다. R2 는 Origin 없는
 * 요청에 **Vary 를 안 붙이면서** `max-age=31536000` 을 준다. 그래서 화면이 그 사진을
 * 평범한 `<img src>`(Origin 없음)로 먼저 받아 두면, 허가 헤더가 없는 사본이 브라우저
 * 캐시에 1년치로 눌러앉고, 뒤이은 이 fetch(CORS 필수)가 Vary 가 없으니 같은 사본을
 * 재사용해 'No Access-Control-Allow-Origin' 으로 막힌다 — 서버는 멀쩡한데 그 브라우저만
 * 실패한다(2026-08-26 실서버 재현: curl 은 매번 통과, 브라우저만 차단).
 *
 * `cache: 'reload'` 는 캐시를 건너뛰고 네트워크로 가므로 Origin 이 실려 정상 응답을 받는다.
 * 성공 경로에서는 쓰지 않는다 — 사진은 장당 수 MB 라 매번 재다운로드하면 draft 저장이
 * 그만큼 느려진다. 오염된 캐시에서만 한 번 더 받는다.
 * 근본 해결은 화면의 `<img>` 에 crossorigin 을 붙이거나(editorExport 가 이미 하는 방식)
 * R2 응답에 Vary 를 강제하는 것이고, 이건 이미 오염된 캐시까지 푸는 우회다. */
const fetchPhotoBlob = async (src) => {
  try {
    return await fetchPhotoBytes(src);
  } catch (e) {
    if (isRemoteAssetPhoto(src) || !/^https?:/i.test(String(src || ''))) throw e;
    return fetchPhotoBytes(src, { cache: 'reload' });
  }
};

export async function collectDraftPhotos(product, fetchBlob = fetchPhotoBlob, storedPhotos = []) {
  const photos = [];
  const okIds = new Set();
  const failedIds = [];
  const failures = [];
  for (const color of product?.colors || []) {
    for (const img of color.images || []) {
      try {
        const blob = await fetchBlob(img.src);
        const mime = !isRemoteAssetPhoto(img.src) && isUploadablePhotoMime(img.type) ? img.type : blob?.type;
        if (!isUploadablePhotoMime(mime)) {
          // 올릴 수 없는 사진 — 담아 두면 확정 단계에서 서버 400 으로 진행이 막힌다.
          failedIds.push(img.id);
          console.warn(`[draft] 업로드할 수 없는 사진 형식 — imageId=${img.id} mime=${mime}`);
          continue;
        }
        photos.push({
          imageId: img.id,
          colorId: color.id,
          slot: img.slot,
          blob,
          mime,
          filename: img.name || `${img.id}`,
        });
        okIds.add(img.id);
      } catch (e) {
        const stored = storedPhotos.find((photo) => photo.imageId === img.id
          && photo.blob?.size && isUploadablePhotoMime(photo.mime));
        if (stored && (isRemoteAssetPhoto(img.src) || /^https?:/i.test(img.src))) {
          photos.push({ ...stored, colorId: color.id, slot: img.slot, filename: img.name || stored.filename });
          okIds.add(img.id);
          continue;
        }
        failures.push(Object.defineProperty({ imageId: img.id, ...(e?.status ? { status: e.status } : {}) }, 'error', { value: e }));
        // objectURL revoke·메모리 소실 등으로 blob 읽기 실패 — 조용히 누락하지 않고 집계+경고.
        failedIds.push(img.id);
        console.warn(`[draft] 사진 blob 추출 실패 — imageId=${img.id}`, e);
      }
    }
  }
  // 원격 자산은 바이트가 없어도 주소를 보존한다. 다음 저장과 확정에서 다시 받는다.
  // 로컬 사진은 담긴 항목만 남겨 죽은 objectURL이 복원되지 않게 한다.
  const cleanProduct = product
    ? { ...product, colors: (product.colors || []).map((c) => ({ ...c, images: (c.images || []).filter((im) => okIds.has(im.id) || isRemoteAssetPhoto(im.src)) })) }
    : product;
  return { photos, cleanProduct, failed: failedIds.length, failedIds, ...(failures.length ? { failures } : {}) };
}

/** 로컬 바이트가 없는 원격 사진도 승격 전에 확보한다. 실패하면 승격을 중단한다. */
export async function ensureDraftRemotePhotos(draft, fetchBlob = fetchPhotoBlob) {
  const photos = draft.photos || [];
  const available = new Set(photos.filter((photo) => photo.blob?.size && isUploadablePhotoMime(photo.mime)).map((photo) => photo.imageId));
  const missingProduct = {
    ...draft.product,
    colors: (draft.product?.colors || []).map((color) => ({
      ...color,
      images: (color.images || []).filter((image) => isRemoteAssetPhoto(image.src) && !available.has(image.id)),
    })),
  };
  const collected = await collectDraftPhotos(missingProduct, fetchBlob);
  if (collected.failed) throw new Error(`사진 ${collected.failed}장을 불러오지 못했어요.`);
  const replaced = new Set(collected.photos.map((photo) => photo.imageId));
  return { ...draft, photos: [...photos.filter((photo) => !replaced.has(photo.imageId)), ...collected.photos] };
}

export async function saveProductDraft(product, analysis = null, composeMode = 'basic', updatedAt = new Date().toISOString(), customMatch = null, isCurrent = () => true) {
  const stored = await loadDraft().catch(() => null);
  if (!isCurrent()) return null;
  const { photos, cleanProduct, failed, failedIds, failures } = await collectDraftPhotos(product, fetchPhotoBlob, stored?.photos);
  if (!isCurrent()) return null;
  // 커스텀 매칭(내 옷) blob 도 상품 사진과 같은 등급으로 저장한다. 이게 없으면 비로그인
  // 셀러의 OAuth 리다이렉트(=풀 페이지 리로드)에서 메모리의 blob 이 사라져, 확정 승격이
  // 읽을 게 없어지고 내 옷이 조용히 유실된다(2026-08-15 전수조사).
  await withStore('readwrite', (s) => isCurrent() ? s.put({
    product: cleanProduct,
    analysis,
    composeMode: composeMode === 'extended' ? 'extended' : 'basic',
    updatedAt,
    photos,
    customMatch: customMatch && customMatch.uploads?.length ? customMatch : null,
  }, KEY) : null);
  if (!isCurrent()) return null;
  // 이 탭 세션에 '미동기화 입력 있음' 표시 — 복원은 이 플래그가 있을 때만(=같은 세션) 한다.
  // sessionStorage 라 탭을 닫으면 사라져, 공용 브라우저의 다른 사용자에겐 복원되지 않는다.
  sessionStorage.setItem(PENDING_KEY, '1');
  return { saved: photos.length, failed, failedIds, ...(failures ? { failures } : {}) }; // 호출측이 일부 누락을 사용자에게 알릴 수 있게.
}

function commitPendingSnapshot() {
  clearTimeout(pendingTimer);
  pendingTimer = null;
  if (!pendingSnapshot) return saveChain;
  const snapshot = pendingSnapshot;
  pendingSnapshot = null;
  // 계정 전환 뒤 늦게 끝난 사진 읽기도 이전 신원으로 저장하지 않는다.
  const isCurrent = () => snapshot.userId === draftSlot.getUserId()
    && snapshot.identityEpoch === draftSlot.getIdentityEpoch();
  if (!isCurrent()) return saveChain;
  // 커스텀 매칭 blob 은 저장 직전에 읽는다 — 스냅샷 큐에 blob 을 들고 있지 않아도 되고,
  // 커밋 시점의 최신 상태가 저장된다. api 는 지연 임포트(모듈 순환 회피).
  saveChain = saveChain.catch(() => null).then(async () => {
    if (!isCurrent()) return null;
    let customMatch = null;
    try {
      const { api } = await import('@/lib/api/index.js');
      customMatch = api.getCustomMatchDraft?.() ?? null;
    } catch { /* 접근자 없음·mock 미로드 — 커스텀 없이 저장 */ }
    return saveProductDraft(
      snapshot.product,
      snapshot.analysis,
      snapshot.composeMode,
      snapshot.updatedAt,
      customMatch,
      isCurrent,
    );
  });
  return saveChain;
}

/** 익명 입력을 마지막 변경 기준으로 직렬화해 저장한다. 사진 blob 추출이 겹쳐
    오래된 저장이 최신 입력을 덮지 않도록 모든 IndexedDB 쓰기는 한 큐를 탄다. */
export function queueProductDraftSave(
  product,
  analysis = null,
  composeMode = 'basic',
  updatedAt = new Date().toISOString(),
) {
  pendingSnapshot = { product, analysis, composeMode, updatedAt,
    userId: draftSlot.getUserId(), identityEpoch: draftSlot.getIdentityEpoch() };
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(commitPendingSnapshot, DRAFT_DEBOUNCE_MS);
}

/** OAuth 이동처럼 페이지가 사라지기 전, 예약·진행 중 저장을 모두 기다린다. */
export async function flushProductDraftSave({
  userId = draftSlot.getUserId(), identityEpoch = draftSlot.getIdentityEpoch(),
  timeoutMs = null, setTimer = setTimeout, clearTimer = clearTimeout,
} = {}) {
  if (userId !== draftSlot.getUserId() || identityEpoch !== draftSlot.getIdentityEpoch()) return null;
  const saving = commitPendingSnapshot().then(() => saveChain);
  if (timeoutMs == null) return saving;
  let timer;
  try {
    return await Promise.race([
      saving,
      new Promise((_, reject) => { timer = setTimer(() => reject(new Error('draft_save_timeout')), timeoutMs); }),
    ]);
  } finally { clearTimer(timer); }
}

export function discardPendingDraftSave() {
  clearTimeout(pendingTimer);
  pendingTimer = null;
  pendingSnapshot = null;
}

/** 저장된 draft 반환(없으면 null). photos[].blob 은 Blob 으로 복원된다. */
export async function loadDraft() {
  const draft = await withStore('readonly', (s) => s.get(KEY));
  return draft ? {
    ...draft,
    analysis: normalizeAnalysisFit(draft.analysis),
    composeMode: draft.composeMode === 'extended' ? 'extended' : 'basic',
  } : null;
}

/** draft 삭제 — sync 성공 후 정리. */
export async function clearDraft() {
  discardPendingDraftSave();
  await saveChain.catch(() => null);
  await withStore('readwrite', (s) => s.delete(KEY));
  sessionStorage.removeItem(PENDING_KEY);
}

/** 오래 걸린 승격이 끝나는 사이 새 draft가 저장됐으면 그 새 작업은 지우지 않는다. */
export async function clearDraftIfCurrent(expectedUpdatedAt, {
  load = loadDraft,
  clear = clearDraft,
  getPending = () => pendingSnapshot,
  waitForSaves = () => saveChain.catch(() => null),
} = {}) {
  if (!expectedUpdatedAt) return false;
  const pending = getPending();
  if (pending?.updatedAt && pending.updatedAt !== expectedUpdatedAt) return false;
  await waitForSaves();
  const current = await load();
  const latestPending = getPending();
  if (latestPending?.updatedAt && latestPending.updatedAt !== expectedUpdatedAt) return false;
  if (current?.updatedAt !== expectedUpdatedAt) return false;
  await clear();
  return true;
}

/** 이 탭 세션에 미동기화 draft 가 있는지 — 복원 게이팅용. */
export function hasPendingDraft() {
  return sessionStorage.getItem(PENDING_KEY) === '1';
}

/** 사진을 다시 읽지 않고 특징 실행 체크포인트만 같은 로컬 저장 큐에 기록한다. */
export function saveFeatureRunDraft(patch, { isCurrent = () => true } = {}) {
  commitPendingSnapshot();
  saveChain = saveChain.catch(() => null).then(async () => {
    if (!isCurrent()) return false;
    const draft = await loadDraft();
    if (!draft || !isCurrent()) return false;
    await withStore('readwrite', (store) => isCurrent()
      ? store.put({ ...draft, analysis: { ...draft.analysis, ...patch } }, KEY) : null);
    return isCurrent();
  });
  return saveChain;
}
