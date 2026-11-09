import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { getTemplate, pageTemplatesCatalog, validateCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignChoices, sectionDesignPhotoCount } from '../../src/lib/pageTemplates/designChoices.js';
import { changeTemplateSectionDesign, removeTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';

const product = { name: '실루엣이 부드러운 데일리 셔츠', clothingType: 'top', colors: [{ id: 'base', isBase: true, name: '아이보리' }] };
const analysis = { targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId: 'show' } };
const board = () => seedStoryboard(product, analysis, { projectId: 'woc3-first' });
const input = (storyboard, p = product) => ({ storyboard, product: p, analysis,
  cutResults: storyboard.filter(b => b.source === 'ai').map(b => ({ blockId: b.id, imageUrl: `photo:${b.id}`, width: 1200, height: 1600 })) });
const opening = args => assemblePage(args).filter(block => block.pageSectionKind === 'first');
const elements = blocks => blocks.flatMap(block => block.elements);

test('E10 new showroom seed renders a single signature hero followed only by a small product name', () => {
  assert.equal(getTemplate('show').sections[0].designId, 'P01-SHOW-01');
  const seeded = assignGenerationExamples(board(), { product, gender: 'women', catalog: [], projectId: 'woc3-first' }).blocks;
  const first = seeded.filter(b => b.pageSectionKind === 'first');
  assert.equal(first.length, 1); assert.equal(first[0].slotKey, 'first.hero');
  assert.match(first[0].exampleId, /^sig_/); assert.equal(first[0].cutType, 'horizon');
  assert.equal(first[0].shot, 'medium'); assert.equal(first[0].direction, 'front');
  const out = elements(opening(input(seeded))), images = out.filter(el => el.type === 'image');
  assert.equal(images.length, 1); assert.equal(images[0].w, 1000);
  const text = out.filter(el => el.type === 'text');
  assert.deepEqual(text.map(el => el.copyRole), ['name']);
  assert.equal(text[0].text, product.name); assert.ok(text[0].y >= images[0].y + images[0].h);
  assert.equal(text[0].style.size, 28); assert.equal(text[0].h, text[0].style.lineHeight);
  assert.equal(validateStoryboardV4(seeded), seeded);
});

test('E10 a long product name still occupies one line and both assemblers agree exactly', () => {
  const p = { ...product, name: '차분하게 떨어지는 실루엣과 편안한 착용감을 갖춘 매일 입기 좋은 데일리 셔츠' };
  const args = input(board(), p), js = opening(args), name = elements(js).find(el => el.copyRole === 'name');
  assert.ok(name && name.style.size <= 28); assert.equal(name.h, name.style.lineHeight);
  assert.equal(name.text, p.name);
  const py = spawnSync('/Users/daily/Documents/wearless_studio/server/.venv/bin/python', ['-c',
    "import sys,json;sys.path[:0]=['server','server/tests'];from page_template_cases import assemble_input;print(json.dumps(assemble_input(json.load(sys.stdin)),ensure_ascii=False))"],
  { input: JSON.stringify(args), encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  assert.equal(py.status, 0, py.stderr);
  assert.deepEqual(JSON.parse(py.stdout), assemblePage(args));
});

test('E10 borrowed ghost opening is offered without another generated cut and design roundtrip restores the board', () => {
  const initial = board(), snapshot = structuredClone(initial), run = sectionRuns(initial)[0];
  const choices = sectionDesignChoices(run, { storyboard: initial, product });
  assert.ok(choices.some(d => d.id === 'P01-SHOW-01')); assert.ok(choices.some(d => d.id === 'P01-D01'));
  assert.equal(sectionDesignPhotoCount(run, 'P01-SHOW-01', initial), 1);
  assert.equal(sectionDesignPhotoCount(run, 'P01-D01', initial), 2, 'visual positions remain separate from generation eligibility');
  const changed = changeTemplateSectionDesign(initial, run.key, 'P01-D01', { product });
  assert.notEqual(changed, initial); assert.equal(validateStoryboardV4(changed), changed);
  assert.equal(uniqueGenerationCutCount(changed), uniqueGenerationCutCount(initial));
  assert.deepEqual(changed.map(b => b.id), initial.map(b => b.id));
  assert.equal(elements(opening(input(changed))).filter(el => el.type === 'image').length, 2);
  const restored = changeTemplateSectionDesign(changed, run.key, 'P01-SHOW-01', { product });
  assert.deepEqual(restored, snapshot); assert.deepEqual(initial, snapshot);
});

test('E10 a ghost alternative cannot silently create an extra cut when its borrowed product source is absent', () => {
  const initial = board(), productRun = sectionRuns(initial).find(run => run.kind === 'color_product');
  const removed = removeTemplateSection(initial, productRun.key, { product }), run = sectionRuns(removed)[0];
  assert.equal(validateStoryboardV4(removed), removed);
  assert.ok(sectionDesignChoices(run, { storyboard: removed, product }).some(d => d.id === 'P01-SHOW-01'));
  assert.ok(!sectionDesignChoices(run, { storyboard: removed, product }).some(d => d.id === 'P01-D01'));
  assert.equal(changeTemplateSectionDesign(removed, run.key, 'P01-D01', { product }), removed);
});

test('E10 design actions without a product option infer the same base color as candidate eligibility', () => {
  const initial = board();
  const changed = changeTemplateSectionDesign(initial, 'show.first', 'P01-D01');
  assert.ok(changed.some(block => block.pageDesignId === 'P01-D01'));
  assert.equal(uniqueGenerationCutCount(changed), uniqueGenerationCutCount(initial));
  assert.deepEqual(changed.map(block => block.id), initial.map(block => block.id));
  assert.equal(validateStoryboardV4(changed), changed);
});

test('E10 common opening candidates allow a zero-cost borrowed ghost but SNS still requires the same visual position count', () => {
  const emo = seedStoryboard(product, { ...analysis, pageStyle: { ...analysis.pageStyle, templateId: 'emo' } });
  assert.ok(sectionDesignChoices(sectionRuns(emo)[0], { storyboard: emo, product }).some(d => d.id === 'P01-D01'));
  const bad = structuredClone(pageTemplatesCatalog);
  bad.designs['P01-SHOW-01'] = { ...bad.designs['P01-D11'], id: 'P01-SHOW-01', layout: [{ type: 'text', copyRole: 'name', singleLine: 'yes' }] };
  assert.throws(() => validateCatalog(bad), /single line/);
});
