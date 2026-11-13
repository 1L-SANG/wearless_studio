import React from 'react';
import { loadStoryboardComponent } from './storyboardComponents.mjs';
import { packStageRows } from '../../../src/lib/pageTemplates/storyboardStage.js';
import { templateSectionName, wearUsageForBlock } from '../../../src/lib/pageTemplates/slots.js';
import { isTextOnlySection } from '../../../src/lib/pageTemplates/sectionRuns.js';
import { reuseSlotsForBlock, reuseSourceForSlot } from '../../../src/lib/pageTemplates/sectionDependencies.js';
import { inferStoryboardSpaceSet } from '../../../src/lib/storyboardSpaceSetCatalog.js';
import { spaceSetDisplayName } from '../../../src/lib/spaceSetDisplayNames.js';
import { examplePlace } from '../../../src/lib/pageTemplates/storyboardGallery.js';
import { canPlanSimilarCut } from '../../../src/lib/storyboardSimilarCuts.js';
import { rejectionOfBlock } from '../../../src/lib/identityScope.js';
import { storyboardPreviewSource } from '../../../src/features/storyboard/storyboardPreview.js';

import { storyboardPositions, isHiddenStoryboardKind, positionSwapReason, storyboardPositionSwapReason } from '../../../src/lib/pageTemplates/storyboardPositions.js';
let loaded;
export async function storyboardStage(blocks, props = {}) {
  const Stage = await (loaded ||= loadStoryboardComponent('StoryboardStage.jsx', 'StoryboardStage', {
    storyboardPositions, isHiddenStoryboardKind, positionSwapReason, storyboardPositionSwapReason, wearUsageForBlock, Icon: 'svg', packStageRows, templateSectionName, reuseSlotsForBlock, reuseSourceForSlot, inferStoryboardSpaceSet, spaceSetDisplayName,
    isTextOnlySection, examplePlace, canPlanSimilarCut, rejectionOfBlock, storyboardPreviewSource, StoryboardPreview: () => null,
  }));
  const runBlocks = blocks.filter(block => block.pageSectionKey === blocks[0].pageSectionKey);
  return Stage({ run: { key: blocks[0].pageSectionKey, kind: blocks[0].pageSectionKind, usage: blocks[0].pageSectionUsage, blocks: runBlocks },
    number: 1, blocks, page: [], catalogs: {}, photoOrdinals: new Map(blocks.map((block, index) => [block.id, index + 1])),
    onSelectPhoto() {}, onSimilar() {}, onRemovePhoto() {}, onAddPhoto() {}, ...props });
}
export const stagePhoto = (id, extra = {}) => ({ id, source: 'ai', cutType: 'styling', shot: 'full', exampleId: 'released',
  thumb: '/released.webp', colorId: 'base', pageSectionKey: 'emo.wear_styling', pageSectionKind: 'wear', pageSectionUsage: 'styling', ...extra });
