import {
  spaceSetIdFromGroupId,
  storyboardSpaceSetById,
  storyboardSpaceSetsFor,
} from './storyboardSpaceSetCatalog.js';

const PLACE_TYPE_GROUPS = {
  cafe: ['cafe', 'mixed-cafe', 'cafe-garden-entrance'],
  home: ['home/living', 'living', 'indoor-home', 'bright-lived-in-home', 'bright-interior'],
  indoor: ['indoor'],
  urban: [
    'street',
    'street-outdoor',
    'urban-outdoor',
    'outdoor-urban',
    'city-ivy-facade',
    'building-corridor',
    'outdoor-storefront',
    'outdoor-convenience',
    'residential-neighborhood-garage',
    'neighborhood-public-library',
    'indoor-loading-zone',
    'sports-center',
    'car-wash',
  ],
  coast: ['coast', 'coast-outdoor', 'outdoor-coast', '05. 작은 해변·항구'],
  nature: ['park', 'outdoor-park', 'riverside-promenade', 'outdoor-rainy-courtyard'],
  resort: ['resort', 'resort-outdoor', 'resort-terrace', 'botanical-resort'],
  night: ['night', 'night-riverwalk'],
  outdoor: ['outdoor'],
};

// 카탈로그 정규화 릴리스(codex/normalize-space-place-types)의 13개 표준 어휘 선반영 —
// 릴리스가 합쳐져도 분산 판정이 같은 버킷으로 이어지도록 한다.
Object.assign(PLACE_TYPE_GROUPS, {
  cafe: [...PLACE_TYPE_GROUPS.cafe, 'cafe-shop-interior'],
  home: [...PLACE_TYPE_GROUPS.home, 'home-interior'],
  indoor: [...PLACE_TYPE_GROUPS.indoor, 'building-interior', 'library-interior', 'atelier-interior'],
  urban: [...PLACE_TYPE_GROUPS.urban, 'urban-building-exterior', 'urban-alley', 'storefront-street', 'industrial-yard', 'service-interior'],
  coast: [...PLACE_TYPE_GROUPS.coast, 'waterfront'],
  nature: [...PLACE_TYPE_GROUPS.nature, 'park-garden'],
});

const NORMALIZED_PLACE_TYPES = new Map(
  Object.entries(PLACE_TYPE_GROUPS)
    .flatMap(([normalized, rawValues]) => rawValues.map((raw) => [raw, normalized])),
);

export function hashSeed(value) {
  const input = String(value);
  let hash = 0x811c9dc5;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

function mulberry32(seed) {
  return () => {
    let value = seed += 0x6d2b79f5;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function sortableId(value) {
  if (value && typeof value === 'object' && value.id != null) return String(value.id);
  return String(value);
}

export function seededPick(list, seedKey) {
  if (!Array.isArray(list) || list.length === 0) return null;
  const sorted = [...list].sort((left, right) => {
    const leftId = sortableId(left);
    const rightId = sortableId(right);
    if (leftId === rightId) return 0;
    return leftId < rightId ? -1 : 1;
  });
  const random = mulberry32(hashSeed(seedKey));
  return sorted[Math.floor(random() * sorted.length)] || null;
}

export function normalizePlaceType(raw, setType) {
  if (typeof setType === 'string' && setType.startsWith('horizon-')) return 'studio';
  return NORMALIZED_PLACE_TYPES.get(raw) || raw;
}

export function hasFullAndMediumMembers(set) {
  return !!set?.members?.some((member) => member.shot === 'full')
    && set.members.some((member) => member.shot === 'medium');
}

export function entryStylingMembers(set) {
  const ordered = [...(set?.members || [])].sort((left, right) => left.order - right.order);
  if (ordered.length <= 2) return ordered;

  if (hasFullAndMediumMembers(set)) {
    const full = ordered.find((member) => member.shot === 'full');
    const medium = ordered.find((member) => member.shot === 'medium');
    return ordered.filter((member) => member === full || member === medium);
  }

  for (let left = 0; left < ordered.length - 1; left += 1) {
    const right = ordered.findIndex((member, index) => (
      index > left && member.direction !== ordered[left].direction
    ));
    if (right >= 0) return [ordered[left], ordered[right]];
  }
  return ordered.slice(0, 2);
}

/* 2컷 진입 규칙이 도입되기 전에 mock 메모리에 만들어진 자동 스타일링 세트는
   Vite HMR 뒤에도 3멤버 그대로 남는다. 사용자가 고른 세트/멤버는 건드리지 않고,
   발행 카탈로그와 정확히 일치하는 자동 run만 현재 진입 멤버로 축소한다. */
export function migrateLegacyEntryStylingRuns(blocks) {
  if (!Array.isArray(blocks) || blocks.length === 0) return { blocks, changed: false };
  const next = [];
  let changed = false;

  for (let index = 0; index < blocks.length;) {
    const groupId = blocks[index]?.spaceGroupId;
    if (!groupId) {
      next.push(blocks[index]);
      index += 1;
      continue;
    }
    let end = index + 1;
    while (end < blocks.length && blocks[end]?.spaceGroupId === groupId) end += 1;
    const run = blocks.slice(index, end);
    const set = storyboardSpaceSetById(spaceSetIdFromGroupId(groupId));
    const expected = set?.setType === 'styling' ? entryStylingMembers(set) : null;
    const catalogOrders = new Set((set?.members || []).map((member) => member.order));
    const isUntouchedLegacyRun = expected
      && run.length > expected.length
      && run.every((block) => (
        block.setSelectionOrigin === 'auto'
        && block.exampleSelectionOrigin === 'auto'
        && catalogOrders.has(block.spaceSetMemberOrder)
      ));
    if (isUntouchedLegacyRun) {
      const expectedOrders = new Set(expected.map((member) => member.order));
      next.push(...run.filter((block) => expectedOrders.has(block.spaceSetMemberOrder)));
      changed = true;
    } else {
      next.push(...run);
    }
    index = end;
  }
  return { blocks: changed ? next : blocks, changed };
}

export function pickEntrySets({
  gender,
  clothingType,
  projectId,
  stylingCount,
}) {
  const seedProjectId = projectId || 'default';
  const count = Math.max(0, Number.isFinite(stylingCount) ? Math.floor(stylingCount) : 0);
  const stylingPool = storyboardSpaceSetsFor({ gender, clothingType })
    .filter((set) => set.setType === 'styling');
  const stylingSets = [];
  const selectedIds = new Set();
  const selectedPlaces = new Set();

  for (let index = 0; index < count; index += 1) {
    const candidates = stylingPool.filter((set) => (
      !selectedIds.has(set.id)
      && !selectedPlaces.has(normalizePlaceType(set.placeType, set.setType))
    ));
    const preferred = candidates.filter(hasFullAndMediumMembers);
    const selectionPool = preferred.length ? preferred : candidates;
    const selected = seededPick(selectionPool, `${seedProjectId}:entry:styling:${index}`);
    stylingSets.push(selected);
    if (selected) {
      selectedIds.add(selected.id);
      selectedPlaces.add(normalizePlaceType(selected.placeType, selected.setType));
    }
  }

  // 세트 배치 범위(setApplicableClothingTypes)로 필터한다. 회전 세트의 완성 이미지가
  // 낱장 갤러리에서 다른 의류로 번지는 일 없이, 세트 자체만 전 의류에 배치될 수 있다.
  const horizonPool = storyboardSpaceSetsFor({ gender, clothingType });
  return {
    stylingSets,
    rotationSet: seededPick(
      horizonPool.filter((set) => set.setType === 'horizon-rotation'),
      `${seedProjectId}:entry:rotation`,
    ),
    sequenceSet: seededPick(
      horizonPool.filter((set) => set.setType === 'horizon-sequence'),
      `${seedProjectId}:entry:sequence`,
    ),
  };
}
