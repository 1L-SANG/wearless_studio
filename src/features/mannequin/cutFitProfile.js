export function cutFitProfileRecord(cut) {
  if (cut?.fitProfileRecorded !== true) {
    return { recorded: false, profile: undefined };
  }
  const profile = cut.fitProfile;
  return {
    recorded: true,
    profile: profile && typeof profile === 'object' && !Array.isArray(profile) ? profile : null,
  };
}

export function analysisForCutFitProfile(analysis, cut) {
  const record = cutFitProfileRecord(cut);
  if (!record.recorded) return analysis;
  return { ...(analysis || {}), fitProfile: record.profile };
}

// 선택 저장과 생성은 서로 다른 요청이다. 기준 버전을 확정한 뒤 조정안을 생성에 넘긴다.
export async function regenerateWithPersistedSelectedVersion({
  pendingPersistence, selectedCut, adjustedProfile, persistSelection,
  persistFitProfile, regenerate, isCurrent = () => true,
}) {
  // 이전 선택의 저장 실패는 현재 선택을 다시 저장하여 복구한다.
  await Promise.resolve(pendingPersistence).catch(() => {});
  if (!isCurrent()) return false;
  if (!selectedCut?.id) throw new Error('선택한 마네킹 버전이 없어요.');
  await persistSelection(selectedCut.id);
  if (!isCurrent()) return false;
  const record = cutFitProfileRecord(selectedCut);
  if (record.recorded) await persistFitProfile(record.profile);
  if (!isCurrent()) return false;
  return regenerate(adjustedProfile);
}
