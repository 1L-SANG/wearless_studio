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
  const launch = validMallId(mallId) ? { mallId, query: url.search.slice(1), expiresAt: Date.now() + TTL } : null;
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
