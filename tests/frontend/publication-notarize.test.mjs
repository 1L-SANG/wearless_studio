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

/* ── 리뷰 라운드 1 — C1(무한 대기) · C2(throw 시 verifyUrl 유실) ─────────────
   deps.timeouts 는 프로덕션 기본값(8s/60s)을 테스트에서만 짧게 갈아 끼우기 위한
   주입 지점이다 — 진짜로 60초를 기다리면서 "멈춤"을 증명할 수는 없다. */

test('네트워크 leg 이 응답 없이 멈춰도(하프오픈 등) notarize 는 타임아웃 안에 원본과 경고를 돌려준다 — C1', { timeout: 3000 }, async () => {
  const api = {
    presignPublication: async () => ({ uploadToken: 't', uploadUrl: 'https://r2/put' }),
    signPublication: async () => assert.fail('타임아웃으로 끊겼어야 할 단계에서 sign 을 부르면 안 된다'),
  };
  // PUT 이 영원히 응답하지 않는 상황을 흉내낸다 — resolve/reject 를 절대 안 부르는 프라미스.
  const fetchImpl = () => new Promise(() => {});
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api, fetchImpl, timeouts: { network: 20, transfer: 20 } });
  assert.equal(out.blob, blob);
  assert.equal(out.verifyUrl, null);
  assert.ok(out.warning);
});

test('서명 이후 재다운로드가 throw 해도(네트워크 끊김 등) 검증 URL 은 살아남는다 — C2', async () => {
  const fetchImpl = async (url) => {
    if (url === 'https://r2/put') return { ok: true };
    throw new TypeError('network drop');
  };
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api: okApi(), fetchImpl });
  assert.equal(out.blob, blob);
  assert.equal(out.verifyUrl, 'https://w/verify/p/p1');
});

test('서명본 blob() 파싱이 throw 해도 검증 URL 은 살아남는다 — C2', async () => {
  const fetchImpl = async (url) => (url === 'https://r2/put'
    ? { ok: true }
    : { ok: true, blob: async () => { throw new Error('malformed body'); } });
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api: okApi(), fetchImpl });
  assert.equal(out.blob, blob);
  assert.equal(out.verifyUrl, 'https://w/verify/p/p1');
});

/* ── 최종 리뷰 C2 — presign 의 404/503 은 "공증이 안 됐다"가 아니라 "공증이 이 요청엔
   아예 없다"는 뜻이다: FM_PROVENANCE_ENABLED=false(배포 직후 런북이 지시하는 초기값),
   FM_PROVENANCE_TOKEN_SECRET 미설정(503), 이 브랜치 이전에 만들어진 REAL 프로젝트(원장
   행 없음 → 영구 404). 이 셋을 WARNING 으로 띄우면 모든 REAL 다운로드마다 토스트가 뜬다
   — 조용히, 브랜치 이전과 동일하게 저장해야 한다. 반면 진짜로 시도했다가 죽은 500 은
   여전히 경고해야 셀러가 진짜 장애를 알 수 있다. ────────────────────────────── */

test('presign 이 404 로 실패하면(라우트 미등록·레거시 프로젝트) 조용히 원본만 저장한다 — C2', async () => {
  const api = {
    presignPublication: async () => { const e = new Error('not found'); e.status = 404; throw e; },
    signPublication: async () => assert.fail('sign 을 부르면 안 된다'),
  };
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api, fetchImpl: async () => assert.fail('fetch 를 부르면 안 된다') });
  assert.equal(out.blob, blob);
  assert.equal(out.verifyUrl, null);
  assert.equal(out.warning, null);
});

test('presign 이 503 으로 실패하면(TOKEN_SECRET 미설정) 조용히 원본만 저장한다 — C2', async () => {
  const api = {
    presignPublication: async () => { const e = new Error('provenance_unconfigured'); e.status = 503; throw e; },
    signPublication: async () => assert.fail('sign 을 부르면 안 된다'),
  };
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api, fetchImpl: async () => assert.fail('fetch 를 부르면 안 된다') });
  assert.equal(out.blob, blob);
  assert.equal(out.verifyUrl, null);
  assert.equal(out.warning, null);
});

test('presign 이 500 으로 실패하면 여전히 경고를 띄운다(진짜 장애) — C2', async () => {
  const api = {
    presignPublication: async () => { const e = new Error('internal error'); e.status = 500; throw e; },
    signPublication: async () => assert.fail('sign 을 부르면 안 된다'),
  };
  const out = await notarize(blob, { projectId: 'p', kind: 'long_png' },
    { api, fetchImpl: async () => assert.fail('fetch 를 부르면 안 된다') });
  assert.equal(out.blob, blob);
  assert.equal(out.verifyUrl, null);
  assert.ok(out.warning);
});
