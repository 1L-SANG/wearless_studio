import test from 'node:test';
import assert from 'node:assert/strict';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import { STORYBOARD_SPACE_SETS, withStoryboardSpaceSetExamples, storyboardSpaceSetsFor, inferStoryboardSpaceSet, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers, replaceSpaceSetRun } from '../../src/lib/storyboardSpaceSets.js';
import { cutTypeDisplay, assignInternalContentRoles } from '../../src/lib/storyboardTaxonomy.js';
import { isMinimalExample } from '../../src/lib/studioExamples.js';
import { filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { generationExampleImageSources } from '../../src/lib/generationExamples.js';
import { photoSlotForBlock } from '../../src/lib/pageTemplates/slots.js';
import { canReplaceStoryboardSet } from '../../src/lib/pageTemplates/photoActions.js';
import { getTemplate } from '../../src/lib/pageTemplates/catalog.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';

const catalogs = { ...withStoryboardSpaceSetExamples({ genExamples: raw }),
  shotTypes: [{ value: 'full' }, { value: 'medium' }], productShotTypes: [{ value: 'ghost' }, { value: 'detail' }] };
const minimalSet = STORYBOARD_SPACE_SETS.find(set => set.studioStyle === 'minimal' && set.applicableClothingTypes.includes('top'));
const horizonSet = STORYBOARD_SPACE_SETS.find(set => set.setType === 'horizon-sequence' && set.gender === 'men' && set.applicableClothingTypes.includes('top') && set.members.length > 3);
const rotationSet = STORYBOARD_SPACE_SETS.find(set => set.setType === 'horizon-rotation' && set.gender === 'men');
const normalSet = STORYBOARD_SPACE_SETS.find(set => set.setType === 'styling' && !set.studioStyle && set.gender === 'men');
const host = { id: 'photo', source: 'ai', taxonomyVersion: 4, pageSectionKey: 'show.wear_styling',
  pageSectionKind: 'wear', pageSectionUsage: 'fit', pageDesignId: 'WEAR-FIT-COLUMN-01',
  slotKey: 'wear_styling.g1', sectionId: 'wear', contentRole: 'coordination', sectionRole: 'styling', colorId: 'base' };
const membersFor = (set, template = host) => createSpaceSetMembers(set, template, { spaceGroupId: spaceSetGroupId(set.id, 'r2'),
  previousMembers: set.members.map((_, i) => ({ ...template, id: `photo-${i}`, slotKey: template.slotKey.replace(/1$/, String(i + 1)) })) });
const chip = tree => nodes(tree).find(node => node.type === 'photo-segment');
const cards = tree => nodes(tree).filter(node => node.props?.['data-space-set']);
const photoIds = tree => nodes(tree).filter(node => node.props?.['data-example-id']).map(node => node.props['data-example-id']);

async function setHarness(initial) {
  const h = hookHarness();
  const Panel = await loadStoryboardComponent('StoryboardSpaceSetPanel.jsx', 'StoryboardSpaceSetPanel', {
    ...h.hooks, filterSpaceSetsForModel, storyboardSpaceSetsFor, inferStoryboardSpaceSet, photoSlotForBlock, Icon: 'icon',
  });
  let props = { mode: 'replace', gender: 'men', clothingType: 'top', usage: 'fit', cutType: 'horizon', ...initial };
  return { render(next = {}) { props = { ...props, ...next }; h.render(Panel, props); return h.render(Panel, props); }, close: h.close };
}

test('R2 set inspectors hide both cut-chip rows and matching edits', async () => {
  const grouped = membersFor(horizonSet);
  for (const block of [grouped[0], membersFor(minimalSet)[0]]) {
    const calls = [];
    const h = await storyboardPhotoPanelHarness({ block, blocks: membersFor(inferStoryboardSpaceSet(block.spaceGroupId)), catalogs, exampleGender: 'men',
      spaceContext: block.spaceGroupId ? { set: inferStoryboardSpaceSet(block.spaceGroupId), siblings: [block] } : null,
      onChangeSpaceSet: () => calls.push('replace'), matchClothing: [{ id: 'match' }] });
    try {
      const tree = h.render();
      assert.equal(nodes(tree).some(node => node.type === 'underline-tabs'), false);
      assert.equal(chip(tree), undefined);
      if (block.spaceGroupId) {
        assert.ok(!textOf(tree).includes('매칭 의류 바꾸기'));
        assert.equal(tree, null, 'R5 removes the entire set-member inspector');
      }
    } finally { h.close(); }
  }
});

test('R2 set picker starts from the current subtype and switches between existing horizon and minimal sets', async () => {
  const members = membersFor(horizonSet), h = await setHarness({ currentMembers: members });
  try {
    let tree = h.render(); assert.equal(chip(tree)?.props.value, 'horizon');
    assert.ok(cards(tree).length);
    assert.ok(cards(tree).every(node => STORYBOARD_SPACE_SETS.find(set => set.id === node.props['data-space-set']).setType === 'horizon-sequence'));
    chip(tree).props.onChange('minimal'); tree = h.render();
    assert.equal(chip(tree).props.value, 'minimal'); assert.ok(cards(tree).length);
    assert.ok(cards(tree).every(node => STORYBOARD_SPACE_SETS.find(set => set.id === node.props['data-space-set']).studioStyle === 'minimal'));
    chip(tree).props.onChange('horizon'); tree = h.render();
    assert.ok(cards(tree).every(node => STORYBOARD_SPACE_SETS.find(set => set.id === node.props['data-space-set']).setType === 'horizon-sequence'));
  } finally { h.close(); }
});

test('R2 choosing minimal and returning to horizon uses the existing whole-set replacement rule', async () => {
  let blocks = membersFor(horizonSet), applied = [];
  const original = structuredClone(blocks);
  const h = await setHarness({ currentMembers: blocks, onChoose(set) {
    assert.ok(canReplaceStoryboardSet(blocks, blocks[0].spaceGroupId, set));
    blocks = assignInternalContentRoles(replaceSpaceSetRun(blocks, blocks[0].spaceGroupId, set, {
      spaceGroupId: spaceSetGroupId(set.id, `r2-${applied.length}`), makeId: (_, index) => `added-${index}`,
    })); applied.push(set.id);
  } });
  try {
    let tree = h.render(); chip(tree)?.props.onChange('minimal'); tree = h.render();
    const pick = cards(tree).find(node => node.props['data-space-set'] === minimalSet.id);
    assert.ok(pick); pick.props.onClick();
    assert.equal(blocks.length, minimalSet.members.length);
    assert.deepEqual(blocks.map(block => block.id), original.slice(0, blocks.length).map(block => block.id));
    assert.deepEqual(blocks.map(block => block.slotKey), original.slice(0, blocks.length).map(block => block.slotKey));
    for (const block of blocks) {
      assert.equal(block.cutType, 'styling'); assert.equal(block.refScope, 'pose');
      assert.equal(cutTypeDisplay(block.cutType, block.exampleId).studioStyle, 'minimal');
      assert.equal(block.spaceVariation, minimalSet.spaceVariation);
    }
    tree = h.render({ currentMembers: blocks, cutType: 'styling' });
    assert.equal(chip(tree)?.props.value, 'minimal');
    chip(tree).props.onChange('horizon'); tree = h.render();
    const back = cards(tree).find(node => node.props['data-space-set'] === horizonSet.id);
    assert.ok(back); back.props.onClick();
    assert.equal(blocks.length, horizonSet.members.length);
    assert.ok(blocks.every(block => block.cutType === 'horizon' && block.refScope === 'all'));
    assert.ok(blocks.slice(minimalSet.members.length).every(block => block.slotKey === null));
    assert.deepEqual(applied, [minimalSet.id, horizonSet.id]);
  } finally { h.close(); }
});

test('R2 minimal in a rotation slot returns to the existing rotation-set list', async () => {
  const setHost = { ...host, pageSectionKey: 'show.wear_fit', slotKey: 'wear_fit.front', contentRole: 'fit', sectionRole: 'studio' };
  const h = await setHarness({ cutType: 'styling', currentMembers: membersFor(minimalSet, setHost) });
  try {
    let tree = h.render(); assert.equal(chip(tree)?.props.value, 'minimal');
    chip(tree).props.onChange('horizon'); tree = h.render();
    assert.ok(cards(tree).length);
    assert.ok(cards(tree).every(node => STORYBOARD_SPACE_SETS.find(set => set.id === node.props['data-space-set']).setType === 'horizon-rotation'));
  } finally { h.close(); }
});

test('R2 standalone-only minimal conditions disable the set-picker chip', async () => {
  for (const [gender, clothingType] of [['men','outer'], ['women','bottom'], ['women','dress']]) {
    assert.ok(catalogs.genExamples.some(example => isMinimalExample(example) && example.gender === gender && example.applicableClothingTypes.includes(clothingType)));
    const h = await setHarness({ gender, clothingType, currentMembers: membersFor(rotationSet) });
    try {
      const option = chip(h.render())?.props.options.find(option => option.value === 'minimal');
      assert.ok(option); assert.equal(option.disabled, true);
      assert.equal(option.disabledReason, '이 조건은 아직 서비스에 공개되지 않았어요');
    } finally { h.close(); }
  }
});

test('R2 ordinary styling-set picker excludes minimal sets and has no studio chips', async () => {
  const h = await setHarness({ usage: 'styling', cutType: 'styling', currentMembers: membersFor(normalSet), clothingType: normalSet.applicableClothingTypes[0] });
  try {
    const tree = h.render(); assert.equal(chip(tree), undefined);
    assert.ok(cards(tree).length);
    assert.ok(cards(tree).every(node => STORYBOARD_SPACE_SETS.find(set => set.id === node.props['data-space-set']).studioStyle !== 'minimal'));
  } finally { h.close(); }
});

const singleBlock = { ...host, id: 'single', pageSectionKey: 'show.first', pageSectionKind: 'first',
  pageDesignId: 'P01-SHOW-01', slotKey: 'first.hero', cutType: 'styling', shot: 'full', direction: 'front', contentRole: 'hero' };
async function galleryHarness(block = singleBlock, extra = {}) {
  const h = hookHarness();
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, filterSpaceSetsForModel, storyboardSpaceSetsFor, generationExampleImageSources, photoSlotForBlock, Icon: 'icon',
  });
  const props = { block, catalogs, cut: block.cutType, shot: block.shot, exampleId: block.exampleId,
    clothingType: 'top', gender: 'men', onExampleChange() {}, ...extra };
  return { render() { h.render(Gallery, props); return h.render(Gallery, props); }, close: h.close };
}

test('R2 horizon-forbidden single slot has no studio subtype chips or minimal candidates', async () => {
  const template = getTemplate('emo');
  const section = template.sections.find(section => ['review','faq'].includes(section.kind)
    && section.photoSlots.some(slot => slot.recipe?.allowedCutTypes.includes('styling') && !slot.recipe.allowedCutTypes.includes('horizon')));
  assert.ok(section);
  const slot = section.photoSlots.find(slot => slot.recipe?.cutType === 'styling');
  const h = await galleryHarness({ ...singleBlock, pageSectionKey: `emo.${section.instanceKey}`, pageSectionKind: section.kind,
    pageDesignId: section.designId, slotKey: `${section.instanceKey}.${slot.slotKey}` });
  try {
    const tree = h.render(); assert.equal(chip(tree), undefined);
    assert.ok(!nodes(tree).find(node => node.type === 'photo-filters').props.tabs.some(tab => tab.value === 'studio'));
    assert.ok(photoIds(tree).every(id => !isMinimalExample(catalogs.genExamples.find(example => example.id === id))));
  } finally { h.close(); }

});

test('R2 single-photo gallery selection saves the example-owned cut and reopens in its studio subtype', async () => {
  const minimal = catalogs.genExamples.find(example => isMinimalExample(example) && example.gender === 'men' && example.applicableClothingTypes.includes('top'));
  const panel = await storyboardPhotoPanelHarness({ block: singleBlock, catalogs, exampleGender: 'men', clothingType: 'top' });
  try {
    const guide = nodes(panel.render()).find(node => node.type === 'mood-guide');
    await guide.props.onExampleChange(minimal.id, 'pose');
    assert.equal(panel.block.cutType, 'styling'); assert.equal(panel.block.exampleId, minimal.id);
    assert.equal(panel.block.refScope, 'pose'); assert.ok(panel.block.spaceGroupId);
    const gallery = await galleryHarness(panel.block);
    try {
      const tree = gallery.render();
      assert.equal(nodes(tree).find(node => node.type === 'photo-filters'), undefined);
      assert.equal(chip(tree), undefined);
      assert.ok(photoIds(tree).every(id => isMinimalExample(catalogs.genExamples.find(example => example.id === id))));
    } finally { gallery.close(); }
  } finally { panel.close(); }
});
