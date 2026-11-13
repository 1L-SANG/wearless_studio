import tags from '../../../server/app/data/example_tags.json' with { type: 'json' };
import { STORYBOARD_SPACE_SETS } from '../storyboardSpaceSetCatalog.js';

export const PLACE_TABS = Object.freeze([
  { value: 'all', label: '전체' }, { value: 'cafe', label: '카페' }, { value: 'home', label: '집' },
  { value: 'indoor', label: '실내' }, { value: 'alley', label: '생활 골목' }, { value: 'street', label: '상권 거리' },
  { value: 'building', label: '건물 앞' }, { value: 'park', label: '공원' }, { value: 'nature', label: '자연' },
]);
const category = key => PLACE_TABS.find(tab => tab.value === key)
  || (key === 'studio' ? { value: 'studio', label: '스튜디오' } : { value: 'unknown', label: '기타' });
const PLACE_KEYS = {
  'cafe-shop-interior': 'cafe', 'home-interior': 'home', 'atelier-interior': 'indoor',
  'library-interior': 'indoor', 'building-interior': 'indoor', 'service-interior': 'indoor',
  'industrial-yard': 'building', 'urban-alley': 'alley', 'storefront-street': 'street',
  'urban-building-exterior': 'building', 'park-garden': 'park', waterfront: 'nature',
  'resort-terrace': 'nature', 'horizon-studio': 'studio',
};

export function placeCategory(raw, scene = {}) {
  if (PLACE_KEYS[raw]) return category(PLACE_KEYS[raw]);
  const group = scene.placeGroup || raw;
  if (group === 'urban') return category({ commercial_street: 'street', building_front: 'building' }[scene.urbanSubtype] || 'alley');
  if (group === 'indoor_family') return category('indoor');
  if (['outdoor_family', 'coast', 'resort', 'night'].includes(group)) return category('nature');
  return category(group);
}

const setByExample = new Map(STORYBOARD_SPACE_SETS.flatMap(set => set.members.map(member => [member.exampleId, set])));
export function examplePlace(example) {
  if (example?.cutType === 'horizon') return category('studio');
  const exampleTags = tags.examples[example?.id] || {};
  const set = setByExample.get(example?.id);
  const setTags = tags.sets[example?.spaceSetId || exampleTags.setId || set?.id] || {};
  return placeCategory(example?.placeType || exampleTags.placeType || setTags.placeType || set?.placeType,
    exampleTags.scene || setTags.scene);
}
export const exampleSpaceSet = example => setByExample.get(example?.id) || null;
export const visiblePlaceTabs = (selected = 'all', expanded = false) => expanded ? PLACE_TABS
  : PLACE_TABS.filter((tab, index) => index <= 7 || tab.value === selected);
export const usedGalleryExampleIds = (blocks, selectedId) => new Set((blocks || [])
  .filter(block => block.id !== selectedId).map(block => block.exampleId).filter(Boolean));
export const filterGalleryExamples = (examples, { place = 'all', shot = 'all' } = {}) => examples.filter(example =>
  (place === 'all' || examplePlace(example).value === place) && (shot === 'all' || example.shot === shot));

// These entries and their shuffle belong to the picker session, never to saved blocks.
export function groupGalleryExamples(examples, sets, { selectedId, setsOnly = false, minimumSetMembers = 2 } = {}) {
  const byId = new Map(examples.map(example => [example.id, example]));
  const groups = new Map(), byMember = new Map();
  for (const set of sets) {
    const members = set.members.map(member => byId.get(member.exampleId)).filter(Boolean);
    if (!members.length || (!setsOnly && members.length < minimumSetMembers)) continue;
    const entry = { id: set.id, kind: 'stack', set, members,
      example: members.find(example => example.id === selectedId) || members[0] };
    groups.set(set.id, entry);
    members.forEach(example => byMember.set(example.id, entry));
  }
  if (setsOnly) return [...groups.values()];
  const emitted = new Set();
  return examples.flatMap(example => {
    const group = byMember.get(example.id);
    if (!group) return [{ id: example.id, kind: 'example', example }];
    if (emitted.has(group.id)) return [];
    emitted.add(group.id);
    return [group];
  });
}

export const createGallerySessionSeed = () => Math.floor(Math.random() * 0x100000000);
export function sampleGalleryEntries(entries, { selectedId, seed, limit = 48 } = {}) {
  let state = Number(seed) >>> 0;
  const random = () => {
    state += 0x6D2B79F5;
    let value = Math.imul(state ^ state >>> 15, 1 | state);
    value ^= value + Math.imul(value ^ value >>> 7, 61 | value);
    return ((value ^ value >>> 14) >>> 0) / 0x100000000;
  };
  const selected = entries.find(entry => entry.kind === 'stack'
    ? entry.members.some(example => example.id === selectedId) : entry.example.id === selectedId);
  const shuffled = entries.filter(entry => entry !== selected);
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const other = Math.floor(random() * (index + 1));
    [shuffled[index], shuffled[other]] = [shuffled[other], shuffled[index]];
  }
  return (selected ? [selected, ...shuffled] : shuffled).slice(0, Math.min(48, Math.max(0, limit)));
}

export function stackPopupColumns(count, availableWidth = Infinity) {
  const desired = count <= 4 ? count : Math.min(4, Math.ceil(count / 2));
  return Math.max(1, Math.min(desired, Math.floor((availableWidth - 16) / 104)));
}

export function stackPopupPlacement({ anchor, gallery, viewport, width, height, scrollTop = 0 }) {
  const room = edge => [anchor.top - 8 - viewport.top - edge, viewport.bottom - edge - anchor.bottom - 8];
  const [above, below] = room(8), [aboveEdge, belowEdge] = room(0);
  let side = 'top', scrollBy = 0;
  if (above >= height) side = 'top';
  else if (below >= height) side = 'bottom';
  else if (aboveEdge >= height) side = 'top';
  else if (belowEdge >= height) side = 'bottom';
  else if (above >= below && scrollTop >= height - above) scrollBy = -(height - above);
  else { side = 'bottom'; scrollBy = height - below; }
  const center = anchor.left + anchor.width / 2;
  const left = Math.max(gallery.left, Math.min(center - width / 2, gallery.right - width));
  return { side, left, top: side === 'top' ? anchor.top - 8 - height : anchor.bottom + 8,
    pointer: Math.max(18, Math.min(center - left, width - 18)), scrollBy };
}

export function samePlaceExamplePair(example, examples, sets = []) {
  const set = sets.find(item => item.members.some(member => member.exampleId === example.id));
  if (!set && examplePlace(example).value === 'unknown') return [example, example];
  const candidates = examples.filter(item => item.id !== example.id && item.cutType === example.cutType
    && item.shot !== example.shot && (set ? set.members.some(member => member.exampleId === item.id)
      : examplePlace(item).value === examplePlace(example).value));
  const companion = candidates[0] || examples.find(item => item.id !== example.id && item.cutType === example.cutType
    && examplePlace(item).value === examplePlace(example).value);
  return [example, companion || example];
}
