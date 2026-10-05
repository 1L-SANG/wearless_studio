export const HANDOFF_POLICY = { phone: 'handoff', tablet: 'handoff', desktop: 'full' };

export function classifyDevice({ userAgent = '', userAgentDataMobile, maxTouchPoints = 0, screenShortSide } = {}) {
  if (/iPhone|iPod/i.test(userAgent)
    || (/Android/i.test(userAgent) && /Mobile/i.test(userAgent))
    || userAgentDataMobile === true) return 'phone';
  // 데스크톱 사이트를 요청한 폰도 실제 화면의 짧은 변으로 판별한다.
  if (screenShortSide > 0 && screenShortSide < 600) return 'phone';
  // 갤럭시 탭 삼성 인터넷의 기본 데스크톱 모드는 'X11; Linux' 로 자기를 소개한다. 터치가 있으면 태블릿으로 본다(크롬북 CrOS 제외).
  if (/iPad/i.test(userAgent)
    || (/Macintosh/i.test(userAgent) && maxTouchPoints > 1)
    || /Android/i.test(userAgent)
    || (/Linux/i.test(userAgent) && !/CrOS/i.test(userAgent) && maxTouchPoints > 1)) return 'tablet';
  return 'desktop';
}

let mobile;

// 문서 수명 동안 같은 정책을 유지한다. 회전이나 창 크기 변경은 판정에 쓰지 않는다.
export function isMobileDevice() {
  if (mobile === undefined) {
    const nav = globalThis.navigator;
    const screen = globalThis.screen;
    mobile = HANDOFF_POLICY[classifyDevice({
      userAgent: nav?.userAgent,
      userAgentDataMobile: nav?.userAgentData?.mobile,
      maxTouchPoints: nav?.maxTouchPoints,
      screenShortSide: Math.min(screen?.width, screen?.height),
    })] === 'handoff';
  }
  return mobile;
}
