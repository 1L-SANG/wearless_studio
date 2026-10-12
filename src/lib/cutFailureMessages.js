/* ADR-0017, 빈 칸과 AI 탭에서 원본이 필요한 실패만 구분한다. */
const SOURCE_DEPENDENT_REASONS = new Set([
  'similar_cut_source_failed',
  'duplicate_cut_source_failed',
]);

export function failedCutPresentation(element = {}) {
  const retryable = !SOURCE_DEPENDENT_REASONS.has(element.genFailReason);
  return {
    retryable,
    guidance: retryable ? '저희 쪽에서 다시 만들 수 있어요' : '원본 컷을 먼저 만들어 주세요',
  };
}
