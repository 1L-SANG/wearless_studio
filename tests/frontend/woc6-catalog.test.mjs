import test from 'node:test';
import assert from 'node:assert/strict';
import { pageTemplatesCatalog as catalog, getTemplate, sectionDefinition, validateCatalog, walkLayout, wearAdditionDefinition } from '../../src/lib/pageTemplates/catalog.js';
import { resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';

const clone = () => structuredClone(catalog);
const visible = (kind, usage) => Object.values(catalog.designs)
  .filter(design => design.kind === kind && (!usage || design.usage === usage) && !design.pickerHidden)
  .map(design => design.id).sort();
const nodesOf = id => {
  assert.ok(catalog.designs[id], `missing design ${id}`);
  const nodes = [];
  walkLayout(catalog.designs[id].layout, node => nodes.push(node));
  return nodes;
};

test('WOC6 offers the agreed layouts while saved hidden definitions remain valid', () => {
  assert.deepEqual(visible('wear', 'styling'), ['P02-D01', 'WEAR-STYLING-2COL-01', 'WEAR-STYLING-HERO-01',
    ...['COLUMN', '2COL', 'HERO'].flatMap(layout => [1, 2, 3].map(count => `WEAR-STYLING-FRAME-${layout}-0${count}`))].sort());
  assert.deepEqual(visible('wear', 'fit'), ['WEAR-FIT-2COL-01', 'WEAR-FIT-COLUMN-01', 'WEAR-FIT-HERO-01']);
  assert.deepEqual(visible('color_product'), ['CP-COLUMN-01', 'CP-EMO-01']);
  assert.deepEqual(visible('points'), ['P06-D01', 'PT-BELOW-CARD-01', 'PT-COLUMN-01', 'PT-COLUMN-CARD-01']);
  assert.deepEqual(visible('first'), ['P01-D09', 'P01-SHOW-01']);
  assert.deepEqual(visible('fabric'), ['T03-D01', 'T03-D06']);
  assert.deepEqual(visible('review'), ['I09-D06']);
  assert.deepEqual(visible('faq'), ['T08-D07']);
  assert.deepEqual(visible('sns'), []);
  for (const id of ['P02-D02', 'P04-D01', 'CP-SHOW-01', 'CP-GUIDE-01', 'PT-EMO-01', 'PT-EMO-TEXT', 'T05-D07', 'T07-D06', 'P01-D11', 'P01-D01', 'T03-TEXT-01', 'I09-D14', 'P07-D08']) {
    assert.ok(catalog.designs[id]?.layout.length, `saved design ${id} must still render`);
    assert.equal(catalog.designs[id].pickerHidden, true);
  }
  assert.equal(validateCatalog(catalog), catalog);
});

test('choice axes and eligibility minima describe each available design', () => {
  for (const kind of ['wear', 'color_product', 'points']) {
    for (const id of visible(kind)) {
      const design = catalog.designs[id];
      assert.ok(design.choiceAxes?.layout.id);
      assert.ok(design.choiceAxes?.layout.name);
      assert.ok(design.choiceAxes?.decoration.id);
      assert.ok(design.choiceAxes?.decoration.name);
    }
  }
  for (const prefix of ['WEAR-STYLING', 'WEAR-FIT']) {
    assert.equal(catalog.designs[`${prefix}-2COL-01`].minPhotos, 2);
    assert.equal(catalog.designs[`${prefix}-HERO-01`].minPhotos, 3);
  }
  assert.equal(catalog.designs['CP-EMO-01'].minColors, 2);
});

test('validators accept the photoFlow contract and reject unsupported row policies', () => {
  for (const pattern of ['twoColumn', 'heroPair']) {
    const value = clone();
    value.designs['P02-D01'].layout = [{ type: 'photoFlow', slot: '*', pattern, groupBy: 'spaceGroup', aspect: '4/5', gap: 24, captions: [] }];
    assert.equal(validateCatalog(value), value);
  }
  for (const [property, value] of [['pattern', 'masonry'], ['groupBy', 'color'], ['slot', ''], ['aspect', 'source'], ['captions', {}]]) {
    const invalid = clone();
    invalid.designs['P02-D01'].layout = [{ type: 'photoFlow', slot: '*', pattern: 'twoColumn', groupBy: 'spaceGroup', aspect: '4/5', gap: 24, captions: [], [property]: value }];
    assert.throws(() => validateCatalog(invalid), /photoFlow/);
  }
});

test('validators reject malformed choice axes and fractional or nonpositive eligibility bounds', () => {
  for (const value of [null, {}, { layout: { id: 'column', name: '' }, decoration: { id: 'basic', name: '기본' } }, { layout: { id: 'masonry', name: '기본' }, decoration: { id: 'basic', name: '기본' } }]) {
    const invalid = clone();
    invalid.designs['P02-D01'].choiceAxes = value;
    assert.throws(() => validateCatalog(invalid), /choice axes/);
  }
  for (const property of ['minPhotos', 'minColors']) {
    for (const value of [null, 0, -1, 1.5, true, '2']) {
      const invalid = clone();
      invalid.designs['P02-D01'][property] = value;
      assert.throws(() => validateCatalog(invalid), /design minimum/);
    }
  }
});

test('natural photo validation refuses a second crop or an invalid fallback ratio', () => {
  const value = clone();
  value.designs['P02-D01'].layout = [{ type: 'photo', slot: '*', fit: 'contain', aspect: 'source', fallbackAspect: '2/3' }];
  assert.equal(validateCatalog(value), value);
  for (const patch of [{ fit: 'stretch' }, { fallbackAspect: '0/3' }, { fallbackAspect: 'source' }, { cropTop: 0.1 }]) {
    const invalid = structuredClone(value);
    Object.assign(invalid.designs['P02-D01'].layout[0], patch);
    assert.throws(() => validateCatalog(invalid), /photo fit|fallback aspect|contain crop/);
  }
});

test('point choices keep natural proportions and card descriptions on one line', () => {
  for (const id of ['PT-COLUMN-01', 'P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01']) {
    const nodes = nodesOf(id);
    assert.ok(nodes.some(node => node.copyRole === 'title'));
    for (const photo of nodes.filter(node => node.type === 'photo')) {
      assert.deepEqual([photo.fit, photo.aspect, photo.fallbackAspect], ['contain', 'source', '2/3']);
      assert.equal(photo.cropTop, undefined);
    }
    if (id.includes('CARD')) {
      const panel = nodes.find(node => node.type === 'panel');
      assert.equal(panel?.fill, 'panel');
      assert.equal(panel.shadow, undefined);
      assert.equal(panel.border, undefined);
      assert.equal(nodes.find(node => node.copyRole === 'items.n.desc')?.singleLine, true);
    }
  }
  assert.equal(nodesOf('PT-COLUMN-01').some(node => node.copyRole === 'items.n.desc'), false);
});

test('new section definitions choose the current layout without changing legacy slot recipes', () => {
  for (const templateId of ['emo', 'show']) {
    const template = getTemplate(templateId);
    for (const section of template.sections) {
      if (section.kind === 'wear') assert.equal(section.designId, section.usage === 'fit' ? 'WEAR-FIT-COLUMN-01' : 'P02-D01');
      if (section.kind === 'color_product') assert.equal(section.designId, 'CP-COLUMN-01');
      if (section.kind === 'points') assert.equal(section.designId, 'PT-COLUMN-01');
      if (section.kind === 'sns') assert.equal(section.presence, 'optional');
    }
    assert.equal(wearAdditionDefinition(templateId, 'fit').designId, 'WEAR-FIT-COLUMN-01');
    assert.equal(wearAdditionDefinition(templateId, 'styling').designId, 'P02-D01');
    assert.equal(template.addPlacements.color_product.designId, 'CP-COLUMN-01');
    assert.equal(template.addPlacements.points.designId, 'PT-COLUMN-01');
    const paired = sectionDefinition(template, `${templateId}.first`, 'first', undefined, 'P01-D09-G');
    assert.equal(paired.photoSlots.find(slot => slot.slotKey === 'ghost').fill, 'reuse');
    assert.equal(template.addPlacements.color_product.photoSlots.find(slot => slot.slotKey === 'back').requiresBack, true);
  }
});

test('plain Gothic tokens override headings while body and stored catalog style stay stable', () => {
  assert.deepEqual(Object.keys(catalog.fontStyles), ['template', 'plainGothic', 'serif', 'boldGothic']);
  assert.equal(catalog.fontStyleLabels?.plainGothic, '담백 고딕');
  assert.equal(catalog.catalogVersion, 'page-templates-2026-10-28.2');
  for (const templateId of ['emo', 'show']) {
    const tokens = resolvePageTokens({ templateId, fontStyle: 'plainGothic' });
    assert.equal(tokens.fontStyle, 'plainGothic');
    assert.deepEqual([tokens.fonts.labelEn.font, tokens.fonts.labelEn.weight, tokens.fonts.labelEn.italic, tokens.fonts.labelEn.uppercase, tokens.fonts.labelEn.trackingEm], ['Pretendard', 500, false, true, 0.12]);
    assert.deepEqual([tokens.fonts.titleKo.font, tokens.fonts.titleKo.weight, tokens.fonts.titleKo.italic, tokens.fonts.titleKo.trackingEm], ['Pretendard', 600, false, -0.02]);
    assert.deepEqual(tokens.fonts.body, getTemplate(templateId).fonts.body);
  }
});
