/* =============================================================
   lib/matchingFit.js — matching-garment fit profile helpers.
   The garment vocabulary is selected only from server/mock metadata;
   display names are never inspected.
   ============================================================= */
import { axesFor } from './fitAxes.js';

const MATCHING_AXIS = Object.freeze({ pants: 'cut', skirt: 'silhouette' });

const selectionId = (selection) => {
  if (typeof selection === 'string') return selection;
  if (!selection || typeof selection !== 'object') return null;
  return selection.clothingId || selection.id || null;
};

// Keep this resolution order aligned with server/app/agents/mannequin.py:
// contract matchSelections main, historical {main} shape, then the selected
// legacy matchClothing item with the lowest selOrder. An unroled list entry is
// never authoritative.
export function resolveMainMatchingItem(analysis) {
  const contractCandidates = Array.isArray(analysis?.matchCandidates) ? analysis.matchCandidates : [];
  const legacyCandidates = Array.isArray(analysis?.matchClothing) ? analysis.matchClothing : [];
  const byId = new Map();
  [...contractCandidates, ...legacyCandidates].forEach((item) => {
    if (!item?.id) return;
    byId.set(item.id, { ...(byId.get(item.id) || {}), ...item });
  });

  const selections = analysis?.matchSelections;
  let mainId = null;
  if (Array.isArray(selections)) {
    mainId = selectionId(selections.find((selection) => selection?.role === 'main'));
  } else if (selections && typeof selections === 'object') {
    mainId = selectionId(selections.main);
  }
  if (mainId) return byId.get(mainId) || null;

  const legacyMain = legacyCandidates
    .filter((item) => item?.selected && item?.id)
    .slice()
    .sort((a, b) => (a.selOrder || 99) - (b.selOrder || 99))[0];
  return legacyMain ? (byId.get(legacyMain.id) || legacyMain) : null;
}

export function matchingFitDefinition(item, gender) {
  if (typeof item?.id !== 'string') return null;
  const fitCategory = item.fitCategory;
  const axisKey = MATCHING_AXIS[fitCategory];
  if (!axisKey) return null;
  const values = axesFor(fitCategory, gender)?.[axisKey] || [];
  if (!values.length) return null;
  return { clothingId: item.id, fitCategory, axisKey, values };
}

// Return a sanitized v2 matchingFit. A legacy matchCut is migrated only when
// the authoritative main item is full-length pants (fitCategory='pants').
export function matchingFitFromProfile(profile, definition) {
  if (!profile || !definition) return null;
  const { clothingId, fitCategory, axisKey, values } = definition;
  const isValidValue = (value) => values.some((option) => option.value === value);
  const current = profile.matchingFit;
  const currentValue = current?.axes?.[axisKey];
  if (
    current?.clothingId === clothingId
    && current.fitCategory === fitCategory
    && isValidValue(currentValue)
  ) {
    return { clothingId, fitCategory, axes: { [axisKey]: currentValue } };
  }

  if (fitCategory === 'pants' && isValidValue(profile.matchCut)) {
    return { clothingId, fitCategory: 'pants', axes: { cut: profile.matchCut } };
  }
  return null;
}
