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

test('WOC23 showroom specification board leads with the product name and seller MD', () => {
  const block = introOf(assemblePage(inputFor('show')));
  const name = block.elements.find(el => el.copyRole === 'name'), body = block.elements.find(el => el.copyRole === 'body');
  assert.equal(name.text, product.name); assert.equal(name.style.size, 54); assert.equal(name.style.weight, 700);
  assert.equal(body.style.size, 30); assert.equal(body.text, memo);
  assert.equal(block.bg, catalog.canvasColor);
  assert.deepEqual([name.x, name.w, name.style.align], [56, 888, 'left']);
  assert.equal(body.y, name.y + name.h + 40);
  assert.deepEqual(block.elements.filter(el => el.copyRole === 'facts').map(el => el.text), ['COLOR', '아이보리', 'FABRIC', '면 100%', 'FIT', '스탠다드핏']);
  assert.equal(block.elements.some(el => /^summary\./.test(el.copyRole || '')), false);
});

test('WOC23 empty showroom MD keeps its product title and empty AI body slot', () => {
  const block = introOf(assemblePage(inputFor('show', '', '')));
  const label = block.elements.find(element => element.copyRole === 'label');
  const body = block.elements.find(element => element.copyRole === 'body');
  assert.equal(label, undefined);
  assert.equal(block.elements.find(el => el.copyRole === 'name').text, product.name);
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
