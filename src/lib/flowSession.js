const FLOW_SESSION_KEY = 'wl_flowSession';
export const CONFIRMED_INPUT_REPEAT_MS = 7000;

function readNavigationType() {
  try {
    return performance.getEntriesByType('navigation')[0]?.type || '';
  } catch { return ''; }
}

export function readFlowSession() {
  try {
    return JSON.parse(sessionStorage.getItem(FLOW_SESSION_KEY) || 'null');
  } catch { return null; }
}

export function markFlowSession(projectId, path, patch = {}) {
  if (!projectId) return;
  try {
    const current = readFlowSession();
    const base = current?.projectId === projectId ? current : {};
    sessionStorage.setItem(FLOW_SESSION_KEY, JSON.stringify({
      ...base,
      ...patch,
      projectId,
      path,
    }));
  } catch { /* sessionStorage 차단 시 현재 문서 수명의 모듈 표식만 사용 */ }
}

export function clearFlowSession() {
  try { sessionStorage.removeItem(FLOW_SESSION_KEY); } catch { /* noop */ }
}

export function markProductInfoConfirmed(projectId) {
  markFlowSession(projectId, '/create/storyboard', {
    productInfoConfirmed: true,
    confirmedInputEntryAt: null,
    confirmedInputEntryCount: 0,
    confirmedInputEntryToken: null,
  });
}

export function isProductInfoConfirmed(projectId) {
  const marker = readFlowSession();
  return marker?.projectId === projectId && marker.productInfoConfirmed === true;
}

export function registerConfirmedInputEntry(projectId, now = Date.now(), entryToken = null) {
  if (!isProductInfoConfirmed(projectId)) return 'continue';
  const marker = readFlowSession();
  if (entryToken && marker?.confirmedInputEntryToken === entryToken) {
    return (marker.confirmedInputEntryCount || 0) >= 2 ? 'start-new' : 'redirect';
  }
  const repeated = Number.isFinite(marker?.confirmedInputEntryAt)
    && now - marker.confirmedInputEntryAt <= CONFIRMED_INPUT_REPEAT_MS;
  const count = repeated ? (marker.confirmedInputEntryCount || 1) + 1 : 1;
  markFlowSession(projectId, '/create/storyboard', {
    confirmedInputEntryAt: now,
    confirmedInputEntryCount: count,
    confirmedInputEntryToken: entryToken,
  });
  return count >= 2 ? 'start-new' : 'redirect';
}

export function isSameTabProjectReload(projectId) {
  const marker = readFlowSession();
  return readNavigationType() === 'reload' && marker?.projectId === projectId;
}

export function setAnalysisRunning(projectId, running) {
  if (!projectId) return;
  markFlowSession(projectId, window.location.pathname, { analysisRunning: running });
}

export function isAnalysisRunning(projectId) {
  const marker = readFlowSession();
  return marker?.projectId === projectId && marker.analysisRunning === true;
}

export function authorizeFlowContinuation(projectId, path) {
  markFlowSession(projectId, path, { continuation: true });
}

export function hasFlowContinuation(projectId) {
  const marker = readFlowSession();
  return marker?.projectId === projectId && marker.continuation === true;
}

export function consumeFlowContinuation(projectId) {
  const marker = readFlowSession();
  if (!hasFlowContinuation(projectId)) return false;
  markFlowSession(projectId, marker.path, { continuation: false });
  return true;
}
