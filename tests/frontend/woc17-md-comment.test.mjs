import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';

const seller = '  Cotton 셔츠를 추천해요.  \n\n  두 번째 줄\n세 번째 줄\n네 번째 줄  \n';
const ai = 'AI 첫 줄\n\nAI 둘째 줄\nAI 셋째 줄\nAI 넷째 줄';
const inputFor = (templateId, copywriting, mdComment, body = ai) => ({
  storyboard: [{ id: 'intro', taxonomyVersion: 4, source: 'text', sectionId: 'intro',
    pageSectionKey: `${templateId}.intro`, pageSectionKind: 'intro', pageDesignId: 'T01-D07', slotKey: null }],
  product: { name: '코튼 셔츠', colors: [] }, analysis: { mdComment }, copywriting,
  copyResults: [{ blockId: 'intro', texts: [{ role: 'body', text: body }] }],
});
const bodies = blocks => blocks.flatMap(block => block.elements).filter(element => element.copyRole === 'body');
const parityInputs = [];

for (const templateId of ['emo', 'show']) for (const copywriting of [true, false]) {
  for (const original of [seller, '앞줄\r\n\r\n뒤쪽  \t줄\r\n']) {
    const input = inputFor(templateId, copywriting, original); parityInputs.push(input);
    test(`${templateId} seller MD remains exact in one body with copywriting=${copywriting} and ${original.includes('\r') ? 'CRLF' : 'LF'}`, () => {
      const elements = bodies(assemblePage(input));
      assert.deepEqual(elements.map(element => element.text), [original]);
      assert.equal(elements[0].aiDraft, undefined);
      assert.equal(elements[0].paragraphCount, 1);
      assert.equal(input.analysis.mdComment, original);
    });
  }
  for (const absent of [undefined, null, '', ' \t\n ']) for (const body of [ai, '']) {
    const input = inputFor(templateId, copywriting, absent, body); parityInputs.push(input);
    test(`${templateId} absent MD ${JSON.stringify(absent)} keeps ${body ? 'AI' : 'empty'} fallback with copywriting=${copywriting}`, () => {
      const elements = bodies(assemblePage(input));
      assert.deepEqual(elements.map(element => element.text), copywriting && body
        ? ['AI 첫 줄', 'AI 둘째 줄', 'AI 셋째 줄\nAI 넷째 줄'] : ['']);
      assert.ok(elements.every(element => element.aiDraft));
      if (!copywriting || !body) assert.equal(elements[0].placeholder, 'MD 코멘트');
    });
  }
}

test('MD priority and exact multiline content match across JavaScript and Python engines', () => {
  const script = `import json,sys
from app.agents.page_template_assembler import assemble_v4
values=json.load(sys.stdin)
json.dump([assemble_v4(v['storyboard'],[],v['copyResults'],v['product'],v['copywriting'],analysis=v['analysis']) for v in values],sys.stdout,ensure_ascii=False)`;
  const result = spawnSync('server/.venv/bin/python', ['-c', script], { input: JSON.stringify(parityInputs),
    encoding: 'utf8', maxBuffer: 8 * 1024 * 1024, env: { ...process.env, PYTHONPATH: 'server', PYTHONDONTWRITEBYTECODE: '1' } });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), parityInputs.map(input => assemblePage(input)));
});
