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

test('DB 503은 기존 jobId 폴링만 늦추고 생성 POST를 재호출하지 않는다', () => {
  const start = store.indexOf('let dbUnavailableCount = 0;');
  assert.ok(start > 0, 'DB 일시 장애 재조회 분기를 못 찾았다');
  const retry = store.slice(start, store.indexOf('const events = ev?.events || [];', start));
  assert.match(retry, /e\?\.status !== 503/);
  assert.match(retry, /Math\.min\(5000/);
  assert.match(retry, /continue;/);
  assert.doesNotMatch(retry, /startDetailPage/);
  assert.doesNotMatch(retry, /generateDetailPage/);
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
  // 존재하지 않는 문구로 끝을 잡으면 indexOf 가 -1 이라 파일 거의 전체가 슬라이스돼
  // 단정이 엉뚱한 코드에 걸린다(2026-08-17 리뷰). 그 효과의 닫는 줄까지만 자른다.
  const autoSaveStart = editor.indexOf('// 자동 저장 — 생성 중에는');
  assert.ok(autoSaveStart > 0, '자동 저장 블록을 못 찾았다');
  const autoSave = editor.slice(autoSaveStart, autoSaveStart + editor.slice(autoSaveStart).indexOf('\n  }, ['));
  assert.ok(autoSave.length < 2000, `슬라이스가 너무 넓다(${autoSave.length}자) — 단정이 헛돈다`);
  assert.match(autoSave, /if \(genActive\)/);
  // persistable() = 손 안 댄 안내 문구를 걷어내는 저장 관문(2026-08-17 검증).
  assert.match(autoSave, /saveEditorWaitDraft\(projectId, persistable\(latestBlocks\.current\)\)/);
  assert.match(autoSave, /api\.saveEditorBlocks\(projectId, persistable\(latestBlocks\.current\)\)/);
});

test('실패 후 다시 시도는 임시 작업본을 지키고, 이탈은 앞 단계가 아니라 보관함으로 간다', () => {
  const retry = editor.slice(
    editor.indexOf("useAppStore.getState().resetDetailPageJob();", editor.indexOf("genFinalizeError ?")),
    editor.indexOf('>다시 시도</Button>'),
  );
  assert.doesNotMatch(retry, /clearEditorWaitDraft/);

  // 에디터 진입 후 앞 단계 복귀는 금지(오너 8/15) — 되돌아가면 만든 컷·편집이 덮인다.
  // 실패·차단 화면의 이탈은 편집분을 저장한 뒤 보관함으로 내려놓는다.
  assert.doesNotMatch(editor, /navigate\('\/create\/storyboard'\)/);
  assert.doesNotMatch(editor, /discardGenerationAndReturnToStoryboard/);
  // 함수 본문만 잘라 본다 — 뒤따르는 주석 위치로 끝을 잡으면 그 주석이 옮겨질 때
  // 슬라이스가 파일 절반을 삼켜 단정이 헛돈다(2026-08-16 TDZ 수정 때 실제로 깨졌다).
  const leaveStart = editor.indexOf('const leaveToLibrary');
  const leave = editor.slice(leaveStart, editor.indexOf('\n  };', leaveStart) + 5);
  assert.match(leave, /flushExit\(\)/, '편집분을 먼저 저장한다');
  assert.match(leave, /skipExitPersist\.current = true/, '언마운트 정리가 덮어쓰지 않게');
  assert.match(leave, /navigate\('\/library'\)/);
  assert.doesNotMatch(leave, /clearEditorWaitDraft/, '임시 작업본은 남겨 재진입 때 이어서 한다');
});

test('편집을 시작한 프로젝트는 초안 단계로 되돌아갈 수 없다', () => {
  const shell = readFileSync(new URL('../../src/features/shell/shell.jsx', import.meta.url), 'utf8');
  const app = readFileSync(new URL('../../src/App.jsx', import.meta.url), 'utf8');
  // 서버 status='done' 만으로는 부족하다 — 생성이 실패·차단으로 끝나면 done 이 아니다.
  // 단 프로젝트가 실제로 열리는지 확인한 뒤에만 막는다(사라진 프로젝트를 막으면 무한 왕복).
  // 돌아온 프로젝트가 **그 프로젝트가 맞는지**까지 본다 — mock 은 id 를 무시하고 현재
  // 초안을 돌려주므로, 확인 없이 막으면 개발 모드에서 모달↔입력 화면 왕복이 된다(8/17 리뷰).
  assert.match(shell, /const sameProject = p\?\.id === pid;/);
  assert.match(shell, /if \(!cancelled && sameProject && \(p\.status === 'done' \|\| hasEditorEntered\(pid\)\)\) setBlocked\(true\);/);
  assert.match(app, /markEditorEntered\(project\.id\);\s*\n\s*setPhase\('ready'\);/);
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
  // 경계를 'setWardrobe(...' 로 잡으면 콘티 실패 폴백 분기가 앞에 끼면서 슬라이스가 잘린다.
  // 저장 문서 경로(else 분기)의 끝인 withH 정규화 지점을 경계로 쓴다.
  const initialization = editor.slice(
    editor.indexOf('.then(([b, w, c, _a, p, fm, an, sb, mc]) => {'),
    editor.indexOf('withH = upgradeLegacyKiwiTemplateBlocks'),
  );
  assert.match(initialization, /ensureShippingReturnsBlock\(withH, ctx\)/);
});


test('자동저장 실패를 삼키지 않는다 — 임시 보관 + 배너 + 다시 저장', () => {
  // 조용히 실패하면 화면은 멀쩡한데 편집만 사라져, 셀러는 탭을 닫고서야 전부 날아간 걸
  // 안다(오너 신고 2026-08-19). 실패 경로가 셋을 모두 하는지 고정한다.
  const saveStart = editor.indexOf('api.saveEditorBlocks(projectId, persistable(latestBlocks.current)).then');
  assert.ok(saveStart > 0);
  const block = editor.slice(saveStart, editor.indexOf('}, 1500);', saveStart));
  assert.doesNotMatch(block, /catch\(\(\) => \{\}\)/, '실패를 삼키면 안 된다');
  assert.match(block, /const backedUp = saveEditorWaitDraft\(projectId, persistable\(latestBlocks\.current\)\)/, '브라우저에 임시 보관');
  assert.match(block, /setSaveError\(\{ \.\.\.classifyEditorLoadError\(error\), backedUp \}\)/, '원인 + 보관 성공 여부를 배너로');
  assert.match(block, /setSaveError\(null\)/, '성공하면 배너를 거둔다');
  // 배너와 복구 수단
  assert.match(editor, /className="ed-savebar"/);
  assert.match(editor, /const retrySaveNow = \(\) => \{/);
  assert.match(editor, /openLogin\(`\/editor\/\$\{projectId\}`\)/, '로그인이 풀린 경우 제자리 복귀');
});


test('임시 보관 실패는 안심시키지 않는다 — 저장 공간이 없으면 사실대로', () => {
  // 보관까지 실패했는데 "보관해 뒀어요"라고 말하면 셀러는 안심하고 창을 닫는다.
  // 저장 실패를 삼키던 것과 같은 종류의 거짓말이라 같은 강도로 막는다.
  assert.match(editor, /saveError\.backedUp/, '배너가 보관 성공 여부를 읽는다');
  assert.match(editor, /창을 닫으면 편집 내용이 사라져요/, '보관 실패 시 경고');
});

test('saveEditorWaitDraft 는 보관 성공 여부를 돌려준다', async () => {
  const { saveEditorWaitDraft } = await import('../../src/lib/editorWaitDraft.js');
  const ok = { setItem() {} };
  const full = { setItem() { throw new Error('QuotaExceededError'); } };
  assert.equal(saveEditorWaitDraft('p1', [{ id: 'b' }], ok), true);
  assert.equal(saveEditorWaitDraft('p1', [{ id: 'b' }], full), false, '공간 초과는 false');
  assert.equal(saveEditorWaitDraft('', [], ok), false);
  assert.equal(saveEditorWaitDraft('p1', null, ok), false);
});


test('에디터를 나갈 때의 저장도 실패를 흘리지 않는다', () => {
  // 화면이 곧 사라져 알릴 방법이 없다 — 대신 브라우저에 보관해 다음 진입에서 복원한다.
  // catch 가 없던 동안에는 서버가 죽은 채로 나가면 편집이 통째로 사라졌다.
  const flush = editor.slice(editor.indexOf('const flushExit = () => {'));
  assert.match(flush.slice(0, 1800),
    /api\.saveEditorBlocks\(projectId, persistable\(bs\)\)\s*\.catch\(\(\) => saveEditorWaitDraft\(projectId, persistable\(bs\)\)\)/,
    '이탈 플러시: persistable 게이트 + 실패 시 로컬 보관');
  const unmount = editor.slice(editor.indexOf('if (skipExitPersist.current'));
  assert.match(unmount.slice(0, 900),
    /\.catch\(\(\) => saveEditorWaitDraft\(projectId, persistable\(latestBlocks\.current\)\)\)/,
    '언마운트 정리 저장도 마찬가지');
});

test('수동 [저장] 버튼은 실패를 반드시 말한다', () => {
  // onClick={save} 라 실패가 unhandled rejection 으로 흘렀다 — 누른 사람은 저장된 줄 안다.
  const save = editor.slice(editor.indexOf('const save = async () => {'));
  const body = save.slice(0, 1600);
  assert.match(body, /try \{\s*await api\.saveEditorBlocks\(projectId, persistable\(/, 'persistable 게이트 + try');
  assert.match(body, /catch \(error\) \{/, '실패를 잡는다');
  assert.match(body, /saveEditorWaitDraft\(projectId, persistable\(/, '실패분을 보관');
  assert.match(body, /setSaveError\(\{ \.\.\.classifyEditorLoadError\(error\), backedUp \}\)/, '배너로');
  assert.match(body, /저장하지 못했어요/, '토스트로도 즉시 알림');
});


test('모든 서버 저장은 persistable 게이트를 통과한다 (불변식)', () => {
  // 스냅샷이 아니라 불변식으로 잡는다 — 나중에 저장 경로가 하나 더 생겨도 걸린다.
  // 게이트를 빠뜨리면 손 안 댄 '내용을 입력하세요.'가 셀러의 상품 페이지에 그대로 실린다.
  const sites = [...editor.matchAll(/api\.saveEditorBlocks\(projectId,\s*([^;]{0,80})/g)];
  assert.ok(sites.length >= 6, `저장 경로가 ${sites.length}개뿐 — 찾기 정규식을 의심하라`);
  for (const [, arg] of sites) {
    assert.match(arg, /^persistable\(/, `게이트 없는 저장 경로: ${arg.slice(0, 50)}`);
  }
});

test('모든 서버 저장 실패는 잡힌다 (불변식)', () => {
  // 잡히지 않은 저장 실패는 unhandled rejection 으로 흘러 화면이 아무 말도 안 한다.
  // 각 호출 뒤 300자 안에 .catch( 가 있거나, 그 호출이 try 블록 안(await)이어야 한다.
  for (const m of editor.matchAll(/api\.saveEditorBlocks\(projectId,/g)) {
    const before = editor.slice(Math.max(0, m.index - 60), m.index);
    const after = editor.slice(m.index, m.index + 320);
    const guarded = /\.catch\(/.test(after) || /await /.test(before);
    assert.ok(guarded, `실패를 안 잡는 저장 경로: ...${before.slice(-40)}`);
  }
});
