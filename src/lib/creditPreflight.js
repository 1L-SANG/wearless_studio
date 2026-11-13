// 유료 단계 진입 전, store 에 이미 캐시된 잔액만으로 부족 여부와 안내 문구를 만드는 순수 로직.
// 표시용 사전 확인이므로 계정·비용을 모르면 통과시키고, 최종 정합성은 서버 402가 책임진다.
import { CREDIT_COSTS, detailPageCreditCost } from './limits.js';
import { isMannequinStageEnabled } from './mannequinStage.js';

const SHORTFALL_TITLE = '크레딧이 부족해요';

function creditShortfall(account, requiredCredits) {
  if (account == null || requiredCredits == null) return null;
  const availableCredits = account.credits;
  if (!Number.isFinite(availableCredits) || !Number.isFinite(requiredCredits)) return null;
  if (availableCredits >= requiredCredits) return null;

  return {
    availableCredits,
    requiredCredits,
    message: `${SHORTFALL_TITLE}. 보유 ${availableCredits}, 필요 ${requiredCredits}`,
  };
}

export function mannequinGenerationCreditShortfall(
  account,
  requiredCredits = CREDIT_COSTS.mannequinGenerate,
  mannequinStageEnabled = isMannequinStageEnabled(),
) {
  if (!mannequinStageEnabled) return null;
  return creditShortfall(account, requiredCredits);
}

export function detailPageGenerationCreditShortfall(account, aiCutCount, extensionModelFee = 0) {
  const requiredCredits = detailPageCreditCost(aiCutCount);
  return creditShortfall(account, requiredCredits > 0 ? requiredCredits + extensionModelFee : requiredCredits);
}
