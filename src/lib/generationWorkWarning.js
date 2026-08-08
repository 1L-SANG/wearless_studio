// 생성 관련 분석 필드를 바꾸기 전에 경고가 필요한지, 어떤 문구를 보여줄지 판정하는 순수 로직.
// node --test 로 직접 검증하기 위해 @/ 별칭 · import.meta.env 의존 없이 분리했다
// (generationRelevance.js 와 같은 이유).
//
// "마네킹 컷이 이미 있다"만으로는 늦다 — 이 흐름(입력 → 콘티 → 마네킹)은 콘티 진입 시
// 마네킹 생성을 백그라운드로 먼저 쏘고, 컷은 그 job 이 끝나야 나타난다. job 이 도는 동안
// 성별 등을 바꾸면 컷이 0장이라 경고가 안 뜨고, 잠시 뒤 job 이 '옛 선택'으로 완성한 유료
// 컷이 도착한 뒤 마네킹 화면의 dirty 플래그가 또 한 번 유료 재생성을 부른다 — 같은 마음
// 바뀜에 두 번 과금되는 셈이다. 그래서 신호를 "컷이 있다" 에서 "컷이 있다, 또는 지금 이
// 프로젝트의 생성이 돌고 있다"로 넓힌다.
export function generationWorkWarningKind({ cutsExist, jobStatus, jobProjectId, projectId }) {
  if (cutsExist) return 'cuts';
  if (jobStatus === 'running' && jobProjectId != null && jobProjectId === projectId) return 'running';
  return 'none';
}
