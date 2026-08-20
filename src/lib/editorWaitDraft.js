import { rebaseAssetUrls, relativizeAssetUrls } from './assetUrl.js';

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
    if (!(saved?.version === 1 && Array.isArray(saved.blocks))) return null;
    // 옛 로컬 빌드가 localhost 등 잘못된 호스트로 절대화한 편집분이 이 버퍼에 남아 있으면
    // (서버 데이터보다 우선 복원되므로) 이미지가 이 브라우저에서만 403 난다. 읽을 때 현재
    // API 호스트로 재기준화해 자가치유한다.
    return rebaseAssetUrls(saved.blocks);
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
    // 저장소에 호스트를 박지 않는다 — 상대경로로 보관해 빌드 환경(API 베이스)이 바뀌어도
    // 읽을 때 현재 호스트로 재기준화되게 한다(재발 차단).
    target.setItem(keyFor(projectId), JSON.stringify({ version: 1, blocks: relativizeAssetUrls(blocks) }));
    return true;
  } catch { return false; }
}

export function clearEditorWaitDraft(projectId, storage) {
  if (!projectId) return;
  const target = browserStorage(storage);
  if (!target) return;
  try { target.removeItem(keyFor(projectId)); } catch { /* 무시 */ }
}
