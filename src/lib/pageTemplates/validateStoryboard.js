import { getTemplate, pageTemplatesCatalog } from './catalog.js';
import { isReuseFallback, isWornCropSource } from './sectionDependencies.js';

export function validateStoryboardV4(blocks) {
  const fail = code => { throw Object.assign(new Error(code), { code, status: 400 }); };
  if (!Array.isArray(blocks) || blocks.some(b => !b || b.taxonomyVersion !== 4)) fail('storyboard_outdated');
  const seen = new Set(), ids = new Set(), sectionIds = new Map(), kinds = new Map();
  let prefix = null, current = null, run = [];
  const validateRun = () => {
    if (!run.length) return;
    const first = run[0], texts = run.filter(b => b.source === 'text'), photos = run.filter(b => b.source !== 'text');
    let policy = pageTemplatesCatalog.kinds[first.pageSectionKind].storyboardTextCard;
    if (policy === 'only' && photos.length && photos.every(isReuseFallback)) policy = 'lead';
    if (first.pageSectionKind === 'fabric' && !photos.length) policy = 'only';
    if (texts.length > 1 || policy === 'none' && texts.length || policy === 'only' && (texts.length !== 1 || photos.length)
      || policy === 'lead' && (!texts.length || texts[0] !== first)) fail('invalid_storyboard_text_card');
  };
  for (const b of blocks) {
    const templateId = String(b.pageSectionKey || '').split('.')[0], kind = pageTemplatesCatalog.kinds[b.pageSectionKind];
    if (!getTemplate(templateId) || !kind || !b.pageSectionKey?.startsWith(`${templateId}.`) || !b.pageSectionKey.split('.')[1] || !b.sectionId) fail('invalid_storyboard_section');
    if (prefix && prefix !== templateId) fail('mixed_storyboard_templates');
    prefix = templateId;
    if (!b.id || ids.has(b.id)) fail('invalid_storyboard_block_id');
    ids.add(b.id);
    if (sectionIds.has(b.sectionId) && sectionIds.get(b.sectionId) !== b.pageSectionKey) fail('invalid_storyboard_section');
    sectionIds.set(b.sectionId, b.pageSectionKey);
    if (pageTemplatesCatalog.designs[b.pageDesignId]?.kind !== b.pageSectionKind) fail('invalid_storyboard_design');
    if (kind.kind === 'wear' ? !['styling', 'fit'].includes(b.pageSectionUsage) : b.pageSectionUsage != null) fail('invalid_storyboard_usage');
    if (current !== b.pageSectionKey) {
      validateRun();
      if (seen.has(b.pageSectionKey)) fail('noncontiguous_storyboard_section');
      if (b.pageSectionKind === 'first' && seen.size) fail('invalid_storyboard_first');
      const count = (kinds.get(kind.kind) || 0) + 1;
      if (!kind.repeatable && count > 1) fail('repeated_storyboard_section');
      kinds.set(kind.kind, count); seen.add(b.pageSectionKey); current = b.pageSectionKey; run = [];
    }
    if (run.length && ['sectionId', 'pageSectionKind', 'pageSectionUsage', 'pageDesignId'].some(key => b[key] !== run[0][key])) fail('invalid_storyboard_section');
    if (!['ai', 'mine', 'text'].includes(b.source)) fail('invalid_storyboard_source');
    if (b.source === 'text') {
      if (['cutType', 'contentRole', 'sectionRole', 'slotKey'].some(key => b[key] != null)
        || ['exampleId', 'exampleSelectionOrigin', 'exampleChoice', 'spaceGroupId', 'spaceVariation', 'spaceSetMemberOrder', 'setSelectionOrigin', 'matchIds', 'matchIdsOrigin', 'refImages', 'refAssetIds', 'refScope', 'ref_scope', 'ownImages', 'similarSourceBlockId', 'detailTargetId', 'detailTargetOrigin', 'colorId', 'colorIds', 'faceExposure', 'shot', 'direction', 'sideStyle', 'modelId', 'angle', 'outerClosureState', 'horizonBackgroundMode', 'outfitSelectionsSnapshot', 'outfitReferenceAssets', 'thumb', 'previewThumb', 'baseThumb'].some(key => Object.hasOwn(b, key))) fail('invalid_storyboard_text_card');
    }
    run.push(b);
  }
  validateRun();
  const byId = new Map(blocks.map(block => [block.id, block]));
  for (const block of blocks) {
    if (block.pointItems == null) continue;
    if (block.source !== 'text' || block.pageSectionKind !== 'points' || !Array.isArray(block.pointItems)) fail('invalid_storyboard_point_items');
    for (const item of block.pointItems) {
      if (!item || !['detail', 'wornCrop'].includes(item.kind) || typeof item.sourceBlockId !== 'string') fail('invalid_storyboard_point_items');
      const source = byId.get(item.sourceBlockId);
      if (item.kind === 'detail') {
        if (source && (source.source !== 'ai' || source.shot !== 'detail' || source.pageSectionKey !== block.pageSectionKey)) fail('invalid_storyboard_point_items');
      } else {
        const region = item.cropRegion;
        if (!['shoulder', 'neckline', 'length', 'silhouette', 'waistline'].includes(item.bodyPart)
          || !Array.isArray(region) || region.length !== 4 || region.some(value => !Number.isFinite(value) || value < 0 || value > 1)
          || region[2] <= 0 || region[3] <= 0 || region[0] + region[2] > 1.00001 || region[1] + region[3] > 1.00001
          || source && !isWornCropSource(source)) fail('invalid_storyboard_point_items');
      }
    }
  }
  return blocks;
}
