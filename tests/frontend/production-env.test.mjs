import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { loadConfigFromFile } from 'vite';
import { assertProductionEnv } from '../../scripts/ci/assert-production-env.mjs';

const production = {
  VERCEL_ENV: 'production',
  VITE_API_MODE: 'http',
  VITE_API_BASE_URL: 'https://api.wearless.kr',
};

test('운영 환경의 http 모드와 HTTPS API 주소를 허용하고 입력을 바꾸지 않는다', () => {
  const env = Object.freeze({ ...production });
  assert.doesNotThrow(() => assertProductionEnv(env));
  assert.deepEqual(env, production);
});

for (const value of ['mock', '[SENSITIVE]', '', undefined, 'HTTP', 'http ']) {
  test(`운영 API 모드가 정확히 http가 아니면 거부한다: ${String(value)}`, () => {
    assert.throws(() => assertProductionEnv({ ...production, VITE_API_MODE: value }), /VITE_API_MODE.*http/);
  });
}

for (const value of ['[SENSITIVE]', '', undefined, 'http://api.wearless.kr', 'https://', 'https://bad host']) {
  test(`운영 API 주소가 올바른 HTTPS URL이 아니면 거부한다: ${String(value)}`, () => {
    assert.throws(() => assertProductionEnv({ ...production, VITE_API_BASE_URL: value }), /VITE_API_BASE_URL.*https:\/\//);
  });
}

test('API 외의 VITE_ 변수에 남은 Sensitive 자리표시도 거부한다', () => {
  assert.throws(() => assertProductionEnv({
    ...production, VITE_GENEXAMPLE_BG_ENABLED: '[SENSITIVE]',
  }), /VITE_GENEXAMPLE_BG_ENABLED.*\[SENSITIVE\].*Sensitive/);
});

test('문제가 있는 변수들을 모두 알리되 실제 값은 출력하지 않는다', () => {
  assert.throws(() => assertProductionEnv({
    ...production, VITE_API_MODE: 'bad-mode-private-value', VITE_API_BASE_URL: 'bad-url-private-value',
    VITE_OTHER: '[SENSITIVE]',
  }), (error) => {
    assert.match(error.message, /운영 빌드/);
    for (const name of ['VITE_API_MODE', 'VITE_API_BASE_URL', 'VITE_OTHER']) assert.ok(error.message.includes(name));
    assert.doesNotMatch(error.message, /bad-mode-private-value|bad-url-private-value/);
    return true;
  });
});

test('VITE_ 접두사가 없는 Sensitive 자리표시는 프론트 검사 대상이 아니다', () => {
  assert.doesNotThrow(() => assertProductionEnv({ ...production, SERVER_SECRET: '[SENSITIVE]' }));
});

for (const value of [undefined, 'preview']) {
  test(`Vercel 운영 환경이 아니면 검사하지 않는다: ${String(value)}`, () => {
    assert.doesNotThrow(() => assertProductionEnv({
      VERCEL_ENV: value, VITE_API_MODE: '[SENSITIVE]', VITE_API_BASE_URL: '', VITE_OTHER: '[SENSITIVE]',
    }));
  });
}

async function loadConfig(t, env, command = 'build') {
  const keys = ['VERCEL_ENV', 'VITE_API_MODE', 'VITE_API_BASE_URL', 'VITE_GENEXAMPLE_BG_ENABLED'];
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  const setEnv = (values) => {
    for (const key of keys) {
      if (values[key] === undefined) delete process.env[key];
      else process.env[key] = values[key];
    }
  };
  setEnv(env);
  t.after(() => setEnv(previous));
  const root = fileURLToPath(new URL('../..', import.meta.url));
  return loadConfigFromFile({ command, mode: 'production' }, `${root}/vite.config.js`, root, 'silent', undefined, 'native');
}

test('실제 Vite 설정도 운영 빌드의 Sensitive 자리표시를 차단한다', async (t) => {
  await assert.rejects(() => loadConfig(t, {
    ...production, VITE_API_MODE: '[SENSITIVE]', VITE_API_BASE_URL: '[SENSITIVE]',
  }), /VITE_API_MODE.*http/);
});

test('실제 Vite 설정은 올바른 운영 빌드를 허용한다', async (t) => {
  assert.ok((await loadConfig(t, production)).config.build.rollupOptions.input.seller);
});

for (const value of [undefined, 'preview']) {
  test(`실제 Vite 설정은 운영이 아닌 빌드를 허용한다: ${String(value)}`, async (t) => {
    assert.ok((await loadConfig(t, { VERCEL_ENV: value, VITE_API_MODE: '[SENSITIVE]' })).config);
  });
}

test('실제 Vite 설정은 serve 명령에서 운영 검사를 하지 않는다', async (t) => {
  assert.ok((await loadConfig(t, { VERCEL_ENV: 'production', VITE_API_MODE: 'mock' }, 'serve')).config);
});
