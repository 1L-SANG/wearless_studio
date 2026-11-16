import React, { useState } from 'react';
import { generationExampleImageSources } from '../../lib/generationExamples.js';
import { ExampleThumb } from './StoryboardControls.jsx';
import styles from './StoryboardChoices.module.css';

// One enclosing button owns focus and selection. Side pictures only preview on a mouse.
export function PhotoMosaic({ members, cover, selected = false, all = false, unplacedExampleIds = [] }) {
  const [hovered, setHovered] = useState(null);
  const sides = members.filter(example => example.id !== cover.id);
  const shown = all ? sides : sides.slice(0, 3);
  const preview = members.find(example => example.id === hovered) || cover;
  const image = example => <ExampleThumb example={{ ...example, thumb: generationExampleImageSources(example).src || example.thumb }} />;
  return <span className={`${styles.mosaic}${all ? ` ${styles.allMosaic}` : ''}${selected ? ` ${styles.selectedMosaic}` : ''}`}
    data-count={all ? members.length : Math.min(members.length, 4)}>
    <span className={styles.mosaicCover} data-unplaced={unplacedExampleIds.includes(preview.id) || undefined}>{image(preview)}</span>
    <span className={styles.mosaicSides}>{shown.map((example, index) => <span key={example.id} className={styles.mosaicSide}
      data-unplaced={unplacedExampleIds.includes(example.id) || undefined}
      onPointerEnter={event => { if (event.pointerType === 'mouse') setHovered(example.id); }} onPointerLeave={() => setHovered(null)}>
      {image(example)}{!all && index === shown.length - 1 && sides.length > shown.length
        && <span className={styles.more}>+{sides.length - shown.length}장</span>}
    </span>)}</span>
  </span>;
}
