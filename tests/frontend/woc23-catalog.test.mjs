import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { assemblePage, balancedPhotoRows, wrapText, measureTextWidth } from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog as catalog, validateCatalog, walkLayout } from '../../src/lib/pageTemplates/catalog.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignChoices } from '../../src/lib/pageTemplates/designChoices.js';
import { changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';

const fixtures = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url))).cases;
const inputOf = name => structuredClone(fixtures.find(item => item.name === name).input);
const els = blocks => blocks.flatMap(block => block.elements);
const section = (blocks, kind) => blocks.filter(block => block.pageSectionKind === kind);
const images = blocks => els(blocks).filter(el => el.type === 'image');
const copy = (blocks, role) => els(blocks).find(el => el.copyRole === role);
const rows = { 1: [1], 2: [2], 3: [3], 4: [2, 2], 5: [2, 3], 6: [3, 3], 7: [2, 2, 3], 8: [2, 3, 3] };
const colorProductCases = JSON.parse(readFileSync(new URL('../fixtures/page_templates_color_products.json', import.meta.url))).cases;

function assertPythonParity(values, colorLayout) {
  const result = spawnSync(process.env.PAGE_TEMPLATE_PYTHON || 'server/.venv/bin/python', ['-c', `import json,sys
from app.agents.page_template_assembler import assemble_v4
from app.agents.page_templates import CATALOG
payload=json.load(sys.stdin)
if payload.get('colorLayout') is not None: CATALOG['designs']['CP-COLUMN-01']['layout']=payload['colorLayout']
json.dump([assemble_v4(v['storyboard'],v['cutResults'],v['copyResults'],v['product'],True,analysis=v['analysis']) for v in payload['values']],sys.stdout,ensure_ascii=False)`],
  { input: JSON.stringify({ values, colorLayout }), encoding: 'utf8', env: { ...process.env, PYTHONPATH: 'server' }, maxBuffer: 32 * 1024 * 1024 });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), values.map(assemblePage));
}

function colorProductInput(template, designId, item) {
  const input = inputOf(`woc23-${template}-${designId}-1`);
  input.product.colors = item.names.map((name, index) => ({ id: `c${index}`, name, swatchId: 'blue', isBase: index === 0 }));
  input.storyboard = (item.renderColorIndexes || item.names.map((_, index) => index)).map(index => ({ ...input.storyboard[0],
    id: `color-${index}`, slotKey: `color_product.front.c${index}`, colorId: `c${index}` }));
  return input;
}

for (const template of ['emo', 'show']) for (const designId of ['CP-COLUMN-01', 'CP-EMO-01']) {
  for (const item of colorProductCases) test(`WOC23 product language ${template} ${designId} ${item.id}`, () => {
    const input = colorProductInput(template, designId, item);
    const labels = els(assemblePage(input)).filter(el => /^colors\.\d+\.name$/.test(el.copyRole || ''));
    const indices = designId === 'CP-COLUMN-01' ? item.names.map((_, index) => index) : item.renderColorIndexes || item.names.map((_, index) => index);
    assert.deepEqual(labels.map(el => el.text), indices.map(index => !item.english ? item.names[index] : template === 'show'
      ? item.english[index] : item.names[index].toUpperCase() === item.english[index] ? item.english[index] : `${item.names[index]} ${item.english[index]}`));
    for (const label of labels) {
      assert.equal(label.style.size, template === 'show' ? 28 : 30);
      assert.equal(label.style.font, 'Pretendard');
      assert.equal(label.style.weight, template === 'show' ? 400 : 600);
      assert.equal(label.style.tracking, template === 'show' && item.english ? 3.36 : 0);
    }
  });
}

test('WOC23 product language is identical in both assemblers for every template and color design', () => {
  assertPythonParity(['emo', 'show'].flatMap(template => ['CP-COLUMN-01', 'CP-EMO-01'].flatMap(designId =>
    colorProductCases.map(item => colorProductInput(template, designId, item)))));
});

test('WOC23 direct colors.n.en slots share the product-wide English decision', () => {
  const original = catalog.designs['CP-COLUMN-01'].layout;
  try {
    catalog.designs['CP-COLUMN-01'].layout = [
      { type: 'text', copyRole: 'colors.n.en', style: 'body', hideEmpty: true }, ...original,
    ];
    const values = [];
    for (const template of ['emo', 'show']) for (const id of ['all-known', 'one-unknown']) {
      const item = colorProductCases.find(item => item.id === id);
      const input = colorProductInput(template, 'CP-COLUMN-01', item);
      const names = els(assemblePage(input)).filter(el => el.copyRole === 'colors.0.en');
      assert.deepEqual(names.map(el => el.text), item.english ? ['BLACK'] : []);
      values.push(input);
    }
    assertPythonParity(values, catalog.designs['CP-COLUMN-01'].layout);
  } finally {
    catalog.designs['CP-COLUMN-01'].layout = original;
  }
});

for (const template of ['emo', 'show']) test(`WOC23 product language leaves the ${template} COLOR fact row unchanged`, () => {
  const input = inputOf(`woc23-${template}-T06-D01`);
  input.product.colors = colorProductCases.find(item => item.id === 'one-unknown').names.map((name, index) => ({ id: `c${index}`, name, swatchId: 'blue', isBase: index === 0 }));
  input.storyboard = input.storyboard.filter(card => ['first', 'intro'].includes(card.pageSectionKind));
  assert.equal(els(assemblePage(input)).find(el => el.factLabel === 'COLOR' && el.factPart === 'value').text, '블랙, 아이보리, 자두, 그레이');
  assertPythonParity([input]);
});

for (const template of ['emo', 'show']) {
  const type = catalog.templates.find(item => item.id === template);
  test(`WOC23 ${template}: role sizes are template values`, () => {
    assert.deepEqual(Object.fromEntries(Object.entries(type.typography).map(([role, value]) => [role, value.size])), template === 'show'
      ? { label: 42, productName: 54, title: 46, intro: 30, body: 32, small: 28, colorName: 28 }
      : { label: 48, productName: 51, title: 48, intro: 32, body: 32, small: 28, colorName: 30 });
  });
  for (const designId of ['T06-D01', 'T06-COMMENT-01']) test(`WOC23 ${template}: ${designId} puts the product in the board after a caption-free photo`, () => {
    const input = inputOf(`woc23-${template}-${designId}`), blocks = assemblePage(input);
    const first = section(blocks, 'first'), intro = section(blocks, 'intro');
    assert.equal(els(first).filter(el => el.type === 'text').length, 0);
    assert.equal(first[0].h, images(first)[0].h);
    assert.equal(copy(intro, 'name').text, input.product.name);
    assert.equal(copy(intro, 'name').style.size, type.typography.productName.size);
    assert.equal(copy(intro, 'name').style.weight, type.typography.productName.weight);
    assert.equal(copy(intro, 'body').text, input.analysis.mdComment);
    assert.ok(els(intro).some(el => el.factLabel === 'FABRIC'));
    assert.ok(!els(intro).some(el => el.copyRole?.startsWith('summary.')));
    if (designId === 'T06-COMMENT-01') {
      assert.equal(intro[0].bg, type.colors.invertBg);
      assert.equal(copy(intro, 'label').text.toUpperCase(), 'COMMENT');
      assert.equal(copy(intro, 'label').style.align, type.align);
    } else assert.equal(intro[0].bg, catalog.canvasColor);
  });
  test(`WOC23 ${template}: the visible first-page choice selects the dark intro without changing saved intro cards`, () => {
    const input = inputOf(`${template}-2-mono`), first = sectionRuns(input.storyboard)[0];
    assert.ok(sectionDesignChoices(first, { storyboard: input.storyboard, product: input.product }).some(design => design.id === 'P01-SHOW-COMMENT-01'));
    const savedIntro = input.storyboard.filter(card => card.pageSectionKind === 'intro');
    input.storyboard = changeTemplateSectionDesign(input.storyboard, first.key, 'P01-SHOW-COMMENT-01', { product: input.product });
    assert.deepEqual(input.storyboard.filter(card => card.pageSectionKind === 'intro'), savedIntro);
    const blocks = assemblePage(input), intro = section(blocks, 'intro');
    assert.equal(intro[0].pageDesignId, 'T06-COMMENT-01');
    assert.equal(copy(intro, 'label').text.toUpperCase(), 'COMMENT');
    assert.equal(intro[0].bg, type.colors.invertBg);
  });
  for (const [count, expected] of Object.entries(rows)) test(`WOC23 ${template}: ${count} color thumbnails use ${expected.join('+')} rows and row-specific widths`, () => {
    const blocks = assemblePage(inputOf(`woc23-${template}-CP-EMO-01-${count}`));
    const photos = images(blocks);
    assert.deepEqual(balancedPhotoRows(Number(count)), expected);
    assert.equal(blocks.length - 4, expected.length);
    let offset = 0;
    expected.forEach(columns => {
      const row = photos.slice(offset, offset + columns), width = Math.floor((888 - 24 * (columns - 1)) / columns);
      assert.deepEqual(row.map(photo => photo.w), Array(columns).fill(width));
      assert.deepEqual(row.map(photo => photo.x), Array.from({ length: columns }, (_, index) => 56 + index * (width + 24)));
      row.forEach(photo => {
        const block = blocks.find(block => block.elements.includes(photo));
        const label = block.elements.find(el => el.copyRole?.endsWith('.name') && el.sourceBlockId);
        assert.equal(label.style.size, type.typography.colorName.size);
      });
      offset += columns;
    });
    const names = els(blocks).filter(el => /^colors\.\d+\.name$/.test(el.copyRole || ''));
    assert.equal(names.length, Number(count));
    assert.ok(names.every(el => el.style.weight === type.typography.colorName.weight));
    assert.equal(els(blocks).filter(el => el.shape === 'circle').length, Number(count));
  });
  test(`WOC23 ${template}: color A lists names once before full-width photos`, () => {
    const input = inputOf(`woc23-${template}-CP-COLUMN-01-5`), blocks = assemblePage(input);
    const names = els(blocks).filter(el => /^colors\.\d+\.name$/.test(el.copyRole || ''));
    assert.equal(names.length, 5);
    assert.ok(names.every(el => el.y < images(blocks)[0].y));
    assert.ok(images(blocks).every(photo => photo.x === 0 && photo.w === 1000));
    assert.equal(els(blocks).filter(el => el.copyRole?.startsWith('cap.')).length, 0);
  });
  test(`WOC23 ${template}: all shared sections start with the template label, including LOOKBOOK`, () => {
    const input = inputOf(`${template}-2-mono`);
    for (const design of Object.values(catalog.designs).filter(design => !design.pickerHidden && design.kind !== 'first' && design.id !== 'T06-D01')) {
      const run = input.storyboard.filter(card => card.pageSectionKind === design.kind && (design.kind !== 'wear' || card.pageSectionUsage === design.usage));
      if (!run.length) continue;
      const value = { ...input, storyboard: run.map(card => ({ ...card, pageDesignId: design.id })) };
      const blocks = assemblePage(value), label = els(blocks).find(el => el.copyRole === (design.kind === 'color_product' ? 'title' : 'label'));
      assert.equal(label.x, 56, design.id);
      assert.equal(label.style.size, type.typography.label.size, design.id);
      assert.equal(label.style.align, type.align, design.id);
      assert.equal(label.y, Math.min(...blocks[0].elements.filter(el => el.type === 'text' || el.type === 'image').map(el => el.y)), design.id);
      if (design.kind === 'wear' && design.usage === 'fit') assert.equal(label.text.toUpperCase(), 'LOOKBOOK');
    }
  });
  test(`WOC23 ${template}: full-width wear keeps only the template photo gap`, () => {
    const input = inputOf(`woc20-${template}-wear-sets`), blocks = section(assemblePage(input), 'wear');
    for (const key of new Set(blocks.map(block => block.pageSectionKey))) {
      const group = blocks.filter(block => block.pageSectionKey === key), photos = images(group);
      assert.ok(photos.every(photo => photo.x === 0 && photo.w === 1000));
      group.slice(0, -1).forEach(block => {
        const photo = images([block]).at(-1);
        assert.equal(block.h - photo.y - photo.h, template === 'show' ? 40 : 0);
      });
      assert.equal(els(group).filter(el => el.copyRole?.startsWith('cap.') || el.copyRole?.startsWith('colorName.')).length, 0);
    }
  });
  test(`WOC23 ${template}: a frame groups its small shop name below the product title`, () => {
    const input = inputOf(`woc20-${template}-wear-sets`);
    const run = input.storyboard.filter(card => card.pageSectionUsage === 'styling');
    input.storyboard = (run.length ? run : input.storyboard.slice(0, 2)).map(card => ({ ...card, pageSectionUsage: 'styling', pageDesignId: 'WEAR-STYLING-FRAME-COLUMN-01' }));
    input.shopName = '데일리 몰';
    const blocks = assemblePage(input), name = copy(blocks, 'name'), shop = copy(blocks, 'who');
    const photo = images(blocks).find(el => !el.blur);
    assert.equal(name.style.size, template === 'show' ? 54 : 51);
    assert.equal(name.style.align, type.align);
    assert.equal(shop.style.size, 28);
    assert.ok(shop.y >= name.y + name.h);
    assert.ok(photo.y > shop.y + shop.h);
  });
  test(`WOC23 ${template}: shared detail and outside fabric copy follow the selected template`, () => {
    const input = inputOf(`woc11-${template}-PT-TEXT-PHOTO-01-template-mono`);
    input.analysis.partDescriptionCopy.forEach(part => { part.desc = '세탁 뒤에도 뜨지 않게 눌러 박은 카라 끝'; });
    const points = section(assemblePage(input), 'points'), title = copy(points, 'items.0.name'), desc = copy(points, 'items.0.desc');
    assert.equal(title.style.size, type.typography.title.size);
    assert.equal(title.style.weight, type.typography.title.weight);
    assert.equal(title.style.align, type.align);
    assert.match(title.text, /^1\. /);
    assert.equal(desc.style.size, 32); assert.equal(desc.style.weight, 400);
    assert.equal(desc.h, desc.style.lineHeight);
    const fabricInput = inputOf(`${template}-2-mono`);
    fabricInput.storyboard = fabricInput.storyboard.filter(card => card.pageSectionKind === 'fabric').map(card => ({ ...card, pageDesignId: 'T03-D06' }));
    fabricInput.copyResults = [{ blockId: fabricInput.storyboard[0].id, texts: [
      { role: 'title', text: '부드러운 면 혼방 골지' },
      { role: 'body', text: '면이 많아 피부에 닿는 느낌이 부드럽습니다.\n폴리에스터를 섞어 빨아도 골지가 처지지 않습니다.' },
    ] }];
    const fabric = assemblePage(fabricInput), photo = images(fabric)[0], fabricTitle = copy(fabric, 'title');
    assert.equal(copy(fabric, 'label').x, 56);
    assert.ok(fabricTitle.y >= photo.y + photo.h + 44);
    assert.equal(fabricTitle.style.size, type.typography.title.size);
    assert.equal(copy(fabric, 'mix').style.size, 28);
    assert.equal(copy(fabric, 'body').style.size, 32);
    assert.equal(copy(fabric, 'body').h, copy(fabric, 'body').style.lineHeight * 2);
  });
  for (const fontStyle of Object.keys(catalog.fontStyles)) test(`WOC23 ${template}/${fontStyle}: narrow fabric copy keeps 32px text and fits its measured block`, () => {
    const input = inputOf(`${template}-2-mono`);
    input.analysis.pageStyle.fontStyle = fontStyle;
    input.storyboard = input.storyboard.filter(card => card.pageSectionKind === 'fabric').map(card => ({ ...card, pageDesignId: 'T03-D01' }));
    input.copyResults = [{ blockId: input.storyboard[0].id, texts: [
      { role: 'title', text: '부드러운 면 혼방 골지' },
      { role: 'body', text: '면이 많아 피부에 닿는 느낌이 부드럽습니다.\n폴리에스터를 섞어 세탁 뒤에도 골지가 처지지 않습니다.' },
    ] }];
    const blocks = assemblePage(input), body = copy(blocks, 'body');
    assert.equal(body.w, 428); assert.equal(body.style.size, 32);
    assert.equal(body.style.font, 'Pretendard');
    const lines = wrapText(body.text, body.w, body.style.size, catalog.textMetrics, body.style.tracking);
    assert.equal(body.h, lines.length * body.style.lineHeight);
    assert.ok(lines.every(line => measureTextWidth(line, 10000, body.style.size, catalog.textMetrics, body.style.tracking) <= body.w));
    for (const block of section(blocks, 'fabric')) assert.ok(block.elements.every(el => el.y + el.h <= block.h));
  });
}

const colorNameFixture = JSON.parse(readFileSync(new URL('../fixtures/page_templates_color_names.json', import.meta.url)));

test('WOC23 Korean color dictionary includes the clothing names with exact uppercase translations', () => {
  assert.equal(Object.hasOwn(catalog, 'swatchEnglishNames'), false);
  for (const item of colorNameFixture.cases.slice(0, colorNameFixture.dictionaryCases)) {
    assert.equal(catalog.colorNameEnglish[item.name], item.english, item.name);
  }
});

test('WOC23 individual Korean names, whitespace, unknown compounds and seller English match across both assemblers and color designs', () => {
  const values = [];
  for (const template of ['emo', 'show']) for (const designId of ['CP-COLUMN-01', 'CP-EMO-01']) {
    for (const item of colorNameFixture.cases) {
      const cases = [item];
      const input = inputOf(`woc23-${template}-${designId}-1`);
      input.product.colors = cases.map(({ name, label, swatchId }, index) => ({ id: `c${index}`, name, label, swatchId, isBase: index === 0 }));
      input.storyboard = input.product.colors.map((color, index) => ({ ...input.storyboard[0], id: `color-${index}`, slotKey: `color_product.front.${color.id}`, colorId: color.id }));
      const names = els(assemblePage(input)).filter(el => /^colors\.\d+\.name$/.test(el.copyRole || '')).map(el => el.text);
      cases.forEach((item, index) => {
        const name = (item.expectedName ?? item.name).trim(), en = item.english;
        assert.equal(names[index], template === 'show' ? en || name : en ? name.toUpperCase() === en ? en : `${name} ${en}` : name, `${template} ${designId} ${item.id}`);
      });
      values.push(input);
    }
  }
  assertPythonParity(values);
});

test('WOC23 both templates offer the same shared color, fabric and intro designs', () => {
  const product = { name: '니트', clothingType: 'top', colors: [{ id: 'base', isBase: true }, { id: 'extra' }] };
  const choices = templateId => {
    const analysis = { pageStyle: { ...defaultPageStyle(), templateId }, targetGenders: ['women'],
      detailRecommendations: { version: 1, status: 'ready', candidates: [{ id: 'fabric', kind: 'fabric', label: '원단', rank: 1, informationGroup: 'fabric', photoUse: 'standalone', visibility: 'clear', direction: 'front' }] } };
    const storyboard = seedStoryboard(product, analysis);
    return Object.fromEntries(sectionRuns(storyboard).filter(run => ['first', 'intro', 'fabric', 'color_product'].includes(run.kind))
      .map(run => [run.kind, sectionDesignChoices(run, { storyboard, product }).map(design => design.id).sort()]));
  };
  assert.deepEqual(choices('show'), choices('emo'));
  assert.ok(choices('show').intro.includes('T06-COMMENT-01'));
  for (const design of Object.values(catalog.designs)) walkLayout(design.layout, node => {
    assert.ok(!/^cap\./.test(node.copyRole || ''), design.id);
    assert.ok(!(design.kind === 'wear' && /^colorName\./.test(node.copyRole || '')), design.id);
  });
});

for (const mutate of [c => { delete c.colorNameEnglish; }, c => { c.templates[0].typography.body.size = 0; },
  c => { c.designs['CP-EMO-01'].layout[1].balancedRows = 'yes'; }]) test('WOC23 invalid shared catalog values fail early', () => {
  const value = structuredClone(catalog); mutate(value); assert.throws(() => validateCatalog(value));
});
