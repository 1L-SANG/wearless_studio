import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createClientEventCollector, CLIENT_EVENT_QUEUE_KEY, CLIENT_EVENT_BODY_LIMIT } from '../../src/lib/clientEvents.js';
import { sanitizeQueuedEvent } from '../../src/lib/clientEventPrivacy.js';

const ID = 'cdcc757c-c732-4102-9114-bcfb8511163c';
const userAgent = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 Version/18.2 Mobile/15E148 Safari/604.1';
function fixture() {
  const storage = new Map();
  let time = Date.parse('2026-10-08T12:00:00Z');
  const env = { navigator: { onLine: false, userAgent, connection: { effectiveType: '4g', rtt: 25.5, downlink: 12.5, saveData: false } },
    document: { visibilityState: 'visible' }, location: { pathname: `/editor/${ID}?input=private#text` },
    innerWidth: 390, innerHeight: 844,
    localStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) } };
  const collector = createClientEventCollector({ env, now: () => time });
  return { env, collector, storage, advance: (ms) => { time += ms; } };
}
const error = () => Object.assign(new TypeError('입력한 상품 문구 private@example.com personal-photo.jpeg https://api.test/private.js?secret=input#hash'), {
  requestId: ID, code: 'backend_failure', status: 500,
  stack: 'TypeError: private typed text\n at private@example.com (https://user:password@app.test/assets/private-file.js?token=private#typed:10:20)\nload@https://app.test/assets/typed-file.js?private=yes#hash:12:40',
});
const report = (f, more = {}) => f.collector.report({ kind: 'ui_error', action: 'load', messageCode: 'screen_load_failed',
  blocking: true, error: error(), context: { step: 'generating', projectId: ID, jobId: ID }, ...more });

test('diagnostic fields use the server shape with separate message and server codes', () => {
  const f = fixture(); f.advance(1234);
  const payload = report(f);
  assert.equal(payload.step, 'generating');
  assert.equal(payload.message_code, 'screen_load_failed');
  assert.equal(payload.error_code, 'backend_failure');
  assert.deepEqual(payload.browser, { family: 'Mobile Safari', major: '18' });
  assert.deepEqual(payload.os, { family: 'iOS', major: '18' });
  assert.deepEqual(payload.viewport, { width: 390, height: 844 });
  assert.deepEqual(payload.network, { effective_type: '4g', rtt_ms: 25.5, downlink_mbps: 12.5, save_data: false, online: false });
  assert.equal(payload.page_age_ms, 1234);
  assert.equal(payload.visibility, 'visible');
  assert.equal(payload.tech_message, 'TypeError');
  assert.match(payload.stack, /at \/assets\/:segment:10:20/);
  assert.match(payload.stack, /at \/assets\/:segment:12:40/);
  for (const secret of ['private', 'typed', '@', '.js', '.jpeg', '?', '#', 'password', '입력한', '상품', 'Mozilla']) {
    assert.equal(JSON.stringify(payload).includes(secret), false, secret);
  }
});

for (const [ua, browser, os] of [
  ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/132.0.1.1 Safari/537.36 Edg/132.0.1.1', ['Edge', '132'], ['Windows', '10']],
  ['Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) Gecko/20100101 Firefox/133.0', ['Firefox', '133'], ['macOS', '14']],
  ['Mozilla/5.0 (Linux; Android 14) Chrome/132.0.1.1 Mobile Safari/537.36', ['Chrome', '132'], ['Android', '14']],
  ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_2) AppleWebKit/605 CriOS/132.0 Mobile Safari/604', ['Chrome', '132'], ['iOS', '18']],
  ['Mozilla/5.0 (Linux; Android 14) Chrome/132.0 SamsungBrowser/26.0', ['Samsung Internet', '26'], ['Android', '14']],
  ['private@example.com typed text', ['unknown', 'unknown'], ['unknown', 'unknown']],
]) {
  test(`platform detection sends only family and major: ${browser[0]}, ${os[0]}`, () => {
    const f = fixture(); f.env.navigator.userAgent = ua;
    const payload = report(f);
    assert.deepEqual(payload.browser, { family: browser[0], major: browser[1] });
    assert.deepEqual(payload.os, { family: os[0], major: os[1] });
  });
}

for (const kind of ['ui_error', 'error_boundary', 'runtime_error', 'unhandled_rejection', 'api_error', 'network_error',
  'toast_error', 'save_error', 'upload_error', 'generation_error', 'analysis_error']) {
  test(`stack is sent only for the four allowed kinds: ${kind}`, () => {
    const f = fixture(); const payload = report(f, { kind });
    assert.equal(Object.hasOwn(payload, 'stack'), ['ui_error', 'error_boundary', 'runtime_error', 'unhandled_rejection'].includes(kind));
    assert.equal(payload.tech_message, 'TypeError');
  });
}

test('missing network API, hidden visibility and page age keep a small valid payload', () => {
  const f = fixture(); delete f.env.navigator.connection;
  f.env.document.visibilityState = 'hidden'; f.advance(604800001);
  const payload = report(f);
  assert.deepEqual(payload.network, { effective_type: 'unknown', online: false });
  assert.equal(payload.visibility, 'hidden');
  assert.equal(payload.page_age_ms, 604800000);
});

test('queued diagnostics retain allowed fields and remove invalid numbers, labels and private text', () => {
  const f = fixture();
  const payload = report(f);
  const clean = sanitizeQueuedEvent({ ...payload, step: 'typed text', message_code: 'x'.repeat(33), error_code: 'private.jpg',
    browser: { family: 'private@example.com', major: 'longversion', user_agent: 'private' },
    os: { family: 'iOS', major: '18', private: 'typed text' }, viewport: { width: 16385, height: true },
    network: { effective_type: 'wifi', rtt_ms: Infinity, downlink_mbps: '12', save_data: 1, online: 'true' },
    page_age_ms: -1, visibility: 'private', tech_message: 'typed private@example.com filename.jpeg' });
  assert.equal(clean.step, 'other'); assert.equal(clean.message_code, 'other'); assert.equal(clean.error_code, 'other');
  assert.deepEqual(clean.browser, {}); assert.deepEqual(clean.os, { family: 'iOS', major: '18' });
  assert.deepEqual(clean.viewport, {}); assert.deepEqual(clean.network, { effective_type: 'unknown' });
  assert.equal(Object.hasOwn(clean, 'page_age_ms'), false); assert.equal(Object.hasOwn(clean, 'visibility'), false);
  assert.equal(clean.tech_message, '[redacted]');
  assert.deepEqual(sanitizeQueuedEvent(payload), payload);
  const queued = JSON.parse(f.storage.get(CLIENT_EVENT_QUEUE_KEY)).items[0].payload;
  assert.deepEqual(queued, payload);
});

test('network bounds are inclusive and exclude booleans, strings, nonfinite numbers and out of range values', () => {
  const base = report(fixture());
  for (const [key, max] of [['rtt_ms', 60000], ['downlink_mbps', 10000]]) {
    for (const value of [0, 0.5, max]) assert.equal(sanitizeQueuedEvent({ ...base, network: { [key]: value } }).network[key], value);
    for (const value of [-1, max + 1, true, '12', NaN, Infinity, -Infinity]) {
      assert.equal(Object.hasOwn(sanitizeQueuedEvent({ ...base, network: { [key]: value } }).network, key), false);
    }
  }
  for (const size of [0, 16384]) assert.deepEqual(sanitizeQueuedEvent({ ...base, viewport: { width: size, height: size } }).viewport, { width: size, height: size });
});

test('step follows the current screen and generation context without sending IDs or typed path segments', () => {
  for (const [path, step] of [['/create/input', 'input'], ['/create/analysis', 'input'], ['/create/storyboard', 'storyboard'],
    ['/create/mannequin', 'mannequin'], [`/editor/${ID}`, 'editor'], ['/library', 'library'], ['/unknown/private-name', 'other']]) {
    const f = fixture(); f.env.location.pathname = path;
    assert.equal(report(f, { context: {} }).step, step);
  }
  assert.equal(report(fixture(), { context: {}, kind: 'generation_error', action: 'detailPageGenerate' }).step, 'generating');
});

test('maximum diagnostic payload round trips through the actual server sanitizer within 32KiB', (t) => {
  const python = fileURLToPath(new URL('../../server/.venv/bin/python', import.meta.url));
  if (!existsSync(python)) { t.skip('server/.venv is required for the server contract round trip'); return; }
  const f = fixture();
  for (let i = 0; i < 30; i++) f.collector.addBreadcrumb({ type: 'api', method: 'GET', route: `/v1/projects/${ID}/editor`,
    status: 599, request_id: ID, duration_ms: 3600000, count: 10000, error_code: 'a'.repeat(32) });
  const problem = error();
  problem.stack = Array.from({ length: 50 }, () => `at typed (https://app.test/${'editor/'.repeat(13)}private.js?token=secret#input:123456:789012)`).join('\n');
  const payload = report(f, { error: problem });
  assert.ok(payload.tech_message.length <= 500); assert.ok(payload.stack.length <= 2000);
  assert.ok(Buffer.byteLength(JSON.stringify(payload)) <= CLIENT_EVENT_BODY_LIMIT);
  const code = 'import json,sys\nfrom app.client_events import sanitize_event\nprint(json.dumps(sanitize_event(json.load(sys.stdin))))';
  const clean = JSON.parse(execFileSync(python, ['-c', code], { input: JSON.stringify(payload), encoding: 'utf8',
    cwd: fileURLToPath(new URL('../../server', import.meta.url)) }));
  clean.occurred_at = new Date(clean.occurred_at).toISOString();
  for (const crumb of clean.breadcrumbs) crumb.at = new Date(crumb.at).toISOString();
  assert.deepEqual(clean, payload);
});
