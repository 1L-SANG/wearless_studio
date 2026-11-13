import React from 'react';
import { storyboardPositionId } from '../../lib/pageTemplates/storyboardPositions.js';
import { BackdropSvg } from '../editor/EditorBackdropImage.jsx';
import { hasBackdropBlur } from '../editor/editorImageBlur.js';
import styles from './StoryboardLayout.module.css';
import { detailNoticeLines } from './storyboardPreview.js';

const rgbaPaint = value => {
  const match = value?.match(/^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/);
  return match ? { color: `rgb(${match[1]}, ${match[2]}, ${match[3]})`, opacity: Number(match[4]) } : null;
};
const gradientPaint = fill => {
  const match = fill?.match(/^linear-gradient\(180deg,\s*(rgba\([^)]+\)) 0%,\s*(rgba\([^)]+\)) 100%\)$/);
  const stops = match && [rgbaPaint(match[1]), rgbaPaint(match[2])];
  return stops?.every(Boolean) ? stops : null;
};
const shadowPaint = shadow => {
  const match = shadow?.match(/^(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px\s+(rgba\([^)]+\))$/);
  const color = match && rgbaPaint(match[4]);
  return color ? { x: Number(match[1]), y: Number(match[2]), blur: Number(match[3]), ...color } : null;
};

const tonePaint = (paint, colors) => colors && [colors.accentBg, colors.accentLine, colors.accentInk].includes(paint) ? paint : undefined;

function PreviewElement({ element: el, wireframe, onScrim, photoPanel, emphasizeText, toneColors, detailLabels }) {
  const clipId = React.useId();
  if (el.hidden) return null;
  if (hasBackdropBlur(el)) return wireframe
    ? <rect x={el.x} y={el.y} width={el.w} height={el.h} className={styles.previewWireBackdrop} />
    : <BackdropSvg element={el} x={el.x} y={el.y} width={el.w} height={el.h} />;
  if (el.type === 'image') return <svg x={el.x} y={el.y} width={el.w} height={el.h} viewBox={`0 0 ${el.w} ${el.h}`}>
    <defs><clipPath id={clipId}><rect width={el.w} height={el.h} rx={el.radius || 0} /></clipPath></defs>
    <rect width={el.w} height={el.h} className={wireframe ? styles.previewWirePhoto : styles.previewEmpty} rx={el.radius || 0} />
    {!wireframe && el.src && el.previewDetailLabel && el.crop && (el.crop.iw < el.w || el.crop.ih < el.h) &&
      <image className={styles.detailSource} href={el.src} width={el.w} height={el.h} preserveAspectRatio="xMidYMid slice" clipPath={`url(#${clipId})`} />}
    {!wireframe && el.src && <image className={el.previewDetailLabel ? styles.detailSource : undefined} href={el.src} width={el.crop?.iw || el.w} height={el.crop?.ih || el.h}
      x={-(el.crop?.ox || 0)} y={-(el.crop?.oy || 0)} preserveAspectRatio="xMidYMid slice" clipPath={`url(#${clipId})`} />}
    {!wireframe && el.previewDetailLabel && <>
      <defs><linearGradient id={`${clipId}-detail`} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="var(--bg-1)" className={styles.detailFadeStart} />
        <stop offset="52%" stopColor="var(--bg-1)" className={styles.detailFadeStart} />
        <stop offset="100%" stopColor="var(--bg-1)" className={styles.detailFadeEnd} />
      </linearGradient></defs>
      <rect width={el.w} height={el.h} rx={el.radius || 0} fill={`url(#${clipId}-detail)`} data-detail-veil />
      {detailLabels && <g className={styles.previewDetailNotice} data-detail-notice>
        {detailNoticeLines(el.w, el.h, el.previewDetailLabel).map((line, index) =>
          <text key={index} x={line.x} y={line.y} className={line.title ? styles.previewDetailTitle : styles.previewDetailNote}>{line.text}</text>)}
      </g>}
    </>}
  </svg>;
  if (el.type === 'shape') {
    const gradient = gradientPaint(el.fill), shadow = shadowPaint(el.boxShadow);
    const gradientId = `${clipId}-gradient`, shadowId = `${clipId}-shadow`;
    const tinted = wireframe && tonePaint(el.fill, toneColors);
    const panelPaint = wireframe && photoPanel && !gradient
      ? tinted ? `color-mix(in srgb, var(--sb-wire-panel) 70%, ${tinted})` : 'var(--sb-wire-panel)' : undefined;
    const paint = {
      className: wireframe && !gradient && !tinted && !panelPaint ? styles.previewWireShape : undefined,
      fill: gradient ? `url(#${gradientId})` : panelPaint || (wireframe ? tinted : el.fill),
      stroke: wireframe ? undefined : el.stroke, strokeWidth: el.strokeWidth || 0,
      filter: shadow ? `url(#${shadowId})` : undefined,
    };
    return <g opacity={el.opacity ?? 1}>
      {(gradient || shadow) && <defs>
        {gradient && <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          {gradient.map((stop, index) => <stop key={index} offset={`${index * 100}%`}
            stopColor={wireframe ? 'var(--sb-wire-scrim-end)' : stop.color} stopOpacity={stop.opacity} />)}
        </linearGradient>}
        {shadow && <filter id={shadowId} x="-50%" y="-100%" width="200%" height="300%">
          <feDropShadow dx={shadow.x} dy={shadow.y} stdDeviation={shadow.blur / 2}
            floodColor={wireframe ? 'var(--sb-wire-3)' : shadow.color} floodOpacity={shadow.opacity} />
        </filter>}
      </defs>}
      {el.shape === 'circle'
        ? <ellipse cx={el.x + el.w / 2} cy={el.y + el.h / 2} rx={el.w / 2} ry={el.h / 2} {...paint} />
        : <rect x={el.x} y={el.y} width={el.w} height={el.h} rx={el.radius || 0} {...paint} />}
    </g>;
  }
  if (el.type === 'line') return <line x1={el.x} y1={el.y} x2={el.x + el.w} y2={el.y + el.h}
    className={styles.previewRule} strokeWidth={el.thickness || el.strokeWidth || 1} />;
  if (el.type !== 'text') return null;
  const size = Number.isFinite(el.style?.size) && el.style.size > 0 ? el.style.size : 16;
  const lineHeight = Number.isFinite(el.style?.lineHeight) && el.style.lineHeight > 0 ? el.style.lineHeight : size * 1.5;
  const count = Math.max(1, Math.min(5, Math.floor(el.h / lineHeight)));
  return <g aria-hidden="true">{Array.from({ length: count }, (_, i) => {
    const width = Math.max(0, el.w * (i === count - 1 ? .63 : .9));
    const x = el.x + (el.style?.align === 'center' ? (el.w - width) / 2 : el.style?.align === 'right' ? el.w - width : 0);
    const barHeight = Math.min(emphasizeText ? Math.max(size * .6, 10) : size * .35, el.h);
    return <rect key={i} x={x} y={el.y + i * lineHeight + (emphasizeText ? (lineHeight - barHeight) / 2 : size * .35)} width={width} height={barHeight} rx={size * .15}
      className={`${styles.previewTextLine}${wireframe && onScrim ? ` ${styles.previewTextOnScrim}` : ''}`} />;
  })}</g>;
}

// Preview geometry is assembled once. Copy stays as faint lines until generation.
export function StoryboardPreview({ blocks = [], label = '페이지 미리보기', fit = false, wireframe = false,
  highlightedPosition, selectedSourceId, selectedPositionId, onPositionClick, emphasizeText = false, toneColors, detailLabels = false }) {
  const visible = (Array.isArray(blocks) ? blocks : []).filter(block => block && Number.isFinite(block.h) && block.h > 0);
  const height = visible.reduce((sum, block) => sum + block.h, 0) || 1;
  let y = 0;
  return <svg className={`${styles.preview}${fit ? ` ${styles.previewFit}` : ''}`} viewBox={`0 0 1000 ${height}`}
    width={fit ? '100%' : undefined} height={fit ? '100%' : undefined} preserveAspectRatio="xMidYMid meet" role="img" aria-label={label}>
    {visible.map(block => {
      const top = y; y += block.h;
      return <g key={block.id} transform={`translate(0 ${top})`}>
        <rect width="1000" height={block.h} className={wireframe && !tonePaint(block.bg, toneColors) ? styles.previewWireCanvas : undefined}
          fill={wireframe ? tonePaint(block.bg, toneColors) : block.bg} />
        {(block.elements || []).filter(element => element && ['x', 'y', 'w', 'h'].every(key => Number.isFinite(element[key])))
          .map(element => {
            // A panel precedes and encloses its photo. Overlay bubbles and
            // section-gap paint are not photo panels and retain their old ink.
            const photoPanel = element.type === 'shape' && element.shape === 'rect'
              && block.elements.slice(block.elements.indexOf(element) + 1).some(photo => photo?.type === 'image'
              && photo.x >= element.x && photo.y >= element.y
              && photo.x + photo.w <= element.x + element.w && photo.y + photo.h <= element.y + element.h);
            const onScrim = wireframe && element.type === 'text'
              && block.elements.slice(0, block.elements.indexOf(element)).some(paint => paint?.type === 'shape'
              && gradientPaint(paint.fill) && element.x < paint.x + paint.w && element.x + element.w > paint.x
              && element.y < paint.y + paint.h && element.y + element.h > paint.y);
            const viewId = storyboardPositionId(block, element);
            const highlighted = highlightedPosition === viewId || (!highlightedPosition && (selectedPositionId
              ? viewId === selectedPositionId : selectedSourceId && element.sourceBlockId === selectedSourceId));
            return <React.Fragment key={element.id}><PreviewElement element={element} wireframe={wireframe} onScrim={onScrim} photoPanel={photoPanel} emphasizeText={emphasizeText} toneColors={toneColors} detailLabels={detailLabels} />
              {element.type === 'image' && !hasBackdropBlur(element) && (onPositionClick || highlighted) && <rect x={element.x} y={element.y} width={element.w} height={element.h}
                data-preview-position={viewId} className={`${styles.positionHit}${highlighted ? ` ${styles.positionHighlight}` : ''}`}
                vectorEffect="non-scaling-stroke" onClick={onPositionClick ? event => { event.stopPropagation(); onPositionClick({ viewId, sourceBlockId: element.sourceBlockId, slotKey: element.slotKey }); } : undefined} />}
            </React.Fragment>;
          })}
      </g>;
    })}
  </svg>;
}
