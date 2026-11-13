import test from 'node:test';
import assert from 'node:assert/strict';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { decorateGenBlocks, fillGenBlocks, mergeServerBlocks, patchTemplateElement } from '../../src/lib/editorWaitSkeleton.js';

const memo = '  첫 줄  \n\n둘째 줄\n\t셋째 줄  \n';
const inputFor = (templateId = 'emo', mdComment = memo, body = '') => ({
  storyboard: [{ id: 'intro', taxonomyVersion: 4, source: 'text', sectionId: 'intro',
    pageSectionKey: `${templateId}.intro`, pageSectionKind: 'intro', pageDesignId: templateId === 'show' ? 'T06-D01' : 'T01-D07', slotKey: null }],
  product: { name: '셔츠', colors: [] }, analysis: { mdComment }, copywriting: true,
  copyResults: [{ blockId: 'intro', texts: [{ role: 'body', text: body }] }],
});
const job = text => ({ status: 'running', cuts: {}, live: [],
  copy: text == null ? {} : { intro: [{ role: 'body', text }] } });
const bodies = blocks => blocks.flatMap(block => block.elements).filter(element => element.copyRole === 'body');

for (const templateId of ['emo', 'show']) for (const [cached, incoming] of [
  ['pending', null], ['seller-event', memo], ['ai-event', 'AI가 다시 쓴 문장'],
]) {
  test(`${templateId} seller MD survives ${cached}, live copy and completed assembly unchanged`, () => {
    const input = inputFor(templateId);
    const assembled = assemblePage(input);
    assert.deepEqual(bodies(assembled).map(element => element.text), [memo]);
    const waiting = decorateGenBlocks(assembled, job(incoming), {});
    assert.deepEqual(bodies(waiting).map(element => element.text), [memo]);
    const filled = fillGenBlocks(waiting, job('늦게 온 AI 첫 문단\nAI 둘째 문단'));
    assert.deepEqual(bodies(filled).map(element => element.text), [memo]);
    const complete = mergeServerBlocks(filled, assemblePage(input));
    assert.deepEqual(bodies(complete).map(element => element.text), [memo]);
    assert.equal(bodies(complete)[0].aiDraft, undefined);
  });
}

for (const templateId of ['emo', 'show']) for (const edited of ['직접 고친 첫 줄\n\n둘째 줄  ', '']) {
  test(`${templateId} manual seller MD ${edited ? 'edit' : 'deletion'} wins over copy events and completion`, () => {
    const input = inputFor(templateId);
    const waiting = decorateGenBlocks(assemblePage(input), job(memo), {});
    const body = bodies(waiting)[0];
    const local = waiting.map(block => block.pageSectionKind === 'intro'
      ? patchTemplateElement(block, body.id, { text: edited }) : block);
    const filled = fillGenBlocks(local, job('늦게 온 AI 본문'));
    const complete = mergeServerBlocks(filled, assemblePage(input));
    assert.deepEqual(bodies(filled).map(element => element.text), [edited]);
    assert.deepEqual(bodies(complete).map(element => element.text), [edited]);
  });
}

for (const templateId of ['emo', 'show']) test(`${templateId} absent seller MD keeps cached/live AI body and final merge`, () => {
  const initial = '초안 하나\n초안 둘\n초안 셋\n초안 넷';
  const incoming = '수신 하나\n\n수신 둘\n수신 셋\n수신 넷';
  const final = '최종 하나\n최종 둘\n최종 셋\n최종 넷';
  const waiting = decorateGenBlocks(assemblePage(inputFor(templateId, '', initial)), job(incoming), {});
  assert.deepEqual(bodies(waiting).map(element => element.text), templateId === 'show' ? [incoming] : ['수신 하나', '수신 둘', '수신 셋\n수신 넷']);
  assert.ok(bodies(waiting).every(element => element.aiDraft));
  const filled = fillGenBlocks(waiting, job(final));
  assert.deepEqual(bodies(filled).map(element => element.text), templateId === 'show' ? [final] : ['최종 하나', '최종 둘', '최종 셋\n최종 넷']);
  const complete = mergeServerBlocks(filled, assemblePage(inputFor(templateId, '', final)));
  assert.deepEqual(bodies(complete).map(element => element.text), templateId === 'show' ? [final] : ['최종 하나', '최종 둘', '최종 셋\n최종 넷']);
});
