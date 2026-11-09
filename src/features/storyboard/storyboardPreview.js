import { generationExampleImageSources } from '../../lib/generationExamples.js';
import { signatureCutById } from '../../lib/signatureCutPool.js';
import { detailTargetPresentation, detailTargetPreview } from '../../lib/detailRecommendations.js';
import { mineImageUrl } from '../../lib/storyboardMineImages.js';
import { assemblePage } from '../../lib/pageTemplates/assemble.js';
import { productPresentationOf } from '../../lib/storyboardTaxonomy.js';
import { productSourcePhotos } from '../../lib/productSourcePhotos.js';

const estimatedSize = Object.freeze({ width: 1024, height: 1365, dimensionsKnown: false });

export function storyboardPreviewSource(block, catalogs) {
  if (!block) return null;
  const recommendations = catalogs?.detailRecommendations ?? catalogs?.analysis?.detailRecommendations;
  const region = detailTargetPreview(block, catalogs?.detailProduct, recommendations);
  if (region) return region.src;
  const detail = detailTargetPresentation(block, catalogs?.detailProduct, recommendations);
  if (detail) return detail.src;
  if (productPresentationOf(block)) {
    const direction = block.direction === 'back' ? 'back' : 'front';
    return productSourcePhotos(catalogs?.detailProduct, block.colorId)[direction]?.src || null;
  }
  if (block.source === 'mine') return block.thumb || mineImageUrl(block.ownImages?.[0]);
  const signature = signatureCutById(block.exampleId);
  if (signature) return generationExampleImageSources(signature).src;
  const example = catalogs?.genExamples?.find(item => item.id === block.exampleId);
  return block.previewThumb || (example ? generationExampleImageSources(example).src : null) || block.thumb;
}

export const storyboardPreviewCuts = (blocks, catalogs) => blocks.filter(block => block.source !== 'text').map(block => ({
  // Catalog thumbnails do not carry measured native dimensions. Keep the
  // existing aspect estimate separate from the generated image's pixel limit.
  blockId: block.id, imageUrl: storyboardPreviewSource(block, catalogs), ...estimatedSize,
}));

// Reading intrinsic image size needs no pixel/canvas access or CORS mode. These
// are the same originals already displayed in the board; nothing is persisted.
export function observeDetailPreviewDimensions(urls, onMeasure, {
  ImageCtor = typeof Image === 'undefined' ? null : Image,
} = {}) {
  if (!ImageCtor) return () => {};
  let active = true;
  const images = [];
  for (const src of new Set(urls)) {
    try {
      const image = new ImageCtor();
      images.push(image);
      image.decoding = 'async';
      image.onload = () => {
        const width = image.naturalWidth, height = image.naturalHeight;
        if (active && [width, height].every(value => Number.isFinite(value) && value > 0)) {
          const vector = /^data:image\/svg\+xml[;,]/i.test(src) || /\.svg(?:[?#]|$)/i.test(src);
          onMeasure(src, { width, height, ...(vector ? { dimensionsKnown: false } : {}) });
        }
      };
      image.onerror = () => {};
      image.src = src;
    } catch { /* Keep the estimated crop when the original cannot be read. */ }
  }
  return () => {
    active = false;
    images.forEach(image => { image.onload = null; image.onerror = null; });
  };
}

// Same center/cover and zoom caps as wornCrop. Native size is used only for the
// original's crop; the layout continues using its existing thumbnail estimate.
export function detailPreviewCrop(width, height, preview) {
  if (![width, height].every(value => Number.isFinite(value) && value > 0) || !preview?.region) return null;
  const { x, y, w, h } = preview.region;
  const sized = [preview.width, preview.height].every(value => Number.isFinite(value) && value > 0);
  const known = sized && preview.dimensionsKnown !== false;
  const sw = sized ? preview.width : estimatedSize.width, sh = sized ? preview.height : estimatedSize.height;
  const scale = Math.min(Math.max(width / (sw * w), height / (sh * h)), width / (sw * .3), known ? 1 : Infinity);
  const iw = Math.round(sw * scale), ih = Math.round(sh * scale);
  return { iw, ih,
    ox: Math.max(0, Math.min(iw - width, Math.round((x + w / 2) * iw - width / 2))),
    oy: Math.max(0, Math.min(ih - height, Math.round((y + h / 2) * ih - height / 2))),
  };
}

// Only the storyboard presentation calls this wrapper. The generated page and
// editor continue calling assemblePage with real results and no preview fields.
export function assembleStoryboardPreview({ catalogs = {}, cutResults = [], ...input }) {
  const storyboard = input.storyboard || [];
  const product = input.product || catalogs.detailProduct || {}, analysis = input.analysis || catalogs.analysis || {};
  const previewCatalogs = { ...catalogs, detailProduct: product,
    detailRecommendations: catalogs.detailRecommendations ?? analysis.detailRecommendations };
  const completed = new Map(cutResults.filter(cut => cut?.imageUrl).map(cut => [cut.blockId, cut]));
  const cuts = [...storyboardPreviewCuts(storyboard, previewCatalogs).filter(cut => !completed.has(cut.blockId)), ...completed.values()];
  const targets = new Map(storyboard.map(block => [block.id,
    detailTargetPreview(block, product, previewCatalogs.detailRecommendations)]));
  return assemblePage({ ...input, storyboard, product, analysis, cutResults: cuts }).map(block => {
    let changed = false;
    const elements = block.elements.map(element => {
      if (element.type !== 'image' || completed.has(element.sourceBlockId)) return element;
      const target = targets.get(element.sourceBlockId);
      const measured = target && catalogs.detailPreviewDimensions?.[target.src];
      const crop = target && detailPreviewCrop(element.w, element.h,
        measured ? { ...target, ...measured, dimensionsKnown: measured.dimensionsKnown !== false } : target);
      if (!crop) return element;
      changed = true;
      return { ...element, src: target.src, crop, previewDetailCrop: true };
    });
    return changed ? { ...block, elements } : block;
  });
}
