import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../../src/features/auth/Login.jsx', import.meta.url), 'utf8');
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
for (const provider of ['google', 'kakao']) test(`${provider} OAuth 시작은 정착 대기와 초안 플러시 뒤에 실행한다`, async () => {
  const waiting = deferred(); const events = [];
  const bindings = { blocked: false, pending: null, isSignup: false, IS_SELLER: true, isAnalysisFastEntryEnabled: () => true,
    markSignupConsent() {}, clearSignupConsent() {}, readSignupConsent: () => null, oauthAttempt: { current: null },
    setPending: (value) => events.push(['pending', value]),
    waitForFeaturesBeforeLogin: async () => { events.push('wait'); await waiting.promise; events.push('flushed'); },
    signIn: async (value) => { events.push(['signIn', value]); return {}; },
  };
  const text = source.slice(source.indexOf('  const handle = async (provider)'), source.indexOf('  /* 앱 안 브라우저'));
  const handle = new Function(...Object.keys(bindings), `${text}; return handle;`)(...Object.values(bindings));
  const request = handle(provider); await Promise.resolve();
  assert.deepEqual(events, [['pending', provider], 'wait']); waiting.resolve(); await request;
  assert.deepEqual(events, [['pending', provider], 'wait', 'flushed', ['signIn', provider]]);
});
test('로그인 대기 중 창을 닫으면 OAuth를 시작하지 않는다', async () => {
  const waiting = deferred(); let signIns = 0; const attempt = { current: null };
  const bindings = { blocked: false, pending: null, isSignup: false, IS_SELLER: true, isAnalysisFastEntryEnabled: () => true,
    markSignupConsent() {}, clearSignupConsent() {}, readSignupConsent: () => null, oauthAttempt: attempt, setPending() {},
    waitForFeaturesBeforeLogin: () => waiting.promise, signIn: async () => { signIns++; return {}; } };
  const text = source.slice(source.indexOf('  const handle = async (provider)'), source.indexOf('  /* 앱 안 브라우저'));
  const handle = new Function(...Object.keys(bindings), `${text}; return handle;`)(...Object.values(bindings));
  const request = handle('kakao'); attempt.current = null; waiting.resolve(); await request; assert.equal(signIns, 0);
});
