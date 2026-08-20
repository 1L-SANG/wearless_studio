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
/** product 의 사진을 draft 에 담을 수 있는 형태로 모은다. → {photos, cleanProduct, failed}
 *
 * 담는 조건은 두 개다: blob 을 읽을 수 있고, **서버에 올릴 수 있는 mime** 이어야 한다.
 * mime 은 셀러 파일의 type 을 먼저 믿되 화이트리스트에 없으면 blob 의 실제 타입으로 고친다
 * (filesToMetas 폴백이 남긴 'image' 같은 잘못된 값이 그대로 확정 업로드까지 가면 서버가
 * 400 으로 거부하고, 셀러는 자기가 올린 jpg 가 거부됐다고 읽는다 — 2026-08-17 사고).
 * 목 데모의 SVG 플레이스홀더도 여기서 걸린다: data: 는 fetch 가 성공하므로 mime 검사만이
 * 유일한 문지기다.
 *
 * fetchBlob 주입은 테스트용 — 화면·IndexedDB 없이 이 판정만 검증한다. */
export async function collectDraftPhotos(product, fetchBlob = (src) => fetch(src).then((r) => r.blob())) {
  const photos = [];
  const okIds = new Set();
  let failed = 0;
  for (const color of product?.colors || []) {
    for (const img of color.images || []) {
      try {
        const blob = await fetchBlob(img.src);
        const mime = isUploadablePhotoMime(img.type) ? img.type : blob?.type;
        if (!isUploadablePhotoMime(mime)) {
          // 올릴 수 없는 사진 — 담아 두면 확정 단계에서 서버 400 으로 진행이 막힌다.
          failed += 1;
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
        // objectURL revoke·메모리 소실 등으로 blob 읽기 실패 — 조용히 누락하지 않고 집계+경고.
        failed += 1;
        console.warn(`[draft] 사진 blob 추출 실패 — imageId=${img.id}`, e);
      }
    }
  }
  // 담긴 이미지만 product 에 남긴다 — 담기지 않은 사진이 죽은 src 로 '정상 이미지인 척'
  // 복원되는(좀비) 것을 막는다. photos[] 와 product.images[] 가 항상 일치.
  const cleanProduct = product
    ? { ...product, colors: (product.colors || []).map((c) => ({ ...c, images: (c.images || []).filter((im) => okIds.has(im.id)) })) }
    : product;
  return { photos, cleanProduct, failed };
}

export async function saveProductDraft(product, analysis = null, composeMode = 'basic', updatedAt = new Date().toISOString(), customMatch = null) {
  const { photos, cleanProduct, failed } = await collectDraftPhotos(product);
  // 커스텀 매칭(내 옷) blob 도 상품 사진과 같은 등급으로 저장한다. 이게 없으면 비로그인
  // 셀러의 OAuth 리다이렉트(=풀 페이지 리로드)에서 메모리의 blob 이 사라져, 확정 승격이
  // 읽을 게 없어지고 내 옷이 조용히 유실된다(2026-08-15 전수조사).
  await withStore('readwrite', (s) => s.put({
    product: cleanProduct,
    analysis,
    composeMode: composeMode === 'extended' ? 'extended' : 'basic',
    updatedAt,
    photos,
    customMatch: customMatch && customMatch.uploads?.length ? customMatch : null,
  }, KEY));
  // 이 탭 세션에 '미동기화 입력 있음' 표시 — 복원은 이 플래그가 있을 때만(=같은 세션) 한다.
  // sessionStorage 라 탭을 닫으면 사라져, 공용 브라우저의 다른 사용자에겐 복원되지 않는다.
  sessionStorage.setItem(PENDING_KEY, '1');
  return { saved: photos.length, failed }; // 호출측이 일부 누락을 사용자에게 알릴 수 있게.
}

function commitPendingSnapshot() {
  clearTimeout(pendingTimer);
  pendingTimer = null;
  if (!pendingSnapshot) return saveChain;
  const snapshot = pendingSnapshot;
  pendingSnapshot = null;
  // 커스텀 매칭 blob 은 저장 직전에 읽는다 — 스냅샷 큐에 blob 을 들고 있지 않아도 되고,
  // 커밋 시점의 최신 상태가 저장된다. api 는 지연 임포트(모듈 순환 회피).
  saveChain = saveChain.catch(() => null).then(async () => {
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
  pendingSnapshot = { product, analysis, composeMode, updatedAt };
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(commitPendingSnapshot, DRAFT_DEBOUNCE_MS);
}

/** OAuth 이동처럼 페이지가 사라지기 전, 예약·진행 중 저장을 모두 기다린다. */
export async function flushProductDraftSave() {
  await commitPendingSnapshot();
  return saveChain;
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
