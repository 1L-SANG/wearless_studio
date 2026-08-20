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

/** 편집분을 이 브라우저에 임시 보관. **정말 보관됐는지 boolean 으로 돌려준다** —
    저장 공간 초과·사생활 모드에서는 조용히 실패하는데, 그걸 모르고 화면이 "보관해 뒀어요"
    라고 말하면 셀러는 안심하고 창을 닫고 편집을 잃는다(2026-08-19). 대기 화면 경로처럼
    반환값이 필요 없는 호출부는 그대로 무시하면 된다. */
export function saveEditorWaitDraft(projectId, blocks, storage) {
  if (!projectId || !Array.isArray(blocks)) return false;
  const target = browserStorage(storage);
  if (!target) return false;
  try {
    target.setItem(keyFor(projectId), JSON.stringify({ version: 1, blocks }));
    return true;
  } catch { return false; }
}

export function clearEditorWaitDraft(projectId, storage) {
  if (!projectId) return;
  const target = browserStorage(storage);
  if (!target) return;
  try { target.removeItem(keyFor(projectId)); } catch { /* 무시 */ }
}
