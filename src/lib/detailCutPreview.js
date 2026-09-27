/* =============================================================
   lib/detailCutPreview — 생성 중 REAL 얼굴 컷 미리보기(2026-09-26, 오너 결정 b). 순수 함수.

   REAL(실존 모델) 얼굴 컷의 cut_done 에는 주소가 없다 — 서버가 최종 권한 확인 전까지 출력
   위치를 이벤트 원장에 남기지 않는다(492cbc64). 그래서 대기 타일은 '완성됐어요'만 말했다.
   이제 그 자리를 서버의 미리보기 라우트로 채운다. 라우트는 요청마다 소유·잡 상태를 보고 REAL
   이면 라이선스를 지금 다시 확인한 뒤에만 바이트를 준다. 인증 헤더가 필요해 <img src> 로는 못
   건다 — fetch → blob → objectURL 이고, 타일이 사라질 때 objectURL 을 거둔다.

   거절(403 = 라이선스가 지금 유효하지 않음)·없음(404)은 조용히 '완성됐어요' 타일로 남는다.
   오류 배너·콘솔 로그를 내지 않는다. 완료 병합의 안정 주소가 언제나 이긴다(이 미리보기는
   블록 데이터에 들어가지 않는다 — 저장본·임시 작업본에 blob: 주소가 남지 않는다).
   ============================================================= */

/** 일시 장애(5xx·연결 끊김)만 다시 묻는 간격. 4xx 는 다시 묻지 않는다. */
export const DETAIL_CUT_PREVIEW_RETRY_MS = [2000, 5000];

const defaultSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function detailCutPreviewPath(projectId, jobId, blockId) {
  return `/v1/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}`
    + `/cuts/${encodeURIComponent(blockId)}/preview`;
}

const retryable = (error) => (typeof error?.status === 'number' && error.status >= 500)
  // fetch 자체가 끊기면 TypeError('Failed to fetch') — 응답 코드가 없다.
  || (error instanceof TypeError && error.status === undefined);

const isImageBlob = (blob) => Boolean(blob)
  && (typeof blob.type !== 'string' || blob.type === '' || blob.type.startsWith('image/'));

/** 미리보기 1장을 받는다. 결과는 { kind: 'image', blob } 또는 { kind: 'tile', status }.
    예외를 던지지 않는다 — 어떤 실패도 '완성됐어요' 타일로 남는 것이 전부다. */
export async function fetchDetailCutPreview({
  fetchBlob, projectId, jobId, blockId, signal,
  sleep = defaultSleep, retryDelaysMs = DETAIL_CUT_PREVIEW_RETRY_MS,
}) {
  if (typeof fetchBlob !== 'function' || !projectId || !jobId || !blockId) {
    return { kind: 'tile', status: 0 };
  }
  const path = detailCutPreviewPath(projectId, jobId, blockId);
  for (let attempt = 0; ; attempt += 1) {
    if (signal?.aborted) return { kind: 'tile', status: 0, aborted: true };
    try {
      const blob = await fetchBlob(path, { signal });
      return isImageBlob(blob) ? { kind: 'image', blob } : { kind: 'tile', status: 0 };
    } catch (error) {
      if (error?.name === 'AbortError' || signal?.aborted) {
        return { kind: 'tile', status: 0, aborted: true };
      }
      if (!retryable(error) || attempt >= retryDelaysMs.length) {
        return { kind: 'tile', status: typeof error?.status === 'number' ? error.status : 0 };
      }
      await sleep(retryDelaysMs[attempt]);
    }
  }
}

/** 타일 하나의 미리보기 수명. load(signal) 이 이미지를 주면 objectURL 을 만들어 onUrl 로 알리고,
    돌려준 close() 가 요청을 끊고 objectURL 을 거둔다. close 뒤에 도착한 응답은 버린다. */
export function openDetailCutPreview({ load, createObjectURL, revokeObjectURL, onUrl }) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  let closed = false;
  let url = null;
  Promise.resolve()
    .then(() => load(controller?.signal))
    .then((result) => {
      if (closed || result?.kind !== 'image' || !result.blob) return;
      url = createObjectURL(result.blob);
      onUrl(url);
    })
    .catch(() => { /* 미리보기는 거들 뿐 — 실패는 '완성됐어요' 타일로 남는다 */ });
  return () => {
    closed = true;
    controller?.abort();
    if (url) {
      revokeObjectURL(url);
      url = null;
    }
  };
}

/** 같은 컷 자리를 여러 곳이 그린다(캔버스 + 왼쪽 블록 썸네일 + 전체 미리보기). 자리마다 따로
    받으면 컷 하나에 요청이 두세 번 간다 — 키(프로젝트·잡·블록)별로 한 번만 받아 objectURL 하나를
    나눠 쓰고, 마지막 사용처가 사라질 때 요청을 끊고 거둔다. */
export function createDetailCutPreviewCache({ createObjectURL, revokeObjectURL }) {
  const entries = new Map();
  return {
    acquire(key, load, onUrl) {
      let entry = entries.get(key);
      if (!entry) {
        const created = { refs: 0, url: null, listeners: new Set(), close: null };
        created.close = openDetailCutPreview({
          load,
          createObjectURL,
          revokeObjectURL,
          onUrl: (url) => {
            created.url = url;
            for (const listener of created.listeners) listener(url);
          },
        });
        entries.set(key, created);
        entry = created;
      }
      entry.refs += 1;
      entry.listeners.add(onUrl);
      if (entry.url) onUrl(entry.url);
      let released = false;
      return () => {
        if (released) return;
        released = true;
        entry.listeners.delete(onUrl);
        entry.refs -= 1;
        if (entry.refs === 0) {
          entries.delete(key);
          entry.close();
        }
      };
    },
    size: () => entries.size,
  };
}
