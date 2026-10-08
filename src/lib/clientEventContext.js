import { sanitizeDiagnostics } from './clientEventPrivacy.js';

const major = (version) => typeof version === 'string' ? version.match(/^(\d{1,8})(?:[._]|$)/)?.[1] || 'unknown' : 'unknown';

function browserPlatform(ua) {
  for (const [pattern, family] of [[/Edg(?:A|iOS|e)?\/([\d.]+)/i, 'Edge'], [/OPR\/([\d.]+)/i, 'Opera'],
    [/SamsungBrowser\/([\d.]+)/i, 'Samsung Internet'], [/(?:Firefox|FxiOS)\/([\d.]+)/i, 'Firefox'],
    [/(?:Chrome|CriOS)\/([\d.]+)/i, 'Chrome'], [/Version\/([\d.]+).*Safari/i, /iPhone|iPad|iPod|Mobile/i.test(ua) ? 'Mobile Safari' : 'Safari']]) {
    const match = ua.match(pattern);
    if (match) return { family, major: major(match[1]) };
  }
  return { family: 'unknown', major: 'unknown' };
}

function osPlatform(ua, touches) {
  if (/iPhone|iPad|iPod/i.test(ua) || (/Macintosh/i.test(ua) && touches > 1)) {
    return { family: 'iOS', major: major(ua.match(/(?:CPU (?:iPhone )?OS |Version\/)([\d._]+)/i)?.[1]) };
  }
  for (const [pattern, family] of [[/Android[ /]([\d.]+)/i, 'Android'], [/Windows NT ([\d.]+)/i, 'Windows'],
    [/Mac OS X ([\d._]+)/i, 'macOS'], [/CrOS \S+ ([\d.]+)/i, 'Chrome OS']]) {
    const match = ua.match(pattern);
    if (match) return { family, major: major(match[1]) };
  }
  return { family: /Linux/i.test(ua) ? 'Linux' : 'unknown', major: 'unknown' };
}

export function routeStep(path) {
  const segments = typeof path === 'string' ? path.split(/[?#]/)[0].split('/') : [];
  for (const step of ['editor', 'storyboard', 'mannequin', 'library']) if (segments.includes(step)) return step;
  return segments.some((part) => ['input', 'product-input', 'analysis', 'analyze'].includes(part)) ? 'input' : 'other';
}

export function collectDiagnostics({ env, kind, error, step, messageCode, pageAge }) {
  const nav = env.navigator;
  const ua = typeof nav?.userAgent === 'string' ? nav.userAgent : '';
  const connection = nav?.connection || nav?.mozConnection || nav?.webkitConnection;
  // 원래 User-Agent와 자유 문장은 정해진 가족 이름과 기술 분류로만 바꾼다.
  const name = ['TypeError', 'ReferenceError', 'SyntaxError', 'RangeError'].includes(error?.name) ? `${error.name}: ` : '';
  return sanitizeDiagnostics({ kind, step, message_code: messageCode,
    browser: browserPlatform(ua), os: osPlatform(ua, nav?.maxTouchPoints),
    viewport: { width: env.innerWidth, height: env.innerHeight },
    network: { effective_type: connection?.effectiveType, rtt_ms: connection?.rtt,
      downlink_mbps: connection?.downlink, save_data: connection?.saveData, online: nav?.onLine },
    page_age_ms: pageAge, visibility: env.document?.visibilityState,
    tech_message: name + (typeof error?.message === 'string' ? error.message : ''),
    stack: typeof error?.stack === 'string' ? error.stack : '',
  });
}
