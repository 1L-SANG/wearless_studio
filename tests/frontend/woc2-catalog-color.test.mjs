import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import * as catalogModule from '../../src/lib/pageTemplates/catalog.js';
import * as slots from '../../src/lib/pageTemplates/slots.js';
import * as pageStyle from '../../src/lib/pageTemplates/pageStyle.js';
import { resolvePageTokens, sectionTokens } from '../../src/lib/pageTemplates/tokens.js';
import { sectionDesignChoices, sectionDesignPhotoCount } from '../../src/lib/pageTemplates/designChoices.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { outfitHarness, nodes, textOf } from './helpers/outfitHarness.mjs';

const { pageTemplatesCatalog: catalog, getTemplate, sectionDefinition } = catalogModule;
const worn = (cutType, index, extra = {}) => ({ id: `w${index}`, source: 'ai', pageSectionKind: 'wear',
  pageSectionKey: 'show.wear_styling', pageSectionUsage: 'styling', pageDesignId: 'P02-D02',
  slotKey: `wear_styling.g${index}`, cutType, shot: 'full', direction: 'front', colorId: 'base', ...extra });
const runOf = blocks => ({ key: blocks[0].pageSectionKey, kind: blocks[0].pageSectionKind,
  usage: blocks[0].pageSectionUsage, designId: blocks[0].pageDesignId, blocks });
const contrast = (a, b) => {
  const luminance = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + .05) / (dark + .05);
};

test('only the two shipping templates can be selected while guide designs remain usable', () => {
  assert.deepEqual(catalog.templates.map(t => t.id), ['emo', 'show']);
  assert.equal(getTemplate('guide'), undefined);
  assert.equal(pageStyle.validPageStyle({ ...pageStyle.defaultPageStyle(), templateId: 'guide' }), false);
  for (const id of ['P01-D11', 'T03-GUIDE-01', 'T05-D07', 'T07-D06', 'CP-GUIDE-01']) {
    assert.ok(catalog.designs[id]);
    assert.equal(catalog.designs[id].pickerHidden, true);
  }
  assert.equal(catalog.kinds.points.name, '디테일 요소');
  assert.deepEqual(Object.values(catalog.kinds).filter(kind => kind.repeatable).map(kind => kind.kind), ['wear']);
});

test('new show continuous studio keeps its original instance key and physical recipe', () => {
  const definition = getTemplate('show').sections.find(s => s.instanceKey === 'wear_styling');
  assert.equal(definition.usage, 'fit');
  assert.equal(definition.designId, 'WEAR-FIT-COLUMN-01');
  assert.equal(definition.photoSlots[0].recipe.set, 'sequence');
  assert.equal(definition.photoSlots[0].recipe.cutType, 'horizon');
  assert.equal(catalog.designs['P02-D02'].usage, 'fit');
});

test('show opening uses the signature pool medium front recipe', () => {
  const recipe = getTemplate('show').sections.find(s => s.kind === 'first').photoSlots.find(slot => slot.slotKey === 'hero').recipe;
  assert.equal(recipe.cutType, 'horizon'); assert.equal(recipe.direction, 'front');
  assert.equal(recipe.shot, 'medium'); assert.deepEqual(recipe.allowedShots, ['full', 'medium']);
  const block = { source: 'ai', pageSectionKind: 'first', pageSectionKey: 'show.first', slotKey: 'first.hero', pageDesignId: 'P01-D01' };
  assert.deepEqual(slots.photoSlotForBlock(block).recipe.allowedShots, ['full', 'medium']);
});

test('wear additions use life pairs and studio rotations independently of initial sections', () => {
  assert.equal(typeof catalogModule.wearAdditionDefinition, 'function');
  for (const template of ['emo', 'show', getTemplate('show')]) {
    const life = catalogModule.wearAdditionDefinition(template, 'styling');
    assert.equal(life.kind, 'wear'); assert.equal(life.usage, 'styling');
    assert.equal(life.designId, 'P02-D01');
    assert.equal(life.photoSlots[0].count, 2);
    assert.equal(life.photoSlots[0].recipe.cutType, 'styling');
    assert.equal(life.photoSlots[0].recipe.set, 'styling');
    const studio = catalogModule.wearAdditionDefinition(template, 'fit');
    assert.equal(studio.designId, 'WEAR-FIT-COLUMN-01'); assert.equal(studio.usage, 'fit');
    assert.deepEqual(studio.photoSlots.map(slot => slot.recipe.direction), ['front', 'side', 'back']);
    assert.ok(studio.photoSlots.every(slot => slot.count === 1 && slot.recipe.set === 'rotation'));
  }
  const extraLife = sectionDefinition(getTemplate('show'), 'show.wear_styling_2', 'wear', 'styling');
  assert.equal(extraLife.photoSlots[0].recipe.cutType, 'styling');
});

test('saved wear display names follow physical photos without rewriting the saved title', () => {
  assert.equal(typeof slots.wearUsageForBlock, 'function');
  for (const [cutType, storedUsage, wantUsage, wantName] of [
    ['horizon', 'styling', 'fit', '스튜디오 착용컷'], ['styling', 'fit', 'styling', '일상 착용컷'],
  ]) {
    const block = worn(cutType, 1, { pageSectionUsage: storedUsage, name: '저장된 제목', sectionTitle: '이전 제목' });
    const snapshot = structuredClone(block);
    assert.equal(slots.wearUsageForBlock(block), wantUsage);
    assert.equal(slots.templateSectionName(block), wantName);
    assert.deepEqual(block, snapshot);
  }
  assert.equal(slots.templateSectionName({ pageSectionKind: 'wear', pageSectionKey: 'show.wear_styling', pageSectionUsage: 'styling' }), '스튜디오 착용컷');
});

test('common wear candidates use effective physical usage and preserve the number of photos', () => {
  const studio = [1, 2, 3].map(i => worn('horizon', i));
  assert.deepEqual(sectionDesignChoices(runOf(studio), { storyboard: studio }).map(d => d.id), ['WEAR-FIT-COLUMN-01', 'WEAR-FIT-2COL-01', 'WEAR-FIT-HERO-01']);
  const life = [1, 2].map(i => worn('styling', i, { pageSectionKey: 'show.wear_styling_2', pageDesignId: 'P02-D01' }));
  assert.deepEqual(sectionDesignChoices(runOf(life), { storyboard: life }).map(d => d.id), [
    'P02-D01', 'WEAR-STYLING-2COL-01', 'WEAR-STYLING-FRAME-COLUMN-01', 'WEAR-STYLING-FRAME-COLUMN-02',
  ]);
  for (const blocks of [studio, life])
    for (const design of sectionDesignChoices(runOf(blocks), { storyboard: blocks }))
      assert.equal(sectionDesignPhotoCount(runOf(blocks), design.id, blocks), blocks.length);
});

for (const colorMode of ['mono', 'point']) test(`retained summary design stays visible and uses its permitted surface in emo/${colorMode}`, () => {
  const product = { name: '셔츠', clothingType: 'top', colors: [{ id: 'base', isBase: true, swatchId: 'blue' }] };
  const analysis = { targetGenders: ['women'], pageStyle: { ...pageStyle.defaultPageStyle(), colorMode } };
  const storyboard = seedStoryboard(product, analysis).map(block => block.pageSectionKind === 'intro' ? { ...block, pageDesignId: 'T06-D01' } : block);
  const intro = assemblePage({ storyboard, product, analysis }).filter(block => block.pageSectionKey === 'emo.intro');
  const elements = intro.flatMap(block => block.elements);
  assert.ok(elements.some(element => element.type === 'text'), 'retained shared summary produces readable content');
  if (colorMode === 'point') assert.ok(elements.some(element => element.fill === '#edf4ff'), 'point summary uses the garment-tone surface');
});

test('color design candidates must display every sale color, including a fourth color', () => {
  const product = { colors: ['c1', 'c2', 'c3', 'c4'].map(id => ({ id })) };
  const blocks = product.colors.map(({ id }, i) => ({ id: `p${i}`, source: 'ai', pageSectionKind: 'color_product',
    pageSectionKey: 'emo.color_product', pageDesignId: 'CP-EMO-01', colorId: id, slotKey: `color_product.front.${id}` }));
  const offered = sectionDesignChoices(runOf(blocks), { storyboard: blocks, product });
  assert.deepEqual(offered.map(d => d.id), ['CP-COLUMN-01', 'CP-EMO-01']);
  const missingColor = blocks.slice(0, 3);
  assert.deepEqual(sectionDesignChoices(runOf(missingColor), { storyboard: missingColor, product }), []);
  const savedNumberedSlots = blocks.map((block, i) => ({ ...block, slotKey: `color_product.front${i + 1}` }));
  assert.deepEqual(sectionDesignChoices(runOf(savedNumberedSlots), { storyboard: savedNumberedSlots, product }).map(d => d.id),
    ['CP-COLUMN-01', 'CP-EMO-01']);
});

test('a fixed four-position layout that repeats only two colors is excluded without emptying the common pool', () => {
  const product = { colors: ['c1', 'c2', 'c3', 'c4'].map(id => ({ id })) };
  const blocks = product.colors.map(({ id }, i) => ({ id: `color${i}`, source: 'ai', pageSectionKind: 'color_product',
    pageSectionKey: 'emo.color_product', pageDesignId: 'CP-EMO-01', colorId: id, slotKey: `color_product.front${i + 1}` }));
  const run = runOf(blocks), id = 'CP-TEST-FIXED';
  try {
    catalog.designs[id] = { ...catalog.designs['CP-SHOW-01'], id, pickerHidden: false,
      layout: [{ type: 'photoGrid', columns: 2, slots: ['front1', 'front2', 'front3', 'front4'], aspect: '1/1' }] };
    assert.ok(sectionDesignChoices(run, { storyboard: blocks, product }).some(design => design.id === id),
      'the visible fixed layout is selectable when it covers all four colors');
    // Four real positions pass the photo-count gate, but repeated slots have
    // capacity for only two sale colors. The independent coverage gate must reject it.
    catalog.designs[id].layout[0].slots = ['front1', 'front2', 'front1', 'front2'];
    assert.equal(sectionDesignPhotoCount(run, id, blocks), 4);
    assert.deepEqual(sectionDesignChoices(run, { storyboard: blocks, product }).map(d => d.id), ['CP-COLUMN-01', 'CP-EMO-01']);
  } finally { delete catalog.designs[id]; }
});

test('current point choices support crops and photos while saved natural designs and the album still render', () => {
  const source = worn('horizon', 1, { taxonomyVersion: 4, pageSectionKey: 'emo.wear_fit', pageSectionUsage: 'fit' });
  const lead = { id: 'points', taxonomyVersion: 4, source: 'text', pageSectionKind: 'points', pageSectionKey: 'emo.points', pageDesignId: 'PT-EMO-01',
    pointItems: [1, 2, 3].map(i => ({ kind: 'wornCrop', sourceBlockId: source.id, label: `부위 ${i}`,
      bodyPart: `part${i}`, cropRegion: [.1, .1, .7, .7] })) };
  const blocks = [source, lead];
  const expected = ['PT-PHOTO-CARD-01', 'PT-TEXT-PHOTO-01'];
  assert.deepEqual(sectionDesignChoices(runOf([lead]), { storyboard: blocks }).map(design => design.id), expected);
  const details = [1, 2, 3].map(i => ({ id: `d${i}`, taxonomyVersion: 4, source: 'ai', pageSectionKind: 'points', pageSectionKey: 'emo.points',
    pageDesignId: 'PT-EMO-01', slotKey: `points.detail${i}`, cutType: 'product', shot: 'detail', colorId: 'base' }));
  assert.deepEqual(sectionDesignChoices(runOf(details), { storyboard: details }).map(design => design.id), expected);
  // Construct stored hidden IDs directly: the current picker intentionally cannot select them.
  for (const id of ['PT-COLUMN-01', 'P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01']) {
    const storyboard = blocks.map(block => block.pageSectionKind === 'points' ? { ...block, pageDesignId: id } : block);
    const images = assemblePage({ storyboard, product: { colors: [{ id: 'base' }] },
      cutResults: [{ blockId: source.id, imageUrl: '/native.webp', width: 1000, height: 1600 }] })
      .filter(block => block.pageSectionKind === 'points').flatMap(block => block.elements).filter(element => element.type === 'image');
    assert.equal(images.length, 3);
    assert.ok(images.every(image => image.pointItemKind === 'wornCrop' && image.h === Math.round(image.w * 1.6)),
      `${id} keeps each ROI's natural proportions`);
  }
  const savedAlbum = details.map(block => ({ ...block, pageDesignId: 'T07-D06' }));
  assert.equal(assemblePage({ storyboard: savedAlbum, product: { colors: [{ id: 'base' }] } })
    .flatMap(block => block.elements).filter(element => element.type === 'image').length, 3);
  assert.equal(sectionDesignChoices(runOf(savedAlbum), { storyboard: savedAlbum }).some(design => design.id === 'T07-D06'), false);
});

test('neutral clothing point mode retains the template mono greys and warm clothing retains its palette', () => {
  for (const templateId of ['emo', 'show']) {
    const mono = resolvePageTokens({ templateId, colorMode: 'mono' }).colors;
    for (const swatch of ['white', 'gray', 'black', 'unknown', 'constructor', 'toString', null]) {
      assert.deepEqual(resolvePageTokens({ templateId, colorMode: 'point' }, swatch).colors, mono, `${templateId}/${swatch}`);
    }
  }
  for (const [swatch, ink, bg] of [['ivory', '#5b544c', '#f6f3ee'], ['beige', '#75603b', '#fbf2e3'], ['brown', '#815939', '#fef1e6']]) {
    const colors = resolvePageTokens({ templateId: 'emo', colorMode: 'point' }, swatch).colors;
    assert.equal(colors.accentInk, ink); assert.equal(colors.accentBg, bg);
  }
});

test('all resolved text roles meet 4.5 contrast on their opaque page surfaces', () => {
  for (const templateId of ['emo', 'show']) for (const colorMode of ['mono', 'point']) for (const swatch of Object.keys(catalog.pointPalette)) {
    const tokens = resolvePageTokens({ templateId, colorMode }, swatch);
    for (const color of ['ink', 'muted', 'accentInk']) for (const bg of ['canvas', 'panel', 'accentBg', 'summaryBg', 'pointBg']) {
      assert.ok(contrast(tokens.colors[color], tokens.colors[bg]) >= 4.5, `${templateId}/${colorMode}/${swatch}: ${color} on ${bg}`);
    }
    if (templateId === 'show') {
      const colors = sectionTokens(tokens, { block: { invert: true } }).colors;
      for (const role of ['ink', 'muted', 'accentInk']) assert.ok(contrast(colors[role], colors.panel) >= 4.5);
    }
  }
});

test('analysis restoration and template switches retain the chosen color mode', () => {
  const selected = { ...pageStyle.defaultPageStyle(), colorMode: 'point', fontStyle: 'serif' };
  assert.equal(pageStyle.templateSelectionStyle(selected).colorMode, 'point');
  assert.equal(pageStyle.templateSelectionStyle(selected).fontStyle, 'template');
  assert.equal(pageStyle.selectTemplateStyle('show', '2026-11-05T00:00:00Z', selected).colorMode, 'point');
  assert.equal(typeof pageStyle.selectColorModeStyle, 'function');
  assert.equal(pageStyle.selectColorModeStyle(selected, 'mono', '2026-11-05T00:00:00Z').colorMode, 'mono');
});

test('both runtimes resolve identical palettes and fonts across all modes and swatches', () => {
  const fontStyles = ['template', 'plainGothic', 'serif', 'boldGothic'];
  assert.deepEqual(Object.keys(catalog.fontStyles), fontStyles);
  const cases = ['emo', 'show', 'guide'].flatMap(templateId => fontStyles.flatMap(fontStyle => ['mono', 'point'].flatMap(colorMode =>
    [...Object.keys(catalog.pointPalette), 'unknown', null].map(swatch => [{ templateId, fontStyle, colorMode }, swatch]))));
  const python = spawnSync('python3', ['-c',
    'import json,sys; from app.agents.page_templates import resolve_page_tokens; print(json.dumps([resolve_page_tokens(style,swatch) for style,swatch in json.load(sys.stdin)]))'],
  { cwd: new URL('../../server', import.meta.url), input: JSON.stringify(cases), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } });
  assert.equal(python.status, 0, python.error?.message || python.stderr);
  assert.deepEqual(JSON.parse(python.stdout), cases.map(([style, swatch]) => resolvePageTokens(style, swatch)));
});

test('analysis only exposes template choices after tone selection moves to the storyboard', async t => {
  const h = await outfitHarness(); t.after(() => h.close());
  const picker = await h.load('/src/features/analysis/PageTemplatePicker.jsx');
  const tree = h.render(picker.PageTemplatePicker, { value: 'emo', onChange() {} });
  assert.equal(picker.PageColorModePicker, undefined);
  assert.ok(!textOf(tree).includes('모노톤')); assert.ok(!textOf(tree).includes('옷 색에 맞춤'));
});

test('analysis template saves retain a previously selected storyboard tone', async () => {
  const source = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
  const start = source.indexOf('  const savePageStyle =');
  assert.ok(start >= 0, 'shared style-save action exists');
  const end = source.indexOf('  useEffect(() => {', start);
  const saved = [], errors = [], scope = { ...pageStyle,
    pageStyleTouchedRef: { current: false }, pageStyleValueRef: { current: { ...pageStyle.defaultPageStyle(), colorMode: 'point' } },
    pageStyleSaveRef: { current: Promise.resolve() }, projectId: 'p',
    invalidateStoryboardEntryPrefetch() {}, setPageStyle() {}, setPageStyleError: value => errors.push(value),
    onChange: async patch => { saved.push(patch.pageStyle); },
  };
  const actions = new Function('scope', `with (scope) { ${source.slice(start, end)}; return { changeTemplate }; }`)(scope);
  actions.changeTemplate('show'); actions.changeTemplate('emo');
  await scope.pageStyleSaveRef.current;
  assert.deepEqual(saved.map(style => [style.templateId, style.colorMode]), [['show', 'point'], ['emo', 'point']]);
  assert.ok(saved.every(style => pageStyle.validPageStyle(style)));
  assert.equal(errors.some(Boolean), false);
});
