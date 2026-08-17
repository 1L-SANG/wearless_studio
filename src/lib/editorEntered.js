/* =============================================================
   lib/editorEntered — "이 프로젝트는 편집을 시작했다" 표식.

   에디터에 들어온 이상 앞 단계(입력·마네킹·콘티)로는 돌아가지 않는다(오너 8/15).
   되돌아가 다시 생성하면 이미 만든 컷과 편집이 덮여 되살릴 수 없기 때문이다.
   서버 status='done' 만으로는 부족하다 — 생성이 실패·차단으로 끝난 프로젝트는
   done 이 아니어서 뒤로가기·주소창으로 그대로 들어가진다.

   localStorage 를 쓰는 이유: 새로고침·새 탭에서도 유지돼야 한다. 저장소가 막힌
   브라우저(사생활 모드)에서는 조용히 표식 없음으로 동작한다 — 가드가 없을 뿐 앱은 산다.
   ============================================================= */

const KEY = (projectId) => `ed-entered-${projectId}`;

export function markEditorEntered(projectId) {
  if (!projectId) return;
  try { localStorage.setItem(KEY(projectId), '1'); } catch { /* 저장소 차단 — 가드 없이 진행 */ }
}

export function hasEditorEntered(projectId) {
  if (!projectId) return false;
  try { return localStorage.getItem(KEY(projectId)) === '1'; } catch { return false; }
}

/** 표식을 지운다 — **프로젝트를 실제로 없앨 때만** 쓴다.
    새 제작을 시작할 때 이전 프로젝트 표식을 지우면 안 된다: 그 프로젝트를 나중에
    보관함에서 다시 열었을 때 앞 단계 복귀가 열려 편집분이 덮인다(2026-08-17 리뷰). */
export function clearEditorEntered(projectId) {
  if (!projectId) return;
  try { localStorage.removeItem(KEY(projectId)); } catch { /* noop */ }
}
