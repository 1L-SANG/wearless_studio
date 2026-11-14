import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { editedTextElement } from '../../src/features/editor/editorTextEdit.js';
import { decorateGenBlocks, fillGenBlocks, mergeServerBlocks, patchTemplateElement } from '../../src/lib/editorWaitSkeleton.js';
import { saveEditorWaitDraft, loadEditorWaitDraft } from '../../src/lib/editorWaitDraft.js';
import { createEditorRevisionSync } from '../../src/lib/editorRevisionSync.js';
import { relativizeAssetUrls, rebaseAssetUrls } from '../../src/lib/assetUrl.js';

const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const draft = (extra = {}) => ({ id: 'copy', type: 'text', text: 'AI가 쓴 초안', aiDraft: true,
  x: 0, y: 0, w: 400, h: 60, style: { size: 24 }, ...extra });
const block = (elements = [draft()], extra = {}) => ({ id: 'b', name: '후기', pageSectionKey: 'T01.review',
  pageSectionKind: 'review', pageDesignId: 'R01-D01', h: 200, elements, ...extra });

for (const text of ['실제 구매 후기', '', 'AI가 쓴 초안 ']) {
  test(`내용이 바뀌면 aiDraft를 삭제하고 원본은 보존한다: ${JSON.stringify(text)}`, () => {
    const original = draft();
    assert.equal(Object.hasOwn(editedTextElement(original, { text }), 'aiDraft'), false);
    assert.equal(original.aiDraft, true);
  });
}
for (const patch of [{ text: 'AI가 쓴 초안' }, { h: 70 }, { style: { size: 32 } }, { hidden: true }]) {
  test(`내용이 그대로인 확정과 서식 수정은 aiDraft를 유지한다: ${JSON.stringify(patch)}`, () => {
    assert.equal(editedTextElement(draft({ keepWords: true }), patch).aiDraft, true);
  });
}

function editorAction(start, end, name, bindings) {
  const begin = source.indexOf(start), finish = source.indexOf(end, begin);
  assert.ok(begin >= 0 && finish > begin);
  return new Function(...Object.keys(bindings), `${source.slice(begin, finish)}; return ${name};`)(...Object.values(bindings));
}
for (const route of ['commitTextValue', 'patchEl', 'patchElById', 'flushExit']) {
  for (const changed of [false, true]) test(`${route}: ${changed ? '글 변경 시 삭제' : '같은 내용 확정 시 유지'}`, () => {
    let current = [block()];
    const text = changed ? '셀러가 고친 후기' : current[0].elements[0].text;
    const bindings = { editedTextElement, setBlocks: update => { current = update(current); }, selEl: 'copy', patchTemplateElement };
    if (route === 'commitTextValue') {
      editorAction('  const commitTextValue =', '  // 내용이 빈 일반 텍스트', route, bindings)('b', 'copy', text);
    } else if (route === 'patchEl') {
      editorAction('  const patchEl =', '  const patchBubbleAppearance =', route, bindings)({ text });
    } else if (route === 'patchElById') {
      editorAction('  const patchElById =', '  const commitText =', route, bindings)('b', 'copy', { text });
    } else {
      editorAction('  const flushExit =', '  /* 본 편집 화면의 이탈', route, { ...bindings,
        latestBlocks: { current }, editEl: 'copy', wrapRef: { current: { querySelector: () => ({}) } },
        editableText: () => text, hasTitlePlate: () => false, fitTitlePlateBlocks: (_, blocks) => blocks,
        clearTimeout() {}, saveTimer: { current: null }, genActive: true,
        backupLatestEditorDraft: blocks => { current = blocks; },
      })();
    }
    assert.equal(Object.hasOwn(current[0].elements[0], 'aiDraft'), !changed);
  });
}

const job = { status: 'running', cuts: {}, copy: {} };
const waiting = () => decorateGenBlocks([block([draft({ sourceBlockId: 'shot', copyRole: 'body' })])], job, {});
test('자동 진행 글과 손대지 않은 완성 섹션은 aiDraft를 보존한다', () => {
  const pending = waiting();
  const filled = fillGenBlocks(pending, { ...job, copy: { shot: [{ role: 'body', text: '늦게 온 글' }] } });
  assert.equal(filled[0].elements[0].aiDraft, true);
  const server = [block([draft({ sourceBlockId: 'shot', copyRole: 'body', text: '완성 초안' })])];
  assert.equal(mergeServerBlocks(pending, server)[0].elements[0].aiDraft, true);
});
test('서식만 수정한 요소에 늦은 초안이 오면 서버 aiDraft를 함께 받는다', () => {
  const pending = decorateGenBlocks([block([draft({ aiDraft: undefined, sourceBlockId: 'shot', copyRole: 'body' })])], job, {});
  pending[0] = patchTemplateElement(pending[0], 'copy', { x: 20 });
  const server = [block([draft({ sourceBlockId: 'shot', copyRole: 'body', text: '완성 초안' })])];
  const merged = mergeServerBlocks(pending, server)[0].elements[0];
  assert.equal(merged.text, '완성 초안');
  assert.equal(merged.aiDraft, true);
  assert.equal(merged.x, 20);
});
for (const restoreOriginal of [false, true]) test(`셀러가 고친 글은 늦은 초안 병합으로 표시를 되살리지 않는다 (원문 복원 ${restoreOriginal})`, () => {
  const pending = waiting();
  pending[0] = patchTemplateElement(pending[0], 'copy', { text: '셀러 후기' });
  if (restoreOriginal) pending[0] = patchTemplateElement(pending[0], 'copy', { text: 'AI가 쓴 초안' });
  const server = [block([draft({ sourceBlockId: 'shot', copyRole: 'body', text: '늦은 초안' })])];
  const filled = fillGenBlocks(pending, { ...job, copy: { shot: [{ role: 'body', text: '진행 글' }] } });
  const merged = mergeServerBlocks(filled, server)[0].elements[0];
  assert.equal(merged.text, restoreOriginal ? 'AI가 쓴 초안' : '셀러 후기');
  assert.equal(Object.hasOwn(merged, 'aiDraft'), false);
});
test('로컬 저장과 실제 리비전 저장 직렬화 계약은 표시와 삭제를 모두 보존한다', async () => {
  const blocks = [block([draft(), editedTextElement(draft({ id: 'edited' }), { text: '내 후기' })])];
  const data = new Map();
  const storage = { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) };
  assert.equal(saveEditorWaitDraft('p', blocks, storage), true);
  assert.deepEqual(loadEditorWaitDraft('p', storage), blocks);
  let saved = [], revision = 0;
  const sync = createEditorRevisionSync({
    load: async () => ({ blocks: rebaseAssetUrls(saved), revision }),
    put: async (_id, body) => { saved = JSON.parse(JSON.stringify(body)); return { revision: ++revision }; },
  });
  await sync.load('p');
  await sync.save('p', relativizeAssetUrls(blocks));
  assert.deepEqual(await sync.load('p'), blocks);
});

test('남은 초안은 내보내는 글만 세고 빈 묶음, 숨김, 빈 글을 제외한다', async () => {
  const { getAiDraftSectionNames } = await import('../../src/features/editor/editorAiDrafts.js');
  const hiddenGroup = 'template-copy:empty';
  const blocks = [block([
    draft({ text: '  \n ' }), draft({ id: 'hidden', hidden: true }),
    draft({ id: 'image', type: 'image' }), draft({ id: 'plain', aiDraft: false }),
    draft({ id: 'ornament', groupId: hiddenGroup, text: '★★★★★', templateCopyDecoration: '★★★★★' }),
    draft({ id: 'empty-body', groupId: hiddenGroup, text: '' }),
  ])];
  assert.deepEqual(getAiDraftSectionNames(blocks), []);
  assert.deepEqual(getAiDraftSectionNames([block([draft({ locked: true })])]), ['후기']);
  assert.deepEqual(getAiDraftSectionNames(), []);
});
test('기존 섹션 이름을 페이지 순서대로 한 번씩 반환한다', async () => {
  const { getAiDraftSectionNames, formatAiDraftSectionNames } = await import('../../src/features/editor/editorAiDrafts.js');
  const blocks = ['review', 'intro', 'review', 'faq', 'fabric'].map((kind, i) => block([draft()], {
    id: `b${i}`, pageSectionKind: kind, pageSectionKey: `T01.${kind}`, name: '옛 이름',
  }));
  const names = getAiDraftSectionNames(blocks);
  assert.deepEqual(names, ['후기', '상품 소개', '궁금한 점', '원단 설명']);
  assert.equal(formatAiDraftSectionNames(names), '후기, 상품 소개 외 2곳');
  assert.equal(formatAiDraftSectionNames(names.slice(0, 3)), '후기, 상품 소개, 궁금한 점');
  assert.equal(formatAiDraftSectionNames(names.slice(0, 1)), '후기');
  assert.deepEqual(getAiDraftSectionNames([block([draft()], { pageSectionKind: undefined, name: '직접 구성' })]), ['직접 구성']);
});
