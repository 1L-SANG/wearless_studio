import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { transformWithEsbuild } from 'vite';
import { hasImageSource } from '../../src/features/editor/editorImageSource.js';

const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const panels = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
const adapter = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
const slice = (text, start, end) => text.slice(text.indexOf(start), text.indexOf(end, text.indexOf(start)));
const evaluate = (body, scope) => new Function('scope', `with (scope) { ${body} }`)({ wardrobeContext: { current: { storyboard: [] } }, ...scope });
const React = { createElement: (type, props, ...children) => ({ type, props, children }) };

test('empty, pending and failed canvas slots are never vary sources', () => {
  const body = slice(editor, '  const varySource =', '  // setBlockFocused');
  for (const selectedElObj of [
    { id: 'empty', type: 'image', src: '' },
    { id: 'waiting', type: 'image', genPending: 'live' },
    { id: 'failed', type: 'image', src: null, genPending: 'failed' },
    { id: 'failed-blank', type: 'image', genFailed: true },
  ]) {
    assert.equal(evaluate(`${body}; return varySource;`, { selectedElObj, varyTarget: null, wardrobe: {}, hasImageSource }), null, selectedElObj.id);
  }
  const source = evaluate(`${body}; return varySource;`, { selectedElObj: { id: 'photo', type: 'image', src: '/photo.png' }, varyTarget: null, wardrobe: {}, hasImageSource });
  assert.equal(source.src, '/photo.png');
  assert.equal(evaluate(`${body}; return varySource;`, { selectedElObj: null, varyTarget: { id: 'waiting' }, wardrobe: { misc: [{ id: 'waiting', loading: true }] }, hasImageSource }), null);
});

async function panel(source, onGenerate = () => {}) {
  const { code } = await transformWithEsbuild(slice(panels, 'function VaryPanel(', '/* ---------- AI ---------- */') + '\nreturn VaryPanel;', 'vary.jsx', { loader: 'jsx' });
  return evaluate(code, { React, useRef: value => ({ current: value }), EmptyState: 'EmptyState', Button: 'Button',
    hasImageSource })({ source, catalogs: { creditCosts: { editorImage: 1 } }, onGenerate });
}

test('vary panel shows only the existing empty state without a source URL', async () => {
  for (const source of [null, { id: 'empty', src: '' }, { id: 'pending', genPending: 'live' }, { id: 'failed', genFailed: true }]) {
    const rendered = await panel(source);
    assert.equal(rendered.type, 'EmptyState');
    assert.equal(rendered.props.title, '기준 사진을 골라주세요');
  }
});

test('vary panel releases its click lock after an unknown response so the request can be checked again', async () => {
  let attempts = 0, finish;
  const rendered = await panel({ id: 'photo', src: '/photo.png' }, () => { attempts++; return new Promise(resolve => { finish = resolve; }); });
  const click = rendered.children.find(child => child?.type === 'Button').props.onClick;
  const first = click();
  click();
  assert.equal(attempts, 1);
  finish(null);
  await first;
  const second = click();
  assert.equal(attempts, 2);
  finish(null);
  await second;
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
  const context = {
    api: { generateImage: generateRemote }, projectId: 'project', crypto: { randomUUID },
    varySubmission: { current: null }, unconfirmedVarySubmissions: { current: new Map() }, varyTarget: null,
    hasImageSource,
    uid: () => randomUUID(), setWardrobe: update => { wardrobe = update(wardrobe); },
    setTab: () => {}, genCount: { current: 0 }, setGenDot: () => {}, toast: { push: () => {} }, syncCredits: () => {},
    placeGeneratedImage: () => {}, handleImageJobFailure: () => {},
  };
  const generate = evaluate(slice(editor, '  const varyGenerate =', '  const varyImage =') + '\nreturn varyGenerate;', context);
  return { generate, posts, jobs, context, resolve, wardrobe: () => wardrobe };
}

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

for (const options of [{}, { error: Object.assign(new Error('denied'), { status: 402 }), accept: false }]) test(`confirmed vary completion or rejection rotates its key, rejected ${!!options.error}`, async () => {
  const h = submissionHarness(options);
  await h.generate(request);
  await h.generate(request);
  const keys = h.posts.map(post => post.headers?.['Idempotency-Key']);
  assert.ok(keys[0]);
  assert.notEqual(keys[0], keys[1]);
});

test('confirmed vary rejection after an unknown response ends that submission before the next click', async () => {
  const h = submissionHarness({ errors: [new Error('lost response'), Object.assign(new Error('denied'), { status: 402 })] });
  await h.generate(request);
  await h.generate(request);
  assert.equal(h.context.varySubmission.current, null, '확정 거절은 접수 미확인 상태를 해제한다');
  await h.generate(request);
  const keys = h.posts.map(post => post.headers?.['Idempotency-Key']);
  assert.equal(keys[1], keys[0]);
  assert.notEqual(keys[2], keys[1]);
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

  // Once A is acknowledged, another deliberate click really is a new cut.
  await h.generate(request);
  assert.notEqual(h.posts[3].headers['Idempotency-Key'], keys[0]);
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
  assert.notEqual(h.posts[3].headers['Idempotency-Key'], h.posts[1].headers['Idempotency-Key']);
});
