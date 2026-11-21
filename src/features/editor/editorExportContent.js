import { hasImageSource } from './editorImageSource.js';

export const EXPORT_PHOTO_SLOT_CLASS = 'el-slot';

// Editing controls and placeholders have no representation in the exported image.
export const CHROME_SELECTOR = [
  '.quick', '.blk-label', '.blk-resize', '.selection-marquee', '.crop-bar', '.crop-layer',
  '.align-bar', '.rot', '.hdl', '.edge', '.moveable-control-box',
  '.ed-genwait', '.ed-uploadwait', '.canvas-dropline', '.slot-add', '.image-drop-guide',
  '.el-empty-text', '.el-empty-template-copy', '.canvas-ai-layer', '.canvas-ai-slot-progress', '.canvas-ai-slot-arrived',
  `.${EXPORT_PHOTO_SLOT_CLASS}`, '.editor-snap-guide', '.editor-gap-guide',
].join(',');

export function isExportPhotoSlot(element) {
  return element.type === 'image' && !hasImageSource(element);
}

export function exportStillImageSource(src, poster) {
  return poster || src;
}
