import { safeInternalPath } from '../../lib/kakaoOidc.js';

const ADMIN_PATHS = new Set([
  '/applications', '/review', '/usage-reports', '/payout-statements', '/settlements-chain',
  '/bank-transfers', '/models', '/users', '/staff',
]);

export function adminReturnTarget(intent) {
  const path = safeInternalPath(intent);
  if (!path) return null;
  const pathname = path.split(/[?#]/, 1)[0];
  return ADMIN_PATHS.has(pathname) ? path : null;
}
