import toneOrder from '../../data/exampleToneOrder.json' with { type: 'json' };
import { hashSeed } from '../storyboardEntryPlacement.js';
import { selectGenerationExamples, generationGalleryPageForExample } from '../generationExamples.js';
import { blockAllowedForModel, filterSpaceSetsForModel } from '../identityScope.js';
import { PLACE_TABS, examplePlace, exampleSpaceSet, groupGalleryExamples } from './storyboardGallery.js';

export const PHOTO_PLACE_TABS = Object.freeze(PLACE_TABS.filter(tab => tab.value !== 'all'));
export const isSinglePhotoSection = block => ['first', 'review', 'faq'].includes(block?.pageSectionKind);
export const resolvePhotoPlace = (place, tabs) => tabs.some(tab => tab.value === place) ? place : tabs[0]?.value ?? null;
const photoPlaceValue = example => {
  const place = examplePlace(example).value;
  return example?.cutType === 'styling' && place === 'studio' ? 'indoor' : place;
};
export const initialPhotoPlace = (example, tabs = PHOTO_PLACE_TABS) => resolvePhotoPlace(photoPlaceValue(example), tabs);
export const initialPhotoFraming = (block, example = null) => {
  if (String(example?.id || block?.exampleId || '').startsWith('sig_')) return 'medium';
  const shot = example?.shot ?? (isSinglePhotoSection(block) ? null : block?.shot);
  return ['full', 'medium'].includes(shot) ? shot : 'medium';
};
export const framingLabel = (shot, clothingType) => shot === 'full' ? '전신' : clothingType === 'bottom' ? '하반신' : '상반신';

export function visiblePhotoPlaces(examples) {
  const places = new Set(examples.map(photoPlaceValue));
  return PHOTO_PLACE_TABS.filter(tab => places.has(tab.value));
}

// Only the ranked ring rotates. Unranked entries keep their original order at
// the end, including when the owner has not supplied any tone measurements yet.
export function orderPhotoChoices(entries, { projectId = 'default', scope = '', order = toneOrder.order } = {}) {
  const ranked = [], rest = [];
  entries.forEach((entry, index) => {
    const value = order?.[entry.example?.id];
    if (Number.isInteger(value)) ranked.push({ entry, value, index }); else rest.push(entry);
  });
  ranked.sort((a, b) => a.value - b.value || a.index - b.index);
  const start = ranked.length ? hashSeed(`${projectId || 'default'}:photo-tone:${scope}`) % ranked.length : 0;
  return [...ranked.slice(start), ...ranked.slice(0, start)].map(item => item.entry).concat(rest);
}

export function photoGalleryChoices(catalog, sets, {
  cutTypes = ['styling'], clothingType = 'top', gender, identityKind, shot = 'all', place = 'cafe',
  selectedId, projectId, order, studioStyle = null, groupStudioShoots = true, resolvedPlace = null,
} = {}) {
  const shots = shot === 'all' ? ['full', 'medium'] : [shot];
  const allowedSets = filterSpaceSetsForModel(sets, identityKind), allowedSetIds = new Set(allowedSets.map(set => set.id));
  const candidates = [...new Map(cutTypes.flatMap(cutType => shots.flatMap(value =>
    selectGenerationExamples(catalog, { cutType, studioStyle, shot: value, clothingType, gender, includeSetOnly: true,
      limit: catalog?.length || 0 }))).map(example => [example.id, example])).values()].filter(example => {
    const set = exampleSpaceSet(example);
    if (set && !allowedSetIds.has(set.id)) return false;
    return !identityKind || blockAllowedForModel({ source: 'ai', cutType: example.cutType, shot: example.shot,
      direction: example.direction || 'front', refScope: set && example.cutType === 'styling' ? 'pose' : 'all',
      spaceSetId: set?.id, exampleId: example.id }, identityKind);
  });
  const tabs = visiblePhotoPlaces(candidates);
  const activePlace = studioStyle === 'minimal' ? 'indoor' : place === 'studio' ? 'studio' : resolvedPlace ?? resolvePhotoPlace(place, tabs);
  const filtered = candidates.filter(example => (studioStyle === 'minimal' || (activePlace === 'studio' ? example.cutType === 'horizon' : photoPlaceValue(example) === activePlace)));
  const entries = groupGalleryExamples(filtered, allowedSets, { selectedId, minimumSetMembers: 1,
    groupStudioShoots: groupStudioShoots && (cutTypes.includes('horizon') || studioStyle === 'minimal') });
  return { candidates, tabs, place: activePlace,
    entries: orderPhotoChoices(entries, { projectId, scope: `${activePlace}:${shot}:all`, order }) };
}

export function photoChoicePages(entries, signature = null, pageSize = 6) {
  const size = Math.max(1, pageSize - (signature ? 1 : 0)), pages = [];
  for (let index = 0; index < Math.max(entries.length, 1); index += size) {
    pages.push([...(signature ? [{ id: signature.id, kind: 'signature', example: signature }] : []), ...entries.slice(index, index + size)]);
  }
  return pages;
}

export function photoChoicePageForExample(pages, exampleId) {
  return generationGalleryPageForExample(pages.map(page => page.flatMap(entry => entry.members || [entry.example])), exampleId);
}
