import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { seedStoryboard, resolveStoryboardEntry, isUntouchedTemplateStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { getTemplate } from '../../src/lib/pageTemplates/catalog.js';
import { photoSlotForBlock } from '../../src/lib/pageTemplates/slots.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { addTemplateSection, removeTemplateSection, sectionMenuGroups } from '../../src/lib/pageTemplates/sectionActions.js';
import { assemblePage, pointPhotosForRun } from '../../src/lib/pageTemplates/assemble.js';
import { trustedEmptyBottom, preferExamples } from '../../src/lib/pageTemplates/examplePreferences.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { createSimilarCutBlock, inheritSimilarCutRecipes } from '../../src/lib/storyboardSimilarCuts.js';
import { ensureSections, deriveSections } from '../../src/lib/sections.js';
import { normalizedRecipePatch } from '../../src/lib/storyboardTaxonomy.js';

const candidate = (id, kind = 'structure', rank = 1) => ({ id, kind, label: id, rank, informationGroup: id,
  photoUse: 'standalone', visibility: 'clear', direction: 'front' });
const product = colors => ({ name: '니트', clothingType: 'top', colors: Array.from({ length: colors }, (_, i) => ({
  id: `c${i}`, name: `색 ${i}`, isBase: i === 0, images: i === 0 ? [{ slot: 'Back' }] : [],
})) });
const analysis = (templateId, candidates = [candidate('fabric', 'fabric'), candidate('neck'), candidate('hem')]) => ({
  targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId }, detailRecommendations: { version: 1, status: 'ready', candidates },
});

for (const template of ['emo', 'show']) for (const colors of [1, 2, 3]) test(`${template} × ${colors} colors seeds catalog sections and exact generated counts`, () => {
  const p = product(colors), a = analysis(template), b = seedStoryboard(p, a, { projectId: 'seed-test' });
  assert.deepEqual(deriveSections(b).map(s => s.pageSectionKey), getTemplate(template).sections.filter(s => s.presence !== 'optional').map(s => `${template}.${s.instanceKey}`));
  const expected = { emo: [17, 18, 21], show: [15, 16, 19] }[template][colors - 1];
  assert.equal(b.filter(b => b.source === 'ai').length, expected);
  const album = b.filter(b => b.pageSectionKey === `${template}.wear_styling`);
  assert.deepEqual(album.map(b => b.colorId), [...Array(colors === 1 ? 6 : 4).fill('c0'), ...Array.from({ length: colors - 1 }, (_, i) => [`c${i + 1}`, `c${i + 1}`]).flat()]);
  assert.ok(album.every(b => b.pageSectionUsage === (template === 'show' ? 'fit' : 'styling')));
  assert.deepEqual(b.filter(b => b.pageSectionKey === `${template}.wear_fit`).map(b => b.direction), ['front', 'side', 'back']);
  assert.ok(b.every(b => b.taxonomyVersion === 4 && b.sectionLayout === 'stack'));
  validateStoryboardV4(b);
  assert.equal(isUntouchedTemplateStoryboard(b), true);
});

for (const templateId of ['emo', 'show']) for (const colors of [1, 2, 4]) {
  test(`${templateId} menu styling adds exactly two base-color cuts in one location with ${colors} colors`, () => {
    const p = product(colors), a = analysis(templateId), before = seedStoryboard(p, a);
    const next = addTemplateSection(before, p, a, 'wear', 'styling');
    const added = next.filter(b => !before.some(previous => previous.id === b.id));
    assert.equal(added.length, 2);
    assert.ok(added.every(b => b.source === 'ai' && b.colorId === 'c0'));
    assert.equal(new Set(added.map(b => b.spaceGroupId)).size, 1);
    assert.ok(added[0].spaceGroupId);
    assert.ok(added.every(b => b.exampleId && b.sectionCustom));
    assert.deepEqual(next.filter(b => before.some(previous => previous.id === b.id)), before);
    validateStoryboardV4(next);
    const fit = addTemplateSection(next, p, a, 'wear', 'fit').filter(b => !next.some(previous => previous.id === b.id));
    assert.deepEqual(fit.map(b => b.direction), ['front', 'side', 'back']);
    assert.equal(new Set(fit.map(b => b.spaceGroupId)).size, 1);
  });
}

for (const template of ['emo', 'show']) for (const count of [0, 1, 2, 3, 4]) test(`${template} detail candidates ${count}: clear-only initial points and optional crop-only points share the cap`, () => {
  const candidates = [candidate('fabric', 'fabric'), candidate('neck'), candidate('hem'), candidate('seam')].slice(0, count);
  const p = product(2), a = analysis(template, candidates), seeded = seedStoryboard(p, a);
  const details = seeded.filter(b => b.shot === 'detail');
  assert.equal(details.length, [0, 1, 1, 3, 4][count]);
  const clearCount = Math.max(0, count - 1);
  assert.equal(seeded.some(b => b.pageSectionKind === 'points'), clearCount >= 2);
  const b = clearCount < 2 ? addTemplateSection(seeded, p, a, 'points') : seeded;
  assert.equal(uniqueGenerationCutCount(b), uniqueGenerationCutCount(seeded));
  const lead = b.find(b => b.source === 'text' && b.pointItems);
  const detailCount = clearCount >= 2 ? Math.min(clearCount, 3) : 0;
  const cropCount = clearCount < 2 ? 3 : 0;
  assert.equal(lead.pointItems.length, detailCount || cropCount);
  assert.equal(lead.pointItems.filter(i => i.kind === 'detail').length, detailCount);
  assert.equal(lead.pointItems.filter(i => i.kind === 'wornCrop').length, cropCount);
  const cropItems = lead.pointItems.filter(i => i.kind === 'wornCrop');
  // Seeds have no generated dimensions yet, so new ROIs use the wear 3:4 fallback.
  assert.ok(cropItems.every(({ cropRegion: [x, y, w, h] }) => w > 0 && h > 0
    && x >= 0 && y >= 0 && x + w <= 1 && y + h <= 1 && Math.abs(w * .75 / h - .8) < 1e-9));
  assert.equal(uniqueGenerationCutCount(b), uniqueGenerationCutCount(b.filter(b => b.source === 'ai')));
  const run = b.filter(b => b.pageSectionKey === lead.pageSectionKey);
  assert.equal(pointPhotosForRun(run, b).length, detailCount || cropCount);
  const cutResults = b.filter(b => b.source === 'ai').map(b => ({ blockId: b.id, imageUrl: `generated:${b.id}`, width: 1024, height: 1536 }));
  const page = assemblePage({ storyboard: b, product: p, analysis: a, cutResults });
  const pointImages = page.filter(s => s.pageSectionKey === lead.pageSectionKey).flatMap(s => s.elements).filter(e => e.type === 'image');
  assert.equal(pointImages.length, detailCount || cropCount);
  assert.ok(pointImages.every(e => e.w / e.h === .8), 'new defaults give every detail a 4:5 viewport');
  const crops = pointImages.filter(e => e.pointItemKind === 'wornCrop');
  assert.equal(crops.length, cropCount);
  assert.deepEqual(crops.map(e => e.sourceBlockId), cropItems.map(item => item.sourceBlockId));
  assert.ok(crops.every(e => e.src === `generated:${e.sourceBlockId}` && e.crop
    && e.crop.iw >= e.w && e.crop.ih >= e.h && e.crop.ox >= 0 && e.crop.oy >= 0
    && e.crop.ox + e.w <= e.crop.iw && e.crop.oy + e.h <= e.crop.ih
    && Math.abs(e.crop.iw / e.crop.ih - 2 / 3) < .001), 'new cover crops fill the viewport and retain actual source proportions');
  if (crops.length) {
    const sourceId = crops[0].sourceBlockId;
    const failed = assemblePage({ storyboard: b, product: p, analysis: a, failedCuts: { [sourceId]: { reason: 'source_failed', retryable: true } } });
    assert.ok(failed.flatMap(s => s.elements).filter(e => e.type === 'image' && e.sourceBlockId === sourceId).every(e => e.genFailed));
    const removed = b.filter(b => b.id !== sourceId);
    assert.equal(pointPhotosForRun(run, removed).some(p => p.pointItem?.sourceBlockId === sourceId), false);
    assert.equal(assemblePage({ storyboard: removed, product: p, analysis: a }).flatMap(s => s.elements).some(e => e.sourceBlockId === sourceId), false);
  }
});

test('v4 slot recipes, shooting groups and hero assignment preserve page order', () => {
  const b = seedStoryboard(product(2), analysis('emo'));
  const first = b[0], fit = b.find(b => b.pageSectionUsage === 'fit');
  assert.deepEqual(photoSlotForBlock(first).recipe.allowedCutTypes, ['styling', 'horizon']);
  assert.equal(normalizedRecipePatch({ ...first, cutType: 'product', shot: 'detail' }, 'hero').cutType, 'styling');
  const child = createSimilarCutBlock({ ...first, exampleId: 'example' }, 'similar');
  assert.equal(child.slotKey, null);
  const board = ensureSections([first, child, ...b.slice(1)]);
  assert.equal(board[0].contentRole, 'hero'); assert.equal(board[1].contentRole, 'benefit');
  assert.equal(board[1].pageSectionKey, first.pageSectionKey);
  assert.equal(inheritSimilarCutRecipes([first, { ...child, sectionId: 'other' }])[1].sectionId, first.sectionId);
  assert.equal(normalizedRecipePatch(fit, 'fit').cutType, 'horizon');
  assert.deepEqual(board.map(b => b.id), [first.id, child.id, ...b.slice(1).map(b => b.id)]);
});

test('four menu groups insert optional and repeated sections at the page end without a selected position; removal and undo retain references', () => {
  assert.deepEqual(sectionMenuGroups.map(g => g.name), ['사진', '옷 자세히', '상품 설명', '후기와 소통']);
  const p = product(2), a = analysis('emo'), before = seedStoryboard(p, a);
  let board = addTemplateSection(before, p, a, 'sns');
  assert.equal(deriveSections(board).at(-1).kind, 'sns');
  board = addTemplateSection(board, p, a, 'wear', 'styling');
  board = addTemplateSection(board, p, a, 'points');
  assert.equal(deriveSections(board).filter(s => s.kind === 'wear' && s.usage === 'styling').length, 2);
  assert.equal(deriveSections(board).filter(s => s.kind === 'points').length, 1);
  assert.equal(board.filter(b => b.shot === 'detail').length, 3);
  validateStoryboardV4(board);
  const fitKey = before.find(b => b.pageSectionUsage === 'fit').pageSectionKey;
  const removed = removeTemplateSection(before, fitKey);
  assert.equal(removed.some(b => b.pageSectionKey === fitKey), false);
  assert.equal(deriveSections(before).some(s => s.pageSectionKey === fitKey), true); // undo snapshot remains complete
});

test('only untouched templates rebuild for changed product conditions; compose mode is ignored', () => {
  const p = product(1), a = analysis('show'), b = seedStoryboard(p, a);
  assert.equal(resolveStoryboardEntry(b, p, a).blocks, b);
  assert.notEqual(resolveStoryboardEntry(b, product(2), a).blocks, b);
  const edited = b.map((block, i) => i === 0 ? { ...block, colorId: 'seller-color' } : block);
  assert.equal(resolveStoryboardEntry(edited, product(2), a).blocks, edited);
  assert.deepEqual(seedStoryboard(p, a, { composeMode: 'basic' }).map(b => b.cutType), seedStoryboard(p, a, { composeMode: 'extended' }).map(b => b.cutType));
});

test('old boards rebuild before generation and give an editor notice after generation', () => {
  const old = [{ id: 'old', taxonomyVersion: 3, source: 'ai' }], p = product(2), a = analysis('emo');
  const rebuilt = resolveStoryboardEntry(old, p, a);
  assert.equal(rebuilt.notice, 'rebuilt'); validateStoryboardV4(rebuilt.blocks);
  assert.equal(resolveStoryboardEntry(old, p, a, { generated: true }).notice, 'legacyGenerated');
  assert.throws(() => ensureSections(old), /storyboard_outdated/);
});

test('bottomClear trusts individual tags except the reviewed attribute and falls back unchanged', () => {
  const data = { examples: { a: { status: 'review', review: ['lightSide'], cut: { emptyBottom: true } },
    b: { status: 'review', review: ['emptyBottom'], cut: { emptyBottom: true } }, c: { status: 'ok', cut: { emptyBottom: false } } } };
  assert.equal(trustedEmptyBottom('a', data), true); assert.equal(trustedEmptyBottom('b', data), null);
  const pool = [{ id: 'c' }, { id: 'b' }, { id: 'a' }];
  assert.equal(preferExamples(pool, 'bottomClear', data)[0].id, 'a');
  assert.equal(preferExamples(pool, 'topClear', data), pool);
  assert.equal(preferExamples(pool, 'bottomClear', { examples: {} }), pool);
  const published = JSON.parse(readFileSync(new URL('../../server/app/data/example_tags.json', import.meta.url)));
  assert.equal(published.counts.examples ?? Object.keys(published.examples).length, Object.keys(published.examples).length);
});

test('text blocks never receive examples or enter generation billing', () => {
  const b = seedStoryboard(product(2), analysis('emo'));
  const text = b.filter(b => b.source === 'text');
  const assigned = assignGenerationExamples(b, { catalog: [], product: product(2) }).blocks;
  assert.ok(text.every(t => assigned.includes(t)));
  assert.equal(uniqueGenerationCutCount(text), 0);
  assert.ok(text.every(t => !('matchIds' in t) && !('exampleId' in t) && !('refImages' in t) && t.cutType === null));
});

test('real JS seeds normalize and assemble in Python with the same worn crop geometry', () => {
  const cases = ['emo', 'show'].map(templateId => { const p = product(2), a = analysis(templateId); return { product: p, analysis: a, blocks: seedStoryboard(p, a) }; });
  const result = spawnSync('server/.venv/bin/python', ['-c', `import json,sys;sys.path.insert(0,'server')
from app.agents.content_roles import canonicalize_storyboard
from app.agents.page_assembler import assemble
out=[]
for c in json.load(sys.stdin):
 b=canonicalize_storyboard(c['blocks'])
 cuts=[dict(blockId=x['id'],imageUrl='cut:'+x['id'],width=1024,height=1536) for x in b if x['source']=='ai']
 out.append(assemble(b,cuts,[],c['product'],True,analysis=c['analysis']))
print(json.dumps(out,ensure_ascii=False))`], { input: JSON.stringify(cases), encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  const pages = JSON.parse(result.stdout);
  for (const [i, c] of cases.entries()) {
    const cuts = c.blocks.filter(b => b.source === 'ai').map(b => ({ blockId: b.id, imageUrl: `cut:${b.id}`, width: 1024, height: 1536 }));
    // Python resolves published example direction; geometry and source references must agree.
    const images = page => page.flatMap(b => b.elements).filter(e => e.type === 'image').map(({ id, ...e }) => e);
    assert.deepEqual(images(pages[i]), images(assemblePage({ storyboard: c.blocks, product: c.product, analysis: c.analysis, cutResults: cuts })));
  }
});
