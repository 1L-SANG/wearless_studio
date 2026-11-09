import { uid } from '../ids.js';
import { getTemplate, pageTemplatesCatalog } from './catalog.js';
import { analysisPageStyle } from './pageStyle.js';
import { seedDetailTargets } from './detailBudget.js';
import { genderForClothingType } from '../productGender.js';
import { entryStylingMembers, pickEntrySets } from '../storyboardEntryPlacement.js';
import { spaceSetGroupId } from '../storyboardSpaceSetCatalog.js';
import { matchingIdsForColor } from '../colorwayMatching.js';
import { ensureSections } from '../sections.js';
import { productPresentationOf } from '../storyboardTaxonomy.js';
import { Placeholder } from '../../mock/placeholders.js';

const colorList = product => product?.colors?.length ? product.colors : [{ id: 'col1', isBase: true }];
const baseColor = product => { const colors = colorList(product); return colors.find(c => c.isBase) || colors[0]; };
const numbered = section => section.kind === 'points' && section.photoSlots?.some(slot => slot.recipe?.detailPool === 'points');

// Fractions of the original worn image, applied only when binding a new source.
export const wornCropRegions = {
  top: [ ['shoulder', '어깨선', [0.44, 0.20, 0.30, 0.16]], ['neckline', '목선이 앉는 모양', [0.36, 0.20, 0.28, 0.17]], ['length', '입었을 때 기장', [0.30, 0.27, 0.40, 0.24]] ],
  outer: [ ['shoulder', '어깨선', [0.44, 0.20, 0.30, 0.16]], ['silhouette', '입었을 때 실루엣', [0.28, 0.20, 0.44, 0.34]], ['length', '입었을 때 기장', [0.30, 0.40, 0.40, 0.24]] ],
  bottom: [ ['waistline', '입었을 때 허리선', [0.34, 0.36, 0.32, 0.14]], ['silhouette', '입었을 때 실루엣', [0.30, 0.40, 0.40, 0.50]], ['length', '입었을 때 기장', [0.30, 0.72, 0.40, 0.24]] ],
  dress: [ ['shoulder', '어깨선', [0.44, 0.20, 0.30, 0.16]], ['silhouette', '입었을 때 실루엣', [0.30, 0.30, 0.40, 0.40]], ['length', '입었을 때 기장', [0.30, 0.60, 0.40, 0.26]] ],
};

export function storyboardFingerprint(blocks) {
  const ids = new Map((blocks || []).map((block, index) => [block.id, index]));
  const groups = new Map();
  const group = id => { if (!id) return null; if (!groups.has(id)) groups.set(id, groups.size); return groups.get(id); };
  return JSON.stringify((blocks || []).map(b => ({
    pageSectionKey: b.pageSectionKey, pageSectionKind: b.pageSectionKind, pageSectionUsage: b.pageSectionUsage,
    pageDesignId: b.pageDesignId, slotKey: b.slotKey, source: b.source, cutType: b.cutType, shot: b.shot,
    ...(productPresentationOf(b) ? { productPresentation: productPresentationOf(b) } : {}),
    direction: b.direction,
    colorId: b.colorId, faceExposure: b.faceExposure, matchIds: b.matchIds || [], matchIdsOrigin: b.matchIdsOrigin,
    exampleId: b.exampleSelectionOrigin === 'auto' ? null : b.exampleId || null,
    exampleChoice: b.exampleChoice, exampleSelectionOrigin: b.exampleSelectionOrigin === 'auto' ? null : b.exampleSelectionOrigin || null,
    detailTargetId: b.detailTargetId, detailTargetOrigin: b.detailTargetOrigin, sectionCustom: !!b.sectionCustom,
    group: group(b.spaceGroupId), spaceSetMemberOrder: b.spaceGroupId ? b.spaceSetMemberOrder ?? null : null,
    spaceVariation: b.spaceGroupId ? b.spaceVariation ?? null : null,
    refScope: b.spaceGroupId ? b.refScope : null, refImages: b.refImages || [], refAssetIds: b.refAssetIds || [],
    horizonBackgroundMode: b.cutType === 'horizon' && b.spaceGroupId ? b.horizonBackgroundMode || 'reference' : null, outerClosureState: b.outerClosureState || null,
    ownImages: b.ownImages || [], similarSourceBlockId: b.similarSourceBlockId ? ids.get(b.similarSourceBlockId) : null,
    pointItems: b.pointItems?.map(item => ({ ...item, sourceBlockId: ids.get(item.sourceBlockId) ?? item.sourceBlockId })),
  })));
}

export function seedProductKey(product, analysis = {}) {
  return JSON.stringify({ clothingType: product.clothingType || 'top',
    subCategory: analysis.subCategory ?? null, customCategory: analysis.customCategory ?? null,
    colors: colorList(product).map(c => ({ id: c.id, base: !!c.isBase, back: !!c.images?.some(im => im.slot === 'Back') })),
    targetGenders: analysis.targetGenders || [], detailRecommendations: analysis.detailRecommendations || null,
    sellingPoints: analysis.sellingPoints || [], templateId: analysisPageStyle(analysis.pageStyle).templateId });
}

export function isUntouchedTemplateStoryboard(blocks) {
  return !!blocks?.length && blocks.every(b => b.taxonomyVersion === 4)
    && blocks[0].seedFingerprint === storyboardFingerprint(blocks);
}

export function seedStoryboard(product = {}, analysis = {}, context = {}) {
  const template = getTemplate(analysisPageStyle(analysis.pageStyle).templateId);
  const colors = colorList(product), base = baseColor(product);
  const clothingType = product.clothingType || analysis.clothingType || 'top';
  const baseCount = colors.length === 1 ? 6 : 4;
  const sets = pickEntrySets({ gender: genderForClothingType(clothingType, analysis.targetGenders), clothingType,
    projectId: context.projectId, stylingCount: Math.ceil(baseCount / 2), identityKind: context.identityKind });
  const { fabric, points } = seedDetailTargets(analysis);
  let pointsUsed = false;
  let blocks = [];
  for (const section of context.sectionDefinitions || template.sections.filter(s => s.presence !== 'optional')) {
    if (section.kind === 'fabric' && !fabric) continue;
    if (numbered(section) && points.length < 2 && !context.includeDerivedPoints) continue;
    const sectionId = uid('sec');
    const meta = { taxonomyVersion: 4, pageSectionKey: `${template.id}.${section.instanceKey}`,
      pageSectionKind: section.kind, ...(section.usage ? { pageSectionUsage: section.usage } : {}),
      pageDesignId: section.designId, sectionId, sectionTitle: pageTemplatesCatalog.kinds[section.kind].name,
      sectionLayout: 'stack', sectionCustom: false };
    const photos = [];
    const make = (slot, colorId, suffix = '', extra = {}) => {
      const r = slot.recipe;
      return { ...meta, id: uid('blk'), source: 'ai', slotKey: `${section.instanceKey}.${slot.slotKey}${suffix}`,
        contentRole: slot.contentRole, cutType: r.cutType, shot: r.shot, direction: r.direction,
        ...(r.cutType === 'product' && r.shot === 'ghost' ? { productPresentation: 'ghost' } : {}),
        colorId, matchIds: r.cutType === 'product' ? [] : matchingIdsForColor(colors.find(c => c.id === colorId), analysis.matchClothing || [], { preferMain: colorId === base.id }),
        faceExposure: r.cutType === 'product' ? null : r.faceExposure, angle: 'same', refImages: [],
        examplePrefer: r.examplePrefer || null,
        thumb: Placeholder.photo(`${section.instanceKey}${suffix}`, r.cutType === 'product' ? 'product' : r.cutType, 240, 320),
        ...extra };
    };
    const setPhoto = (slot, member, set, groupId, suffix) => make(slot, base.id, suffix, {
      cutType: member.cutType, shot: member.shot, direction: member.direction,
      spaceGroupId: groupId, spaceVariation: set.spaceVariation, spaceSetMemberOrder: member.order,
      refScope: member.cutType === 'horizon' ? 'all' : 'pose', exampleId: member.exampleId,
      exampleSelectionOrigin: 'auto', setSelectionOrigin: 'auto', thumb: member.thumb });
    if (section.kind === 'wear' && ['styling', 'sequence'].includes(section.photoSlots?.[0]?.recipe?.set)) {
      const slot = section.photoSlots[0];
      // A numeric count is the menu's fixed-size section, built from one location set.
      // Default sections retain the catalog's base/extra-color budget below.
      const fixedCount = typeof slot.count === 'number' ? slot.count : null;
      const addedSet = context.stylingSet || (slot.recipe.set === 'styling' ? sets.stylingSets[0] : sets.sequenceSet);
      if (fixedCount && addedSet) {
        const groupId = spaceSetGroupId(addedSet.id, uid('sg'));
        for (let i = 0; i < fixedCount; i++) {
          // Published styling sets have three poses; a fourth cut uses the same
          // location and a published pose again without inventing a new pose asset.
          const pair = context.stylingMembers || entryStylingMembers(addedSet);
          const member = pair[i % pair.length];
          photos.push(setPhoto(slot, member, addedSet, groupId, String(i + 1)));
        }
      } else if (!fixedCount && slot.recipe.set === 'styling') {
        for (const set of sets.stylingSets) {
          if (!set) continue;
          const groupId = spaceSetGroupId(set.id, uid('sg'));
          for (const member of entryStylingMembers(set)) photos.push(setPhoto(slot, member, set, groupId, String(photos.length + 1)));
        }
      } else if (!fixedCount && sets.sequenceSet) {
        const set = sets.sequenceSet, groupId = spaceSetGroupId(set.id, uid('sg'));
        for (const member of set.members.slice(0, baseCount)) photos.push(setPhoto(slot, member, set, groupId, String(photos.length + 1)));
      }
      while (photos.length < (fixedCount || baseCount)) photos.push(make(slot, base.id, String(photos.length + 1), { shot: photos.length % 2 ? 'medium' : 'full' }));
      for (const color of colors.filter(c => c.id !== base.id).slice(0, fixedCount ? 0 : slot.count.maxExtraColors)) {
        for (let i = 0; i < slot.count.perExtraColor; i++) photos.push(make(slot, color.id, String(photos.length + 1), { shot: i ? 'medium' : 'full' }));
      }
    } else if (section.kind === 'wear' && section.photoSlots?.[0]?.recipe?.set === 'rotation') {
      const set = sets.rotationSet, groupId = set && spaceSetGroupId(set.id, uid('sg'));
      for (const slot of section.photoSlots) {
        const member = set?.members.find(m => m.direction === slot.recipe.direction);
        photos.push(member ? setPhoto(slot, member, set, groupId, '') : make(slot, base.id));
      }
    } else {
      for (const slot of section.photoSlots.filter(s => s.fill === 'generate')) {
        if (slot.recipe.detailPool) {
          const targets = slot.recipe.detailPool === 'fabric' ? (fabric ? [fabric] : [])
            : pointsUsed || context.includeDerivedPoints ? [] : points;
          if (slot.recipe.detailPool === 'points') pointsUsed = true;
          targets.forEach((target, i) => photos.push(make(slot, base.id, slot.recipe.detailPool === 'points' ? String(i + 1) : '', {
            direction: target.direction, ...(target.id ? { detailTargetId: target.id, detailTargetOrigin: 'auto' } : {}) })));
          continue;
        }
        if (slot.requiresBack && !base.images?.some(im => im.slot === 'Back')) continue;
        const selected = slot.color === 'each' ? colors : slot.color === 'cycle' ? Array.from({ length: slot.count }, (_, i) => colors[i % colors.length]) : [base];
        selected.forEach((color, i) => photos.push(make(slot, color.id,
          selected.length > 1 || slot.color === 'cycle' ? String(i + 1) : '')));
      }
    }
    const needsText = ['only', 'lead'].includes(pageTemplatesCatalog.kinds[section.kind].storyboardTextCard);
    if (needsText) blocks.push({ ...meta, id: uid('txt'), source: 'text', slotKey: null, cutType: null, contentRole: null, sectionRole: null });
    blocks.push(...photos);
  }
  for (const section of (context.sectionDefinitions || template.sections).filter(numbered)) {
    const lead = blocks.find(b => b.pageSectionKey === `${template.id}.${section.instanceKey}` && b.source === 'text');
    if (!lead) continue;
    const detail = blocks.filter(b => b.pageSectionKey === lead.pageSectionKey && b.source === 'ai');
    lead.pointItems = detail.map(b => ({ kind: 'detail', sourceBlockId: b.id, detailTargetId: b.detailTargetId }));
  }
  blocks = ensureSections(blocks);
  if (blocks.length) { blocks[0].seedProductKey = seedProductKey(product, analysis); blocks[0].seedFingerprint = storyboardFingerprint(blocks); }
  return blocks;
}

export function resolveStoryboardEntry(saved, product, analysis, { generated = false, ...context } = {}) {
  const outdated = saved?.length && saved.some(b => b.taxonomyVersion !== 4);
  if (outdated && generated) return { blocks: saved, notice: 'legacyGenerated' };
  if (outdated || !saved?.length) return { blocks: seedStoryboard(product, analysis, context), notice: outdated ? 'rebuilt' : null };
  if (isUntouchedTemplateStoryboard(saved) && saved[0].seedProductKey !== seedProductKey(product, analysis)) {
    return { blocks: seedStoryboard(product, analysis, context), notice: null };
  }
  return { blocks: saved, notice: null };
}
