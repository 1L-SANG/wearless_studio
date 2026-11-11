import { getTemplate, pageTemplatesCatalog, wearAdditionDefinition } from './catalog.js';
import { analysisPageStyle } from './pageStyle.js';
import { seedStoryboard, wornCropRegions } from './seed.js';
import { sectionInsertionIndex, sectionRuns } from './sectionRuns.js';
import { sectionDesignChoices } from './designChoices.js';
import { changeTemplateSectionDesign as changeRunDesign } from './sectionRuns.js';
import { preferredWornCropSource, reconcileSectionDependencies, openingSourceProduct } from './sectionDependencies.js';
import { DETAIL_PHOTO_CAP } from './detailBudget.js';
import { storyboardSpaceSetsFor } from '../storyboardSpaceSetCatalog.js';
import { filterSpaceSetsForModel, filterExamplesForModel } from '../identityScope.js';
import { selectGenerationExamples } from '../generationExamples.js';
import { entryStylingMembers } from '../storyboardEntryPlacement.js';
import { samePlaceExamplePair } from './storyboardGallery.js';
import { singleMemberSpaceSet } from '../storyboardSetSelection.js';
import { directionSpecFromExample } from '../storyboardExampleSelection.js';
import { isPairedDesign, pairedDesignId } from './pairedDesigns.js';
import { wornCropRegion, wornCropSourceAspect } from './wornCropGeometry.js';

export { moveTemplateSection } from './sectionRuns.js';

export function changeTemplateSectionDesign(blocks, key, designId, { product = {} } = {}) {
  const run = sectionRuns(blocks).find(run => run.key === key);
  if (!sectionDesignChoices(run, { storyboard: blocks, product }).some(design => design.id === designId)) return blocks;
  const next = changeRunDesign(blocks, key, isPairedDesign(run.designId) ? pairedDesignId(designId) || designId : designId);
  return next === blocks ? blocks : reconcileSectionDependencies(blocks, next, openingSourceProduct(run, product));
}

export const sectionMenuGroups = pageTemplatesCatalog.menuGroups.map(group => ({
  ...group, kinds: Object.values(pageTemplatesCatalog.kinds).filter(kind => kind.menuGroup === group.id),
}));

export const sectionAdditionPhotoCounts = Object.freeze({ styling: 2, fit: 3 });

export function addTemplateSection(blocks, product, analysis, kind, usage, context = {}) {
  const template = getTemplate(blocks[0]?.pageSectionKey?.split('.')[0] || analysisPageStyle(analysis.pageStyle).templateId);
  const definition = pageTemplatesCatalog.kinds[kind];
  if (!definition || kind === 'wear' && !definition.usages.includes(usage)) throw new Error('invalid_storyboard_usage');
  if (!definition.repeatable && blocks.some(b => b.pageSectionKind === kind)) return blocks;
  const index = sectionInsertionIndex(blocks, context.beforeSectionKey);
  if (index < 0) return blocks;
  const placement = template.addPlacements[kind];
  const original = kind === 'wear' ? wearAdditionDefinition(template, usage) : template.sections.find(s => s.kind === kind);
  const existingKeys = new Set(blocks.map(b => b.pageSectionKey));
  let instanceKey = original?.instanceKey || `${kind}_1`;
  for (let index = 1; existingKeys.has(`${template.id}.${instanceKey}`); index++) instanceKey = `${kind}_${index}`;
  const section = { ...(original || placement), instanceKey, kind, ...(usage ? { usage } : {}) };
  if (context.designId && pageTemplatesCatalog.designs[context.designId]?.kind === kind) section.designId = context.designId;
  let stylingSet = null, stylingMembers = null;
  if (kind === 'wear' && usage === 'styling') {
    section.photoSlots = section.photoSlots.map(slot => ({ ...slot, count: sectionAdditionPhotoCounts.styling }));
    const gender = context.gender || analysis.targetGenders?.[0] || 'women';
    const sets = filterSpaceSetsForModel(storyboardSpaceSetsFor({ gender, clothingType: product.clothingType || 'top' }), context.identityKind);
    const used = new Set(blocks.map(block => block.exampleId).filter(Boolean));
    const example = context.example;
    const cut = example?.cutType || section.photoSlots[0]?.recipe.cutType;
    const pool = sets.filter(set => cut === 'styling' ? set.setType === 'styling' : set.setType === 'horizon-sequence');
    stylingSet = example ? sets.find(set => set.members.some(member => member.exampleId === example.id)) : null;
    if (!stylingSet && !example) stylingSet = [...pool].sort((a, b) => {
      const score = set => Number(new Set(set.members.map(member => member.shot)).size > 1) * 100
        + entryStylingMembers(set).filter(member => !used.has(member.exampleId)).length;
      return score(b) - score(a);
    })[0];
    if (stylingSet) {
      const first = example && stylingSet.members.find(member => member.exampleId === example.id);
      stylingMembers = first ? [first, stylingSet.members.find(member => member.shot !== first.shot)
        || stylingSet.members.find(member => member.exampleId !== first.exampleId)] : entryStylingMembers(stylingSet);
    }
  }
  const seeded = seedStoryboard(product, { ...analysis, pageStyle: { ...analysisPageStyle(analysis.pageStyle), templateId: template.id } },
    { ...context, stylingSet, stylingMembers, includeDerivedPoints: kind === 'points', sectionDefinitions: [section] });
  if (kind === 'wear' && context.example && stylingSet) {
    for (let i = 0; i < seeded.length; i++) {
      seeded[i] = { ...seeded[i], exampleSelectionOrigin: 'user', setSelectionOrigin: 'user' };
    }
  } else if (kind === 'wear' && context.example) {
    const eligible = filterExamplesForModel(['full', 'medium'].flatMap(shot => selectGenerationExamples(context.catalog || [context.example], {
      cutType: context.example.cutType, shot, clothingType: product.clothingType || 'top', gender: context.gender || analysis.targetGenders?.[0] || 'women',
      includeSetOnly: true, limit: context.catalog?.length || 1,
    })), context.identityKind);
    const companions = context.example.spaceSetId
      ? eligible.filter(example => example.spaceSetId === context.example.spaceSetId) : eligible;
    const pair = samePlaceExamplePair(context.example, companions);
    const ordered = [context.example, ...new Map([...pair.slice(1), ...companions]
      .filter(example => example.id !== context.example.id).map(example => [example.id, example])).values()];
    for (let i = 0; i < seeded.length; i++) {
      const example = ordered[i] || pair[i % pair.length];
      seeded[i] = singleMemberSpaceSet({ ...seeded[i], cutType: example.cutType, shot: example.shot,
        ...directionSpecFromExample(example), contentRole: example.cutType === 'horizon' ? 'fit' : 'coordination',
        sectionRole: example.cutType === 'horizon' ? 'studio' : 'styling',
        exampleId: example.id, exampleSelectionOrigin: 'user', thumb: example.thumb }, { origin: 'user' });
    }
  }
  // Automatic section insertion shares the page's existing detail budget.
  const used = new Set(blocks.filter(b => b.source === 'ai' && b.shot === 'detail').map(b => b.detailTargetId));
  let remaining = Math.max(0, DETAIL_PHOTO_CAP - blocks.filter(b => b.source === 'ai' && b.shot === 'detail').length);
  let added = seeded.filter(b => b.source !== 'ai' || b.shot !== 'detail' || !used.has(b.detailTargetId) && remaining-- > 0);
  const lead = added.find(b => b.source === 'text');
  if (kind === 'points' && section.photoSlots?.some(s => s.recipe?.detailPool === 'points') && lead) {
    const source = preferredWornCropSource(blocks);
    if (!source) return blocks;
    const crops = wornCropRegions[product.clothingType] || wornCropRegions.top;
    const sourceAspect = wornCropSourceAspect(source, { product, cutResults: context.cutResults });
    lead.pointItems = crops.map(([bodyPart, label, region]) => ({ kind: 'wornCrop', sourceBlockId: source.id,
      bodyPart, label, cropRegion: wornCropRegion(region, sourceAspect) }));
  }
  added = added.map(({ seedFingerprint, seedProductKey, ...b }) => ({ ...b, sectionCustom: true }));
  const next = [...blocks];
  next.splice(index, 0, ...added);
  if (context.designId) {
    const run = sectionRuns(next).find(run => run.key === `${template.id}.${instanceKey}`);
    if (!sectionDesignChoices(run && { ...run, designId: original?.designId || placement.designId }, { storyboard: next, product })
      .some(design => design.id === context.designId)) return blocks;
  }
  return reconcileSectionDependencies(blocks, next, product,
    { cropRegions: wornCropRegions[product.clothingType] || wornCropRegions.top, cutResults: context.cutResults });
}

export function removeTemplateSection(blocks, key, { product = {}, cutResults = [] } = {}) {
  const target = blocks.find(block => block.pageSectionKey === key);
  if (!target || target.pageSectionKind === 'first') return blocks;
  const next = blocks.filter(block => block.pageSectionKey !== key);
  return reconcileSectionDependencies(blocks, next, product,
    { cropRegions: wornCropRegions[product.clothingType] || wornCropRegions.top, cutResults });
}

export function sectionRemovalImpact(blocks, key, { product = {}, cutResults = [] } = {}) {
  const target = blocks.find(block => block.pageSectionKey === key);
  const nextBlocks = removeTemplateSection(blocks, key, { product, cutResults });
  const confirmationRequired = target?.pageSectionKind === 'wear';
  if (!confirmationRequired || nextBlocks === blocks) return { nextBlocks, confirmationRequired, consequenceText: '' };
  const consequences = [];
  const oldPoints = blocks.filter(block => block.pointItems?.some(item => item.kind === 'wornCrop'));
  const newById = new Map(nextBlocks.map(block => [block.id, block]));
  const removed = oldPoints.some(block => !newById.has(block.id));
  const cropsRemoved = oldPoints.some(block => newById.has(block.id)
    && !newById.get(block.id).pointItems?.some(item => item.kind === 'wornCrop'));
  const retargeted = oldPoints.some(block => {
    const next = newById.get(block.id);
    return next && block.pointItems.some((item, index) => item.kind === 'wornCrop'
      && next.pointItems?.[index]?.kind === 'wornCrop' && next.pointItems[index].sourceBlockId !== item.sourceBlockId);
  });
  if (removed) consequences.push('남은 착용 사진이 없어 디테일 요소도 함께 빠져요');
  else if (cropsRemoved) consequences.push('따온 사진만 빠지고 디테일 사진은 남아요');
  else if (retargeted) consequences.push('디테일 요소의 원본을 남은 착용 사진으로 옮겨요');
  const beforeAi = new Set(blocks.filter(block => block.source === 'ai').map(block => block.id));
  const snsAdded = nextBlocks.filter(block => block.pageSectionKind === 'sns' && block.source === 'ai' && !beforeAi.has(block.id)).length;
  if (snsAdded) consequences.push(`SNS 화면에 새로 만들 사진이 ${snsAdded}컷 늘어요`);
  return { nextBlocks, confirmationRequired, consequenceText: consequences.join(', ') };
}
