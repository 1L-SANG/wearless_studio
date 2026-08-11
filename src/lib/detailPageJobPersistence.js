const DETAIL_PAGE_JOB_KEY = 'wl_detail_page_job';

function browserStorage(storage) {
  if (storage) return storage;
  try { return globalThis.localStorage; } catch { return null; }
}

export function loadDetailPageJobMarker(storage) {
  const target = browserStorage(storage);
  if (!target) return null;
  try {
    const saved = JSON.parse(target.getItem(DETAIL_PAGE_JOB_KEY));
    if (!saved?.projectId) return null;
    return {
      projectId: saved.projectId,
      jobId: saved.jobId || null,
      startedAt: Number(saved.startedAt) || Date.now(),
    };
  } catch { return null; }
}

export function saveDetailPageJobMarker(job, storage) {
  if (!job?.projectId) return;
  const target = browserStorage(storage);
  if (!target) return;
  try {
    target.setItem(DETAIL_PAGE_JOB_KEY, JSON.stringify({
      projectId: job.projectId,
      jobId: job.jobId || null,
      startedAt: Number(job.startedAt) || Date.now(),
    }));
  } catch { /* 저장 공간·사생활 모드 오류는 현재 탭의 생성에는 영향 없음 */ }
}

export function clearDetailPageJobMarker(storage) {
  const target = browserStorage(storage);
  if (!target) return;
  try { target.removeItem(DETAIL_PAGE_JOB_KEY); } catch { /* 무시 */ }
}
