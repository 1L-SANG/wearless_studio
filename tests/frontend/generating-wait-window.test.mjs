import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  clearDetailPageJobMarker,
  loadDetailPageJobMarker,
  saveDetailPageJobMarker,
} from '../../src/lib/detailPageJobPersistence.js';
import {
  clearEditorWaitDraft,
  loadEditorWaitDraft,
  saveEditorWaitDraft,
} from '../../src/lib/editorWaitDraft.js';
import { canSafelyMergeServerBlocks, fillGenBlocks, mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';

const httpAdapter = readFileSync(
  new URL('../../src/lib/api/httpAdapter.js', import.meta.url),
  'utf8',
);
const store = readFileSync(
  new URL('../../src/store/useAppStore.js', import.meta.url),
  'utf8',
);
const generating = readFileSync(
  new URL('../../src/features/generating/Generating.jsx', import.meta.url),
  'utf8',
);
const editor = readFileSync(
  new URL('../../src/features/editor/Editor.jsx', import.meta.url),
  'utf8',
);

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test('상세페이지 대기 상한은 서버 lease 복구와 같은 15분이다', () => {
  const call = httpAdapter.slice(httpAdapter.indexOf('async generateDetailPage'));
  const body = call.slice(0, call.indexOf('async getProject'));
  assert.match(body, /timeoutMs: 900000/);
  assert.match(store, /\+ 900000/);
  assert.doesNotMatch(body, /timeoutMs: 300000/);
});

test('진행 중 job 표식은 새로고침 뒤 같은 jobId로 복원하고 완료 시 지운다', () => {
  const storage = memoryStorage();
  const job = { projectId: 'p1', jobId: 'j1', startedAt: 1234 };
  saveDetailPageJobMarker(job, storage);
  assert.deepEqual(loadDetailPageJobMarker(storage), job);
  clearDetailPageJobMarker(storage);
  assert.equal(loadDetailPageJobMarker(storage), null);
});

test('생성 중 임시 작업본은 문구 삭제와 배치 변경을 함께 보존한다', () => {
  const storage = memoryStorage();
  const blocks = [{
    id: 'b1', h: 800,
    elements: [{ id: 't1', type: 'text', text: '', x: 321, y: 456, w: 200, h: 40 }],
  }];
  saveEditorWaitDraft('p1', blocks, storage);
  assert.deepEqual(loadEditorWaitDraft('p1', storage), blocks);
  clearEditorWaitDraft('p1', storage);
  assert.equal(loadEditorWaitDraft('p1', storage), null);
});

test('재수신한 생성 이벤트는 자동 이미지 URL만 갱신하고 사용자 교체 이미지는 지킨다', () => {
  const job = { cuts: { sb1: { url: 'new-preview' } }, copy: {}, live: [], failedCuts: [] };
  const auto = [{ id: 'b1', elements: [{
    id: 'i1', type: 'image', sourceBlockId: 'sb1', src: 'old-preview', genAutoSrc: 'old-preview',
  }] }];
  assert.equal(fillGenBlocks(auto, job)[0].elements[0].src, 'new-preview');

  const replaced = [{ id: 'b1', elements: [{
    id: 'i1', type: 'image', sourceBlockId: 'sb1', src: 'seller-image', genAutoSrc: 'old-preview',
  }] }];
  assert.equal(fillGenBlocks(replaced, job)[0].elements[0].src, 'seller-image');
});

test('로컬 스켈레톤에 없는 서버 컷이 있으면 서버 완성본 전체를 보존한다', () => {
  const local = [{ id: 'local-1', elements: [
    { id: 'i1', type: 'image', sourceBlockId: 'sb1', src: 'preview-1' },
  ] }];
  const server = [
    { id: 'server-1', elements: [{ id: 'si1', type: 'image', sourceBlockId: 'sb1', src: '/stable-1' }] },
    { id: 'server-2', elements: [{ id: 'si2', type: 'image', sourceBlockId: 'sb2', src: '/stable-2' }] },
  ];
  assert.equal(canSafelyMergeServerBlocks(local, server), false);
  assert.deepEqual(mergeServerBlocks(local, server), server);
});

test('서버의 모든 컷이 로컬에 있으면 셀러 배치와 안정 이미지 URL을 합친다', () => {
  const local = [{ id: 'seller-row', x: 123, elements: [
    { id: 'i1', type: 'image', sourceBlockId: 'sb1', src: 'preview-1', x: 77 },
    { id: 'i2', type: 'image', sourceBlockId: 'sb2', src: 'preview-2', x: 456 },
  ] }];
  const server = [
    { id: 'server-1', elements: [{ id: 'si1', type: 'image', sourceBlockId: 'sb1', src: '/stable-1' }] },
    { id: 'server-2', elements: [{ id: 'si2', type: 'image', sourceBlockId: 'sb2', src: '/stable-2' }] },
  ];
  const merged = mergeServerBlocks(local, server);
  assert.equal(canSafelyMergeServerBlocks(local, server), true);
  assert.equal(merged[0].id, 'seller-row');
  assert.deepEqual(merged[0].elements.map((el) => el.src), ['/stable-1', '/stable-2']);
});

test('생성 진입 화면은 잡을 시작하고 에디터로 바로 보내며 콘티로 되돌리지 않는다', () => {
  const start = generating.indexOf('startDetailPageGeneration(pid)');
  const openEditor = generating.indexOf('navigate(`/editor/${pid}`');
  assert.ok(start > 0 && openEditor > start);
  assert.doesNotMatch(generating, /navigate\('\/create\/storyboard'/);
});

test('생성 중 자동 저장은 서버 완성본 대신 임시 작업본을 사용한다', () => {
  const autoSave = editor.slice(
    editor.indexOf('// 자동 저장 — 생성 중에는'),
    editor.indexOf('// delete key removes selection'),
  );
  assert.match(autoSave, /if \(genActive\)/);
  assert.match(autoSave, /saveEditorWaitDraft\(projectId, latestBlocks\.current\)/);
  assert.match(autoSave, /api\.saveEditorBlocks\(projectId, latestBlocks\.current\)/);
});

test('실패 후 다시 시도는 임시 작업본을 지키고 콘티 복귀만 폐기한다', () => {
  const retry = editor.slice(
    editor.indexOf("useAppStore.getState().resetDetailPageJob();", editor.indexOf("genFinalizeError ?")),
    editor.indexOf('>다시 시도</Button>'),
  );
  assert.doesNotMatch(retry, /clearEditorWaitDraft/);

  const discard = editor.slice(
    editor.indexOf('const discardGenerationAndReturnToStoryboard'),
    editor.indexOf('/* kb.current', editor.indexOf('const discardGenerationAndReturnToStoryboard')),
  );
  assert.match(discard, /clearEditorWaitDraft\(projectId\)/);
  assert.match(discard, /resetDetailPageJob\(\)/);
  assert.match(discard, /navigate\('\/create\/storyboard'\)/);
});

test('완료 병합은 기본 정보 템플릿을 같은 방문에서 적용한다', () => {
  const completion = editor.slice(
    editor.indexOf('const server = await getFinalEditorBlocks'),
    editor.indexOf("toast.push(restoredServerLayout"),
  );
  assert.match(completion, /canSafelyMergeServerBlocks\(current, server\)/);
  assert.match(completion, /needsDefaultTemplate\(merged\)/);
  assert.match(completion, /applyInfoTemplate\(merged, ctx\)\.blocks/);
  assert.match(completion, /ensureShippingReturnsBlock\(merged, ctx\)/);
});

test('저장된 문서를 여는 경로도 누락된 배송·교환·반품 프레임을 복구한다', () => {
  const initialization = editor.slice(
    editor.indexOf('.then(([b, w, c, _a, p, fm, an, sb, mc]) => {'),
    editor.indexOf('setWardrobe(mergeEditorImagesIntoWardrobe'),
  );
  assert.match(initialization, /ensureShippingReturnsBlock\(withH, ctx\)/);
});
