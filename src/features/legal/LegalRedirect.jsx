import { useEffect } from 'react';

export function LegalRedirect({ to }) {
  useEffect(() => {
    window.location.replace(to);
  }, [to]);

  return (
    <div className="route-loading">
      <div>
        <p>법적 고지 문서로 이동하고 있어요.</p>
        <a href={to}>문서 바로 열기</a>
      </div>
    </div>
  );
}
