import test from 'node:test';
import assert from 'node:assert/strict';
import { seedStoryboard, resolveStoryboardEntry, storyboardFingerprint, seedProductKey } from '../../src/lib/pageTemplates/seed.js';
import { addTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';
import { addStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { sectionRuns, visibleSectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';

const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, images: [{ slot: 'Front', src: '/front.jpg' }] }] };
const candidate = (id, kind = 'structure', extra = {}) => ({ id, kind, label: id, rank: 1,
  informationGroup: id, photoUse: 'standalone', visibility: 'clear', direction: 'front', ...extra });
const analysis = (templateId, candidates = []) => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId },
  detailRecommendations: { version: 1, status: 'ready', candidates } });
const details = blocks => blocks.filter(block => block.source === 'ai' && block.shot === 'detail');

for (const templateId of ['emo', 'show']) {
  test(`${templateId}: no fabric candidate or original omits the initial fabric section entirely`, () => {
    const a = analysis(templateId), blocks = seedStoryboard(product, a);
    assert.equal(blocks.some(block => block.pageSectionKind === 'fabric'), false);
    assert.deepEqual(addTemplateSection(blocks, product, a, 'fabric'), blocks);
  });

  test(`${templateId}: three structural details and one fabric all receive initial photos`, () => {
    const blocks = seedStoryboard(product, analysis(templateId, [candidate('neck'), candidate('cuff'),
      candidate('hem'), candidate('weave', 'fabric')]));
    assert.deepEqual(details(blocks).map(block => block.detailTargetId).sort(), ['cuff', 'hem', 'neck', 'weave']);
    assert.equal(blocks.filter(block => block.pageSectionKind === 'fabric').some(block => block.source === 'text'), false);
  });

  test(`${templateId}: a seller detail upload alone cannot qualify fabric without analysis`, () => {
    const p = { ...product, colors: [{ ...product.colors[0], images: [...product.colors[0].images,
      { id: 'fabric-original', slot: 'Detail', src: '/fabric-original.jpg' }] }] };
    const blocks = seedStoryboard(p, analysis(templateId));
    assert.equal(blocks.some(block => block.pageSectionKind === 'fabric'), false);
  });

  test(`${templateId}: canonical structural subjects use at most three initial points`, () => {
    const blocks = seedStoryboard(product, analysis(templateId, [candidate('neck', 'neckline'), candidate('cuff', 'cuff'),
      candidate('hem', 'hem'), candidate('pocket', 'pocket')]));
    assert.deepEqual(details(blocks).map(block => block.detailTargetId), ['neck', 'cuff', 'hem']);
  });

  for (const images of [
    [{ slot: 'Front', src: '/front.jpg' }, { slot: 'Back', src: '/back.jpg' }],
    [{ slot: 'Detail' }, { slot: 'BackDetail', id: '', src: '' }],
  ]) test(`${templateId}: overview-only and blank closeup records cannot qualify fabric (${images[0].slot})`, () => {
    const p = { ...product, colors: [{ id: 'base', isBase: true, images }] };
    const blocks = seedStoryboard(p, analysis(templateId));
    assert.equal(blocks.some(block => block.pageSectionKind === 'fabric'), false);
    assert.deepEqual(addTemplateSection(blocks, p, analysis(templateId), 'fabric'), blocks);
  });

  for (const slot of ['Detail', 'BackDetail']) test(`${templateId}: ${slot} originals do not create initial or added fabric without a candidate`, () => {
    const p = { ...product, colors: [{ ...product.colors[0], images: [{ id: `seller-${slot}`, slot }] }] };
    const a = analysis(templateId), blocks = seedStoryboard(p, a), fabric = blocks.find(block => block.pageSectionKind === 'fabric');
    assert.equal(fabric, undefined);
    assert.equal(validateStoryboardV4(blocks), blocks);
    const missing = blocks.filter(block => block.pageSectionKind !== 'fabric');
    const added = addTemplateSection(missing, p, a, 'fabric').filter(block => !missing.some(old => old.id === block.id));
    assert.equal(added.length, 0);
  });

  for (const slot of ['Detail', 'BackDetail']) test(`${templateId}: three structures plus a seller ${slot} upload remain three without a fabric candidate`, () => {
    const p = { ...product, colors: [{ ...product.colors[0], images: [{ id: 'seller-closeup', slot }] }] };
    const blocks = seedStoryboard(p, analysis(templateId, [candidate('neck'), candidate('cuff'), candidate('hem')]));
    assert.equal(details(blocks).length, 3);
    const fabric = details(blocks).find(block => block.pageSectionKind === 'fabric');
    assert.equal(fabric, undefined);
  });

  test(`${templateId}: a clear fabric recommendation retains its binding ahead of an unclassified seller original`, () => {
    const p = { ...product, colors: [{ ...product.colors[0], images: [{ id: 'seller-closeup', slot: 'Detail' }] }] };
    const blocks = seedStoryboard(p, analysis(templateId, [candidate('weave', 'fabric', { direction: 'back' })]));
    const fabric = details(blocks).find(block => block.pageSectionKind === 'fabric');
    assert.equal(fabric.detailTargetId, 'weave');
    assert.equal(fabric.detailTargetOrigin, 'auto');
    assert.equal(fabric.direction, 'back');
  });

  for (const [count, expected] of [[0, 1], [1, 1], [2, 3], [4, 4], [5, 4]]) test(`${templateId}: ${count} structures keep the two-target minimum and three-target maximum with fabric`, () => {
    const blocks = seedStoryboard(product, analysis(templateId, [
      ...Array.from({ length: count }, (_, i) => candidate(`part-${i}`)), candidate('weave', 'fabric'),
    ]));
    assert.equal(details(blocks).length, expected);
    assert.equal(blocks.some(block => block.pageSectionKind === 'points'), count >= 2);
    assert.equal(details(blocks).filter(block => block.pageSectionKind === 'fabric').length, 1);
  });

  test(`${templateId}: uncertain or contextual fabric candidates never create a fabric section`, () => {
    const blocks = seedStoryboard(product, analysis(templateId, [candidate('uncertain', 'fabric', { visibility: 'uncertain' }),
      candidate('contextual', 'fabric', { photoUse: 'context' })]));
    assert.equal(blocks.some(block => block.pageSectionKind === 'fabric'), false);
  });

  test(`${templateId}: an old saved zero-photo fabric run remains unchanged and hidden`, () => {
    const a = analysis(templateId), blocks = seedStoryboard(product, analysis(templateId, [candidate('weave', 'fabric')])).map(block => {
      if (block.pageSectionKind !== 'fabric') return block;
      const { taxonomyVersion, pageSectionKey, pageSectionKind, pageDesignId, sectionId, sectionTitle, sectionLayout, sectionCustom, id } = block;
      return { taxonomyVersion, pageSectionKey, pageSectionKind, pageDesignId, sectionId, sectionTitle, sectionLayout, sectionCustom,
        id, source: 'text', slotKey: null, cutType: null, contentRole: null, sectionRole: null };
    });
    blocks[0].seedProductKey = seedProductKey(product, a);
    blocks[0].seedFingerprint = storyboardFingerprint(blocks);
    assert.equal(resolveStoryboardEntry(blocks, product, a).blocks, blocks);
    assert.equal(sectionRuns(blocks).some(run => run.kind === 'fabric'), true);
    assert.equal(visibleSectionRuns(blocks).some(run => run.kind === 'fabric'), false);
    assert.equal(validateStoryboardV4(blocks), blocks);
  });

  test(`${templateId}: the initial budget does not change the manual photo-add gate or reject saved extra details`, () => {
    const blocks = seedStoryboard(product, analysis(templateId, [candidate('neck'), candidate('cuff'), candidate('hem'), candidate('weave', 'fabric')]));
    const point = blocks.find(block => block.pageSectionKind === 'points' && block.source === 'ai');
    assert.equal(addStoryboardPhoto(blocks, point.pageSectionKey, { product }), blocks);
    const index = blocks.indexOf(point), extra = { ...point, id: 'manual-extra-detail', slotKey: null, detailTargetOrigin: 'user' };
    const saved = [...blocks.slice(0, index), extra, ...blocks.slice(index)];
    assert.equal(details(saved).length, 5);
    assert.equal(validateStoryboardV4(saved), saved);
  });
}

for (const slot of ['Detail', 'BackDetail']) for (const hasOverview of [false, true]) {
  test(`cross-color ${slot} cannot imply fabric even with a base overview (${hasOverview})`, () => {
    const p = { ...product, colors: [
      { id: 'base', isBase: true, images: hasOverview ? [{ id: 'base-overview', slot: slot === 'Detail' ? 'Front' : 'Back' }] : [] },
      { id: 'other', images: [{ id: 'other-detail', slot }] },
    ] };
    const blocks = seedStoryboard(p, analysis('emo')), fabric = blocks.find(block => block.pageSectionKind === 'fabric');
    assert.equal(fabric, undefined);
  });
}
