import test from 'node:test';
import assert from 'node:assert/strict';
import { pageTemplatesCatalog, validateCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { sectionDesignChoices, sectionDesignPhotoCount, sectionDesignGenerationCount, defaultSectionDesignId } from '../../src/lib/pageTemplates/designChoices.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { addTemplateSection, changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { addStageAddition, stageAdditions } from '../../src/lib/pageTemplates/stageAdditions.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';

const product = { name: '셔츠', clothingType: 'top', colors: [{ id: 'base', isBase: true }, { id: 'second' }] };
const analysis = templateId => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId },
  detailRecommendations: { version: 1, status: 'ready', candidates: ['fabric', 'neck', 'hem'].map((id, index) => ({
    id, kind: id === 'fabric' ? 'fabric' : 'structure', label: id, rank: index + 1, informationGroup: id,
    photoUse: 'standalone', visibility: 'clear', direction: 'front',
  })) } });
const board = id => seedStoryboard(product, analysis(id));
const choices = (blocks, run) => sectionDesignChoices(run, { storyboard: blocks });

for (const templateId of ['emo', 'show']) test(`${templateId}: candidates preserve generated cuts, source cards and compatible photo positions`, () => {
  const original = board(templateId), snapshot = structuredClone(original);
  for (const run of sectionRuns(original)) {
    const offered = choices(original, run);
    assert.ok(offered.length);
    assert.equal(offered.filter(design => design.id === defaultSectionDesignId(run, original)).length, 1);
    const images = blocks => assemblePage({ storyboard: blocks, product, analysis: analysis(templateId) })
      .filter(block => block.pageSectionKey === run.key).flatMap(block => block.elements).filter(element => element.type === 'image');
    const count = images(original).length;
    for (const design of offered) {
      const next = changeTemplateSectionDesign(original, run.key, design.id, { product });
      validateStoryboardV4(next);
      if (run.kind === 'first') assert.equal(sectionDesignGenerationCount(run, design.id, original, product), 1);
      else assert.equal(images(next).length, count, `${run.key} ${design.id}`);
      assert.deepEqual(next.map(block => block.id), original.map(block => block.id));
      assert.ok(next.filter(block => block.pageSectionKey === run.key).every(block => block.pageDesignId === design.id));
    }
  }
  assert.deepEqual(original, snapshot);
});

test('saved first ghost pairs remain usable while retired first and SNS layouts stay out of choices', () => {
  const blocks = board('emo'), runs = sectionRuns(blocks);
  assert.deepEqual(choices(blocks, runs[0]).map(design => design.id), ['P01-D09', 'P01-SHOW-01']);
  const show = board('show');
  assert.deepEqual(choices(show, sectionRuns(show)[0]).map(design => design.id), ['P01-SHOW-01', 'P01-D09']);
  assert.equal(changeTemplateSectionDesign(blocks, runs[0].key, 'P01-D11', { product }), blocks);
  const base = blocks.map(block => block.pageSectionKey === runs[0].key ? { ...block, pageDesignId: 'P01-D11' } : block);
  validateStoryboardV4(base);
  assert.equal(assemblePage({ storyboard: base, product, analysis: analysis('emo') })
    .filter(block => block.pageSectionKind === 'first').flatMap(block => block.elements).filter(element => element.type === 'image').length, 1);
  const tile = stageAdditions(base, runs[0].key, { product, analysis: analysis('emo') }).find(item => item.id === 'firstGhost');
  const paired = addStageAddition(base, runs[0].key, tile, { product, analysis: analysis('emo') });
  const pairedRun = sectionRuns(paired)[0];
  assert.equal(pairedRun.designId, 'P01-D01');
  assert.equal(sectionDesignPhotoCount(pairedRun, pairedRun.designId, paired), 2);
  assert.equal(sectionDesignGenerationCount(pairedRun, pairedRun.designId, paired, product), 1);
  assert.deepEqual(choices(paired, pairedRun).map(design => design.id), ['P01-D09', 'P01-SHOW-01']);
  assert.equal(changeTemplateSectionDesign(blocks, runs[0].key, 'P01-D01', { product }), blocks);
  const withSNS = addTemplateSection(blocks, product, analysis('emo'), 'sns');
  const sns = sectionRuns(withSNS).find(run => run.kind === 'sns');
  assert.ok(sns, 'legacy low-level SNS insertion remains supported');
  assert.deepEqual(choices(withSNS, sns), []);
  assert.ok(assemblePage({ storyboard: withSNS, product, analysis: analysis('emo') })
    .filter(block => block.pageSectionKind === 'sns').flatMap(block => block.elements).some(element => element.type === 'image'));
  assert.equal(sectionDesignPhotoCount(sns, 'P07-D08', withSNS), 2);
  assert.equal(changeTemplateSectionDesign(withSNS, sns.key, 'P07-D08'), withSNS);
});

test('fabric candidates preserve one photo and text-only fallback remains one selected default', () => {
  const blocks = board('emo'), run = sectionRuns(blocks).find(run => run.kind === 'fabric');
  assert.deepEqual(choices(blocks, run).map(design => design.id), ['T03-D01', 'T03-D06']);
  assert.ok(choices(blocks, run).every(design => design.id !== 'T03-TEXT-01'));
  const noPhoto = [...seedStoryboard(product, { targetGenders: ['women'], pageStyle: defaultPageStyle() }),
    { id: 'saved-text-fabric', taxonomyVersion: 4, pageSectionKey: 'emo.fabric', pageSectionKind: 'fabric',
      pageDesignId: 'T03-D01', sectionId: 'saved-fabric', source: 'text', slotKey: null, cutType: null, contentRole: null, sectionRole: null }];
  validateStoryboardV4(noPhoto);
  const fallback = sectionRuns(noPhoto).find(run => run.kind === 'fabric');
  assert.deepEqual(choices(noPhoto, fallback).map(design => design.id), ['T03-TEXT-01']);
  assert.equal(defaultSectionDesignId(fallback, noPhoto), 'T03-TEXT-01');
});

test('both templates share four natural detail choices with one column default', () => {
  for (const template of ['emo', 'show']) {
    const defaultId = 'PT-COLUMN-01';
    const blocks = board(template), run = sectionRuns(blocks).find(run => run.kind === 'points');
    assert.equal(defaultSectionDesignId(run, blocks), defaultId);
    const offered = choices(blocks, run);
    assert.equal(offered.filter(design => design.id === defaultId).length, 1);
    assert.deepEqual(offered.map(design => design.id), ['PT-COLUMN-01', 'P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01']);
  }
});

test('section insertion rejects an otherwise visible design that would add a photo position', () => {
  const blocks = board('show');
  const id = 'SNS-TEST-PHOTO-COUNT';
  pageTemplatesCatalog.designs[id] = { ...pageTemplatesCatalog.designs['P07-D08'], id, pickerHidden: false };
  try {
    assert.equal(addTemplateSection(blocks, product, analysis('show'), 'sns', undefined, { designId: id }), blocks);
    pageTemplatesCatalog.designs[id] = { ...pageTemplatesCatalog.designs['I09-D14'], id, pickerHidden: false };
    assert.notEqual(addTemplateSection(blocks, product, analysis('show'), 'sns', undefined, { designId: id }), blocks,
      'the same visible choice is accepted when its photo-position count matches');
  } finally { delete pageTemplatesCatalog.designs[id]; }
});

for (const value of [undefined, null, '', '  ', 12, 'x'.repeat(25)]) test(`catalog requires short displayName metadata: ${JSON.stringify(value)}`, () => {
  const invalid = structuredClone(pageTemplatesCatalog); invalid.designs['P01-D09'].displayName = value;
  assert.throws(() => validateCatalog(invalid), /design displayName/);
});
