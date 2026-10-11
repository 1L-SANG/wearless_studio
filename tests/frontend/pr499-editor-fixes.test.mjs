import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { buildCanvasAiMenu, buildCanvasAiRequest } from '../../src/features/editor/canvasAiActions.js';
import { createCanvasAiJobController } from '../../src/features/editor/canvasAiJobs.js';
import { hasImageSource } from '../../src/features/editor/editorImageSource.js';

const adapter = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
test('empty, pending and failed canvas slots never supply a photo variation source', () => {
  for (const element of [
    { id: 'empty', type: 'image', src: '' },
    { id: 'waiting', type: 'image', genPending: 'live' },
    { id: 'failed', type: 'image', src: null, genPending: 'failed' },
    { id: 'failed-blank', type: 'image', genFailed: true },
  ]) {
    const menu = buildCanvasAiMenu({ block: { id: 'page', elements: [element] }, element });
    assert.equal(menu.source, null, element.id);
    assert.equal(buildCanvasAiRequest({ action: 'similar', source: menu.source }), null);
    assert.equal(buildCanvasAiRequest({ action: 'reshoot', source: menu.source }), null);
  }
  assert.equal(hasImageSource({ id: 'waiting', loading: true }), false);
  assert.equal(hasImageSource({ id: 'photo', type: 'image', src: '/photo.png' }), true);
});

test('a canvas slot without a photo exposes filling actions instead of reshooting controls', () => {
  const element = { id: 'empty', type: 'image', src: '' };
  const menu = buildCanvasAiMenu({ block: { id: 'page', elements: [element] }, element });
  assert.deepEqual(menu.branches.map((branch) => branch.id), ['upload', 'fill']);
  const pending = buildCanvasAiMenu({ block: { id: 'page', elements: [{ ...element, genPending: 'live' }] },
    element: { ...element, genPending: 'live' } });
  assert.deepEqual(pending.branches, []);
});

const request = { source: { id: 'photo', src: '/photo.png', cutType: 'styling', wardrobeGroup: 'navy' }, variationMode: 'similar' };
function submissionHarness({ error = null, errors = [], hold = false, accept = true } = {}) {
  const posts = [], jobs = new Map();
  let wardrobe = {}, resolve;
  const gate = new Promise(done => { resolve = done; });
  const http = async (_path, options) => {
    posts.push(structuredClone(options));
    const key = options.headers?.['Idempotency-Key'];
    if (accept) jobs.set(key || randomUUID(), 'job');
    if (hold) await gate;
    const failure = errors[posts.length - 1] || error;
    if (failure) throw failure;
    return { jobId: 'job' };
  };
  const generateRemote = new Function('http', 'pollJob', 'EDITOR_IMAGE_JOB_TIMEOUT_MS', 'EDITOR_IMAGE_JOB_TIMEOUT_MESSAGE', 'recordClientAction',
    `return ({${adapter.slice(adapter.indexOf('  async generateImage(projectId'), adapter.lastIndexOf('\n};'))}}).generateImage;`)(
    http, async () => ({ data: { id: 'result', src: '/result.png' }, credits: 90 }), 180000, 'wait', () => {});
  const schedules = new Map(), completions = new Map();
  let scheduleId = 0;
  const context = { api: { generateImage: generateRemote } };
  const controller = createCanvasAiJobController({ createKey: randomUUID,
    schedule: (callback) => { const id = ++scheduleId; schedules.set(id, callback); return id; },
    cancelSchedule: (id) => schedules.delete(id),
    onStarted: (job) => {
      const group = job.request.source.wardrobeGroup;
      wardrobe = { ...wardrobe, [group]: [...(wardrobe[group] || []), { id: job.idempotencyKey, loading: true }] };
    },
    onError: (job) => {
      const group = job.request.source.wardrobeGroup;
      wardrobe = { ...wardrobe, [group]: (wardrobe[group] || []).filter((image) => image.id !== job.idempotencyKey) };
    },
    onFinished: (job) => completions.get(job)?.resolve(job.response?.data || null),
  });
  context.controller = controller;
  const generate = (value) => {
    const elementKey = `${value.source.id || 'photo'}:${value.source.src}`;
    const job = controller.start(elementKey, { sourceSrc: value.source.src,
      action: value.variationMode || 'reshoot', value: value.colorId || value.outerClosureState || null, request: value },
    ({ idempotencyKey, onJobStarted, request: retainedRequest }) => context.api.generateImage('project', {
      mode: 'vary', ...retainedRequest, idempotencyKey, onJobStarted,
    }));
    if (!completions.has(job)) {
      let resolve;
      const promise = new Promise((done) => { resolve = done; });
      completions.set(job, { promise, resolve });
    }
    const callbacks = [...schedules.values()]; schedules.clear();
    callbacks.forEach((callback) => callback());
    return completions.get(job).promise;
  };
  return { generate, posts, jobs, context, resolve, wardrobe: () => wardrobe };
}

test('canvas click lock releases after an unknown response and rechecks the same request', async () => {
  const h = submissionHarness({ errors: [new Error('lost response')] });
  await h.generate(request);
  await h.generate(request);
  assert.equal(h.posts.length, 2);
  assert.equal(h.posts[1].headers['Idempotency-Key'], h.posts[0].headers['Idempotency-Key']);
  assert.deepEqual(h.posts[1].body, h.posts[0].body);
  assert.equal(h.jobs.size, 1);
});

test('vary double click in the same tick submits and reserves only once', async () => {
  const h = submissionHarness({ hold: true });
  const pending = [h.generate(request), h.generate(request)];
  try {
    assert.equal(h.posts.length, 1, '변형 요청은 같은 틱에 한 번만 보낸다');
    assert.equal(h.jobs.size, 1);
    assert.equal(h.wardrobe().navy.length, 1);
  } finally { h.resolve(); await Promise.all(pending); }
});

for (const status of [undefined, 503]) test(`unknown vary submission reuses its UUID and original request, status ${status}`, async () => {
  const h = submissionHarness({ error: Object.assign(new Error('lost response'), status ? { status } : {}) });
  await h.generate(request);
  await h.generate(structuredClone(request));
  const keys = h.posts.map(post => post.headers?.['Idempotency-Key']);
  assert.match(keys[0] || '', /^[0-9a-f-]{36}$/i, '변형 요청에 Idempotency-Key가 필요하다');
  assert.equal(keys[1], keys[0]);
  assert.deepEqual(h.posts[1].body, h.posts[0].body);
  assert.equal(h.jobs.size, 1);
  assert.equal('idempotencyKey' in h.posts[0].body, false);
});

test('unknown vary submission keeps its UUID after a preflight failure, while another source or method starts a new request', async () => {
  const h = submissionHarness({ error: new Error('lost response') });
  await h.generate(request);
  const remote = h.context.api.generateImage;
  h.context.api.generateImage = async () => { throw Object.assign(new Error('not sent'), { requestNotSent: true }); };
  await h.generate(request);
  h.context.api.generateImage = remote;
  await h.generate(request);
  await h.generate({ ...request, source: { ...request.source, src: '/other.png' } });
  await h.generate({ ...request, variationMode: undefined });
  const keys = h.posts.map(post => post.headers?.['Idempotency-Key']);
  assert.ok(keys[0]);
  assert.equal(keys[1], keys[0]);
  assert.notEqual(keys[2], keys[1]);
  assert.notEqual(keys[3], keys[2]);
});

for (const options of [{}, { error: Object.assign(new Error('denied'), { status: 402 }), accept: false }]) test(`confirmed canvas completion or rejection retains the tuple key, rejected ${!!options.error}`, async () => {
  const h = submissionHarness(options);
  await h.generate(request);
  await h.generate(request);
  const keys = h.posts.map(post => post.headers?.['Idempotency-Key']);
  assert.ok(keys[0]);
  assert.equal(keys[0], keys[1]);
});

test('confirmed rejection after an unknown response keeps the same canvas tuple for the next check', async () => {
  const h = submissionHarness({ errors: [new Error('lost response'), Object.assign(new Error('denied'), { status: 402 })] });
  await h.generate(request);
  await h.generate(request);
  assert.equal(h.context.controller.get('photo:/photo.png').status, 'failed');
  await h.generate(request);
  const keys = h.posts.map(post => post.headers?.['Idempotency-Key']);
  assert.equal(keys[1], keys[0]);
  assert.equal(keys[2], keys[1]);
});

test('retrying an unknown photo after generating another photo reuses the original key and request', async () => {
  const h = submissionHarness({ errors: [new Error('accepted but response lost')] });
  const other = { ...request, source: { ...request.source, id: 'other', src: '/other.png' } };
  await h.generate(request);
  await h.generate(other);
  await h.generate(structuredClone(request));
  const keys = h.posts.map(post => post.headers['Idempotency-Key']);
  assert.equal(keys[2], keys[0], '다른 사진을 만든 뒤 돌아와도 미확인 요청의 키를 유지한다');
  assert.notEqual(keys[1], keys[0]);
  assert.deepEqual(h.posts[2].body, h.posts[0].body);
  assert.equal(h.jobs.size, 2, '재확인은 세 번째 생성 작업을 만들지 않는다');

  // The source/action/value tuple stays stable after its response is acknowledged.
  await h.generate(request);
  assert.equal(h.posts[3].headers['Idempotency-Key'], keys[0]);
});

test('multiple unknown photos retain independent keys while alternating retries', async () => {
  const h = submissionHarness({ error: new Error('accepted but response lost') });
  const other = { ...request, source: { ...request.source, id: 'other', src: '/other.png' } };
  for (const req of [request, other, request, other]) await h.generate(req);
  const keys = h.posts.map(post => post.headers['Idempotency-Key']);
  assert.equal(keys[2], keys[0]);
  assert.equal(keys[3], keys[1]);
  assert.notEqual(keys[0], keys[1]);
  assert.equal(h.jobs.size, 2);
});

test('rejecting another photo does not discard the first unknown request', async () => {
  const h = submissionHarness({ errors: [new Error('accepted but response lost'), Object.assign(new Error('denied'), { status: 402 })] });
  const other = { ...request, source: { ...request.source, id: 'other', src: '/other.png' } };
  await h.generate(request);
  await h.generate(other);
  await h.generate(request);
  assert.equal(h.posts[2].headers['Idempotency-Key'], h.posts[0].headers['Idempotency-Key']);
  await h.generate(other);
  assert.equal(h.posts[3].headers['Idempotency-Key'], h.posts[1].headers['Idempotency-Key']);
});

test('an unknown canvas tuple retains its original request when other recipe fields change', async () => {
  const h = submissionHarness({ errors: [new Error('lost response')] });
  await h.generate({ ...request, matchIds: ['original-match'] });
  await h.generate({ ...request, matchIds: ['new-match'] });
  assert.equal(h.posts[1].headers['Idempotency-Key'], h.posts[0].headers['Idempotency-Key']);
  assert.deepEqual(h.posts[1].body, h.posts[0].body);
  assert.deepEqual(h.posts[1].body.matchIds, ['original-match']);
});

test('different values of the same canvas action use independent keys', async () => {
  const h = submissionHarness();
  await h.generate({ ...request, variationMode: 'color', colorId: 'ivory' });
  await h.generate({ ...request, variationMode: 'color', colorId: 'black' });
  assert.notEqual(h.posts[0].headers['Idempotency-Key'], h.posts[1].headers['Idempotency-Key']);
  assert.equal(h.jobs.size, 2);
});
