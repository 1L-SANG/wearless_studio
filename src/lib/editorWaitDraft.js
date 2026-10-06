import { rebaseAssetUrls, relativizeAssetUrls } from './assetUrl.js';
import { sameEditorJson } from './editorRevisionSync.js';

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
export function saveEditorWaitDraft(projectId, blocks, storage, { baseRevision = null, conflictPending = false } = {}) {
  if (!projectId || !Array.isArray(blocks)) return false;
  const target = browserStorage(storage);
  if (!target) return false;
  try {
    // 저장소에 호스트를 박지 않는다 — 상대경로로 보관해 빌드 환경(API 베이스)이 바뀌어도
    // 읽을 때 현재 호스트로 재기준화되게 한다(재발 차단).
    // baseRevision = 이 편집이 올라선 서버 editor_revision. 다음 진입 때 그새 다른 곳에서
    // 저장됐는지 가려, 오래된 작업본이 새 내용을 덮지 않게 한다(editorWaitDraftDecision).
    const entry = { version: 1, blocks: relativizeAssetUrls(blocks) };
    if (Number.isInteger(baseRevision)) entry.baseRevision = baseRevision;
    if (conflictPending) entry.conflictPending = true;
    target.setItem(keyFor(projectId), JSON.stringify(entry));
    return true;
  } catch { return false; }
}

/** 작업본 내용과 기준 리비전을 **한 번에** 읽는다({blocks, baseRevision} 또는 null).
    키는 이 브라우저의 모든 탭이 같이 쓴다. 내용과 기준을 따로 읽으면 그 사이 다른 탭의 저장
    성공이 키를 지워 기준이 null(=옛 작업본)로 읽히고, 오래된 내용이 복원돼 다른 탭의 최신
    저장을 덮는다(2026-10-02 로컬 두 탭 실측). */
export function loadEditorWaitDraftEntry(projectId, storage) {
  if (!projectId) return null;
  const target = browserStorage(storage);
  if (!target) return null;
  try {
    const saved = JSON.parse(target.getItem(keyFor(projectId)));
    if (!(saved?.version === 1 && Array.isArray(saved.blocks))) return null;
    return {
      blocks: rebaseAssetUrls(saved.blocks),
      baseRevision: Number.isInteger(saved.baseRevision) ? saved.baseRevision : null,
      ...(saved.conflictPending === true ? { conflictPending: true } : {}),
    };
  } catch { return null; }
}

export function loadEditorWaitDraftBaseRevision(projectId, storage) {
  if (!projectId) return null;
  const target = browserStorage(storage);
  if (!target) return null;
  try {
    const base = JSON.parse(target.getItem(keyFor(projectId)))?.baseRevision;
    return Number.isInteger(base) ? base : null;
  } catch { return null; }
}

/** 진입 때 이 브라우저의 작업본을 서버본 위에 복원할지.
    'apply' = 같은 리비전 위의 편집(또는 기준을 모르는 옛 작업본·옛 서버 — 예전 동작 유지),
    'same'  = 리비전은 올랐지만 내용이 같다(나가면서 한 저장이 성공) — 조용히 정리,
    'stale' = 그새 다른 탭·기기가 저장했다 — 서버본을 쓰고 작업본은 버린다,
    'none'  = 작업본 없음. */
export function editorWaitDraftDecision({ draft, draftBase, server, serverRevision }) {
  if (!Array.isArray(draft)) return 'none';
  if (draftBase == null || serverRevision == null || draftBase === serverRevision) return 'apply';
  return sameEditorJson(relativizeAssetUrls(draft), relativizeAssetUrls(server || [])) ? 'same' : 'stale';
}

export function clearEditorWaitDraft(projectId, storage) {
  if (!projectId) return;
  const target = browserStorage(storage);
  if (!target) return;
  try { target.removeItem(keyFor(projectId)); } catch { /* 무시 */ }
}
