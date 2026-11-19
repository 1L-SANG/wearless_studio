import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { getTemplate, pageTemplatesCatalog as catalog, validateCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';
import { sectionDesignChoices } from '../../src/lib/pageTemplates/designChoices.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';

const CARD = 'PT-PHOTO-CARD-01', PLAIN = 'PT-TEXT-PHOTO-01';
const oldDesigns = ['PT-COLUMN-01', 'P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01'];
const labels = ['스냅 단추 디테일', '드롭 숄더 핏'];
const descriptions = ['은은한 메탈 스냅 단추로\n빈티지한 분위기를 더해요', '어깨선이 자연스럽게 내려와서\n편안하게 입기 좋아요'];
const inputFor = (templateId = 'emo', designId = CARD, patch = {}) => {
  const key = `${templateId}.points`;
  return { storyboard: [{ id: 'lead', taxonomyVersion: 4, source: 'text', pageSectionKey: key, pageSectionKind: 'points', pageDesignId: designId },
    ...[0, 1].map(i => ({ id: `detail${i}`, taxonomyVersion: 4, source: 'ai', pageSectionKey: key, pageSectionKind: 'points', pageDesignId: designId,
      slotKey: `points.detail${i + 1}`, cutType: 'product', shot: 'detail', contentRole: 'detail', detailTargetId: `d${i}` }))],
    cutResults: [0, 1].map(i => ({ blockId: `detail${i}`, imageUrl: `detail:${i}`, width: 1000, height: 1500 })), copyResults: [],
    product: { colors: [{ id: 'blue', swatchId: 'blue', isBase: true }] },
    analysis: { detailRecommendations: { candidates: labels.map((label, i) => ({ id: `d${i}`, label })) },
      partDescriptionCopy: labels.map((point, i) => ({ point, desc: descriptions[i] })) },
    pageStyle: { templateId, fontStyle: 'template', colorMode: 'mono' }, ...patch };
};
const blocksOf = input => assemblePage(input).filter(block => block.pageSectionKind === 'points');
const itemsOf = input => blocksOf(input).flatMap(block => block.elements);
const textOf = (items, role) => items.find(item => item.copyRole === role);
const photoOf = items => items.find(item => item.type === 'image');
const globalItems = input => { let y = 0; return blocksOf(input).flatMap(block => { const elements = block.elements.map(el => ({ ...el, y: el.y + y })); y += block.h; return elements; }); };
const wornInputFor = (designId = PLAIN, meta = { width: 1000, height: 1500 }, mine = false) => {
  const input = inputFor('emo', designId);
  const source = { id: 'wear', taxonomyVersion: 4, source: mine ? 'mine' : 'ai', pageSectionKey: 'emo.wear_styling', pageSectionKind: 'wear',
    pageSectionUsage: 'styling', pageDesignId: 'P02-D01', slotKey: 'wear_styling.g1', cutType: 'styling', shot: 'full', direction: 'front',
    ...(mine ? { ownImages: ['seller:wear'] } : {}) };
  const lead = input.storyboard[0];
  lead.pointItems = [{ kind: 'wornCrop', sourceBlockId: 'wear', bodyPart: 'shoulder', label: labels[1], cropRegion: [.15, .2, .4, .2] }];
  input.storyboard = [source, lead];
  input.cutResults = [{ blockId: 'wear', imageUrl: mine ? 'seller:wear' : 'generated:wear', ...meta }];
  return input;
};

// These regressions catch a missing picker filter/default and preserve saved recipes.
test('WOC11 visible points are two flat designs and both creation defaults render them', () => {
  assert.deepEqual(Object.values(catalog.designs).filter(d => d.kind === 'points' && !d.pickerHidden).map(d => d.id).sort(), [CARD, PLAIN].sort());
  for (const [templateId, expected] of [['emo', CARD], ['show', PLAIN]]) {
    const template = getTemplate(templateId);
    assert.equal(template.sections.find(s => s.kind === 'points').designId, expected);
    assert.equal(template.addPlacements.points.designId, expected);
    const input = inputFor(templateId, expected), run = sectionRuns(input.storyboard)[0];
    assert.deepEqual(sectionDesignChoices(run, { storyboard: input.storyboard, product: input.product }).map(d => d.id).sort(), [CARD, PLAIN].sort());
    for (const id of [CARD, PLAIN]) assert.equal(catalog.designs[id].choiceAxes, undefined);
  }
  for (const id of oldDesigns) assert.equal(catalog.designs[id].pickerHidden, true);
});

for (const templateId of ['emo', 'show']) for (const fontStyle of Object.keys(catalog.fontStyles)) {
  test(`WOC11 card typography and palette follow ${templateId}/${fontStyle}`, () => {
    const input = inputFor(templateId, CARD, { pageStyle: { templateId, fontStyle, colorMode: 'point' } });
    const tokens = resolvePageTokens(input.pageStyle, 'blue'), type = tokens.template.typography;
    const blocks = blocksOf(input), items = itemsOf(input), image = photoOf(items);
    const title = textOf(items, 'items.0.name'), desc = textOf(items, 'items.0.desc'), label = textOf(items, 'label');
    assert.equal(blocks[0].bg, tokens.colors.panel);
    assert.deepEqual([image.x, image.w, image.h, image.radius], [56, 888, 1110, 0]);
    assert.ok(items.some(el => el.type === 'shape' && el.fill === '#ffffff' && el.x === 56 && el.w === 888));
    assert.equal(label.style.font, tokens.fonts.labelEn.font); assert.equal(label.style.size, templateId === 'show' ? 42 : 48);
    assert.deepEqual([title.x, title.w, title.style.size, title.style.font, title.style.weight, title.style.align], [106, 788, templateId === 'show' ? 46 : 48, tokens.fonts.titleKo.font, templateId === 'show' ? 700 : tokens.fonts.titleKo.weight, tokens.template.align]);
    assert.deepEqual([desc.x, desc.w, desc.style.size, desc.style.weight, desc.style.lineHeight], [106, 788, 32, 400, templateId === 'show' ? 48 : 52]);
    assert.equal(desc.text, descriptions[0]); assert.equal(desc.h, desc.style.lineHeight * 2);
    assert.equal(title.y - image.y - image.h, 50);
    const shapes = globalItems(input).filter(el => el.type === 'shape' && el.fill === '#ffffff' && el.w === 888);
    assert.equal(shapes[1].y - shapes[0].y - shapes[0].h, 50);
});
  test(`WOC11 plain numbering and measured gaps follow ${templateId}/${fontStyle}`, () => {
    const input = inputFor(templateId, PLAIN, { pageStyle: { templateId, fontStyle, colorMode: 'point' } });
    const tokens = resolvePageTokens(input.pageStyle, 'blue'), items = globalItems(input);
    const title = textOf(items, 'items.0.name'), desc = textOf(items, 'items.0.desc'), image = photoOf(items), next = textOf(items, 'items.1.name');
    assert.ok(blocksOf(input).every(block => block.bg === '#ffffff'));
    assert.equal(textOf(items, 'label').style.size, templateId === 'show' ? 42 : 48);
    assert.equal(title.text, `1. ${labels[0]}`); assert.equal(next.text, `2. ${labels[1]}`);
    assert.deepEqual([title.x, title.w, title.style.size, title.style.font, title.style.weight, title.style.align], [56, 888, templateId === 'show' ? 46 : 48, tokens.fonts.titleKo.font, templateId === 'show' ? 700 : tokens.fonts.titleKo.weight, tokens.template.align]);
    assert.deepEqual([desc.x, desc.w, desc.style.size, desc.style.weight, desc.style.lineHeight, desc.style.tracking], [56, 888, 32, 400, templateId === 'show' ? 48 : 52, 0]);
    assert.deepEqual([image.x, image.w, image.h], [56, 888, 1110]);
    assert.equal(desc.y - title.y - title.h, 14); assert.equal(image.y - desc.y - desc.h, 32);
    assert.equal(next.y - image.y - image.h, 120);
    assert.equal(desc.text, descriptions[0].replace(/\s+/g, ' '));
    const lastBlock = blocksOf(input).at(-1), lastPhoto = lastBlock.elements.find(el => el.type === 'image');
    assert.equal(lastBlock.h - lastPhoto.y - lastPhoto.h - tokens.template.spacing.sectionGap, 60);
});
}

test('WOC11 card neutral and point surfaces keep the caption white', () => {
  for (const colorMode of ['mono', 'point']) for (const swatch of ['white', 'gray', 'black', 'blue', 'beige']) {
    const input = inputFor('emo', CARD, { baseSwatchId: swatch, pageStyle: { templateId: 'emo', colorMode } });
    const tokens = resolvePageTokens(input.pageStyle, swatch);
    assert.equal(blocksOf(input)[0].bg, tokens.colors.panel);
    assert.ok(itemsOf(input).some(el => el.type === 'shape' && el.fill === '#ffffff' && el.w === 888));
  }
});

test('WOC11 center-cover photos retain 83% of a 2:3 source and handle landscape or unknown metadata', () => {
  for (const design of [CARD, PLAIN]) for (const meta of [{ width: 1000, height: 1500 }, { width: 1500, height: 1000 }, {}, { width: 1000, height: 1000, dimensionsKnown: false }]) {
    const input = inputFor('emo', design);
    input.cutResults[0] = { blockId: 'detail0', imageUrl: 'detail:0', ...meta };
    const image = photoOf(itemsOf(input));
    assert.equal(image.w / image.h, .8);
    if (!meta.width || meta.dimensionsKnown === false) {
      assert.equal(image.crop, undefined, 'unknown images use native center-cover without a guessed source ratio');
      continue;
    }
    assert.ok(image.crop.iw >= image.w && image.crop.ih >= image.h);
    assert.equal(image.crop.ox, Math.floor((image.crop.iw - image.w) / 2 + .5));
    assert.equal(image.crop.oy, Math.floor((image.crop.ih - image.h) / 2 + .5));
    if (meta.height === 1500 || !meta.width || meta.dimensionsKnown === false) assert.ok(Math.abs(image.h / image.crop.ih - 5 / 6) < .001);
  }
});

test('WOC11 seller-owned photos use matching metadata and ignore a previous generated image size', () => {
  const input = inputFor('emo', PLAIN);
  Object.assign(input.storyboard[1], { source: 'mine', ownImages: ['seller:landscape'], cutType: null });
  input.product.colors[0].images = [{ src: 'seller:landscape', width: 1500, height: 1000 }];
  const image = photoOf(itemsOf(input));
  assert.equal(image.src, 'seller:landscape');
  assert.deepEqual(image.crop, { iw: 1665, ih: 1110, ox: 389, oy: 0 });
  input.product.colors[0].images = [];
  assert.equal(photoOf(itemsOf(input)).crop, undefined, 'stale generated size cannot distort a seller photo');
});

test('WOC11 saved worn regions center-cover the fixed viewport without mutating source regions', () => {
  const input = wornInputFor(), original = structuredClone(input);
  const items = itemsOf(input), image = photoOf(items);
  assert.deepEqual([image.x, image.w, image.h, image.pointItemKind], [56, 888, 1110, 'wornCrop']);
  assert.deepEqual(image.crop, { iw: 3700, ih: 5550, ox: 851, oy: 1110 });
  assert.equal(textOf(items, 'items.0.name').text, `1. ${labels[1]}`);
  assert.equal(textOf(items, 'items.0.desc').text, descriptions[1].replace(/\s+/g, ' '));
  assert.deepEqual(input, original);
  const old = photoOf(itemsOf(wornInputFor('P06-D01')));
  assert.deepEqual([old.w, old.h, old.crop], [532, 399, { iw: 1000, ih: 1500, ox: 84, oy: 251 }]);
});

test('WOC11 worn sources use actual landscape size or the 3:4 fallback and preserve repeated source items', () => {
  for (const meta of [{ width: 1500, height: 1000 }, {}, { width: 1000, height: 1000, dimensionsKnown: false }]) {
    const input = wornInputFor(PLAIN, meta);
    input.storyboard[1].pointItems.push({ ...input.storyboard[1].pointItems[0], label: labels[0], bodyPart: 'hem', cropRegion: [.1, .6, .6, .3] });
    const images = itemsOf(input).filter(el => el.type === 'image');
    assert.equal(images.length, 2);
    assert.ok(images.every(image => image.w === 888 && image.h === 1110 && image.crop.iw >= image.w && image.crop.ih >= image.h));
    assert.ok(Math.abs(images[0].crop.iw / images[0].crop.ih - (meta.height === 1000 && meta.dimensionsKnown !== false ? 1.5 : .75)) < .001);
  }
});

test('WOC11 loose photos receive their names, descriptions and fixed viewport without overflow', () => {
  for (const design of [CARD, PLAIN]) {
    const input = inputFor('emo', design); input.storyboard[2].slotKey = null;
    const items = itemsOf(input);
    assert.equal(items.filter(el => el.type === 'image').length, 2);
    assert.equal(textOf(items, 'items.1.name').text, `${design === PLAIN ? '2. ' : ''}${labels[1]}`);
    assert.equal(textOf(items, 'items.1.desc').text, design === PLAIN ? descriptions[1].replace(/\s+/g, ' ') : descriptions[1]);
    assert.ok(items.filter(el => el.type === 'image').every(el => el.w / el.h === .8));
  }
});

test('WOC11 consumes part copy only while saved legacy descriptions keep old seller-copy fallback', () => {
  for (const design of [CARD, PLAIN, 'P06-D01']) {
    const input = inputFor('emo', design); input.analysis.partDescriptionCopy = [];
    input.analysis.featureCopy = [{ point: labels[0], desc: '기존 판매 특징 문장' }];
    input.copyResults = [{ blockId: 'lead', texts: [{ role: 'items.0.desc', text: '기존 역할 문장' }] }];
    const elements = itemsOf(input);
    if (design === 'P06-D01') assert.equal(textOf(elements, 'items.0.desc').text, '기존 판매 특징 문장');
    else {
      assert.ok(!textOf(elements, 'items.0.desc'));
      assert.ok(!elements.some(el => el.type === 'image'), 'completed missing descriptions remove their whole cards');
    }
  }
});

test('WOC11 text and repeat policy validators reject malformed opt-ins', () => {
  for (const [property, value] of [['weight', 'thin'], ['weight', 300], ['numberPrefix', '1.'], ['trackingEm', 'tight'], ['trackingEm', -1], ['lineHeight', 0], ['lineHeight', true], ['copyPolicy', 'feature']]) {
    const invalid = structuredClone(catalog);
    invalid.designs['P06-D01'].layout[0][property] = value;
    assert.throws(() => validateCatalog(invalid), /text weight|number prefix|text tracking|text line height|copy policy/);
  }
  for (const [property, value] of [['includeLoose', 'yes'], ['itemGap', -1], ['itemGap', true]]) {
    const invalid = structuredClone(catalog); invalid.designs['P06-D01'].layout[2][property] = value;
    assert.throws(() => validateCatalog(invalid), /loose|item gap/);
  }
  const valid = structuredClone(catalog);
  Object.assign(valid.designs['P06-D01'].layout[0], { weight: 'light', trackingEm: -.05, lineHeight: 1.35, numberPrefix: true });
  assert.equal(validateCatalog(valid), valid);
});

const legacyDigests = {
  'emo:PT-COLUMN-01': '6932df6218686563dfbfb8359a2f25e35342ef276d6b8b8e540debd894af5538',
  'emo:P06-D01': 'dc693db9476446363632edea3babe12da9f17ceacdef533b131f7d3a26fe38cc',
  'emo:PT-COLUMN-CARD-01': 'a816a00ff49207c50a27aecc2c96718830523d04888c4d5e242da9b6d04cb618',
  'emo:PT-BELOW-CARD-01': 'bcec98e42b8c7c4a5de95881620954070ec25c7c1c0a97bf85bdd40667715bdf',
  'show:PT-COLUMN-01': '9665f62b58c4a655b6229f6c8f35743b0e6ab9a1e971d49e02e3e355adc82acf',
  'show:P06-D01': 'ffe2e54f9d1c9d85547d54ccf799b4d38f7619f5687856523f18d11ab57c5f8a',
  'show:PT-COLUMN-CARD-01': '0f1e59e995b2da06209a14b44999953cb002ab839003dfb63ed141700941118c',
  'show:PT-BELOW-CARD-01': 'e139cb45c686a9f39560db86e722d6f5f5580bba265599b00817cb587e41c4bf',
};
test('WOC11 preserves legacy rendering apart from contain and populated-slot placeholder metadata', () => {
  for (const [key, expected] of Object.entries(legacyDigests)) {
    const [templateId, designId] = key.split(':');
    const blocks = blocksOf(inputFor(templateId, designId));
    const legacy = blocks.map(block => ({ ...block, elements: block.elements.map(element => {
      if (element.type !== 'image') {
        if (element.type === 'text' && element.text?.trim()) {
          const { placeholder, ...previous } = element;
          if (element.copyRole === 'label' && /^[\x20-\x7e]+$/.test(element.text)) delete previous.keepWords;
          return previous;
        }
        return element;
      }
      assert.equal(element.fit, 'contain', key);
      const { fit, ...previous } = element;
      return previous;
    }) }));
    assert.equal(createHash('sha256').update(JSON.stringify(legacy)).digest('hex'), expected, key);
  }
});

test('WOC11 JavaScript and Python engines agree for styles, dimensions, loose cards and old designs', () => {
  const inputs = ['emo', 'show'].flatMap(templateId => [CARD, PLAIN, ...oldDesigns].flatMap(designId => Object.keys(catalog.fontStyles).map(fontStyle => inputFor(templateId, designId, { pageStyle: { templateId, fontStyle, colorMode: 'point' } }))));
  for (const designId of [CARD, PLAIN]) {
    for (const meta of [{ width: 1500, height: 1000 }, {}, { width: 1000, height: 1000, dimensionsKnown: false }]) {
      const input = inputFor('show', designId); input.storyboard[2].slotKey = null;
      input.cutResults[0] = { blockId: 'detail0', imageUrl: 'photo', ...meta }; inputs.push(input);
    }
    for (const meta of [{ width: 1000, height: 1500 }, { width: 1500, height: 1000 }, {}, { width: 1000, height: 1000, dimensionsKnown: false }]) {
      for (const mine of [false, true]) inputs.push(wornInputFor(designId, meta, mine));
    }
    const mine = inputFor('emo', designId);
    Object.assign(mine.storyboard[1], { source: 'mine', ownImages: ['seller:landscape'], cutType: null });
    mine.product.colors[0].images = [{ src: 'seller:landscape', width: 1500, height: 1000 }];
    inputs.push(mine);
  }
  const script = `import json,sys\nfrom app.agents.page_template_assembler import assemble_v4\nvalues=json.load(sys.stdin)\njson.dump([assemble_v4(v['storyboard'],v['cutResults'],v['copyResults'],v['product'],True,analysis=v['analysis'],page_style=v['pageStyle']) for v in values],sys.stdout,ensure_ascii=False)`;
  const result = spawnSync('server/.venv/bin/python', ['-c', script], { input: JSON.stringify(inputs), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, env: { ...process.env, PYTHONPATH: 'server', PYTHONDONTWRITEBYTECODE: '1' } });
  assert.equal(result.status, 0, result.stderr);
  const outputs = JSON.parse(result.stdout);
  inputs.forEach((input, index) => assert.deepEqual(assemblePage(input), outputs[index], `parity case ${index}`));
});
