import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { getTemplate, pageTemplatesCatalog as catalog, sectionDefinition, validateCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { sectionDesignChoices } from '../../src/lib/pageTemplates/designChoices.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';

const fixtures = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url))).cases;
const firstPairs = [['P01-D09', 'P01-D09-G'], ['P01-SHOW-01', 'P01-SHOW-01-G'], ['P01-D11', 'P01-D01']];
const fabricPairs = [['T03-D01', 'T03-D01-P'], ['T03-D06', 'T03-D06-P']];
const images = (output, kind) => output.filter(block => block.pageSectionKind === kind).flatMap(block => block.elements).filter(element => element.type === 'image');
const inputFor = (templateId = 'emo', kind = 'first', designId = 'P01-D09-G') => {
  const input = structuredClone(fixtures.find(item => item.name === `${templateId}-2-mono`).input);
  for (const block of input.storyboard) {
    if (block.pageSectionKind === 'color_product') block.productPresentation = 'ghost';
    if (block.pageSectionKind === kind) block.pageDesignId = designId;
  }
  return input;
};
const originalFront = input => input.storyboard.find(block => block.pageSectionKind === 'color_product' && block.direction === 'front' && block.colorId === 'blue');
const extraPhoto = (input, kind = 'first') => images(assemblePage(input), kind).find(image => image.slotKey === `${kind}.${kind === 'first' ? 'ghost' : 'product'}`);
const ownGhost = input => {
  const first = input.storyboard[0];
  const card = {
    id: 'dedicated-first-ghost', taxonomyVersion: 4, source: 'ai',
    pageSectionKey: first.pageSectionKey, pageSectionKind: 'first', pageDesignId: first.pageDesignId,
    sectionId: first.sectionId, sectionLayout: 'stack', sectionCustom: true, slotKey: 'first.ghost',
    cutType: 'product', shot: 'ghost', productPresentation: 'ghost', direction: 'front', colorId: 'blue',
    contentRole: 'productOverview', sectionRole: 'product', faceExposure: null, matchIds: [], refScope: 'all',
  };
  input.storyboard.splice(1, 0, card);
  input.cutResults.push({ blockId: card.id, imageUrl: 'generated:dedicated-ghost', width: 1024, height: 1365 });
  return card;
};

test('WOC5 paired layouts survive the current picker hiding policy', () => {
  const expected = [...[...firstPairs, ...fabricPairs].map(([, pair]) => pair),
    'P01-D11', 'P02-D02', 'P04-D01', 'CP-SHOW-01', 'CP-GUIDE-01', 'T03-GUIDE-01', 'T03-TEXT-01',
    'PT-EMO-01', 'PT-EMO-TEXT', 'T05-D07', 'T07-D06', 'I09-D14', 'P07-D08', 'I09-SCRIM-01', 'T08-SCRIM-01',
    'PT-COLUMN-01', 'P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01'].sort();
  assert.deepEqual(Object.values(catalog.designs).filter(design => design.pickerHidden).map(design => design.id).sort(), expected);
  for (const [base, pair] of [...firstPairs.slice(0, 2), ...fabricPairs]) {
    assert.deepEqual(catalog.designs[pair].layout.slice(0, -1), catalog.designs[base].layout);
    assert.equal(catalog.designs[pair].layout.at(-1).type, 'panel');
    assert.equal(catalog.designs[pair].layout.at(-1).nodes[0].slot, pair.endsWith('-P') ? 'product' : 'ghost');
  }
  assert.equal(expected.length, 24);
  assert.equal(Object.keys(catalog.designs).length, 52);
  assert.equal(validateCatalog(catalog), catalog);
});

test('pickerHidden is optional but, when present, must be a boolean', () => {
  for (const value of [null, 'true', 1, {}]) {
    const invalid = structuredClone(catalog);
    invalid.designs['P01-D09'].pickerHidden = value;
    assert.throws(() => validateCatalog(invalid), /pickerHidden/);
  }
  const valid = structuredClone(catalog);
  valid.designs['P01-D09'].pickerHidden = false;
  assert.equal(validateCatalog(valid), valid);
});

for (const templateId of ['emo', 'show']) {
  for (const [base, pair] of [...firstPairs, ...fabricPairs]) {
    const kind = pair.endsWith('-P') ? 'fabric' : 'first';
    test(`${templateId} saved ${pair} renders ${kind} reuse without an extra generation block`, () => {
      const input = inputFor(templateId, kind, pair);
      const before = structuredClone(input.storyboard);
      const definition = sectionDefinition(getTemplate(templateId), `${templateId}.${kind}`, kind, undefined, pair);
      const slot = definition.photoSlots.find(slot => slot.slotKey === (kind === 'first' ? 'ghost' : 'product'));
      assert.equal(slot?.fill, 'reuse');
      assert.deepEqual(slot.reuseFrom, { kind: 'color_product', slot: 'front', color: 'base' });
      assert.equal(validateStoryboardV4(input.storyboard), input.storyboard);
      const run = sectionRuns(input.storyboard).find(run => run.kind === kind);
      assert.ok(sectionDesignChoices(run, { storyboard: input.storyboard, product: input.product })
        .every(design => !catalog.designs[design.id].pickerHidden), 'saved paired IDs do not become selectable layouts');
      assert.equal(extraPhoto(input, kind)?.sourceBlockId, originalFront(input).id);
      assert.deepEqual(input.storyboard, before);
      if (base !== 'P01-D11') {
        const baseline = inputFor(templateId, kind, base);
        const originalImages = images(assemblePage(baseline), kind);
        assert.deepEqual(images(assemblePage(input), kind).slice(0, originalImages.length), originalImages);
      }
    });
  }
}

test('first ghost needs uniform explicit ghost mode across every original-run product whole cut', () => {
  for (const mode of [undefined, 'flat', 'natural_light']) {
    const input = inputFor();
    const back = input.storyboard.find(block => block.pageSectionKind === 'color_product' && block.direction === 'back');
    if (mode === undefined) delete back.productPresentation;
    else back.productPresentation = mode;
    assert.equal(extraPhoto(input).sourceBlockId, undefined, `mixed mode ${mode}`);
  }
  const legacySimilar = inputFor();
  const back = legacySimilar.storyboard.find(block => block.pageSectionKind === 'color_product' && block.direction === 'back');
  back.similarSourceBlockId = originalFront(legacySimilar).id;
  back.productPresentation = 'flat';
  assert.equal(extraPhoto(legacySimilar).sourceBlockId, undefined);
});

test('paired centered title retains base wrapping and first ghost panel geometry', () => {
  const pair = inputFor();
  pair.product.name = '[신상] 부드러운 루즈핏 코튼 셔츠 (긴 상품명)';
  const baseline = structuredClone(pair);
  baseline.storyboard[0].pageDesignId = 'P01-D09';
  const baseFirst = assemblePage(baseline).filter(block => block.pageSectionKind === 'first')[0];
  const pairFirst = assemblePage(pair).filter(block => block.pageSectionKind === 'first')[0];
  assert.deepEqual(pairFirst.elements.slice(0, baseFirst.elements.length), baseFirst.elements);
  const legacy = extraPhoto(inputFor('emo', 'first', 'P01-D01'));
  const added = extraPhoto(pair);
  for (const key of ['x', 'w', 'h']) assert.equal(added[key], legacy[key]);
});

test('reuse follows the selected base color and falls back only to the first listed color', () => {
  for (const [kind, designId] of [['first', 'P01-D09-G'], ['fabric', 'T03-D01-P']]) {
    const input = inputFor('emo', kind, designId);
    input.product.colors.reverse();
    assert.equal(extraPhoto(input, kind).sourceBlockId, 'emo.color_product.front1');
    for (const color of input.product.colors) delete color.isBase;
    assert.equal(extraPhoto(input, kind).sourceBlockId, 'emo.color_product.front2');
  }
});

test('first ghost rejects another run, another color, back, mine, detail and similar candidates', () => {
  for (const patch of [
    { pageSectionKey: 'emo.product_extra', slotKey: 'product_extra.front1' },
    { colorId: 'ivory' }, { direction: 'back' }, { source: 'mine', ownImages: ['seller:photo'] },
    { shot: 'detail' }, { cutType: 'styling' }, { similarSourceBlockId: 'other' },
  ]) {
    const input = inputFor();
    Object.assign(originalFront(input), patch);
    assert.equal(extraPhoto(input).sourceBlockId, undefined, JSON.stringify(patch));
  }
  const deleted = inputFor();
  deleted.storyboard = deleted.storyboard.filter(block => block.pageSectionKind !== 'color_product');
  assert.equal(extraPhoto(deleted).sourceBlockId, undefined);
});

test('dedicated clean ghost wins over a reusable source and stays valid after a storage roundtrip', () => {
  const input = inputFor();
  const card = ownGhost(input);
  input.storyboard = JSON.parse(JSON.stringify(input.storyboard));
  validateStoryboardV4(input.storyboard);
  const photo = extraPhoto(input);
  assert.equal(photo.sourceBlockId, card.id);
  assert.equal(photo.src, 'generated:dedicated-ghost');
  assert.equal(images(assemblePage(input), 'first').filter(image => image.sourceBlockId === card.id).length, 1);
});

test('first ghost preserves shared failure and retry metadata without materializing another cut', () => {
  const input = inputFor();
  const source = originalFront(input);
  const cut = input.cutResults.find(cut => cut.blockId === source.id);
  cut.imageUrl = null;
  input.failedCuts = { [source.id]: { reason: 'temporary_error', retryable: true, sourceBlockId: 'retry-source' } };
  const photo = extraPhoto(input);
  assert.equal(photo.sourceBlockId, source.id);
  const original = images(assemblePage(input), 'color_product').find(image => image.sourceBlockId === source.id);
  for (const key of ['genFailed', 'genFailReason', 'genFailRetryable', 'genFailSourceBlockId']) assert.equal(photo[key], original[key]);
  assert.equal(photo.genFailed, true);
  assert.equal(photo.src, null);
});

test('fabric product may reuse flat or mine front photos, but never a back or unrelated source', () => {
  for (const mode of [undefined, 'flat', 'natural_light', 'ghost']) {
    const input = inputFor('emo', 'fabric', 'T03-D01-P');
    const source = originalFront(input);
    if (mode === undefined) delete source.productPresentation;
    else source.productPresentation = mode;
    assert.equal(extraPhoto(input, 'fabric').sourceBlockId, source.id);
  }
  const mine = inputFor('show', 'fabric', 'T03-D06-P');
  Object.assign(originalFront(mine), { source: 'mine', ownImages: ['seller:original-front'], cutType: null, shot: null });
  assert.equal(extraPhoto(mine, 'fabric').src, 'seller:original-front');
  for (const patch of [{ direction: 'back' }, { shot: 'detail' }, { similarSourceBlockId: 'other' }, { colorId: 'ivory' }]) {
    const input = inputFor('emo', 'fabric', 'T03-D01-P');
    Object.assign(originalFront(input), patch);
    assert.equal(extraPhoto(input, 'fabric').sourceBlockId, undefined);
  }
});

test('fabric mine reuse requires a real first image and a front or absent direction', () => {
  for (const ownImages of [[], [''], [null], [42], 'seller:front']) {
    const input = inputFor('emo', 'fabric', 'T03-D01-P');
    Object.assign(originalFront(input), { source: 'mine', ownImages, cutType: null, shot: null });
    assert.equal(extraPhoto(input, 'fabric').sourceBlockId, undefined, JSON.stringify(ownImages));
  }
  for (const direction of [undefined, null, 'front', '', 'back', 'side']) {
    const input = inputFor('emo', 'fabric', 'T03-D01-P');
    const source = originalFront(input);
    Object.assign(source, { source: 'mine', ownImages: ['seller:front'], cutType: null, shot: null, direction });
    assert.equal(extraPhoto(input, 'fabric').sourceBlockId, direction == null || direction === 'front' ? source.id : undefined);
  }
});

test('both engines match hidden layout rendering, strict source selection and stored clean ghost canonicalization', () => {
  const inputs = ['emo', 'show'].flatMap(templateId => [...firstPairs, ...fabricPairs].map(([, pair]) => inputFor(templateId, pair.endsWith('-P') ? 'fabric' : 'first', pair)));
  const own = inputFor();
  ownGhost(own);
  inputs.push(own);
  for (const mode of [undefined, 'flat', 'natural_light']) {
    const input = inputFor();
    const source = originalFront(input);
    if (mode === undefined) delete source.productPresentation;
    else source.productPresentation = mode;
    inputs.push(input);
  }
  for (const [kind, designId] of [['first', 'P01-D09-G'], ['fabric', 'T03-D01-P']]) {
    for (const patch of [{ direction: 'back' }, { shot: 'detail' }, { colorId: 'ivory' },
      { source: 'mine', ownImages: ['seller:front'], cutType: null, shot: null }]) {
      const input = inputFor('emo', kind, designId);
      Object.assign(originalFront(input), patch);
      inputs.push(input);
    }
  }
  for (const patch of [{ ownImages: [] }, { ownImages: [''] }, { ownImages: 'seller:front' }, { direction: '' }, { direction: null }]) {
    const input = inputFor('emo', 'fabric', 'T03-D01-P');
    Object.assign(originalFront(input), { source: 'mine', ownImages: ['seller:front'], cutType: null, shot: null }, patch);
    inputs.push(input);
  }
  const script = `import json,sys
from app.agents.content_roles import canonicalize_storyboard, validate_storyboard_v4
from app.agents.page_template_assembler import assemble_v4
values=json.load(sys.stdin)
out=[]
for value in values:
    validate_storyboard_v4(value['storyboard'])
    stored=canonicalize_storyboard(value['storyboard'], for_storage=True)
    assert [b['pageDesignId'] for b in stored] == [b['pageDesignId'] for b in value['storyboard']]
    for block in stored:
        if block['id'] == 'dedicated-first-ghost':
            assert (block['cutType'],block['shot'],block['productPresentation'],block['contentRole']) == ('product','ghost','ghost','productOverview')
    out.append(assemble_v4(value['storyboard'],value.get('cutResults',[]),value.get('copyResults',[]),value['product'],True,analysis=value.get('analysis'),page_style=value.get('pageStyle')))
json.dump(out,sys.stdout,ensure_ascii=False)`;
  const result = spawnSync((process.env.PAGE_TEMPLATE_PYTHON || new URL('../../server/.venv/bin/python', import.meta.url).pathname), ['-c', script], {
    input: JSON.stringify(inputs), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: 'server', PYTHONDONTWRITEBYTECODE: '1' },
  });
  assert.equal(result.status, 0, result.stderr);
  const outputs = JSON.parse(result.stdout);
  inputs.forEach((input, index) => assert.deepEqual(assemblePage(input), outputs[index], `case ${index}`));
});
