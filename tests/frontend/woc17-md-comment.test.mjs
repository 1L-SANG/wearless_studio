import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';

const seller = '  Cotton 셔츠를 추천해요.  \n\n  두 번째 줄\n세 번째 줄\n네 번째 줄  \n';
const ai = 'AI 첫 줄\n\nAI 둘째 줄\nAI 셋째 줄\nAI 넷째 줄';
const inputFor = (templateId, mdComment, body = ai) => ({
  storyboard: [{ id: 'intro', taxonomyVersion: 4, source: 'text', sectionId: 'intro',
    pageSectionKey: `${templateId}.intro`, pageSectionKind: 'intro', pageDesignId: templateId === 'show' ? 'T06-D01' : 'T01-D07', slotKey: null }],
  product: { name: '코튼 셔츠', colors: [] }, analysis: { mdComment }, copywriting: true,
  copyResults: [{ blockId: 'intro', texts: [{ role: 'body', text: body }] }],
});
const bodies = blocks => blocks.flatMap(block => block.elements).filter(element => element.copyRole === 'body');
const parityInputs = [];

for (const templateId of ['emo', 'show']) {
  for (const original of [seller, '앞줄\r\n\r\n뒤쪽  \t줄\r\n']) {
    const input = inputFor(templateId, original); parityInputs.push(input);
    test(`${templateId} seller MD remains exact in one body with copy enabled and ${original.includes('\r') ? 'CRLF' : 'LF'}`, () => {
      const elements = bodies(assemblePage(input));
      assert.deepEqual(elements.map(element => element.text), [original]);
      assert.equal(elements[0].aiDraft, undefined);
      assert.equal(elements[0].paragraphCount, templateId === 'emo' ? 1 : undefined);
      if (templateId === 'show') {
        assert.deepEqual([elements[0].x, elements[0].y, elements[0].w], [112, 107, 776]);
        assert.equal(elements[0].style.align, 'center');
      }
      assert.equal(input.analysis.mdComment, original);
    });
  }
  for (const absent of [undefined, null, '', ' \t\n ']) for (const body of [ai, '']) {
    const input = inputFor(templateId, absent, body); parityInputs.push(input);
    test(`${templateId} absent MD ${JSON.stringify(absent)} keeps ${body ? 'AI' : 'empty'} fallback with copy enabled`, () => {
      const elements = bodies(assemblePage(input));
      assert.deepEqual(elements.map(element => element.text), body ? templateId === 'show' ? [body]
        : ['AI 첫 줄', 'AI 둘째 줄', 'AI 셋째 줄\nAI 넷째 줄'] : ['']);
      assert.ok(elements.every(element => element.aiDraft));
      if (!body) assert.equal(elements[0].placeholder, 'MD 코멘트');
    });
  }
}

const longSeller = 'Cotton '.repeat(27) + '!';
const longInput = inputFor('show', longSeller);
parityInputs.push(longInput);
test('show 190-character MD keeps the raw text and wraps within the padded panel', () => {
  const [body] = bodies(assemblePage(longInput));
  assert.equal(body.text, longSeller);
  assert.deepEqual([body.x, body.y, body.w], [112, 107, 776]);
  // Eight complete words fit per 776px line, so this 190-character comment needs four lines.
  assert.equal(longSeller.length, 190);
  assert.equal(body.h, 160);
  assert.equal(body.style.lineHeight, 40);
  assert.equal(body.style.align, 'center');
});

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
