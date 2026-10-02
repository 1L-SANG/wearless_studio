import test from 'node:test';
import assert from 'node:assert/strict';

import { createEditorRevisionSync, sameEditorJson } from '../../src/lib/editorRevisionSync.js';

function fakeServer({ revision = 0, blocks = [] } = {}) {
  const server = { revision, blocks, puts: [], gate: null };
  server.load = async () => ({ blocks: server.blocks, revision: server.revision });
  server.put = async (_projectId, body, baseRevision) => {
    server.puts.push({ body, baseRevision });
    if (server.gate) await server.gate;
    if (baseRevision != null && baseRevision !== server.revision) {
      const error = new Error('conflict');
      error.status = 409;
      error.code = 'editor_conflict';
      error.meta = { revision: server.revision };
      throw error;
    }
    server.revision += 1;
    server.blocks = body;
    return { ok: true, revision: server.revision };
  };
  return server;
}

test('저장은 마지막으로 본 리비전을 싣고, 응답 리비전으로 다음 저장을 잇는다', async () => {
  const server = fakeServer({ revision: 4, blocks: [{ id: 'a' }] });
  const sync = createEditorRevisionSync({ load: server.load, put: server.put });
  assert.deepEqual(await sync.load('p1'), [{ id: 'a' }]);
  assert.equal(sync.revisionOf('p1'), 4);
  await sync.save('p1', [{ id: 'b' }]);
  await sync.save('p1', [{ id: 'c' }]);
  assert.deepEqual(server.puts.map((p) => p.baseRevision), [4, 5]);
  assert.equal(sync.revisionOf('p1'), 6);
});

test('불러온 그대로면 PUT 하지 않는다 — jsonb 키 순서가 달라도 같은 내용', async () => {
  const server = fakeServer({ revision: 2, blocks: [{ id: 'a', h: 10, elements: [] }] });
  const sync = createEditorRevisionSync({ load: server.load, put: server.put });
  await sync.load('p1');
  await sync.save('p1', [{ elements: [], h: 10, id: 'a' }]);
  assert.equal(server.puts.length, 0);
  assert.equal(sync.revisionOf('p1'), 2);
});

test('다른 탭이 먼저 저장했으면 409 를 던지고 덮어쓰지 않는다', async () => {
  const server = fakeServer({ revision: 1, blocks: [] });
  const sync = createEditorRevisionSync({ load: server.load, put: server.put });
  await sync.load('p1');
  server.revision = 2; server.blocks = [{ id: 'other-tab' }];
  await assert.rejects(sync.save('p1', [{ id: 'stale' }]), (error) => error.code === 'editor_conflict');
  assert.deepEqual(server.blocks, [{ id: 'other-tab' }]);
});

test('같은 탭의 저장은 한 번에 하나 — 순서가 뒤집히지 않고 밀린 것은 최신 하나로 합친다', async () => {
  const server = fakeServer({ revision: 0, blocks: [] });
  const sync = createEditorRevisionSync({ load: server.load, put: server.put });
  await sync.load('p1');
  let release;
  server.gate = new Promise((resolve) => { release = resolve; });
  const first = sync.save('p1', [{ id: 'v1' }]);
  await new Promise((resolve) => setImmediate(resolve));   // v1 이 날아가 응답을 기다리는 중
  const second = sync.save('p1', [{ id: 'v2' }]);
  const third = sync.save('p1', [{ id: 'v3' }]);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(server.puts.length, 1);   // 두 번째부터는 앞의 응답을 기다린다
  server.gate = null;
  release();
  await Promise.all([first, second, third]);
  assert.deepEqual(server.puts.map((p) => p.body), [[{ id: 'v1' }], [{ id: 'v3' }]]);
  assert.deepEqual(server.puts.map((p) => p.baseRevision), [0, 1]);
  assert.deepEqual(server.blocks, [{ id: 'v3' }]);
});

test('앞 저장이 실패해도 다음 저장은 계속 나간다', async () => {
  const server = fakeServer({ revision: 0, blocks: [] });
  let failOnce = true;
  const put = async (projectId, body, base) => {
    if (failOnce) { failOnce = false; throw new Error('network'); }
    return server.put(projectId, body, base);
  };
  const sync = createEditorRevisionSync({ load: server.load, put });
  await sync.load('p1');
  await assert.rejects(sync.save('p1', [{ id: 'a' }]));
  await sync.save('p1', [{ id: 'b' }]);
  assert.deepEqual(server.blocks, [{ id: 'b' }]);
});

test('리비전을 모르는 옛 서버(배열 응답)면 조건 없이 저장한다', async () => {
  const server = fakeServer({ revision: 0, blocks: [] });
  const sync = createEditorRevisionSync({ load: async () => [{ id: 'a' }], put: server.put });
  assert.deepEqual(await sync.load('p1'), [{ id: 'a' }]);
  assert.equal(sync.revisionOf('p1'), null);
  await sync.save('p1', [{ id: 'b' }]);
  assert.equal(server.puts[0].baseRevision, null);
});

test('sameEditorJson 은 키 순서를 무시하고 배열 순서는 본다', () => {
  assert.equal(sameEditorJson({ a: 1, b: [1, 2] }, { b: [1, 2], a: 1 }), true);
  assert.equal(sameEditorJson([1, 2], [2, 1]), false);
  assert.equal(sameEditorJson({ a: 1 }, { a: 1, b: undefined }), true);
});

test('더 새 저장에 밀린 호출은 그 새 저장의 결과를 받는다 — 새 저장이 409 면 밀린 쪽도 실패', async () => {
  // 실측(2026-10-02 로컬 두 탭): [저장] 직후 자동 저장이 이어 들어와 [저장]이 밀렸고, 자동 저장은
  // 409 로 거절됐는데 [저장] 쪽은 성공으로 끝나 "저장했어요" 가 충돌 안내와 함께 떴다.
  const server = fakeServer({ revision: 1, blocks: [{ id: 'a' }] });
  const sync = createEditorRevisionSync({ load: server.load, put: server.put });
  await sync.load('p1');
  server.revision = 2; // 다른 탭이 먼저 저장
  let release;
  server.gate = new Promise((r) => { release = r; });
  const first = sync.save('p1', [{ id: 'x' }]);   // 나가서 대기 중
  while (server.puts.length < 1) await new Promise((r) => setImmediate(r));
  const manual = sync.save('p1', [{ id: 'y' }]);  // 밀릴 호출
  const auto = sync.save('p1', [{ id: 'z' }]);    // 최신
  release();
  await assert.rejects(first, { code: 'editor_conflict' });
  await assert.rejects(auto, { code: 'editor_conflict' });
  await assert.rejects(manual, { code: 'editor_conflict' });
});

test('밀린 호출은 새 저장이 성공하면 성공으로 끝난다', async () => {
  const server = fakeServer({ revision: 1, blocks: [{ id: 'a' }] });
  const sync = createEditorRevisionSync({ load: server.load, put: server.put });
  await sync.load('p1');
  let release;
  server.gate = new Promise((r) => { release = r; });
  const first = sync.save('p1', [{ id: 'x' }]);
  while (server.puts.length < 1) await new Promise((r) => setImmediate(r));
  const skipped = sync.save('p1', [{ id: 'y' }]);
  const latest = sync.save('p1', [{ id: 'z' }]);
  release();
  await Promise.all([first, skipped, latest]);
  assert.deepEqual(server.puts.map((p) => p.body), [[{ id: 'x' }], [{ id: 'z' }]]);
});
