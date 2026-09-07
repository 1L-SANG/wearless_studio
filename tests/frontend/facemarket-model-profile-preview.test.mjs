import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import {
  defaultTestCutSelection,
  profilePhysiqueLine,
  splitTestCutsByKind,
  validityLabel,
} from '../../src/features/model/modelProfilePreview.js';

async function apiHarness() {
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true },
    plugins: [{
      name: 'facemarket-model-profile-api-test-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === 'virtual:model-profile-api-runtime') return '\0model-profile-api-runtime';
        if (id === '@/lib/api/httpAdapter.js') return '\0model-profile-http';
        if (id === '@/lib/supabase.js') return '\0model-profile-supabase';
        return null;
      },
      load(id) {
        if (id === '\0model-profile-api-runtime') return 'export const calls = [];';
        if (id === '\0model-profile-http') return `
          import { calls } from 'virtual:model-profile-api-runtime';
          export const http = async (path, options) => {
            calls.push({ path, options });
            return { path, options };
          };
        `;
        if (id === '\0model-profile-supabase') return `
          export const supabase = {
            auth: { getSession: async () => ({ data: { session: { access_token: 'test-token' } } }) },
          };
        `;
        return null;
      },
    }],
  });
  const runtime = await server.ssrLoadModule('virtual:model-profile-api-runtime');
  const api = await server.ssrLoadModule('/src/lib/api/facemarket.js');
  return { api, runtime, server };
}

async function adminModelsHarness() {
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true },
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'facemarket-admin-models-test-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === '@/lib/api/facemarket.js' || id.endsWith('/src/lib/api/facemarket.js')) {
          return '\0admin-models-api';
        }
        return null;
      },
      load(id) {
        if (id !== '\0admin-models-api') return null;
        return `
          export const adminDeleteModelTestCut = async () => {};
          export const adminFetchModelTestCutUrl = async () => '';
          export const adminListModels = async () => ({ items: [] });
          export const adminModelDetail = async () => ({});
          export const adminModelTestCuts = async () => ({});
          export const adminSendModelTestCuts = async () => ({});
          export const adminSuspendModel = async () => ({});
          export const adminUnsuspendModel = async () => ({});
          export const adminUploadModelTestCuts = async () => ({});
        `;
      },
    }],
  });
  const module = await server.ssrLoadModule('/src/features/admin/AdminModels.jsx');
  return { module, server };
}

test('키·체형 문구는 실제 키를 구간보다 우선하고 있는 정보만 보여 준다', () => {
  assert.equal(profilePhysiqueLine({
    heightCm: 178,
    heightBucket: 'm_175_180',
    bodyType: 'toned',
  }), '키 178cm · 잔잔한 근육');
  assert.equal(profilePhysiqueLine({
    heightCm: null,
    heightBucket: 'm_175_180',
    bodyType: 'bulk',
  }), '키 175–180cm · 벌크업');
  assert.equal(profilePhysiqueLine({
    heightCm: null,
    heightBucket: null,
    bodyType: 'slim',
  }), '마름');
  assert.equal(profilePhysiqueLine({}), '—');
});

test('유효기간은 10년 이상이면 영구, 연 단위면 연수, 나머지는 일수로 표시한다', () => {
  assert.equal(validityLabel(3650), '영구');
  assert.equal(validityLabel(4000), '영구');
  assert.equal(validityLabel(730), '2년');
  assert.equal(validityLabel(540), '540일');
  assert.equal(validityLabel(null), '—');
});

test('테스트컷은 종류별 입력 순서를 보존하고 알 수 없는 종류는 노출하지 않는다', () => {
  const closeupOne = { id: 'closeup-1', kind: 'closeup' };
  const closeupTwo = { id: 'closeup-2', kind: 'closeup' };
  const fullbodyOne = { id: 'fullbody-1', kind: 'fullbody' };

  assert.deepEqual(splitTestCutsByKind([
    fullbodyOne,
    closeupOne,
    { id: 'unknown-1', kind: 'unknown' },
    closeupTwo,
  ]), {
    closeup: [closeupOne, closeupTwo],
    fullbody: [fullbodyOne],
  });
});

test('기본 선택은 각 종류의 첫 컷이며 없는 종류는 선택하지 않는다', () => {
  assert.deepEqual(defaultTestCutSelection([
    { id: 'fullbody-1', kind: 'fullbody' },
    { id: 'closeup-1', kind: 'closeup' },
    { id: 'closeup-2', kind: 'closeup' },
  ]), {
    closeupCutId: 'closeup-1',
    fullbodyCutId: 'fullbody-1',
  });
  assert.deepEqual(defaultTestCutSelection([
    { id: 'closeup-only', kind: 'closeup' },
  ]), {
    closeupCutId: 'closeup-only',
    fullbodyCutId: null,
  });
});

test('어드민 테스트컷 업로드는 이미지와 종류를 multipart로 함께 보낸다', async () => {
  const { api, server } = await apiHarness();
  const originalFetch = globalThis.fetch;
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return new Response(JSON.stringify({ testCuts: [] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  try {
    await api.adminUploadModelTestCuts(
      'model/1',
      [new File(['one'], 'one.jpg'), new File(['two'], 'two.jpg')],
      'fullbody',
    );
    assert.equal(request.url, '/v1/facemarket/admin/models/model%2F1/test-cuts');
    assert.equal(request.options.body.get('kind'), 'fullbody');
    assert.equal(request.options.body.getAll('images').length, 2);
  } finally {
    globalThis.fetch = originalFetch;
    await server.close();
  }
});

test('모델 확정 API는 확대샷과 전신샷 id를 함께 보낸다', async () => {
  const { api, runtime, server } = await apiHarness();
  try {
    await api.confirmMyModelTestCuts({
      closeupCutId: 'closeup-1',
      fullbodyCutId: 'fullbody-1',
    });
    assert.deepEqual(runtime.calls, [{
      path: '/v1/facemarket/model/test-cuts/confirm',
      options: {
        method: 'POST',
        body: { closeupCutId: 'closeup-1', fullbodyCutId: 'fullbody-1' },
      },
    }]);
  } finally {
    await server.close();
  }
});

test('어드민 이미지 추가 input은 보이는 포커스 표시 안에 포함된다', async () => {
  const { module, server } = await adminModelsHarness();
  try {
    assert.equal(typeof module.TestCutUpload, 'function');
    const html = renderToStaticMarkup(createElement(module.TestCutUpload, {
      inputId: 'test-upload',
      disabled: false,
      onChange() {},
    }));
    const labelStart = html.indexOf('<label');
    const inputStart = html.indexOf('<input');
    const labelEnd = html.indexOf('</label>');
    assert.ok(labelStart !== -1 && labelStart < inputStart && inputStart < labelEnd, html);
    assert.match(html, /focus-within:ring-2/);
    assert.match(html, /class="sr-only"/);
  } finally {
    await server.close();
  }
});
