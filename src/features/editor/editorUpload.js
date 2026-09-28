/* 의류 탭 '직접 이미지 업로드' — 여러 장을 한 번에 고를 때 쓰는 순수 도우미.
   Editor.jsx 는 파일 하나를 올리는 기존 경로(uploadEditorImage)를 그대로 쓰고, 여기서는
   몇 장을 동시에 올릴지만 정한다. 상대 경로 import 가 없어 테스트가 vite 없이 바로 읽는다. */

/** 한 번에 고를 수 있는 최대 장수. 넘으면 앞에서부터 이만큼만 올리고 알린다. */
export const MAX_WARDROBE_UPLOADS = 20;
/** 동시에 올리는 장수 — 모바일 회선·HEIC 변환 부담을 생각해 작게 둔다. */
export const WARDROBE_UPLOAD_CONCURRENCY = 3;

/**
 * items 를 limit 개씩 동시에 worker 로 처리한다. 한 장이 실패해도 나머지는 계속 간다.
 * 결과는 입력 순서대로 `{ ok: true, value } | { ok: false, error }`.
 */
export async function runWithConcurrency(items, limit, worker) {
  const list = Array.from(items || []);
  const results = new Array(list.length);
  let next = 0;
  const lane = async () => {
    while (next < list.length) {
      const index = next;
      next += 1;
      try {
        results[index] = { ok: true, value: await worker(list[index], index) };
      } catch (error) {
        results[index] = { ok: false, error };
      }
    }
  };
  const lanes = Math.max(1, Math.min(limit || 1, list.length));
  await Promise.all(Array.from({ length: lanes }, lane));
  return results;
}
