// 서버 client_events.py의 허용 목록만 사용한다. 입력값은 진단 자료로 복사하지 않는다.
export const CLIENT_EVENT_KINDS = new Set(['api_error', 'ui_error', 'runtime_error', 'unhandled_rejection',
  'error_boundary', 'network_error', 'toast_error', 'save_error', 'upload_error', 'generation_error', 'analysis_error']);
export const CLIENT_EVENT_ACTIONS = new Set(['save', 'upload', 'generate', 'analyze', 'restore', 'navigate',
  'load', 'poll', 'download', 'export', 'login', 'unknown', 'mannequinGenerate', 'mannequinAdjust', 'detailPageGenerate', 'editorImage']);
export const CLIENT_EVENT_STEPS = new Set(['input', 'storyboard', 'mannequin', 'generating', 'editor', 'library', 'other']);
export const WHOLE_SCREEN_KINDS = new Set(['ui_error', 'error_boundary', 'runtime_error', 'unhandled_rejection']);
const ROUTE_SEGMENTS = new Set(('v1 seller projects project editor storyboard product-input input analysis analyze mannequin '
  + 'matching library assets photos jobs events generate save upload complete presign bytes file detail-page detail-pages '
  + 'facemarket models personalization profiles me account draft-slot handoff mobile pricing credits unknown').split(' '));

export const validUuid = (value) => typeof value === 'string'
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) ? value.toLowerCase() : null;
export const safeCode = (value) => typeof value === 'string' && /^[A-Za-z0-9_.:-]+$/.test(value.slice(0, 64))
  ? value.slice(0, 64) : null;
export const safeErrorCode = (value) => typeof value === 'string' && /^[a-z][a-z_]{0,31}$/.test(value) ? value : 'other';
export const safeInteger = (value, max) => Number.isInteger(value) && value >= 0 && value <= max ? value : null;

export function routeTemplate(value) {
  if (typeof value !== 'string' || value.length > 512) return '/unknown';
  let path;
  try {
    new URL(value, 'https://route.invalid');
    path = value.split(/[?#]/)[0].replace(/^(?:[a-z][a-z0-9+.-]*:)?\/\/[^/]*/i, '');
  } catch { return '/unknown'; }
  return '/' + path.split('/').slice(0, 16).filter(Boolean).map((part) => (
    ROUTE_SEGMENTS.has(part) ? part
      : validUuid(part) || /^\p{Decimal_Number}+$/u.test(part) || part === ':id' || /^\{[a-zA-Z_]+\}$/.test(part) ? ':id' : ':segment'
  )).join('/');
}

// 자유 문장을 정규식으로 개인정보와 구별할 수 없으므로 알려진 기술 분류만 남긴다.
export function sanitizeTechnicalMessage(value) {
  if (typeof value !== 'string') return '';
  return (/Failed to fetch|NetworkError|Load failed/i.test(value) ? 'NetworkError'
    : /TypeError/.test(value) ? 'TypeError' : /ReferenceError/.test(value) ? 'ReferenceError'
      : /SyntaxError/.test(value) ? 'SyntaxError' : /RangeError/.test(value) ? 'RangeError' : '[redacted]').slice(0, 500);
}

export function sanitizeTechnicalStack(value) {
  if (typeof value !== 'string') return '';
  const frames = value.split('\n').slice(0, 30).flatMap((line) => {
    const frame = line.match(/(?:https?:\/\/[^\s)]+|\/[^\s)]+):(\d+|\[digits\]):(\d+|\[digits\])\)?$/);
    if (!frame) return [];
    const address = frame[0].replace(/:(?:\d+|\[digits\]):(?:\d+|\[digits\])\)?$/, '');
    const position = (part) => /^\d{6,}$/.test(part) ? '[digits]' : part;
    return [`at ${routeTemplate(address)}:${position(frame[1])}:${position(frame[2])}`];
  });
  // 중간에서 잘린 프레임이 다음 큐 정제에서 사라지지 않게 완전한 줄만 남긴다.
  while (frames.join('\n').length > 2000) frames.pop();
  return frames.join('\n');
}

export function sanitizeDiagnostics(raw) {
  const clean = { step: CLIENT_EVENT_STEPS.has(raw.step) ? raw.step : 'other', message_code: safeErrorCode(raw.message_code) };
  for (const key of ['browser', 'os']) {
    if (!raw[key] || typeof raw[key] !== 'object' || Array.isArray(raw[key])) continue;
    clean[key] = {};
    if (typeof raw[key].family === 'string' && /^[A-Za-z][A-Za-z0-9 _-]{0,31}$/.test(raw[key].family)) clean[key].family = raw[key].family;
    if (typeof raw[key].major === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,7}$/.test(raw[key].major)) clean[key].major = raw[key].major;
  }
  if (raw.viewport && typeof raw.viewport === 'object' && !Array.isArray(raw.viewport)) {
    clean.viewport = {};
    for (const key of ['width', 'height']) if (safeInteger(raw.viewport[key], 16384) != null) clean.viewport[key] = raw.viewport[key];
  }
  if (raw.network && typeof raw.network === 'object' && !Array.isArray(raw.network)) {
    clean.network = { effective_type: ['slow-2g', '2g', '3g', '4g', 'unknown'].includes(raw.network.effective_type) ? raw.network.effective_type : 'unknown' };
    for (const [key, max] of [['rtt_ms', 60000], ['downlink_mbps', 10000]]) {
      const value = raw.network[key];
      if (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= max) clean.network[key] = value;
    }
    for (const key of ['save_data', 'online']) if (typeof raw.network[key] === 'boolean') clean.network[key] = raw.network[key];
  }
  if (safeInteger(raw.page_age_ms, 604800000) != null) clean.page_age_ms = raw.page_age_ms;
  if (['visible', 'hidden'].includes(raw.visibility)) clean.visibility = raw.visibility;
  if (typeof raw.tech_message === 'string') clean.tech_message = sanitizeTechnicalMessage(raw.tech_message);
  if (WHOLE_SCREEN_KINDS.has(raw.kind) && typeof raw.stack === 'string') clean.stack = sanitizeTechnicalStack(raw.stack);
  return clean;
}

export function sanitizeContext(raw = {}) {
  const clean = {};
  for (const key of ['online', 'local_backup']) if (typeof raw?.[key] === 'boolean') clean[key] = raw[key];
  if (['visible', 'hidden'].includes(raw?.visibility)) clean.visibility = raw.visibility;
  if (['owned', 'lost', 'none', 'unknown'].includes(raw?.draft_slot_status)) clean.draft_slot_status = raw.draft_slot_status;
  if (safeInteger(raw?.photo_pending_count, 1000) != null) clean.photo_pending_count = raw.photo_pending_count;
  return clean;
}

export function sanitizeBreadcrumb(raw) {
  const clean = { type: ['api', 'action', 'navigation'].includes(raw.type) ? raw.type : 'unknown',
    route: routeTemplate(raw.route), action: CLIENT_EVENT_ACTIONS.has(raw.action) ? raw.action : 'unknown' };
  if (typeof raw.at === 'string' && Number.isFinite(Date.parse(raw.at))) clean.at = new Date(raw.at).toISOString();
  for (const [key, limit] of [['status', 599], ['count', 10000], ['duration_ms', 3600000]]) {
    if (safeInteger(raw[key], limit) != null && (key !== 'count' || raw[key] > 0)) clean[key] = raw[key];
  }
  if (validUuid(raw.request_id)) clean.request_id = validUuid(raw.request_id);
  clean.error_code = safeErrorCode(raw.error_code);
  if (['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].includes(raw.method)) clean.method = raw.method;
  return clean;
}

export function sanitizeQueuedEvent(raw) {
  if (!raw || !validUuid(raw.event_id) || raw.schema_version !== 1 || !CLIENT_EVENT_KINDS.has(raw.kind)
    || typeof raw.occurred_at !== 'string' || raw.occurred_at.length > 40
    || !/(?:Z|[+-]\d\d:\d\d)$/.test(raw.occurred_at) || !Number.isFinite(Date.parse(raw.occurred_at))) return null;
  return { event_id: validUuid(raw.event_id), schema_version: 1, occurred_at: new Date(raw.occurred_at).toISOString(),
    kind: raw.kind, action: CLIENT_EVENT_ACTIONS.has(raw.action) ? raw.action : 'unknown', route: routeTemplate(raw.route),
    status: safeInteger(raw.status, 599), error_code: safeErrorCode(raw.error_code), request_id: validUuid(raw.request_id),
    project_id: validUuid(raw.project_id), job_id: validUuid(raw.job_id), build_sha: safeCode(raw.build_sha),
    device: ['desktop', 'mobile', 'tablet'].includes(raw.device) ? raw.device : 'unknown', blocking: raw.blocking === true,
    ...sanitizeDiagnostics(raw), context: sanitizeContext(raw.context), breadcrumbs: Array.isArray(raw.breadcrumbs)
      ? raw.breadcrumbs.slice(-30).filter((item) => item && typeof item === 'object').map(sanitizeBreadcrumb) : [] };
}
