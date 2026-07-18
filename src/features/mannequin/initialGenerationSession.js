const KEY_PREFIX = 'wl_initial_mannequin_generation:';

function key(projectId) {
  return `${KEY_PREFIX}${projectId}`;
}

export function markInitialGenerationRequested(projectId) {
  if (!projectId || typeof sessionStorage === 'undefined') return;
  try { sessionStorage.setItem(key(projectId), '1'); } catch { /* sessionStorage unavailable */ }
}

export function clearInitialGenerationRequested(projectId) {
  if (!projectId || typeof sessionStorage === 'undefined') return;
  try { sessionStorage.removeItem(key(projectId)); } catch { /* sessionStorage unavailable */ }
}

export function hadInitialGenerationRequest(projectId) {
  if (!projectId || typeof sessionStorage === 'undefined') return false;
  try { return sessionStorage.getItem(key(projectId)) === '1'; } catch { return false; }
}

export function cutsExistedBeforeInitialGeneration(projectId, cuts) {
  return (cuts || []).length > 0 && !hadInitialGenerationRequest(projectId);
}
