import { useEffect, useMemo, useState } from 'react';
import { detailTargetPreview } from '../../lib/detailRecommendations.js';
import { observeDetailPreviewDimensions } from './storyboardPreview.js';

export function useDetailPreviewDimensions(blocks, catalogs) {
  const [dimensions, setDimensions] = useState({});
  const sourceKey = useMemo(() => {
    const recommendations = catalogs?.detailRecommendations ?? catalogs?.analysis?.detailRecommendations;
    const sources = (blocks || []).map(block => detailTargetPreview(block, catalogs?.detailProduct, recommendations))
      .filter(target => target && target.dimensionsKnown !== true).map(target => target.src);
    return JSON.stringify([...new Set(sources)].sort());
  }, [blocks, catalogs]);
  useEffect(() => observeDetailPreviewDimensions(JSON.parse(sourceKey), (src, size) => {
    setDimensions(previous => previous[src]?.width === size.width && previous[src]?.height === size.height
      && previous[src]?.dimensionsKnown === size.dimensionsKnown
      ? previous : { ...previous, [src]: size });
  }), [sourceKey]);
  return dimensions;
}
