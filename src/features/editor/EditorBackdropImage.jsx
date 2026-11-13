import React from 'react';
import { backdropEdgeTiles } from './editorImageBlur.js';
import { isGifImage } from '../../lib/gif/gifMedia.js';
import styles from './EditorBackdropImage.module.css';

// Nested-SVG compatible so editor and storyboard use the same crop and edge
// extension. Export replaces this preview with independently baked PNG bytes.
export function BackdropSvg({ element: el, src = el.src, ...props }) {
  const id = React.useId().replaceAll(':', '');
  const sourceId = `backdrop-source-${id}`, filterId = `backdrop-blur-${id}`, clipId = `backdrop-clip-${id}`;
  const pad = Math.ceil(el.blur * 3) + 1;
  const source = <image href={src} x={-(el.crop?.ox || 0)} y={-(el.crop?.oy || 0)}
    width={el.crop?.iw || el.w} height={el.crop?.ih || el.h}
    preserveAspectRatio={el.crop || el.fit === 'fill' ? 'none' : el.fit === 'contain' ? 'xMidYMid meet' : 'xMidYMid slice'} />;
  return <svg viewBox={`0 0 ${el.w} ${el.h}`} width="100%" height="100%" preserveAspectRatio="none" overflow="hidden" aria-hidden="true" {...props}>
    <defs>
      <clipPath id={clipId}><rect width={el.w} height={el.h} rx={el.radius || 0} /></clipPath>
      {el.blur > 0 && <>
        <g id={sourceId}>{source}</g>
        <filter id={filterId} filterUnits="userSpaceOnUse" x={-pad} y={-pad}
          width={el.w + pad * 2} height={el.h + pad * 2} colorInterpolationFilters="sRGB">
          <feGaussianBlur stdDeviation={el.blur} />
        </filter>
      </>}
    </defs>
    <g clipPath={`url(#${clipId})`}>
      {el.blur > 0 ? <g filter={`url(#${filterId})`}>
        {backdropEdgeTiles(el.w, el.h, el.blur).map(tile => <svg key={tile.key} x={tile.x} y={tile.y}
          width={tile.width} height={tile.height} viewBox={tile.viewBox} preserveAspectRatio="none" overflow="hidden">
          <use href={`#${sourceId}`} />
        </svg>)}
      </g> : source}
    </g>
  </svg>;
}

export function EditorBackdropImage({ element: el, src = el.src }) {
  return <div className={styles.frame} data-editor-blur={el.blur} data-blur-width={el.w} data-blur-height={el.h}
    data-blur-fit={el.fit || 'cover'} data-blur-crop={el.crop ? JSON.stringify(el.crop) : undefined}>
    <BackdropSvg element={el} src={src} />
    <img className={styles.source} src={src} data-gif-poster={isGifImage(el) ? el.posterSrc : undefined} alt="" draggable={false} />
  </div>;
}
