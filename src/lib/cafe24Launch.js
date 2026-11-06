const LAUNCH_KEY = 'cafe24-app-launch:v1';
const TTL = 10 * 60 * 1000;
const validMallId = (value) => /^[a-z0-9][a-z0-9_-]{1,29}$/.test(value);

export function readCafe24Launch() {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(LAUNCH_KEY));
    if (value && validMallId(value.mallId) && typeof value.query === 'string' && typeof value.expiresAt === 'number' && value.expiresAt > Date.now()
      && value.expiresAt <= Date.now() + TTL) return value;
    window.sessionStorage.removeItem(LAUNCH_KEY);
  } catch { /* A blocked store falls back to manual installation. */ }
  return null;
}

export function captureCafe24Launch() {
  const url = new URL(window.location.href);
  if (!url.searchParams.has('mall_id')) return readCafe24Launch();
  const mallId = url.searchParams.get('mall_id').trim().toLowerCase();
  const launch = validMallId(mallId) ? { mallId, query: window.location.search.slice(1), expiresAt: Date.now() + TTL } : null;
  try {
    window.sessionStorage.removeItem(LAUNCH_KEY);
    if (launch) {
      window.sessionStorage.setItem(LAUNCH_KEY, JSON.stringify(launch));
    }
  } catch { /* The URL is still cleaned when storage is unavailable. */ }
  for (const key of [...url.searchParams.keys()]) if (key !== 'returnTo') url.searchParams.delete(key);
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  return launch;
}

export function consumeCafe24Launch() {
  const launch = readCafe24Launch();
  try {
    // Consume before any request, including failures and StrictMode remounts.
    window.sessionStorage.removeItem(LAUNCH_KEY);
    return launch;
  } catch { return null; }
}

export const CAFE24_INSTALL_COPY = {
  cancelled: '설치를 취소했어요. 다시 시도하면 돼요',
  state_invalid: '설치 확인이 만료됐어요. 같은 브라우저에서 앱을 다시 설치해 주세요',
  mall_mismatch: '입력한 몰 아이디와 로그인한 몰이 달라요. 몰 아이디를 확인해 주세요',
  missing_permission: '권한을 모두 허용해야 등록할 수 있어요. 다시 설치하며 권한을 허용해 주세요',
  not_allowed: '지금은 시범 운영 중이라 등록된 몰만 연결할 수 있어요',
  cafe24_not_allowed: '지금은 시범 운영 중이라 등록된 몰만 연결할 수 있어요',
  invalid_mall_id: '몰 아이디를 확인해 주세요',
  connection_changed: '그 사이 연결이 바뀌었어요. 지금 상태를 확인한 뒤 다시 시도해 주세요',
  token_exchange_failed: '카페24에서 인증을 마치지 못했어요. 잠시 뒤 다시 시도해 주세요',
  account_closed: '계정 삭제가 완료되어 사용할 수 없습니다.',
  rate_limited: '설치 시도가 많아요. 1시간 뒤에 다시 시도해 주세요',
  launch_reused: '이미 쓴 실행 주소예요. 카페24에서 앱을 다시 열어 주세요',
};
