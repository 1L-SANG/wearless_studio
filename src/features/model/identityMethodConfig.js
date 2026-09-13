/* =============================================================
   identityMethodConfig — 인증 수단 설정의 순수 파생 로직(ModelRegister.jsx 전용).
   ModelRegister.jsx 는 JSX 를 포함해 이 레포의 plain `node --test`(JSX 트랜스폼 없음)로
   직접 import 할 수 없다 — 그래서 값이 옳은지(특히 아래 삼항의 방향이 뒤집히지 않았는지,
   리뷰 IMPORTANT 4)를 실제로 여러 입력값으로 검증하려면 이렇게 React 없는 순수 함수로
   뽑아 둬야 한다. `import.meta.env` 는 모듈 로드 시점에 값이 고정돼 테스트가 바꿔 넣을 수
   없으므로, 값을 인자로 받는 함수여야 테스트가 "URL 이 있을 때/없을 때" 둘 다 만들 수 있다.
   ============================================================= */

// VITE_FM_IDENTITY_METHODS(콤마 분리, 기본 'mid')를 배열로 바꾼다. 배열 길이가 1 이면
// IdentityMethodStep 이 화면을 안 그리고 그 방법으로 바로 진행한다(발표/롤백 모드).
// raw 가 ',,' 처럼 구분자만 있고 실제 값이 없는 경우도 방어한다 — 그대로 두면 빈 배열이
// 나와 IdentityMethodStep 이 null 을 그리고(length<=1) 자동 선택도 안 돈다(length===1 이
// 아니므로) — 위저드가 consent 이후로 영영 못 넘어간다.
export function parseIdentityMethods(raw) {
  const methods = (raw || 'mid')
    .split(',')
    .map((method) => method.trim())
    .filter(Boolean);
  return methods.length > 0 ? methods : ['mid'];
}

// VITE_CX_AUTH_CONFIG_URL(configUrl) 이 없으면 간편인증 버튼을 막을 이유 문구를, 있으면
// null 을 돌려준다. 이 삼항이 뒤집히면(URL 이 있을 때 막고 없을 때 열어 버리면) 없는 설정
// 으로 위젯을 열어 v1.0 경로를 타서 조용히 실패한다 — 그 방향을 직접 값으로 검증한다.
export function deriveSimpleAuthUnavailableReason(configUrl) {
  return configUrl
    ? null
    : '간편인증은 지금 설정되지 않았어요. 모바일 신분증으로 확인해 주세요.';
}
