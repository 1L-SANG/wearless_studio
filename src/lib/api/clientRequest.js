import { clientEvents, newRequestId } from '../clientEvents.js';
import { routeTemplate, safeCode, validUuid } from '../clientEventPrivacy.js';

const responses = new WeakMap();
const BASE_URL = import.meta.env?.VITE_API_BASE_URL ?? '';

export function isOwnApiRequest(target, baseUrl = BASE_URL, origin = globalThis.location?.origin) {
  try {
    const base = new URL(baseUrl || origin);
    const url = new URL(target, origin || base);
    return ['http:', 'https:'].includes(url.protocol) && url.origin === base.origin
      && url.pathname.startsWith('/v1/') && !/^\/v1\/assets\/[^/]+\/file\/?$/.test(url.pathname);
  } catch { return false; }
}

export function attachResponseError(error, response) {
  const info = responses.get(response);
  if (!info) return error;
  error.requestId = info.request_id;
  error.status ??= info.status;
  error.clientApi = { ...info, error_code: safeCode(error.code) };
  return error;
}

export async function readClientResponse(response, format = 'json') {
  try { return await response[format](); }
  catch (error) { throw attachResponseError(error, response); }
}

export async function clientApiFetch(target, options = {}) {
  if (!isOwnApiRequest(target)) {
    const headers = new Headers(options.headers);
    if (!headers.has('X-Client-Request-Id')) return fetch(target, options);
    headers.delete('X-Client-Request-Id');
    return fetch(target, { ...options, headers });
  }
  const requestId = newRequestId();
  const method = (options.method || 'GET').toUpperCase();
  const started = Date.now();
  const route = routeTemplate(target);
  const headers = new Headers(options.headers);
  headers.set('X-Client-Request-Id', requestId);
  let response;
  try {
    if (globalThis.navigator?.onLine === false) {
      throw Object.assign(new Error('인터넷 연결이 끊겼어요. 연결을 확인해 주세요.'), { code: 'browser_offline', requestNotSent: true });
    }
    // fetch는 리다이렉트 뒤에 임의 헤더를 유지한다. 외부 전송은 따라가기 전에 막는다.
    response = await fetch(target, { ...options, headers, redirect: 'error' });
  } catch (cause) {
    const error = cause && typeof cause === 'object' ? cause : new Error('요청을 완료하지 못했어요.');
    if (options.signal?.aborted && typeof cause !== 'object') error.name = 'AbortError';
    error.requestId = requestId;
    error.clientApi = { method, route, status: 0, duration_ms: Math.min(3600000, Date.now() - started), request_id: requestId };
    if (error.name !== 'AbortError') clientEvents.addBreadcrumb({ type: 'api', ...error.clientApi, route: target });
    throw error;
  }
  const info = { method, route, status: response.status, duration_ms: Math.min(3600000, Date.now() - started),
    request_id: validUuid(response.headers?.get('X-Request-Id')) || requestId };
  responses.set(response, info);
  // 성공 JSON의 오류 job도 원래 생성 요청과 연결한다. 원문은 발자취에 넣지 않는다.
  let errorCode;
  if (!response.ok) {
    try { errorCode = safeCode((await response.clone().json())?.error?.code); } catch { /* 비 JSON */ }
  }
  clientEvents.addBreadcrumb({ type: 'api', ...info, route: target, error_code: errorCode });
  return response;
}
