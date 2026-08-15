/* =============================================================
   lib/retryRead — 읽기 요청 자동 재시도(백오프).

   "못 만들면 안 되지, 우린 상업 서비스야"(오너 8/15). 일시 장애 한 번에 화면이
   비어 버리는 것을 사용자에게 떠넘기지 않고 조용히 다시 시도한다.

   4xx 는 재시도하지 않는다 — 같은 요청을 다시 보내도 같은 답이고(404 없음·403 권한),
   기다리기만 길어진다. 5xx·네트워크 오류만 재시도 대상이다
   (판정 규칙은 storyboardPersistence 의 기존 정책과 같다).

   타이머를 주입받아 node --test 에서 실시간 대기 없이 검증한다. 순수 모듈.
   ============================================================= */

export const DEFAULT_READ_RETRY_DELAYS = [1000, 2000, 5000, 10000];

/** 4xx = 결정적 실패(재시도 무의미), 그 외(5xx·네트워크·타임아웃) = 일시적. */
export function isRetryableReadError(error) {
  const status = error?.status;
  if (Number.isInteger(status) && status >= 400 && status < 500) return false;
  return true;
}

/**
 * fetchOnce 를 성공할 때까지 재시도한다. delays 를 다 쓰면 마지막 오류를 던진다.
 * @param {() => Promise<any>} fetchOnce
 * @param {{delays?: number[], sleep?: (ms:number)=>Promise<void>, shouldRetry?: (e:any)=>boolean, onRetry?: (info:{attempt:number, error:any})=>void}} [options]
 */
export async function retryRead(fetchOnce, options = {}) {
  const {
    delays = DEFAULT_READ_RETRY_DELAYS,
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    shouldRetry = isRetryableReadError,
    onRetry,
  } = options;
  let lastError;
  for (let attempt = 0; attempt <= delays.length; attempt += 1) {
    try {
      return await fetchOnce();
    } catch (error) {
      lastError = error;
      if (attempt === delays.length || !shouldRetry(error)) throw error;
      onRetry?.({ attempt: attempt + 1, error });
      await sleep(delays[attempt]);
    }
  }
  throw lastError;
}
