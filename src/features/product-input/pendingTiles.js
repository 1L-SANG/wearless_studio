import { looksLikeImageFile } from '../../lib/imageTranscode.js';

export const PENDING_TILE_DELAY_MS = 120;

export const getPendingTileCount = (filesOrCount, room) => {
  const fileCount = typeof filesOrCount === 'number'
    ? filesOrCount
    : [...(filesOrCount || [])].filter(looksLikeImageFile).length;
  return Math.min(Math.max(0, Number(fileCount) || 0), Math.max(0, Number(room) || 0));
};

const REQUIRED_BASE_SLOTS = ['Front', 'Back'];

export const getBaseSlotUploadRoom = (images, slot, max = 6) => {
  const current = images || [];
  const reservedForOtherRequiredSlots = REQUIRED_BASE_SLOTS.filter((requiredSlot) => (
    requiredSlot !== slot && !current.some((image) => image.slot === requiredSlot)
  )).length;
  return Math.max(0, max - current.length - reservedForOtherRequiredSlots);
};
