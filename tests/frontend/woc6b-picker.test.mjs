import test from 'node:test';
import assert from 'node:assert/strict';
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { addTemplateSection, changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { sectionDesignAxes } from '../../src/lib/pageTemplates/designAxes.js';
import { sectionDesignChoices, sectionDesignPhotoCount, defaultSectionDesignId, currentSectionDesignId } from '../../src/lib/pageTemplates/designChoices.js';
import { sectionDesignPreviewSample } from '../../src/lib/pageTemplates/designPreview.js';
import { templateSectionName } from '../../src/lib/pageTemplates/slots.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';

const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, name: '블루', swatchId: 'blue', images: [{ slot: 'Front', src: '/front.jpg' }] }] };
const analysis = { targetGenders: ['women'], pageStyle: defaultPageStyle() };
const catalogs = { detailProduct: product, analysis, genExamples: [] };
const daily = blocks => sectionRuns(blocks).find(run => run.kind === 'wear' && run.usage === 'styling');
const board = count => {
  const original = seedStoryboard(product, analysis), run = daily(original), photo = run.blocks.find(b => b.source !== 'text');
  const photos = Array.from({ length: count }, (_, i) => ({ ...photo, id: `test-wear-${i}`, slotKey: `wear_styling.g${i + 1}`, shot: i < 2 ? 'medium' : 'full' }));
  return [...original.slice(0, run.start), ...photos, ...original.slice(run.end)];
};
const axes = blocks => sectionDesignAxes(daily(blocks), { storyboard: blocks, product });
const choices = blocks => sectionDesignChoices(daily(blocks), { storyboard: blocks, product });

for (const count of [1, 2, 3, 4]) test(`WOC6B frame count choices and default fit ${count} actual photos`, () => {
  const blocks = board(count), run = daily(blocks), before = structuredClone(blocks);
  const entry = axes(blocks).decorations.find(d => d.id === 'frame'); assert.ok(entry);
  const expectedCount = Math.min(2, count);
  assert.equal(catalog.designs[entry.designId].framePhotos, expectedCount);
  const framed = changeTemplateSectionDesign(blocks, run.key, entry.designId, { product });
  assert.deepEqual(axes(framed).framePhotoChoices.map(c => c.count), Array.from({ length: Math.min(3, count) }, (_, i) => i + 1));
  assert.equal(axes(framed).framePhotoChoices.find(c => c.selected).count, expectedCount);
  assert.deepEqual(axes(framed).layouts.map(option => option.id), count === 4 ? ['column', 'twoColumn'] : ['column']);
  assert.ok(axes(framed).layouts.every(option => catalog.designs[option.designId].framePhotos === expectedCount));
  assert.ok(choices(framed).every(d => !d.framePhotos || d.framePhotos <= count));
  assert.ok(choices(framed).every(d => sectionDesignPhotoCount(run, d.id, framed) === count));
  assert.equal(uniqueGenerationCutCount(framed, product), uniqueGenerationCutCount(blocks, product));
  assert.equal(validateStoryboardV4(framed), framed); assert.deepEqual(blocks, before);
});

test('WOC6B frame choices preserve requested counts, fall back to column and retain stored photos', () => {
  const initial = board(4), key = daily(initial).key;
  const snapshot = structuredClone(initial), cutCount = uniqueGenerationCutCount(initial, product);
  const withoutDesign = values => values.map(({ pageDesignId, ...block }) => block);
  let blocks = changeTemplateSectionDesign(initial, key, axes(initial).decorations.find(d => d.id === 'frame').designId, { product });
  const choose = (field, id) => {
    const option = axes(blocks)[field].find(o => o.id === id || o.count === id); assert.ok(option, `${field}:${id}`);
    blocks = changeTemplateSectionDesign(blocks, key, option.designId, { product });
    validateStoryboardV4(blocks);
    assert.ok(blocks.filter(block => block.pageSectionKey === key).every(block => block.pageDesignId === option.designId));
    assert.equal(uniqueGenerationCutCount(blocks, product), cutCount);
    assert.deepEqual(withoutDesign(blocks), withoutDesign(initial));
  };
  choose('layouts', 'twoColumn'); assert.equal(daily(blocks).designId, 'WEAR-STYLING-FRAME-2COL-02');
  choose('framePhotoChoices', 3);
  assert.equal(daily(blocks).designId, 'WEAR-STYLING-FRAME-COLUMN-03');
  assert.deepEqual(axes(blocks).layouts.map(option => option.id), ['column']);
  choose('framePhotoChoices', 1);
  choose('layouts', 'heroPair'); assert.equal(daily(blocks).designId, 'WEAR-STYLING-FRAME-HERO-01');
  choose('framePhotoChoices', 2); assert.equal(daily(blocks).designId, 'WEAR-STYLING-FRAME-COLUMN-02');
  choose('framePhotoChoices', 1); choose('layouts', 'heroPair');
  choose('decorations', 'basic'); assert.equal(axes(blocks).currentLayoutId, 'heroPair'); assert.deepEqual(axes(blocks).framePhotoChoices, []);
  choose('decorations', 'frame'); assert.equal(daily(blocks).designId, 'WEAR-STYLING-FRAME-COLUMN-02');
  assert.deepEqual(initial, snapshot, 'the original undo snapshot retains every source, photo and copy field');
});

test('WOC6B studio and color/product never offer frame decoration', () => {
  const blocks = seedStoryboard(product, analysis);
  for (const run of sectionRuns(blocks).filter(r => r.kind === 'color_product' || r.usage === 'fit')) {
    const value = sectionDesignAxes(run, { storyboard: blocks, product });
    assert.ok(value.decorations.every(d => d.id !== 'frame'));
    assert.deepEqual(value.framePhotoChoices, []);
  }
});

test('WOC6B actual section picker shows frame-count pills only for frame and saves the selected combination', async () => {
  const hooks = hookHarness(), chosen = [];
  const Picker = await loadStoryboardComponent('StoryboardSectionPicker.jsx', 'StoryboardSectionPicker', { ...hooks.hooks, sectionDesignAxes, pageTemplatesCatalog: catalog, sectionRuns, addTemplateSection, changeTemplateSectionDesign, sectionDesignChoices, defaultSectionDesignId, currentSectionDesignId, sectionDesignPreviewSample, templateSectionName, Icon: 'icon', StoryboardPreview: 'preview', NEW_SECTION_DRAG_MIME: 'application/x-section' });
  let blocks = board(2), runKey = daily(blocks).key;
  const before = structuredClone(blocks), cutCount = uniqueGenerationCutCount(blocks, product);
  const render = () => hooks.render(Picker, { mode: 'design', blocks, runKey, catalogs, onChoose: (kind, usage, id) => {
    chosen.push(id); blocks = changeTemplateSectionDesign(blocks, runKey, id, { product });
    assert.equal(validateStoryboardV4(blocks), blocks);
    assert.equal(uniqueGenerationCutCount(blocks, product), cutCount);
    assert.ok(blocks.filter(block => block.pageSectionKey === runKey).every(block => block.pageDesignId === id));
  } });
  const byText = (tree, text) => nodes(tree).find(n => n.type === 'button' && textOf(n).trim() === text);
  try {
    let tree = render();
    assert.ok(nodes(tree).some(n => n.props?.role === 'group' && n.props['aria-label'] === '꾸밈'));
    assert.ok(!nodes(tree).some(n => n.props?.['aria-label'] === '액자에 넣을 사진'));
    byText(tree, '액자').props.onClick(); tree = render();
    const group = nodes(tree).find(n => n.props?.role === 'group' && n.props['aria-label'] === '액자에 넣을 사진'); assert.ok(group);
    const row = nodes(tree).find(n => n.props?.['data-frame-count'] === true); assert.ok(row);
    assert.match(row.props.className, /frameCounts/);
    assert.match(group.props.className, /modal-actions/);
    assert.match(group.props.className, /segment/);
    assert.deepEqual(nodes(group).filter(n => n.type === 'button').map(textOf), ['1장', '2장']);
    assert.equal(byText(group, '2장').props['aria-pressed'], true);
    byText(group, '1장').props.onClick();
    assert.equal(chosen.at(-1), 'WEAR-STYLING-FRAME-COLUMN-01');
    tree = render(); byText(tree, '기본').props.onClick(); tree = render();
    assert.ok(!nodes(tree).some(n => n.props?.['aria-label'] === '액자에 넣을 사진'));
    assert.deepEqual(blocks, before, 'choosing basic restores all original saved fields');
  } finally { hooks.close(); }
});
