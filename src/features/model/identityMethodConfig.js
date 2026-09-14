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

// isMobileLike: "이 기기로 신분증을 카메라로 찍어 끝까지 갈 수 있는가"를 묻는 힌트.
// User-Agent 는 쓰지 않는다 — 위조가 너무 쉽고, 데스크톱 브라우저의 모바일 에뮬레이션·
// 태블릿에서 오판이 잦다. 대신 입력 방식을 본다: 거친 포인터(coarse, 손가락)가 주 입력
// 이면 후면 카메라가 있는 기기일 가능성이 높다.
//
// 이건 안내용 힌트이지 통제가 아니다. 진짜 통제는 서버가 업로드된 사진의 마스크 기하를
// 검사하는 것이고(설계 §5), 그 검사는 어떤 기기가 찍었다고 주장하든 무관하게 동작한다.
// 그래서 여기서 판별에 실패하는 모든 경우(fine pointer 로 오판, window·matchMedia 부재—
// SSR·구형 브라우저)는 안전한 방향인 "허용"으로 접는다(fail open). 반대로 접으면(fail
// closed) 실제로 끝까지 갈 수 있는 사용자를 이 힌트 하나가 막아 버린다.
//
// windowLike 를 인자로 받는 이유는 파일 헤더에 적은 관례와 같다 — window 는
// import.meta.env 와 달리 모듈 로드 뒤에도 몽키패치로 바꿀 수 있는 전역이지만, "테스트가
// 값을 인자로 넣어 변주한다"는 이 파일의 방식을 그대로 따르기 위해 인자로 받는다. 기본값은
// 호출 시점의 실제 window(있다면) — 프로덕션 코드는 인자 없이 `isMobileLike()`로 부른다.
export function isMobileLike(windowLike = (typeof window === 'undefined' ? undefined : window)) {
  if (!windowLike || typeof windowLike.matchMedia !== 'function') return true;
  return Boolean(windowLike.matchMedia('(pointer: coarse)').matches);
}

// isMobileLike() 가 false 일 때 사용자에게 보여줄 문구. 두 곳이 이 문구를 그대로 써야 한다:
// (1) IdentityMethodStep — 선택 화면에서 간편인증 버튼을 비활성화할 때, (2) ModelRegister의
// startEnrollment — 수단이 하나뿐이라(VITE_FM_IDENTITY_METHODS=simple_auth) 그 선택 화면
// 자체가 안 뜨고 곧장 여기로 오는 경로를 막을 때. 문구를 두 파일에 각자 적으면 언젠가
// 말이 갈린다(하나만 고치고 하나는 안 고치는 흔한 실수) — 여기 하나로 묶어 둔다.
export const SIMPLE_AUTH_DEVICE_REASON = '간편인증은 휴대폰에서 진행해 주세요. 폰에서 같은 계정으로 접속하면 여기서부터 이어져요.';
