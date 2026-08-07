const EDITOR_WAIT_DRAFT_PREFIX = 'ew-draft-';

function browserStorage(storage) {
  if (storage) return storage;
  try { return globalThis.localStorage; } catch { return null; }
}

const keyFor = (projectId) => `${EDITOR_WAIT_DRAFT_PREFIX}${projectId}`;

export function loadEditorWaitDraft(projectId, storage) {
  if (!projectId) return null;
  const target = browserStorage(storage);
  if (!target) return null;
  try {
    const saved = JSON.parse(target.getItem(keyFor(projectId)));
    return saved?.version === 1 && Array.isArray(saved.blocks) ? saved.blocks : null;
  } catch { return null; }
}

export function saveEditorWaitDraft(projectId, blocks, storage) {
  if (!projectId || !Array.isArray(blocks)) return;
  const target = browserStorage(storage);
  if (!target) return;
  try {
    target.setItem(keyFor(projectId), JSON.stringify({ version: 1, blocks }));
  } catch { /* 저장 공간·사생활 모드 오류는 현재 화면의 편집에는 영향 없음 */ }
}

export function clearEditorWaitDraft(projectId, storage) {
  if (!projectId) return;
  const target = browserStorage(storage);
  if (!target) return;
  try { target.removeItem(keyFor(projectId)); } catch { /* 무시 */ }
}
