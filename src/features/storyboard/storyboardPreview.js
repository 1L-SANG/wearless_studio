import { generationExampleImageSources } from '../../lib/generationExamples.js';
import { signatureCutById } from '../../lib/signatureCutPool.js';
import { detailTargetPresentation, detailTargetPreview, detailTargetById, isProductDetail } from '../../lib/detailRecommendations.js';
import { mineImageUrl } from '../../lib/storyboardMineImages.js';
import { analysisPageStyle } from '../../lib/pageTemplates/pageStyle.js';
import { assemblePage, wrapText } from '../../lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
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

// Preview may upscale even known small originals. Keep the 30%-of-source-width
// zoom cap; generation and saved wornCrop geometry have their own unchanged rules.
export function detailPreviewCrop(width, height, preview) {
  if (![width, height].every(value => Number.isFinite(value) && value > 0) || !preview?.region) return null;
  const { x, y, w, h } = preview.region;
  const sized = [preview.width, preview.height].every(value => Number.isFinite(value) && value > 0);
  const sw = sized ? preview.width : estimatedSize.width, sh = sized ? preview.height : estimatedSize.height;
  const scale = Math.min(width / (sw * w), height / (sh * h), width / (sw * .3));
  const iw = Math.round(sw * scale), ih = Math.round(sh * scale);
  // Keep the whole region visible and use surrounding original pixels. When a
  // source edge is reached, clamp to it; if the source is narrower than the
  // viewport, center it over the same photograph used as an edge backdrop.
  const offset = (center, extent, viewport) => extent < viewport ? Math.round((extent - viewport) / 2)
    : Math.max(0, Math.min(extent - viewport, Math.round(center * extent - viewport / 2)));
  return { iw, ih, ox: offset(x + w / 2, iw, width), oy: offset(y + h / 2, ih, height) };
}

// Recompute against the stage's actual 3:4 viewport, without a second cover
// zoom. The source/region is ephemeral preview metadata, never saved blocks.
export function detailStageGeometry(element) {
  if (!element?.previewDetailSource) return element;
  const h = element.w * 4 / 3;
  return { ...element, h, crop: detailPreviewCrop(element.w, h, element.previewDetailSource) };
}

// SVG labels avoid a foreignObject percentage-height dependency in overview.
// These canvas-unit font sizes match the scoped overview typography tokens.
export function detailNoticeLines(width, height, label) {
  const x = 24, w = Math.max(1, width - x * 2);
  const lines = [
    ...wrapText(`${label} 확대컷`, w, 32).map(text => ({ text, size: 32, lineHeight: 32 * 1.3, title: true })),
    ...wrapText('AI가 이 부분을 새로 만들어요', w, 26).map(text => ({ text, size: 26, lineHeight: 26 * 1.35, title: false })),
  ];
  let y = height - 20 - 4 - lines.reduce((sum, line) => sum + line.lineHeight, 0);
  return lines.map((line, index) => {
    if (index && lines[index - 1].title !== line.title) y += 4;
    y += line.lineHeight;
    return { ...line, x, y: y - (line.lineHeight - line.size), w };
  });
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
  const byId = new Map(storyboard.map(block => [block.id, block]));
  const targets = new Map(storyboard.map(block => [block.id,
    detailTargetPreview(block, product, previewCatalogs.detailRecommendations)]));
  const shopName = input.shopName ?? (storyboard.some(block => pageTemplatesCatalog.designs[block.pageDesignId]?.framePhotos)
    ? catalogs.shopName : undefined);
  return assemblePage({ ...input, storyboard, product, analysis, pageStyle: input.pageStyle || analysisPageStyle(analysis.pageStyle, storyboard), shopName, cutResults: cuts }).map(block => {
    let changed = false;
    const elements = block.elements.map(element => {
      if (element.type !== 'image' || completed.has(element.sourceBlockId)) return element;
      const target = targets.get(element.sourceBlockId);
      const measured = target && catalogs.detailPreviewDimensions?.[target.src];
      const previewSource = measured ? { ...target, ...measured, dimensionsKnown: measured.dimensionsKnown !== false } : target;
      const crop = target && detailPreviewCrop(element.w, element.h, previewSource);
      const source = byId.get(element.sourceBlockId);
      const planned = isProductDetail(source) && Boolean(source.detailTargetId);
      if (!crop && !planned) return element;
      changed = true;
      return { ...element, ...(crop ? { src: target.src, crop, previewDetailCrop: true, previewDetailSource: previewSource } : {}),
        ...(planned ? { previewDetailLabel: detailTargetById(previewCatalogs.detailRecommendations, source.detailTargetId)?.label || '디테일' } : {}) };
    });
    return changed ? { ...block, elements } : block;
  });
}
