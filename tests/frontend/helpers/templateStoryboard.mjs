import { seedStoryboard, isUntouchedTemplateStoryboard } from '../../../src/lib/pageTemplates/seed.js';

// Existing recipe/selection regressions use a real v4 seed. The retired mode argument
// remains only in these test call sites and has no effect on the fixture.
export const seedTemplateFixture = (colors, _retiredMode, context = {}) => seedStoryboard({ ...context, colors }, context, context);
export const isDefaultTemplateFixture = (blocks) => isUntouchedTemplateStoryboard(blocks);

export function v4Fixture(blocks) {
  let run = 0, last = null;
  return blocks.map((block, index) => {
    if (block.taxonomyVersion === 4) return block;
    const sid = block.sectionId || block.sectionRole || block.cutType || 'photos';
    if (sid !== last) { run++; last = sid; }
    const first = block.contentRole === 'hero' || block.sectionRole === 'hooking';
    return { ...block, id: block.id || `fixture-${index}`, taxonomyVersion: 4, sectionId: sid,
      pageSectionKey: `emo.${first ? 'first' : `wear_${run}`}`, pageSectionKind: first ? 'first' : 'wear',
      ...(!first ? { pageSectionUsage: block.cutType === 'horizon' ? 'fit' : 'styling' } : {}),
      pageDesignId: first ? 'P01-D09' : block.cutType === 'horizon' ? 'P04-D01' : 'P02-D01',
      slotKey: null, sectionLayout: 'stack', source: block.source || 'ai' };
  });
}
