import { sectionRuns } from '../../lib/pageTemplates/sectionRuns.js';
import { changeTemplateSectionDesign } from '../../lib/pageTemplates/sectionActions.js';
import { pairedPhotoSlot } from '../../lib/pageTemplates/pairedDesigns.js';
import { assembleStoryboardPreview } from './storyboardPreview.js';

// A bounded per-panel cache. Design-only saves reuse the already assembled SVG
// input; photos, dependencies, text/tone, source dimensions and shop changes do not.
export function createSectionDesignPreviewCache(assemble = assembleStoryboardPreview) {
  let fingerprint, pages = new Map();
  return {
    prepare(blocks, runKey, catalogs) {
      const run = sectionRuns(blocks).find(item => item.key === runKey);
      if (!run) return () => [];
      const usedExamples = new Set(blocks.map(block => block.exampleId).filter(Boolean));
      const next = JSON.stringify({ runKey, attachment: pairedPhotoSlot(run.designId),
        blocks: blocks.map(block => {
          const { seedFingerprint, seedProductKey, ...visual } = block;
          if (block.pageSectionKey === runKey) delete visual.pageDesignId;
          return visual;
        }), product: catalogs.detailProduct, analysis: catalogs.analysis,
        examples: catalogs.genExamples?.filter(example => usedExamples.has(example.id)),
        detailRecommendations: catalogs.detailRecommendations, dimensions: catalogs.detailPreviewDimensions, shopName: catalogs.shopName });
      if (next !== fingerprint) { fingerprint = next; pages = new Map(); }
      return designId => {
        if (!pages.has(designId)) {
          const storyboard = changeTemplateSectionDesign(blocks, runKey, designId, { product: catalogs.detailProduct });
          pages.set(designId, assemble({ storyboard, product: catalogs.detailProduct, analysis: catalogs.analysis, catalogs })
            .filter(block => block.pageSectionKey === runKey));
        }
        return pages.get(designId);
      };
    },
  };
}
