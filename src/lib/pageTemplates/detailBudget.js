import { recommendedDetailTargets } from '../detailRecommendations.js';
import { pageTemplatesCatalog } from './catalog.js';

export const DETAIL_PHOTO_CAP = pageTemplatesCatalog.photoBudgets.detail;

// The server emits concrete subject kinds; `structure` is retained for existing
// recommendation data. Fabric has its own single-photo allocation.
const STRUCTURAL_KINDS = new Set(['structure', 'neckline', 'closure', 'pocket', 'waist', 'cuff', 'hem', 'construction', 'surface', 'label']);
export function seedDetailTargets(analysis = {}) {
  const recommendations = analysis.detailRecommendations;
  const candidates = recommendations?.candidates || [];
  const ranked = eligible => recommendedDetailTargets({ ...recommendations,
    candidates: candidates.filter(eligible),
  }, 'extended', analysis.sellingPoints);
  const structural = ranked(candidate => STRUCTURAL_KINDS.has(candidate.kind)).slice(0, Math.min(3, DETAIL_PHOTO_CAP));
  const points = structural.length >= 2 ? structural : [];
  const fabric = points.length < DETAIL_PHOTO_CAP
    ? ranked(candidate => candidate.kind === 'fabric')[0] : null;
  return { points, fabric };
}
