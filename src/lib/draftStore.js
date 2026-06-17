/* =============================================================
   lib/draftStore — 비로그인 입력 임시 보관 (IndexedDB).

   OAuth 풀페이지 리다이렉트로 페이지가 통째로 새로고침되면 ProductInput 의
   사진(URL.createObjectURL 로 만든 objectURL/메모리 blob)과 로컬 입력이 소실된다.
   리다이렉트 직전에 상품정보(JSON)와 사진 blob 을 IndexedDB 에 저장해 두고,
   로그인 복귀 후 복원→백엔드 sync(@/lib/draftSync) 한다.
   (sessionStorage 는 문자열만 → blob 보관 불가라 IndexedDB 필수.)

   draft = { product, photos: [{ imageId, colorId, slot, blob, mime, filename }] }
   ============================================================= */

const DB_NAME = 'wearless-draft';
const DB_VERSION = 1;
const STORE = 'draft';
const KEY = 'current';
const PENDING_KEY = 'wl_draftPending'; // sessionStorage(탭 세션 한정) — 미동기화 draft 존재 표시

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
export async function saveProductDraft(product) {
  const photos = [];
  for (const color of product?.colors || []) {
    for (const img of color.images || []) {
      try {
        const blob = await fetch(img.src).then((r) => r.blob());
        photos.push({
          imageId: img.id,
          colorId: color.id,
          slot: img.slot,
          blob,
          mime: img.type || blob.type || 'image/jpeg',
          filename: img.name || `${img.id}`,
        });
      } catch {
        // objectURL 이 이미 revoke 되었거나 읽기 실패 — 해당 사진만 건너뛴다.
      }
    }
  }
  await withStore('readwrite', (s) => s.put({ product, photos }, KEY));
  // 이 탭 세션에 '미동기화 입력 있음' 표시 — 복원은 이 플래그가 있을 때만(=같은 세션) 한다.
  // sessionStorage 라 탭을 닫으면 사라져, 공용 브라우저의 다른 사용자에겐 복원되지 않는다.
  sessionStorage.setItem(PENDING_KEY, '1');
}

/** 저장된 draft 반환(없으면 null). photos[].blob 은 Blob 으로 복원된다. */
export async function loadDraft() {
  const draft = await withStore('readonly', (s) => s.get(KEY));
  return draft || null;
}

/** draft 삭제 — sync 성공 후 정리. */
export async function clearDraft() {
  await withStore('readwrite', (s) => s.delete(KEY));
  sessionStorage.removeItem(PENDING_KEY);
}

/** 이 탭 세션에 미동기화 draft 가 있는지 — 복원 게이팅용. */
export function hasPendingDraft() {
  return sessionStorage.getItem(PENDING_KEY) === '1';
}
