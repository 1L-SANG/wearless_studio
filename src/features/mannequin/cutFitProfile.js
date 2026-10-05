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
