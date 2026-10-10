import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { isRealModelSelection } from '../../src/features/analysis/modelSelection.js';
import { buildFailedCutRetry } from '../../src/features/editor/failedCutRetry.js';
import * as React from 'react';
import { transformWithEsbuild } from 'vite';

// JSX 전체를 흉내 내지 않고 실제 부모 핸들러와 어댑터, 폴링 함수를 실행한다.
// 외부 HTTP만 대체하므로 유료 호출 없이 접수와 크레딧 예약 건수를 검사한다.
const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const panels = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
const adapter = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
const handlerSource = editor.slice(editor.indexOf('  const generateImage = async ('), editor.indexOf('  const failedCutRetry ='));
const retrySource = editor.slice(editor.indexOf('  const retryFailedCut ='), editor.indexOf('  const varyGenerate ='));
const imageHelpers = editor.slice(editor.indexOf('  const placeGeneratedImage ='), editor.indexOf('  const saveBrandUseCategory ='));
const adapterSource = adapter.slice(adapter.indexOf('  async generateImage(projectId'), adapter.lastIndexOf('\n};'));
const pollSource = adapter.slice(adapter.indexOf('async function pollJob('), adapter.indexOf('// 사진 1장 업로드'));
const request = { cutType: 'product', shot: 'ghost', colorId: 'white', modelId: null };
const flush = () => new Promise((resolve) => setImmediate(resolve));

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function harness({ holdPost = false, loseFirstResponse = false, timeout = false, rejectPost = null,
  analysis = {}, brandUseCategorySaving = false } = {}) {
  const posts = [];
  const jobs = new Map();
  const polls = [];
  const gate = deferred();
  const finish = deferred();
  const submitting = [];
  const pendingRequests = [];
  const notifications = [];
  const placements = [];
  let wardrobe = {};
  let now = 0;
  let nextId = 0;
  let slow = false;
  const http = async (path, options) => {
    if (options?.method === 'POST') {
      posts.push(structuredClone(options));
      if (rejectPost) throw rejectPost;
      const key = options.headers?.['Idempotency-Key'];
      const jobId = key && jobs.has(key) ? jobs.get(key) : `job-${jobs.size + 1}`;
      jobs.set(key || randomUUID(), jobId);
      if (loseFirstResponse && posts.length === 1) {
        throw Object.assign(new Error('응답을 잃었어요'), { code: 'api_network' });
      }
      if (holdPost) await gate.promise;
      return { jobId };
    }
    const jobId = path.split('/').at(-1);
    polls.push(jobId);
    if (timeout && !slow) {
      now += 180_001;
      slow = true;
      return { status: 'running', progress: 10 };
    }
    if (!timeout) await finish.promise;
    return { status: 'done', progress: 100, result: { data: { id: jobId, src: `/images/${jobId}.png` }, credits: 81 } };
  };
  const pollJob = new Function('http', 'Date', 'setTimeout', 'jobFailure', `${pollSource}; return pollJob;`)(
    http, { now: () => now }, (fn) => fn(), (job) => Object.assign(new Error(job.errorMessage), { code: 'job_failed' }),
  );
  const generateRemote = new Function('http', 'pollJob', 'EDITOR_IMAGE_JOB_TIMEOUT_MS', 'EDITOR_IMAGE_JOB_TIMEOUT_MESSAGE', 'recordClientAction',
    `return ({${adapterSource}}).generateImage;`)(http, pollJob, 180_000, '아직 만들고 있어요.', () => {});
  const context = {
    api: { generateImage: generateRemote, getJob: (id) => http(`/v1/jobs/${id}`) },
    projectId: 'project-1', analysis, brandUseCategorySaving,
    isRealModelSelection, crypto: { randomUUID },
    generateSubmission: { current: null }, setGenerateSubmitting: (value) => submitting.push(value),
    setPendingGenerateRequest: (value) => pendingRequests.push(value),
    uid: () => `tile-${++nextId}`, genCount: { current: 0 }, setGenDot: () => {},
    setWardrobe: (update) => { wardrobe = update(wardrobe); }, syncCredits: () => {},
    toast: { push: (message) => notifications.push(message) },
    slowJobs: { current: new Set() }, slowJobTimers: { current: new Set() },
    pendingSlot: null, failedCutRetry: null,
    setSlotImage: (blockId, elId, image) => placements.push({ blockId, elId, image }),
    setPendingSlot: (slot) => { context.pendingSlot = slot; }, setTab: () => {},
  };
  const generate = new Function(...Object.keys(context), `${imageHelpers}\n${handlerSource}\nreturn generateImage;`)(...Object.values(context));
  const retryFor = (slot, cutRequest, selectedRetry = null) => {
    context.pendingSlot = slot;
    const sourceBlockId = slot.sourceBlockId || `${slot.blockId}:${slot.elId}`;
    context.failedCutRetry = selectedRetry || buildFailedCutRetry([
      { id: sourceBlockId, source: 'ai', ...cutRequest },
    ], sourceBlockId);
    return new Function(...Object.keys(context), `${imageHelpers}\n${handlerSource}\n${retrySource}\nreturn retryFailedCut;`)(...Object.values(context));
  };
  return { generate, retryFor, placements, generateRemote, posts, jobs, polls, gate, finish, submitting, pendingRequests, notifications, context, wardrobe: () => wardrobe };
}

test('같은 틱에 세 번 누르면 요청과 로딩 타일, 크레딧 예약이 한 건이다', async () => {
  const h = harness({ holdPost: true });
  const pending = [h.generate(request), h.generate(request), h.generate(request)];
  try {
    assert.equal(h.posts.length, 1, '접수 전 연타는 POST 한 번만 보낸다');
    assert.equal(h.jobs.size, 1);
    assert.equal(h.wardrobe().white.length, 1);
    assert.deepEqual(h.submitting, [true]);
  } finally {
    h.gate.resolve(); h.finish.resolve(); await Promise.all(pending);
  }
});

async function previousRequestButton(context, retry) {
  const start = panels.indexOf('  if (pendingGenerateRequest) {');
  const end = panels.indexOf('  if (failedCutRetry) {', start);
  const { code } = await transformWithEsbuild(`function Pending(){${panels.slice(start, end)}}`, 'Pending.jsx', { jsx: 'transform' });
  const args = { React, pendingGenerateRequest: context.generateSubmission.current.request,
    failedCutRetry: context.failedCutRetry, generateSubmitting: false,
    onGenerate: () => assert.fail('현재 칸의 새 요청을 만들지 않는다'), onRetryFailedCut: retry,
    colorOpts: [], detailColorOpts: [], ALL_CUT_TYPE_OPTIONS: [],
    catalogs: { productShotTypes: [], shotTypes: [] }, PanelHead: () => null, Button: () => null };
  const panel = new Function(...Object.keys(args), `${code}; return Pending;`)(...Object.values(args))();
  return panel.props.children.find(element => element?.props?.icon === 'refresh').props.onClick;
}

for (const selected of ['seller_action', 'similar', 'duplicate']) test(`이전 요청 확인은 ${selected} 칸으로 이동해도 원래 요청과 원래 칸만 재생한다`, async () => {
  const h = harness({ loseFirstResponse: true });
  const originalSlot = { blockId: 'original-page', elId: 'original-empty', sourceBlockId: 'old', genFailed: true };
  await h.retryFor(originalSlot, request)();
  h.finish.resolve();
  const source = { id: 'root', source: 'ai', cutType: 'styling' };
  const storyboard = [source, { ...source, id: 'selected',
    ...(selected === 'similar' ? { similarSourceBlockId: 'root' } : {}) }];
  const failure = selected === 'seller_action'
    ? { genFailReason: 'no_garment_photo', genFailRetryable: false }
    : selected === 'duplicate' ? { genFailReason: 'duplicate_cut_source_failed', genFailRetryable: false }
      : { genFailReason: 'similar_cut_source_failed', genFailRetryable: false };
  const editorBlocks = [{ id: 'root-page', elements: [{ type: 'image', sourceBlockId: 'root', src: '/v1/assets/root/file' }] },
    { id: 'original-page', elements: [{ id: 'original-empty', type: 'image', sourceBlockId: 'old', src: null }] },
    { id: 'selected-page', elements: [{ id: 'selected-empty', type: 'image', sourceBlockId: 'selected', src: null }] }];
  const built = buildFailedCutRetry(storyboard, 'selected', null, null, { failure, editorBlocks });
  const selectedSlot = { blockId: 'selected-page', elId: 'selected-empty', genFailed: true };
  h.context.latestBlocks = { current: editorBlocks };
  h.context.blocks = editorBlocks;
  h.context.varyGenerate = async () => assert.fail('이전 접수 확인은 새 변형 요청이 아니다');
  const retry = h.retryFor(selectedSlot, request, built);
  const click = await previousRequestButton(h.context, retry);
  await click();
  assert.equal(h.posts.length, 2);
  assert.equal(h.jobs.size, 1);
  assert.equal(h.posts[1].headers['Idempotency-Key'], h.posts[0].headers['Idempotency-Key']);
  assert.deepEqual(h.posts[1].body, h.posts[0].body);
  assert.equal(h.placements.length, 1);
  assert.equal(h.placements[0].blockId, 'original-page');
  assert.equal(h.placements[0].elId, 'original-empty');
  assert.equal(h.placements[0].image.src, '/images/job-1.png');
  assert.deepEqual(h.context.pendingSlot, selectedSlot);
});

test('접수 응답을 잃은 뒤 재전송하면 같은 UUID와 같은 요청을 보내고 예약은 한 건이다', async () => {
  const h = harness({ loseFirstResponse: true });
  await h.generate(request);
  const pending = h.generate({ ...request, colorId: 'black' });
  h.finish.resolve();
  await pending;
  assert.equal(h.posts.length, 2);
  const keys = h.posts.map((post) => post.headers?.['Idempotency-Key']);
  assert.match(keys[0] || '', /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.equal(keys[1], keys[0]);
  assert.equal('idempotencyKey' in h.posts[0].body, false);
  assert.equal('onJobStarted' in h.posts[0].body, false);
  assert.deepEqual(h.posts[1].body, h.posts[0].body, '설정을 바꿔도 접수 여부를 모르는 기존 요청부터 확인한다');
  assert.equal(h.jobs.size, 1);
  assert.equal(h.wardrobe().white[0].src, '/images/job-1.png');
});

test('접수 응답을 잃으면 이전 요청을 화면에 표시하고 접수 확인 후에만 지운다', async () => {
  const h = harness({ loseFirstResponse: true, holdPost: true });
  await h.generate(request);
  assert.equal(h.pendingRequests.at(-1)?.colorId, 'white');
  assert.equal(h.pendingRequests.at(-1)?.shot, 'ghost');
  const pending = h.generate({ ...request, colorId: 'black', shot: 'detail' });
  try {
    assert.equal(h.pendingRequests.at(-1)?.colorId, 'white', '재확인 중에도 이전 설정을 보여준다');
    h.gate.resolve();
    await flush();
    assert.equal(h.pendingRequests.at(-1), null, '서버 접수 확인이 오면 새 생성 화면을 복원한다');
  } finally { h.gate.resolve(); h.finish.resolve(); await pending; }
});

test('접수 여부가 불명확한 요청은 재전송 전 로그인 확인이 실패해도 UUID를 보존한다', async () => {
  const h = harness({ loseFirstResponse: true });
  await h.generate(request);
  const original = h.context.api.generateImage;
  h.context.api.generateImage = async () => {
    throw Object.assign(new Error('로그인 상태 확인 실패'), { requestNotSent: true });
  };
  await h.generate(request);
  h.context.api.generateImage = original;
  h.finish.resolve();
  await h.generate(request);
  assert.equal(h.posts.length, 2);
  assert.equal(h.posts[1].headers['Idempotency-Key'], h.posts[0].headers['Idempotency-Key']);
  assert.equal(h.jobs.size, 1);
});

test('폴링 시간 초과 뒤에는 새 POST 없이 기존 작업을 추적해 이미지를 채운다', async () => {
  const h = harness({ timeout: true });
  await h.generate(request);
  await flush();
  assert.equal(h.posts.length, 1);
  assert.equal(h.jobs.size, 1);
  assert.deepEqual(h.polls, ['job-1', 'job-1']);
  assert.equal(h.wardrobe().white[0].src, '/images/job-1.png');
  assert.equal(h.context.slowJobs.current.size, 0);
});

test('서버가 접수한 뒤 다시 누르면 첫 이미지 완성 전에도 새 UUID로 새 작업을 만든다', async () => {
  const h = harness();
  const first = h.generate(request);
  await flush();
  const second = h.generate(request);
  await flush();
  try {
    assert.equal(h.posts.length, 2);
    assert.equal(h.jobs.size, 2);
    const keys = h.posts.map((post) => post.headers?.['Idempotency-Key']);
    assert.ok(keys[0]); assert.ok(keys[1]); assert.notEqual(keys[0], keys[1]);
    assert.deepEqual(h.submitting, [true, false, true, false]);
  } finally {
    h.finish.resolve(); await Promise.all([first, second]);
  }
});

test('확실한 접수 거절은 잠금을 풀고 다음 생성에 새 UUID를 쓴다', async () => {
  const h = harness({ rejectPost: Object.assign(new Error('크레딧 부족'), { status: 402 }) });
  await h.generate(request);
  await h.generate(request);
  const keys = h.posts.map((post) => post.headers?.['Idempotency-Key']);
  assert.ok(keys[0]); assert.notEqual(keys[0], keys[1]);
  assert.deepEqual(h.wardrobe().white, []);
  assert.equal(h.context.generateSubmission.current, null);
  assert.deepEqual(h.submitting, [true, false, true, false]);
});

test('이전 작업의 완료는 다음 작업의 접수 잠금을 풀지 않는다', async () => {
  const h = harness({ holdPost: true });
  const first = h.generate(request);
  const submitFirst = h.gate;
  submitFirst.resolve();
  await flush();
  // 다음 POST를 대기시키는 경계는 별도의 실제 어댑터 호출로 유지한다.
  const nextPost = deferred();
  const original = h.context.api.generateImage;
  h.context.api.generateImage = async (...args) => { await nextPost.promise; return original(...args); };
  const second = h.generate(request);
  h.finish.resolve();
  await first;
  const third = h.generate(request);
  try {
    assert.equal(h.submitting.at(-1), true);
    assert.equal(await third, null);
  } finally {
    nextPost.resolve(); await second;
  }
});

test('새 생성은 저장한 브랜드 유형과 실제 모델을 원래 요청에 보존한다', async () => {
  const h = harness({ analysis: { brandUseCategory: '일반 의류' } });
  h.finish.resolve();
  await h.generate({ ...request, cutType: 'horizon', modelId: 'real-model-1', brandUseCategory: '액티브웨어' });
  assert.equal(h.posts[0].body.brandUseCategory, '일반 의류');
  assert.equal(h.posts[0].body.modelId, 'real-model-1');
});

test('브랜드 유형이 준비되지 않은 실제 모델은 잠금과 로딩 타일을 만들기 전에 거절한다', async () => {
  for (const options of [{}, { analysis: { brandUseCategory: '일반 의류' }, brandUseCategorySaving: true }]) {
    const h = harness(options);
    assert.equal(await h.generate({ ...request, cutType: 'styling', modelId: 'real-model-1' }), null);
    assert.equal(h.posts.length, 0);
    assert.deepEqual(h.wardrobe(), {});
    assert.deepEqual(h.submitting, []);
  }
});

test('제품컷은 남아 있는 가상 모델 선택을 요청에서 지우며 브랜드 유형을 요구하지 않는다', async () => {
  const h = harness();
  h.finish.resolve();
  await h.generate({ ...request, modelId: 'mA' });
  assert.equal(h.posts[0].body.cutType, 'product');
  assert.equal(h.posts[0].body.modelId, null);
});

test('이전 새 생성의 접수 확인 결과로 다른 실패 슬롯을 채우지 않는다', async () => {
  const h = harness({ loseFirstResponse: true });
  await h.generate(request);
  const slot = { blockId: 'block-black', elId: 'slot-black', genFailed: true };
  const cutRequest = { ...request, cutType: 'styling', colorId: 'black' };
  h.finish.resolve();
  await h.retryFor(slot, cutRequest)();
  assert.equal(h.posts.length, 2);
  assert.deepEqual(h.posts[1].body, h.posts[0].body);
  assert.equal(h.jobs.size, 1);
  assert.deepEqual(h.placements, [], '흰색 제품컷 결과를 검은색 스타일링 슬롯에 넣지 않는다');
  assert.deepEqual(h.context.pendingSlot, slot);

  await h.retryFor(slot, cutRequest)();
  assert.equal(h.jobs.size, 2);
  assert.equal(h.posts[2].body.colorId, 'black');
  assert.equal(h.placements.length, 1);
  assert.equal(h.placements[0].image.src, '/images/job-2.png');
  assert.equal(h.placements[0].image.wardrobeGroup, 'black');
});

test('같은 실패 슬롯의 접수 응답을 다시 확인하면 같은 UUID로 원래 슬롯을 채운다', async () => {
  const h = harness({ loseFirstResponse: true });
  const slot = { blockId: 'block-white', elId: 'slot-white', genFailed: true };
  await h.retryFor(slot, request)();
  h.finish.resolve();
  await h.retryFor({ ...slot }, request)();
  assert.equal(h.posts[1].headers['Idempotency-Key'], h.posts[0].headers['Idempotency-Key']);
  assert.equal(h.jobs.size, 1);
  assert.equal(h.placements.length, 1);
  assert.equal(h.placements[0].blockId, 'block-white');
  assert.equal(h.placements[0].image.src, '/images/job-1.png');
});
