import test from 'node:test';
import assert from 'node:assert/strict';

import {
  editorWaitDraftDecision,
  loadEditorWaitDraftBaseRevision,
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
