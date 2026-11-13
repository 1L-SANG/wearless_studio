// Optional photo positions are represented only by the existing design ID.
const pairs = Object.freeze({
  'P01-D11': 'P01-D01', 'P01-D09': 'P01-D09-G', 'P01-SHOW-01': 'P01-SHOW-01-G',
  'T03-D01': 'T03-D01-P', 'T03-D06': 'T03-D06-P',
});
const bases = Object.fromEntries(Object.entries(pairs).map(([base, pair]) => [pair, base]));
export const baseDesignId = id => bases[id] || id;
export const pairedDesignId = id => pairs[baseDesignId(id)] || null;
export const isPairedDesign = id => Boolean(bases[id]);
export const pairedPhotoSlot = id => isPairedDesign(id) ? id.startsWith('P01-') ? 'ghost' : 'product' : null;
