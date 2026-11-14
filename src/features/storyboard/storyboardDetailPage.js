import { detailPageCreditCost, extensionModelFee } from '../../lib/limits.js';
import { detailPageGenerationCreditShortfall } from '../../lib/creditPreflight.js';
import { continueAfterStoryboardFlush } from './storyboardNavigation.js';

export function storyboardContinueLabel(mannequinStageEnabled = true) {
  return mannequinStageEnabled ? '다음: 마네킹컷 선택' : '상세페이지 만들기';
}

function modelFee({ quote, plan, selectedModelId } = {}) {
  const fee = quote?.detailPageGenerate?.extensionModelFee;
  const quotedModel = quote?.detailPageGenerate?.selectedModelId;
  const sameModel = quotedModel === undefined || quotedModel === (selectedModelId ?? null);
  return sameModel && Number.isFinite(fee) && fee >= 0 ? fee : extensionModelFee(plan, selectedModelId);
}

export function storyboardDetailPageCredits(aiCutCount, options) {
  const cutCredits = detailPageCreditCost(aiCutCount);
  return cutCredits > 0 ? cutCredits + modelFee(options) : cutCredits;
}

export async function loadStoryboardCreditQuote({ mannequinStageEnabled = true, projectId, getCreditQuote, timeoutMs = 8_000 }) {
  if (mannequinStageEnabled || !projectId) return null;
  const controller = new AbortController();
  let timer;
  try {
    const timeout = new Promise(resolve => {
      timer = setTimeout(() => { controller.abort(); resolve(null); }, timeoutMs);
    });
    return await Promise.race([getCreditQuote(projectId, { signal: controller.signal }), timeout]);
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export function storyboardCreditResume(state, mannequinStageEnabled = true) {
  return !mannequinStageEnabled && state?.creditResume?.action === 'detail-page' ? state.creditResume : null;
}

// 저장과 부가 작업이 끝난 뒤 사전 확인한다. 최종 과금 판정은 생성 요청의 서버 402가 맡는다.
export async function continueStoryboardToDetailPage({ submission, flush, afterFlush, onFailure,
  account, aiCutCount, quote, plan, selectedModelId, onShortfall, navigate, isCurrent = () => true }) {
  if (submission.current || !isCurrent()) return false;
  submission.current = true;
  let moved = false;
  try {
    await continueAfterStoryboardFlush({ flush,
      afterFlush: () => { if (isCurrent()) afterFlush?.(); },
      onFailure: (...args) => { if (isCurrent()) onFailure(...args); },
      navigate: () => {
      if (!isCurrent()) return;
      const currentAccount = typeof account === 'function' ? account() : account;
      const shortfall = detailPageGenerationCreditShortfall(currentAccount, aiCutCount,
        modelFee({ quote, plan: currentAccount?.plan ?? plan, selectedModelId }), { compactMessage: true });
      if (shortfall) { onShortfall(shortfall); return; }
      navigate('/create/generating');
      moved = true;
    } });
    return moved;
  } finally {
    // 성공 뒤에는 언마운트까지 잠근다. 저장 실패와 부족 안내는 다시 시도할 수 있다.
    if (!moved) submission.current = false;
  }
}
