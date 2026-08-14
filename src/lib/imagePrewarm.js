/* =============================================================
   imagePrewarm — 콘티보드 썸네일 선캐싱.

   보드에 놓일 이미지는 우리 R2 썸네일이라 URL이 진입 시점에 이미 확정돼 있다.
   섹션이 접혀 있는 동안(=사용자가 아직 보지 않을 때) 유휴 시간에 미리 받아
   디코드까지 끝내두면, 펼치는 순간 네트워크·디코드 비용이 사라진다.

   실측 근거(2026-08-02): 서비스 썸네일 360x480 WebP = 파일 6.4KB · 디코딩 0.66MB.
   보드 14컷 기준 총 ~9MB — 탭 하나가 보통 수백 MB인 것에 비하면 미미하다.

   원칙 (셀러는 탭을 많이 띄운다 — 남의 탭 자원을 뺏지 않는 것이 최우선)
   - 백그라운드 탭에서는 아예 받지 않는다. 화면에 돌아올 때까지 대기.
   - 유휴 시간에만(requestIdleCallback), 낮은 우선순위로 → 첫 화면 렌더와 경쟁하지 않는다.
   - 데이터 절약 모드면 건너뛰고, 저사양(RAM 4GB 이하)이면 동시 수·총량을 줄인다.
   - 동시 요청 수 제한 + 총량 상한 → 회선·메모리를 독점하지 않는다.
   - 세션 내 중복 요청 금지(warmed) → 재진입·리렌더로 같은 URL을 다시 받지 않는다.
   - 실패는 조용히 무시 → 선캐싱은 최적화일 뿐, 실패해도 화면은 정상 동작한다.
   ============================================================= */

const warmed = new Set();
const MAX_PER_CALL = 40;   // 폭주 방지 상한 (확장형 최대 컷수 + 참조칩 여유)

// data:/blob: 는 이미 메모리에 있으므로 대상이 아니다(목 모드의 SVG 자리표시자 등).
const isWarmable = (url) => typeof url === 'string'
  && (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('/'));

const runIdle = (fn) => (typeof requestIdleCallback === 'function'
  ? requestIdleCallback(fn, { timeout: 1500 })
  : setTimeout(fn, 250));

const conn = () => (typeof navigator !== 'undefined' ? navigator.connection : null);
const saveData = () => !!conn()?.saveData;
const lowMemory = () => (typeof navigator !== 'undefined' && navigator.deviceMemory
  ? navigator.deviceMemory <= 4 : false);

/** 지금 탭이 화면에 있을 때만 진행한다. 숨겨져 있으면 돌아올 때까지 기다린다. */
function whenVisible(fn) {
  if (typeof document === 'undefined' || !document.hidden) { fn(); return () => {}; }
  const on = () => {
    if (document.hidden) return;
    document.removeEventListener('visibilitychange', on);
    fn();
  };
  document.addEventListener('visibilitychange', on);
  return () => document.removeEventListener('visibilitychange', on);
}

/** 이미지 한 장을 받아 decode까지 끝낸다. 선캐싱과 초기 reveal 게이트가 같은 경로를 쓴다. */
export function loadAndDecodeImage(url, {
  ImageCtor = typeof Image === 'undefined' ? null : Image,
  fetchPriority,
} = {}) {
  if (!ImageCtor) return Promise.reject(new Error('image_constructor_unavailable'));
  return new Promise((resolve, reject) => {
    const img = new ImageCtor();
    img.decoding = 'async';
    if (fetchPriority && 'fetchPriority' in img) img.fetchPriority = fetchPriority;
    img.onload = () => {
      const decoded = typeof img.decode === 'function' ? img.decode() : Promise.resolve();
      // decode() 미지원/실패여도 load가 끝난 이미지는 화면에 표시할 수 있다.
      decoded.catch(() => {}).then(resolve);
    };
    img.onerror = () => reject(new Error('image_load_failed'));
    img.src = url;
  });
}

/** URL 목록을 유휴 시간에 미리 받아 캐시를 데운다. 반환값을 호출하면 남은 작업을 취소한다. */
export function prewarmImages(urls, { concurrency = 3 } = {}) {
  if (typeof window === 'undefined' || typeof Image === 'undefined') return () => {};
  if (saveData()) return () => {};   // 데이터 절약 모드는 사용자의 명시적 의사다

  const lanes = lowMemory() ? Math.min(2, concurrency) : concurrency;
  const cap = lowMemory() ? 16 : MAX_PER_CALL;
  const queue = [...new Set(urls)]
    .filter((url) => isWarmable(url) && !warmed.has(url))
    .slice(0, cap);
  if (!queue.length) return () => {};
  queue.forEach((url) => warmed.add(url));

  let cancelled = false;
  let unwatch = () => {};
  const pump = () => {
    if (cancelled) return;
    const url = queue.shift();
    if (!url) return;
    const next = () => {
      if (cancelled) return;
      // 다음 장으로 넘어가기 전에도 탭 가시성을 다시 확인한다(작업 중 다른 탭으로 이동한 경우).
      unwatch = whenVisible(() => runIdle(pump));
    };
    loadAndDecodeImage(url, { fetchPriority: 'low' }).then(next, () => {
      warmed.delete(url);
      next();
    });
  };

  unwatch = whenVisible(() => runIdle(() => {
    for (let i = 0; i < lanes; i += 1) pump();
  }));
  return () => { cancelled = true; unwatch(); };
}
