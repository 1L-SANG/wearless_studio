// k6 읽기 경로 부하 — "잡이 돌아가는 동안 다른 사람 화면(폴링·카탈로그)이 멈추나" 를 잰다.
//
// 돈이 드는 경로(컷 생성·분석)는 절대 치지 않는다. GET 만, 데이터는 안 바뀐다.
//
//   k6 run tools/loadtest/read-paths.js                       # 무인증 경로만
//   TOKEN=<Supabase access_token> k6 run tools/loadtest/read-paths.js   # 셀러 폴링 경로까지
//
// 실시간 그래프 + HTML 리포트:
//   K6_WEB_DASHBOARD=true K6_WEB_DASHBOARD_EXPORT=loadtest-report.html k6 run …
//   (실행 중 http://127.0.0.1:5665)
//
// 조절: BASE, DURATION(기본 3m), RPS_PUBLIC(20), RPS_AUTH(10), RPS_REJECT(5)
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const BASE = __ENV.BASE || 'https://api.wearless.kr';
const TOKEN = __ENV.TOKEN || '';
const DURATION = __ENV.DURATION || '3m';
const RPS_PUBLIC = Number(__ENV.RPS_PUBLIC || 20);
const RPS_AUTH = Number(__ENV.RPS_AUTH || 10);
const RPS_REJECT = Number(__ENV.RPS_REJECT || 5);

// 경로별 지연을 따로 본다 — healthz(DB 안 봄) 와 readyz(select 1) 의 차이가 곧 DB 풀 대기다.
const t = {
  healthz: new Trend('t_healthz', true),
  readyz: new Trend('t_readyz', true),
  pricing: new Trend('t_pricing_plans', true),
  reject: new Trend('t_auth_reject', true),
  account: new Trend('t_me_account', true),
  projects: new Trend('t_projects', true),
  project: new Trend('t_project', true),
  mannequins: new Trend('t_mannequins', true),
};
const unexpected = new Rate('unexpected_status');

const scenarios = {
  public_reads: {
    executor: 'constant-arrival-rate',
    exec: 'publicReads',
    rate: RPS_PUBLIC, timeUnit: '1s', duration: DURATION,
    preAllocatedVUs: 20, maxVUs: 200,
  },
  auth_reject: {
    executor: 'constant-arrival-rate',
    exec: 'authReject',
    rate: RPS_REJECT, timeUnit: '1s', duration: DURATION,
    preAllocatedVUs: 5, maxVUs: 50,
  },
};
if (TOKEN) {
  scenarios.seller_polling = {
    executor: 'constant-arrival-rate',
    exec: 'sellerPolling',
    rate: RPS_AUTH, timeUnit: '1s', duration: DURATION,
    preAllocatedVUs: 10, maxVUs: 100,
  };
}

export const options = {
  scenarios,
  thresholds: {
    // 8/26 장애 기준: 이벤트루프가 얼면 healthz 부터 수 초로 튄다. p95 800ms 면 한국 RTT 포함 정상.
    t_healthz: ['p(95)<800'],
    t_readyz: ['p(95)<1000'],
    unexpected_status: ['rate<0.01'],
    http_req_failed: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const authHeaders = TOKEN ? { headers: { Authorization: `Bearer ${TOKEN}` } } : {};

function hit(url, trend, okStatus, params = {}) {
  const res = http.get(url, params);
  trend.add(res.timings.duration);
  const ok = res.status === okStatus;
  unexpected.add(!ok);
  check(res, { [`${url.replace(BASE, '')} → ${okStatus}`]: () => ok });
  return res;
}

export function setup() {
  if (!TOKEN) return { projectId: null };
  const res = http.get(`${BASE}/v1/projects`, authHeaders);
  if (res.status !== 200) throw new Error(`TOKEN 으로 /v1/projects 가 ${res.status} — 만료됐거나 잘못된 토큰`);
  const body = res.json();
  const list = Array.isArray(body) ? body : (body.items || body.projects || body.data || []);
  const projectId = list.length ? (list[0].id || list[0].projectId) : null;
  return { projectId };
}

export function publicReads() {
  hit(`${BASE}/healthz`, t.healthz, 200);
  hit(`${BASE}/readyz`, t.readyz, 200);
  hit(`${BASE}/v1/pricing-plans`, t.pricing, 200);
}

export function authReject() {
  // 토큰 없는 요청이 JWT 검증 경로를 타고 401 로 빠지는 비용. 4xx 는 실패가 아니다.
  hit(`${BASE}/v1/me/ping`, t.reject, 401, { responseCallback: http.expectedStatuses(401) });
}

export function sellerPolling(data) {
  hit(`${BASE}/v1/me/account`, t.account, 200, authHeaders);
  hit(`${BASE}/v1/projects`, t.projects, 200, authHeaders);
  if (data.projectId) {
    hit(`${BASE}/v1/projects/${data.projectId}`, t.project, 200, authHeaders);
    hit(`${BASE}/v1/projects/${data.projectId}/mannequins`, t.mannequins, 200, authHeaders);
  }
  sleep(0.2);
}
