import React from 'react';
import { baseDesignId } from '../../lib/pageTemplates/pairedDesigns.js';

const paper = 'var(--bg-1)';
const photo = 'var(--sb-design-silver-photo)';
const pale = 'var(--sb-design-silver-pale)';
const title = 'var(--sb-design-silver-title)';
const text = 'var(--sb-design-silver-text)';
const outer = 'var(--sb-design-silver-outer)';

function Bar({ x, y, width, height = 2.6, fill = text }) {
  return <rect x={x} y={y} width={width} height={height} rx={height / 2} fill={fill} />;
}

function Photo({ x, y, width, height }) {
  return <rect x={x} y={y} width={width} height={height} rx="2" fill={photo} />;
}

const shapes = {
  'first-centered': <>
    <rect width="150" height="130" fill={paper} />
    <Photo x={14} y={10} width={122} height={110} />
    <Bar x={47} y={54} width={56} height={4.6} fill={paper} />
    <Bar x={38} y={65} width={74} fill={paper} />
    <Bar x={50} y={72} width={50} fill={paper} />
  </>,
  'first-product-name': <>
    <rect width="150" height="130" fill={paper} />
    <Photo x={14} y={10} width={122} height={88} />
    <Bar x={14} y={106} width={46} height={4.6} fill={title} />
    <Bar x={14} y={116} width={72} />
  </>,
  'first-comment-board': <>
    <rect width="150" height="130" fill={paper} />
    <Photo x={14} y={10} width={122} height={78} />
    <rect x="14" y="88" width="122" height="36" fill={title} />
    <Bar x={22} y={94} width={24} height={2} fill={paper} />
    <Bar x={22} y={101} width={64} height={4.6} fill={paper} />
    <Bar x={22} y={112} width={92} fill={paper} />
  </>,
  'wear-basic': <>
    <rect width="150" height="130" fill={paper} />
    <Photo x={24} y={10} width={102} height={62} />
    <Photo x={24} y={76} width={102} height={54} />
  </>,
  'wear-frame': <>
    <rect width="150" height="130" fill={paper} />
    <rect width="150" height="130" fill={outer} />
    <rect x="30" y="10" width="90" height="110" fill={paper} />
    <Bar x={48} y={17} width={54} height={4.6} fill={title} />
    <Bar x={62} y={25} width={26} height={2} />
    <Photo x={38} y={35} width={74} height={35} />
    <Photo x={38} y={74} width={74} height={35} />
  </>,
  'points-card': <>
    <rect width="150" height="130" fill={pale} />
    <rect x="24" y="12" width="102" height="106" fill={paper} />
    <Photo x={24} y={12} width={102} height={70} />
    <Bar x={55} y={92} width={40} height={4.4} fill={title} />
    <Bar x={46} y={103} width={58} />
    <Bar x={54} y={110} width={42} />
  </>,
  'points-text-first': <>
    <rect width="150" height="130" fill={paper} />
    <Bar x={14} y={14} width={46} height={4.6} fill={title} />
    <Bar x={14} y={26} width={86} />
    <Bar x={14} y={33} width={64} />
    <Photo x={14} y={44} width={122} height={74} />
  </>,
  'fabric-side': <>
    <rect width="150" height="130" fill={paper} />
    <Bar x={14} y={12} width={34} />
    <Bar x={14} y={22} width={68} height={4.6} fill={title} />
    <Photo x={14} y={40} width={58} height={58} />
    <Bar x={82} y={45} width={54} />
    <Bar x={82} y={52} width={46} />
    <Bar x={82} y={59} width={50} />
    <Bar x={82} y={78} width={40} />
    <Bar x={82} y={86} width={48} height={2} fill={title} />
    <Bar x={14} y={110} width={90} />
  </>,
  'fabric-outside': <>
    <rect width="150" height="130" fill={paper} />
    <Bar x={14} y={12} width={34} />
    <Photo x={14} y={26} width={122} height={64} />
    <Bar x={14} y={100} width={70} height={4.6} fill={title} />
    <Bar x={14} y={111} width={42} height={2} />
    <Bar x={14} y={119} width={110} />
    <Bar x={14} y={125} width={94} />
  </>,
};

const designShapes = {
  first: { 'P01-D09': 'first-centered', 'P01-SHOW-01': 'first-product-name', 'P01-SHOW-COMMENT-01': 'first-comment-board' },
  points: { 'PT-PHOTO-CARD-01': 'points-card', 'PT-TEXT-PHOTO-01': 'points-text-first' },
  fabric: { 'T03-D01': 'fabric-side', 'T03-D06': 'fabric-outside' },
};

// These are fixed choice illustrations, independent of the current photos,
// selected layout, frame count and page theme.
export function StoryboardDesignIllustration({ kind, designId, decoration }) {
  const shape = kind === 'wear' ? decoration === 'frame' ? 'wear-frame' : 'wear-basic'
    : designShapes[kind]?.[baseDesignId(designId)];
  if (!shape) return null;
  return <svg viewBox="0 0 150 130" aria-hidden="true" focusable="false" data-design-illustration={shape}>
    {shapes[shape]}
  </svg>;
}
