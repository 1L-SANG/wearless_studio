import { isSupportedCutType } from './storyboardTaxonomy.js';

const ASSET_KINDS = new Set(['worn', 'upload', 'ghost', 'detail', 'product_unknown', 'unknown']);
const WORN_CUT_TYPES = new Set(['styling', 'horizon']);

/** Stable file capabilities may be relative, absolute, or signed with a query. */
export function extractEditorAssetId(src) {
  if (typeof src !== 'string' || !src || (!src.startsWith('/') && !/^https?:\/\//i.test(src))) return null;
  try {
    const url = new URL(src, 'https://editor.invalid');
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    const match = /^\/v1\/assets\/([^/]+)\/file\/?$/.exec(url.pathname);
    if (!match) return null;
    const id = decodeURIComponent(match[1]);
    return id && !/[/?#\x00-\x1f]/.test(id) ? id : null;
  } catch { return null; }
}

/** Asset facts come from the asset-info response, never its current canvas slot. */
export function classifyEditorAssetInfo(info) {
  if (!info || typeof info !== 'object') return 'unknown';
  if (info.kind === 'upload' || info.upload || info.source === 'upload' || info.origin === 'upload') return 'upload';
  const recipe = info.recipe || info.metadata?.generation?.recipe || info.generation?.recipe;
  const cutType = info.cutType || info.cut_type || recipe?.cutType;
  if (cutType && !isSupportedCutType(cutType)) return 'unknown';
  if (ASSET_KINDS.has(info.kind)) return info.kind;
  if (info.detail_recipe || info.metadata?.detail_recipe || info.detailReshoot || recipe?.shot === 'detail') return 'detail';
  if (WORN_CUT_TYPES.has(cutType)) return 'worn';
  if (cutType === 'product') return recipe?.shot === 'ghost' ? 'ghost' : 'product_unknown';
  return 'unknown';
}
