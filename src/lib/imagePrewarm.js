/* =============================================================
   imagePrewarm — 콘티보드 썸네일 선캐싱.

   보드에 놓일 이미지는 우리 R2 썸네일이라 URL이 진입 시점에 이미 확정돼 있다.
   섹션이 접혀 있는 동안(=사용자가 아직 보지 않을 때) 유휴 시간에 미리 받아
   디코드까지 끝내두면, 펼치는 순간 네트워크·디코드 비용이 사라진다.

   원칙
   - 유휴 시간에만(requestIdleCallback), 낮은 우선순위로 → 첫 화면 렌더와 경쟁하지 않는다.
   - 동시 요청 수를 제한 → 저사양·저속 회선에서도 다른 요청을 굶기지 않는다.
   - 세션 내 중복 요청 금지(warmed) → 재진입·리렌더로 같은 URL을 다시 받지 않는다.
   - 실패는 조용히 무시 → 프리워밍은 어디까지나 최적화이고, 실패해도 화면은 정상 동작한다.
   ============================================================= */

const warmed = new Set();

// data:/blob: 는 이미 메모리에 있으므로 대상이 아니다(목 모드의 SVG 자리표시자 등).
const isWarmable = (url) => typeof url === 'string'
  && (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('/'));

const runIdle = (fn) => (typeof requestIdleCallback === 'function'
  ? requestIdleCallback(fn, { timeout: 1500 })
  : setTimeout(fn, 250));

/** URL 목록을 유휴 시간에 미리 받아 캐시를 데운다. 반환값을 호출하면 남은 작업을 취소한다. */
export function prewarmImages(urls, { concurrency = 3 } = {}) {
  if (typeof window === 'undefined' || typeof Image === 'undefined') return () => {};
  const queue = [...new Set(urls)].filter((url) => isWarmable(url) && !warmed.has(url));
  if (!queue.length) return () => {};
  queue.forEach((url) => warmed.add(url));

  let cancelled = false;
  const pump = () => {
    if (cancelled) return;
    const url = queue.shift();
    if (!url) return;
    const img = new Image();
    img.decoding = 'async';
    if ('fetchPriority' in img) img.fetchPriority = 'low';
    const next = () => { if (!cancelled) runIdle(pump); };
    img.onload = () => {
      // decode()까지 마쳐야 펼칠 때 디코드 비용도 남지 않는다(미지원 브라우저는 그냥 넘어간다).
      const decoded = typeof img.decode === 'function' ? img.decode() : Promise.resolve();
      decoded.catch(() => {}).then(next);
    };
    img.onerror = () => { warmed.delete(url); next(); };
    img.src = url;
  };

  runIdle(() => { for (let i = 0; i < concurrency; i += 1) pump(); });
  return () => { cancelled = true; };
}
