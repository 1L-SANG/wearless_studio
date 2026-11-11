import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import details from '../../src/mock/detailRecommendationsFixture.json' with { type: 'json' };
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignChoices, sectionDesignPhotoCount } from '../../src/lib/pageTemplates/designChoices.js';
import { sectionDesignAxes } from '../../src/lib/pageTemplates/designAxes.js';
import { changeTemplateSectionDesign, addTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { sectionDesignPreviewSample } from '../../src/lib/pageTemplates/designPreview.js';

const product = { name: '테스트 니트', clothingType: 'top', colors: [
  { id: 'base', isBase: true, name: '블루', swatchId: 'blue', images: ['Front', 'Back', 'Detail'].map(slot => ({ slot, src: `/${slot}.jpg` })) },
  { id: 'cream', name: '아이보리', swatchId: 'ivory', images: [{ slot: 'Front', src: '/cream.jpg' }] },
] };
const analysis = templateId => ({ targetGenders: ['women'], detailRecommendations: details,
  pageStyle: { ...defaultPageStyle(), templateId } });
const board = (templateId = 'emo', p = product) => seedStoryboard(p, analysis(templateId));
const runOf = (blocks, kind, usage) => sectionRuns(blocks).find(run => run.kind === kind && (!usage || run.usage === usage));
const ids = (blocks, kind, usage, p = product) => sectionDesignChoices(runOf(blocks, kind, usage), { storyboard: blocks, product: p }).map(d => d.id);
const valid = blocks => { assert.equal(validateStoryboardV4(blocks), blocks); return blocks; };

for (const template of ['emo', 'show']) test(`WOC6 ${template} defaults and visible candidate sets are the approved list`, () => {
  const blocks = valid(board(template));
  assert.deepEqual(ids(blocks, 'first'), template === 'emo' ? ['P01-D09', 'P01-SHOW-01'] : ['P01-SHOW-01', 'P01-D09']);
  assert.deepEqual(new Set(ids(blocks, 'fabric')), new Set(['T03-D01', 'T03-D06']));
  assert.deepEqual(new Set(ids(blocks, 'color_product')), new Set(['CP-COLUMN-01', 'CP-EMO-01']));
  assert.deepEqual(new Set(ids(blocks, 'points')), new Set(['PT-COLUMN-01', 'P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01']));
  assert.equal(runOf(blocks, 'points').designId, 'PT-COLUMN-01');
  assert.equal(runOf(blocks, 'color_product').designId, 'CP-COLUMN-01');
  assert.ok(sectionRuns(blocks).filter(run => run.kind === 'wear' && run.usage === 'fit').every(run => run.designId === 'WEAR-FIT-COLUMN-01'));
  assert.ok(!blocks.some(b => b.pageSectionKind === 'sns'));
  const daily = runOf(blocks, 'wear', 'styling') ? blocks : addTemplateSection(blocks, product, analysis(template), 'wear', 'styling');
  const dailyCount = runOf(daily, 'wear', 'styling').blocks.filter(b => b.source !== 'text').length;
  const frames = ['COLUMN', '2COL', 'HERO'].slice(0, Math.min(3, dailyCount))
    .flatMap(layout => Array.from({ length: Math.min(3, dailyCount) }, (_, index) => `WEAR-STYLING-FRAME-${layout}-0${index + 1}`));
  assert.deepEqual(new Set(ids(daily, 'wear', 'styling')), new Set(['P02-D01', 'WEAR-STYLING-2COL-01', ...(dailyCount >= 3 ? ['WEAR-STYLING-HERO-01'] : []), ...frames]));
  assert.deepEqual(new Set(ids(blocks, 'wear', 'fit')), new Set(['WEAR-FIT-COLUMN-01', 'WEAR-FIT-2COL-01', 'WEAR-FIT-HERO-01']));
  for (const [kind, id] of [['review', 'I09-D06'], ['faq', 'T08-D07']]) {
    const withKind = runOf(blocks, kind) ? blocks : addTemplateSection(blocks, product, analysis(template), kind);
    assert.deepEqual(ids(withKind, kind), [id]); valid(withKind);
  }
});

for (const usage of ['styling', 'fit']) test(`WOC6 ${usage} minimum photos refer to actual cards, including loose additions`, () => {
  const original = board(), run = runOf(original, 'wear', usage);
  for (const count of [1, 2, 3]) {
    const chosen = run.blocks.filter(b => b.source !== 'text').slice(0, count).map((b, i) => i === count - 1 ? { ...b, slotKey: null } : b);
    const blocks = original.flatMap(b => b.pageSectionKey === run.key ? [] : [b]); blocks.splice(run.start, 0, ...chosen);
    const current = runOf(blocks, 'wear', usage), choices = sectionDesignChoices(current, { storyboard: blocks, product });
    assert.equal(choices.length, usage === 'styling' ? count * (count + 1) : count); assert.ok(choices.every(d => sectionDesignPhotoCount(current, d.id, blocks) === count));
    assert.equal(sectionDesignAxes(current, { storyboard: blocks, product }).layouts.length, count);
    assert.equal(sectionDesignAxes(current, { storyboard: blocks, product }).decorations.length, usage === 'styling' ? 2 : 1);
    valid(blocks);
  }
});

test('WOC6 color grid needs at least two sale colors and back presence follows source photos', () => {
  for (const template of ['emo', 'show']) for (const count of [1, 2, 3]) for (const back of [false, true]) {
    const colors = Array.from({ length: count }, (_, i) => ({ ...product.colors[i % 2], id: `c${i}`, isBase: i === 0,
      images: [{ slot: 'Front', src: '/front.jpg' }, ...(i === 0 && back ? [{ slot: 'Back', src: '/back.jpg' }] : [])] }));
    const p = { ...product, colors }, blocks = valid(board(template, p)), run = runOf(blocks, 'color_product');
    assert.equal(ids(blocks, 'color_product', undefined, p).length, count > 1 ? 2 : 1);
    assert.equal(run.blocks.filter(b => b.direction === 'back').length, Number(back));
    assert.equal(sectionDesignPhotoCount(run, run.designId, blocks), count + Number(back));
  }
});

test('WOC6 axis changes patch only the run design, keep cuts and roundtrip undo snapshots', () => {
  const initial = board(), key = runOf(initial, 'points').key;
  let blocks = structuredClone(initial);
  const count = uniqueGenerationCutCount(blocks, product), eraseDesign = list => list.map(({ pageDesignId, ...b }) => b);
  for (const [axis, id, expected] of [['decorations', 'card', 'PT-COLUMN-CARD-01'], ['layouts', 'below', 'PT-BELOW-CARD-01'],
    ['decorations', 'basic', 'P06-D01'], ['layouts', 'column', 'PT-COLUMN-01']]) {
    const run = runOf(blocks, 'points'), axes = sectionDesignAxes(run, { storyboard: blocks, product });
    const option = axes[axis].find(o => o.id === id); assert.equal(option.designId, expected);
    blocks = valid(changeTemplateSectionDesign(blocks, key, option.designId, { product }));
    assert.equal(uniqueGenerationCutCount(blocks, product), count); assert.deepEqual(eraseDesign(blocks), eraseDesign(initial));
    assert.ok(blocks.filter(b => b.pageSectionKey === key).every(b => b.pageDesignId === expected));
  }
  assert.deepEqual(blocks, initial);
});

test('WOC6 text point fallback and stored SNS remain valid even though hidden from choices', () => {
  const initial = board(), run = runOf(initial, 'points');
  const textOnly = initial.filter(b => b.pageSectionKey !== run.key || b.source === 'text').map(b => b.pageSectionKey === run.key
    ? { ...b, pointItems: [] } : b);
  assert.deepEqual(ids(textOnly, 'points'), ['PT-EMO-TEXT']); valid(textOnly);
  const fixture = JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json')).cases.find(c => c.input.storyboard.some(b => b.pageSectionKind === 'sns'));
  assert.ok(fixture); const saved = structuredClone(fixture.input);
  assert.ok(assemblePage(saved).some(b => b.pageSectionKind === 'sns'));
  assert.deepEqual(ids(saved.storyboard, 'sns', undefined, saved.product), []);
});

test('WOC6 layout samples are canonical groups only in the preview copy', () => {
  const blocks = board(), before = structuredClone(blocks), run = runOf(blocks, 'wear', 'styling');
  const sample = sectionDesignPreviewSample(blocks, run.key), photos = runOf(sample, 'wear', 'styling').blocks.filter(b => b.source !== 'text');
  assert.equal(photos.length, 4); assert.equal(new Set(photos.map(b => b.spaceGroupId)).size, 1);
  assert.deepEqual(blocks, before);
  const sizes = id => {
    const changed = changeTemplateSectionDesign(sample, run.key, id, { product });
    return assemblePage({ storyboard: changed, product, analysis: analysis('emo') }).filter(b => b.pageSectionKey === run.key)
      .flatMap(b => b.elements).filter(e => e.type === 'image').map(e => e.w);
  };
  assert.notDeepEqual(sizes('WEAR-STYLING-HERO-01'), sizes('WEAR-STYLING-2COL-01'));
});

test('WOC6 supplied section-lead descriptions fill every part card in shared fixtures', () => {
  const fixtures = JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json')).cases;
  for (const template of ['emo', 'show']) for (const id of ['P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01']) {
    const input = fixtures.find(c => c.name === `woc6-${template}-${id}`).input;
    const descriptions = assemblePage(input).filter(b => b.pageSectionKind === 'points').flatMap(b => b.elements)
      .filter(e => /^items\.\d+\.desc$/.test(e.copyRole || ''));
    assert.deepEqual(descriptions.map(e => e.text), ['목선 마감', '여밈 마감', '소맷단 마감']);
    assert.ok(descriptions.every(e => !e.placeholder));
  }
});
