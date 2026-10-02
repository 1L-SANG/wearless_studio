import test from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';

import {
  editorWaitDraftDecision,
  loadEditorWaitDraftBaseRevision,
  loadEditorWaitDraftEntry,
  saveEditorWaitDraft,
} from '../../src/lib/editorWaitDraft.js';

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { data.set(key, String(value)); },
    removeItem: (key) => { data.delete(key); },
  };
}

test('임시 작업본은 그때 본 서버 리비전을 함께 남긴다', () => {
  const storage = memoryStorage();
  saveEditorWaitDraft('p1', [{ id: 'a' }], storage, { baseRevision: 7 });
  assert.equal(loadEditorWaitDraftBaseRevision('p1', storage), 7);
  saveEditorWaitDraft('p1', [{ id: 'a' }], storage);
  assert.equal(loadEditorWaitDraftBaseRevision('p1', storage), null);
});

test('같은 리비전 위의 작업본만 서버본 위에 복원한다', () => {
  const draft = [{ id: 'mine' }];
  const server = [{ id: 'server' }];
  assert.equal(editorWaitDraftDecision({ draft, draftBase: 4, server, serverRevision: 4 }), 'apply');
  // 그 사이 다른 탭·기기가 저장했다 — 오래된 작업본이 새 내용을 덮으면 안 된다.
  assert.equal(editorWaitDraftDecision({ draft, draftBase: 4, server, serverRevision: 5 }), 'stale');
  // 나가면서 한 저장이 성공해 리비전만 하나 올랐다 — 내용이 같으면 조용히 정리.
  assert.equal(editorWaitDraftDecision({ draft: server, draftBase: 4, server: [{ id: 'server' }], serverRevision: 5 }), 'same');
  assert.equal(editorWaitDraftDecision({ draft: null, draftBase: 4, server, serverRevision: 5 }), 'none');
});

test('기준을 모르는 옛 작업본·옛 서버는 예전처럼 복원한다', () => {
  const draft = [{ id: 'mine' }];
  const server = [{ id: 'server' }];
  assert.equal(editorWaitDraftDecision({ draft, draftBase: null, server, serverRevision: 5 }), 'apply');
  assert.equal(editorWaitDraftDecision({ draft, draftBase: 3, server, serverRevision: null }), 'apply');
});

test('내용 비교는 키 순서와 무관하다', () => {
  const draft = [{ id: 'a', elements: [{ src: '/v1/assets/x/file', type: 'image' }] }];
  const server = [{ elements: [{ type: 'image', src: '/v1/assets/x/file' }], id: 'a' }];
  assert.equal(editorWaitDraftDecision({ draft, draftBase: 1, server, serverRevision: 2 }), 'same');
});

test('작업본 내용과 기준 리비전은 한 번에 읽는다 — 사이에 키가 지워져도 짝이 어긋나지 않는다', () => {
  const storage = memoryStorage();
  saveEditorWaitDraft('p1', [{ id: 'old' }], storage, { baseRevision: 6 });
  const entry = loadEditorWaitDraftEntry('p1', storage);
  // 다른 탭의 저장 성공이 공유 키를 지운 상황(실측 2026-10-02 로컬 두 탭)
  storage.removeItem('ew-draft-p1');
  assert.deepEqual(entry, { blocks: [{ id: 'old' }], baseRevision: 6 });
  assert.equal(
    editorWaitDraftDecision({
      draft: entry.blocks, draftBase: entry.baseRevision, server: [{ id: 'new' }], serverRevision: 7,
    }),
    'stale',
  );
  assert.equal(loadEditorWaitDraftEntry('p1', storage), null);
});

test('에디터는 진입 때 읽은 작업본의 기준 리비전을 쓴다 — 응답 뒤에 다시 읽지 않는다', () => {
  // 실측: 진입 때 내용을 읽고 서버 응답 뒤 기준만 다시 읽자, 그새 다른 탭이 키를 지워 기준이
  // null(=옛 작업본)이 됐고 오래된 내용이 복원돼 자동 저장이 다른 탭의 최신 저장을 덮었다.
  const src = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  assert.match(src, /loadEditorWaitDraftEntry\(projectId\)/);
  assert.doesNotMatch(src, /draftBase:\s*loadEditorWaitDraftBaseRevision\(/);
});
