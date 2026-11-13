import test from 'node:test';
import assert from 'node:assert/strict';
import { assemblePage, collectOverlayRegions } from '../../src/lib/pageTemplates/assemble.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { selectTemplateStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { getTemplate, pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { visibleSectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';

const memo = '첫째 줄\n\n둘째 줄의 소개를 그대로 담아요.';
const product = { name: '코튼 셔츠', clothingType: 'top', colors: [{ id: 'base', name: '아이보리', isBase: true, swatchId: 'ivory', images: [] }] };
function inputFor(templateId, mdComment = memo, body = 'AI 소개 본문') {
  const analysis = { pageStyle: selectTemplateStyle(templateId, '2026-11-13T00:00:00Z'), targetGenders: ['women'], mdComment,
    materials: [{ name: '면', ratio: 100 }], fit: '스탠다드핏' };
  const storyboard = seedStoryboard(product, analysis, { projectId: 'showroom-md' });
  const intro = storyboard.find(block => block.pageSectionKind === 'intro');
  const copyResults = [{ blockId: intro.id, texts: [
    { role: 'body', text: body }, { role: 'summary.0', text: '첫 특징' }, { role: 'summary.1', text: '둘째 특징' },
    { role: 'summary.2', text: '셋째 특징' }, { role: 'summary.line', text: '기존 요약 한 줄' },
  ] }];
  return { storyboard, product, analysis, copyResults, copywriting: true };
}
const introOf = page => page.find(block => block.pageSectionKind === 'intro');

for (const templateId of ['emo', 'show']) test(`WOC18 actual ${templateId} default intro includes seller MD although hidden from the storyboard outline`, () => {
  const input = inputFor(templateId);
  const source = input.storyboard.find(block => block.pageSectionKind === 'intro');
  assert.equal(source.pageDesignId, templateId === 'show' ? 'T06-D01' : 'T01-D07');
  assert.equal(visibleSectionRuns(input.storyboard).some(run => run.kind === 'intro'), false);
  const body = introOf(assemblePage(input)).elements.filter(element => element.copyRole === 'body');
  assert.equal(body.length, 1);
  assert.equal(body[0].text, memo);
  assert.equal(body[0].sourceBlockId, source.id);
});

test('WOC18 showroom Comment and body lead the padded summary panel with inherited inverted ink', () => {
  const block = introOf(assemblePage(inputFor('show')));
  const label = block.elements.find(element => element.copyRole === 'label');
  const body = block.elements.find(element => element.copyRole === 'body');
  const facts = block.elements.filter(element => element.copyRole === 'facts');
  assert.ok(label && body && facts.length);
  assert.equal(label.text, 'COMMENT');
  assert.equal(label.style.size, 32);
  assert.equal(body.style.size, 25);
  assert.equal(label.style.color, catalog.neutralColors.invertMuted);
  assert.equal(body.style.color, getTemplate('show').colors.invertInk);
  assert.equal(block.bg, getTemplate('show').colors.invertBg);
  assert.equal(label.y, 56, 'Comment starts at the panel top padding');
  for (const element of [label, body]) {
    assert.deepEqual([element.x, element.w], [112, 776]);
    assert.equal(element.x + element.w / 2, 500);
    assert.equal(element.style.align, 'center');
  }
  assert.equal(body.y, label.y + label.h + 12);
  assert.equal(facts[0].y, body.y + body.h + 30);
  assert.deepEqual([facts[0].x, facts[0].w], [56, 888]);
  assert.deepEqual(facts.map(element => element.text), ['COLOR', '아이보리', 'FABRIC', '면 100%', 'FIT', '스탠다드핏']);
  const summary = block.elements.filter(element => /^summary\.\d+$/.test(element.copyRole || ''));
  assert.deepEqual(summary.map(element => element.text), ['첫 특징', '둘째 특징', '셋째 특징']);
  assert.ok(summary.every(element => element.y >= facts.at(-1).y + facts.at(-1).h));
  assert.equal(block.elements.find(element => element.copyRole === 'summary.line').text, '기존 요약 한 줄');
});

test('WOC18 empty showroom MD still renders Comment and the existing empty AI body slot', () => {
  const block = introOf(assemblePage(inputFor('show', '', '')));
  const label = block.elements.find(element => element.copyRole === 'label');
  const body = block.elements.find(element => element.copyRole === 'body');
  assert.equal(label?.text, 'COMMENT');
  assert.equal(body?.text, '');
  assert.equal(body.placeholder, 'MD 코멘트');
  assert.equal(body.aiDraft, true);
});

for (const templateId of ['emo', 'show']) test(`WOC18 ${templateId} MD body is outside photo contrast and title-plate sampling`, () => {
  const input = inputFor(templateId), empty = { ...input, analysis: { ...input.analysis, mdComment: '' } };
  const plans = collectOverlayRegions(input);
  assert.deepEqual(plans, collectOverlayRegions(empty));
  assert.ok(plans.every(plan => !String(plan.key).includes('intro')));
  assert.equal(introOf(assemblePage(input)).elements.some(element => element.contrastLines || element.titlePlate), false);
});
