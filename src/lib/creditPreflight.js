// 유료 단계 진입 전, store 에 이미 캐시된 잔액만으로 부족 여부와 안내 문구를 만드는 순수 로직.
// 표시용 사전 확인이므로 계정·비용을 모르면 통과시키고, 최종 정합성은 서버 402가 책임진다.
import { CREDIT_COSTS } from './limits.js';

const SHORTFALL_TITLE = '크레딧이 부족해요';

function creditShortfall(account, requiredCredits) {
  if (account == null || requiredCredits == null) return null;
  const availableCredits = account.credits;
  if (!Number.isFinite(availableCredits) || !Number.isFinite(requiredCredits)) return null;
  if (availableCredits >= requiredCredits) return null;

  const description = `보유 ${availableCredits} · 필요 ${requiredCredits}. 충전 후 다시 시도해 주세요.`;
  return {
    availableCredits,
    requiredCredits,
    message: `${SHORTFALL_TITLE} — ${description}`,
  };
}

export function mannequinGenerationCreditShortfall(account) {
  return creditShortfall(account, CREDIT_COSTS.mannequinGenerate);
}

export function detailPageGenerationCreditShortfall(account, aiCutCount) {
  const requiredCredits = aiCutCount == null
    ? null
    : aiCutCount * CREDIT_COSTS.storyboardPerCut;
  return creditShortfall(account, requiredCredits);
}
