/* '동작 줄이기' 설정을 읽는다. 캐러셀에서 두 곳이 쓴다 — 스테이지(감쇠를 건너뛰고 그 프레임에
   확정)와 컨트롤러(자동 회전을 아예 켜지 않는다). 설정은 페이지를 연 뒤에도 바뀌므로
   change 를 구독한다. */
import { useEffect, useState } from 'react';

const QUERY = '(prefers-reduced-motion: reduce)';

export function usePrefersReducedMotion() {
  // 서버 렌더나 matchMedia 가 없는 환경에서는 false 로 시작한다 — 첫 페인트에 모션을
  // 끄는 쪽이 안전해 보이지만, 그러면 설정을 안 켠 대다수에게 애니메이션이 한 번 끊긴다.
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;

    const query = window.matchMedia(QUERY);
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  return reduced;
}
