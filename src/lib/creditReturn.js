const CREDIT_RETURN_KEY = 'wl_creditReturn';

export function recordCreditReturn({ projectId, path, action = null, requiredCredits = null }) {
  if (!projectId || !(path.startsWith('/create/') || path.startsWith('/editor/'))) return;
  try {
    sessionStorage.setItem(CREDIT_RETURN_KEY, JSON.stringify({
      projectId,
      path,
      action,
      requiredCredits,
    }));
  } catch { /* 저장 불가면 일반 pricing 이동만 유지 */ }
}

export function readCreditReturn(projectId) {
  try {
    const value = JSON.parse(sessionStorage.getItem(CREDIT_RETURN_KEY) || 'null');
    if (!value) return null;
    if (!projectId || value.projectId !== projectId) {
      sessionStorage.removeItem(CREDIT_RETURN_KEY);
      return null;
    }
    return value;
  } catch {
    sessionStorage.removeItem(CREDIT_RETURN_KEY);
    return null;
  }
}

export function clearCreditReturn() {
  try { sessionStorage.removeItem(CREDIT_RETURN_KEY); } catch { /* noop */ }
}
