export const PENDING_TILE_DELAY_MS = 120;

export const getPendingTileCount = (fileCount, room) => (
  Math.min(Math.max(0, Number(fileCount) || 0), Math.max(0, Number(room) || 0))
);
