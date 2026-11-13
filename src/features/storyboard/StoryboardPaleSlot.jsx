import React, { useMemo } from 'react';
import { addStageAddition } from '../../lib/pageTemplates/stageAdditions.js';
import { assembleStoryboardPreview, storyboardPreviewSource } from './storyboardPreview.js';
import styles from './StoryboardPaleSlot.module.css';

export function StoryboardPaleSlot({ item, blocks, runKey, catalogs, disabled, onAdd }) {
  const geometry = useMemo(() => {
    if (item.kind !== 'pointsFabric') return null;
    const storyboard = addStageAddition(blocks, runKey, item,
      { product: catalogs.detailProduct, analysis: catalogs.analysis, makeId: () => 'preview-fabric' });
    return assembleStoryboardPreview({ storyboard, catalogs }).flatMap(block => block.elements)
      .find(element => element.type === 'image' && element.sourceBlockId === 'preview-fabric');
  }, [item, blocks, runKey, catalogs]);
  const crop = geometry?.crop;
  const src = item.src || (item.block && storyboardPreviewSource(item.block, catalogs));
  return <button type="button" className={styles.slot} disabled={disabled} onClick={() => onAdd(item)} aria-label={`${item.label} 넣기`}>
    {crop ? <svg viewBox={`0 0 ${geometry.w} ${geometry.h}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true" className={styles.picture}>
      <svg width={geometry.w} height={geometry.h} viewBox={`0 0 ${geometry.w} ${geometry.h}`} overflow="hidden">
        <image href={geometry.src} width={crop.iw} height={crop.ih} x={-crop.ox} y={-crop.oy} preserveAspectRatio="xMidYMid slice" />
      </svg>
    </svg> : src && <img className={styles.picture} src={src} alt="" loading="lazy" decoding="async" />}
    <span className={styles.label}><span aria-hidden="true">＋</span>{item.label}</span>
  </button>;
}
