import { bodyTypeLabel, heightBucketLabel } from '../../lib/facemarketPhysique.js';

export function profilePhysiqueLine(profile) {
  const { heightCm, heightBucket, bodyType } = profile || {};
  const height = Number.isFinite(heightCm)
    ? `${heightCm}cm`
    : heightBucketLabel(heightBucket);
  const physique = bodyTypeLabel(bodyType);

  return [height ? `키 ${height}` : null, physique].filter(Boolean).join(' · ') || '미정';
}

export function validityLabel(validDays) {
  if (validDays === null) return '영구';
  if (!Number.isFinite(validDays) || validDays <= 0) return '미정';
  if (validDays >= 3650) return '영구';
  if (validDays % 365 === 0) return `${validDays / 365}년`;
  return `${validDays}일`;
}

export function splitTestCutsByKind(cuts = []) {
  return cuts.reduce((groups, cut) => {
    if (cut.kind === 'closeup' || cut.kind === 'fullbody') groups[cut.kind].push(cut);
    return groups;
  }, { closeup: [], fullbody: [] });
}

export function defaultTestCutSelection(cuts = []) {
  const grouped = splitTestCutsByKind(cuts);
  return {
    closeupCutId: grouped.closeup[0]?.id || null,
    fullbodyCutId: grouped.fullbody[0]?.id || null,
  };
}
