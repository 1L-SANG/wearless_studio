import { useEffect, useState } from 'react';
import { api, isMockMode } from '../../lib/api/index.js';
import { CREDIT_COSTS, editorCreditCostsFromQuote } from '../../lib/limits.js';

// HTTP 카탈로그의 옵션은 로컬이다. 가격은 실제 서버 견적이 확인된 값만 사용한다.
export function useEditorCreditCosts(projectId, catalogCosts) {
  const [quote, setQuote] = useState(null);
  useEffect(() => {
    if (isMockMode || !projectId) return;
    let alive = true;
    api.getCreditQuote(projectId).then((costs) => {
      if (alive) setQuote({ projectId, costs });
    }).catch(() => {});
    return () => { alive = false; };
  }, [projectId]);
  if (isMockMode) return catalogCosts || CREDIT_COSTS;
  return editorCreditCostsFromQuote(quote?.projectId === projectId ? quote.costs : null,
    catalogCosts?.editorImage);
}
