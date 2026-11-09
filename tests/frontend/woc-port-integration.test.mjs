import test from 'node:test';
import assert from 'node:assert/strict';
import { seedStoryboard, storyboardFingerprint, isUntouchedTemplateStoryboard, resolveStoryboardEntry } from '../../src/lib/pageTemplates/seed.js';
import { addTemplateSection, removeTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';
import { reconcileSectionDependencies } from '../../src/lib/pageTemplates/sectionDependencies.js';
import { ensureSections } from '../../src/lib/sections.js';

const product = clothingType => ({ clothingType, colors: [{ id: 'base', isBase: true,
  images: [{ slot: 'Front', src: '/front.jpg' }, { slot: 'Back', src: '/back.jpg' }] },
{ id: 'extra', images: [{ slot: 'Front', src: '/extra.jpg' }] }] });
const analysis = templateId => ({ targetGenders: ['women'], pageStyle: { templateId, colorMode: 'mono' } });
const fullProducts = blocks => blocks.filter(block => block.source === 'ai' && block.cutType === 'product' && block.shot === 'ghost');

for (const template of ['emo', 'show']) for (const clothing of ['top', 'bottom', 'outer', 'dress']) {
  test(`port ${template}/${clothing} fresh product photos explicitly use ghost through normalization`, () => {
    const seeded = seedStoryboard(product(clothing), analysis(template));
    assert.ok(fullProducts(seeded).length > 0);
    assert.ok(fullProducts(seeded).every(block => block.productPresentation === 'ghost'));
    assert.deepEqual(fullProducts(ensureSections(seeded)).map(block => block.productPresentation), fullProducts(seeded).map(() => 'ghost'));
    assert.ok(seeded.filter(block => block.cutType !== 'product' || block.shot !== 'ghost').every(block => !Object.hasOwn(block, 'productPresentation')));
  });
}

test('port a presentation edit protects the v4 board from automatic reseeding', () => {
  const p = product('top'), a = analysis('emo'), seeded = seedStoryboard(p, a);
  assert.equal(isUntouchedTemplateStoryboard(seeded), true);
  const edited = seeded.map(block => fullProducts([block]).length ? { ...block, productPresentation: 'flat' } : block);
  assert.notEqual(storyboardFingerprint(edited), storyboardFingerprint(seeded));
  assert.equal(isUntouchedTemplateStoryboard(edited), false);
  assert.equal(resolveStoryboardEntry(edited, { ...p, clothingType: 'outer' }, a).blocks, edited);
});

test('port adding a removed product section creates explicit ghost cards', () => {
  const p = product('top'), a = analysis('show'), seeded = seedStoryboard(p, a);
  const key = seeded.find(block => block.pageSectionKind === 'color_product').pageSectionKey;
  const removed = removeTemplateSection(seeded, key, { product: p });
  const added = addTemplateSection(removed, p, a, 'color_product', null);
  assert.ok(fullProducts(added).length > 0);
  assert.ok(fullProducts(added).every(block => block.productPresentation === 'ghost'));
});

test('port a fallback with no prior product prototype gets the existing main ghost default', () => {
  const p = product('top'), a = analysis('show');
  const seeded = seedStoryboard(p, a).filter(block => block.pageSectionKind !== 'color_product')
    .map(block => block.pageSectionKind === 'first' ? { ...block, pageDesignId: 'P01-D01' } : block);
  const repaired = reconcileSectionDependencies(seeded, seeded, p);
  const fallback = fullProducts(repaired).find(block => block.pageSectionKind === 'first');
  assert.ok(fallback); assert.equal(fallback.productPresentation, 'ghost');
});

test('port a copied existing product recipe keeps its mode without implementing new first-ghost transitions', () => {
  const p = product('top'), a = analysis('show');
  const seeded = seedStoryboard(p, a).map(block => block.pageSectionKind === 'first'
    ? { ...block, pageDesignId: 'P01-D01' } : fullProducts([block]).length ? { ...block, productPresentation: 'flat' } : block);
  const key = seeded.find(block => block.pageSectionKind === 'color_product').pageSectionKey;
  const removed = removeTemplateSection(seeded, key, { product: p });
  assert.equal(fullProducts(removed).find(block => block.pageSectionKind === 'first').productPresentation, 'flat');
});
