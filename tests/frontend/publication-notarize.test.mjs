/* 공증 왕복 — 실패해도 다운로드를 막지 않는다는 게 계약이다.
   생성은 이미 끝났고 크레딧도 차감됐다. 도장이 안 찍혔다고 결과물을 인질로 잡지 않는다.

   notarize 를 editorExport.js 에서 직접 import 하지 않는다 — 그 모듈은 html-to-image 와
   DOM 을 끌고 오고 node:test 에는 둘 다 없다. 순수 함수만 별도 모듈로 뽑아 테스트한다. */
import assert from 'node:assert/strict';
import test from 'node:test';

import { notarize } from '../../src/features/editor/publicationNotarize.js';

const blob = { size: 3, type: 'image/png' };
const signedBlob = { size: 9, type: 'image/png' };

const okApi = () => ({
  presignPublication: async () => ({ uploadToken: 't', uploadUrl: 'https://r2/put' }),
  signPublication: async () => ({
    publicationId: 'p1', downloadUrl: 'https://r2/get',
    verifyUrl: 'https://w/verify/p/p1', c2paStatus: 'signed',
  }),
});

test('공증에 성공하면 서명본과 검증 URL 을 돌려준다', async () => {
  const calls = [];
  const fetchImpl = async (url) => {
    calls.push(url);
    return calls.length === 1
      ? { ok: true }
      : { ok: true, blob: async () => signedBlob };
  };
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api: okApi(), fetchImpl });
  assert.equal(out.verifyUrl, 'https://w/verify/p/p1');
  assert.equal(out.blob, signedBlob);
  assert.equal(out.warning, null);
});

test('presign 이 실패해도 원본을 돌려주고 경고만 붙인다', async () => {
  const api = {
    presignPublication: async () => { throw new Error('nope'); },
    signPublication: async () => assert.fail('sign 을 부르면 안 된다'),
  };
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api, fetchImpl: async () => assert.fail('fetch 를 부르면 안 된다') });
  assert.equal(out.blob, blob);
  assert.equal(out.verifyUrl, null);
  assert.ok(out.warning);
});

test('업로드가 실패하면 sign 을 부르지 않고 원본을 돌려준다', async () => {
  let signCalled = false;
  const api = {
    presignPublication: async () => ({ uploadToken: 't', uploadUrl: 'https://r2/put' }),
    signPublication: async () => { signCalled = true; },
  };
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api, fetchImpl: async () => ({ ok: false, status: 500 }) });
  assert.equal(out.blob, blob);
  assert.equal(signCalled, false);
  assert.ok(out.warning);
});

test('서명본 내려받기가 실패해도 검증 URL 은 살린다', async () => {
  const fetchImpl = async (url) =>
    url === 'https://r2/put' ? { ok: true } : { ok: false, status: 404 };
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api: okApi(), fetchImpl });
  assert.equal(out.blob, blob);
  assert.equal(out.verifyUrl, 'https://w/verify/p/p1');
});
