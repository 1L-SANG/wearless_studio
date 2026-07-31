import spaceSetRelease from '../data/storyboardSpaceSets.json' with { type: 'json' };

const ALL_CLOTHING_TYPES = Object.freeze(['top', 'bottom', 'outer', 'dress']);
const SET_TYPES = new Set(['styling', 'horizon-rotation', 'horizon-sequence']);
const SPACE_VARIATIONS = new Set(['subtle', 'fixed']);
const PLATE_POLICIES = new Set(['required', 'not-required']);
const SHOTS = new Set(['full', 'medium']);
const DIRECTIONS = new Set(['front', 'side', 'back']);
const RELEASE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/;
const EXAMPLE_ID = /^ss_[A-Za-z0-9_-]{1,197}$/;
const GROUP_PREFIX = 'ssg1__';
const GROUP_INSTANCE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/;
const SET_SCOPE_BY_GENDER = Object.freeze({
  women: Object.freeze(['top', 'bottom', 'outer', 'dress']),
  men: Object.freeze(['top', 'bottom', 'outer']),
});

const text = (value, fallback = '') => (
  typeof value === 'string' && value.trim() ? value.trim() : fallback
);

const validReleaseId = (value) => (
  typeof value === 'string'
  && RELEASE_ID.test(value)
  && !value.includes('__')
);

function normalizedMember(member, index, setType) {
  if (!member || typeof member !== 'object') return null;
  const exampleId = text(member.exampleId);
  const allUrl = text(member.allUrl);
  const thumb = text(member.thumbUrl);
  const expectedCutType = setType === 'styling' ? 'styling' : 'horizon';
  if (
    !EXAMPLE_ID.test(exampleId)
    || exampleId.includes('__')
    || member.order !== index + 1
    || member.cutType !== expectedCutType
    || !SHOTS.has(member.shot)
    || !DIRECTIONS.has(member.direction)
    || !allUrl
    || !thumb
  ) return null;
  return Object.freeze({
    exampleId,
    order: member.order,
    cutType: member.cutType,
    shot: member.shot,
    direction: member.direction,
    thumb,
    thumbUrl: thumb,
    allUrl,
  });
}

function normalizedSet(set, index) {
  if (!set || typeof set !== 'object') return null;
  const id = text(set.setId);
  const setType = set.setType;
  const clothingTypes = set.applicableClothingTypes;
  const setClothingTypes = set.setApplicableClothingTypes ?? clothingTypes;
  if (
    !validReleaseId(id)
    || !SET_TYPES.has(setType)
    || !['women', 'men'].includes(set.gender)
    || !Array.isArray(clothingTypes)
    || clothingTypes.length === 0
    || new Set(clothingTypes).size !== clothingTypes.length
    || clothingTypes.some((value) => !ALL_CLOTHING_TYPES.includes(value))
    || (set.gender === 'men' && clothingTypes.includes('dress'))
    || !Array.isArray(setClothingTypes)
    || setClothingTypes.length === 0
    || new Set(setClothingTypes).size !== setClothingTypes.length
    || setClothingTypes.some((value) => !ALL_CLOTHING_TYPES.includes(value))
    || (set.gender === 'men' && setClothingTypes.includes('dress'))
    || !SPACE_VARIATIONS.has(set.spaceVariation)
    || !PLATE_POLICIES.has(set.platePolicy)
    || !text(set.name)
    || !text(set.placeType)
    || !text(set.tone)
    || !text(set.compositionLabel)
    || !Array.isArray(set.members)
    || set.members.length < 2
    || set.members.length > 5
  ) return null;
  if (
    set.setApplicableClothingTypes != null
    && setClothingTypes.join(',') !== clothingTypes.join(',')
    && (
      setType !== 'horizon-rotation'
      || setClothingTypes.join(',') !== SET_SCOPE_BY_GENDER[set.gender].join(',')
    )
  ) return null;
  if (clothingTypes.length > 1) {
    const sharedTopOuter = (
      clothingTypes.length === 2
      && clothingTypes.includes('top')
      && clothingTypes.includes('outer')
    );
    const universalRotation = (
      setType === 'horizon-rotation'
      && clothingTypes.join(',') === SET_SCOPE_BY_GENDER[set.gender].join(',')
    );
    if (
      (!sharedTopOuter && !universalRotation)
      || set.members.some((member) => member?.shot !== 'full')
    ) return null;
  }
  if (
    (setType === 'horizon-sequence' && set.platePolicy !== 'not-required')
    || (setType !== 'horizon-sequence' && set.platePolicy !== 'required')
  ) return null;
  if (
    setType === 'horizon-rotation'
    && (
      set.members.length !== 3
      || set.members.some((member) => member?.shot !== 'full')
      || set.members.map((member) => member?.direction).join(',') !== 'front,side,back'
    )
  ) return null;
  const plate = set.representativePlate;
  const representativePlate = set.platePolicy === 'required'
    && plate
    && typeof plate === 'object'
    && text(plate.url)
    ? Object.freeze({ url: text(plate.url), thumbUrl: text(plate.url) })
    : null;
  if (
    (set.platePolicy === 'required' && !representativePlate)
    || (set.platePolicy === 'not-required' && plate !== null)
  ) return null;
  const members = set.members.map((member, memberIndex) => (
    normalizedMember(member, memberIndex, setType)
  ));
  if (
    members.some((member) => !member)
    || new Set(members.map((member) => member.exampleId)).size !== members.length
  ) return null;
  return Object.freeze({
    id,
    setId: id,
    name: text(set.name),
    setType,
    gender: set.gender,
    applicableClothingTypes: Object.freeze([...clothingTypes]),
    setApplicableClothingTypes: Object.freeze([...setClothingTypes]),
    place: text(set.placeType),
    placeType: text(set.placeType),
    tone: text(set.tone),
    compositionLabel: text(set.compositionLabel),
    spaceVariation: set.spaceVariation,
    platePolicy: set.platePolicy,
    representativePlate,
    members: Object.freeze(members),
  });
}

export function normalizeStoryboardSpaceSetRelease(release) {
  const schemaVersion = release?._meta?.schemaVersion ?? release?.schemaVersion;
  const source = Array.isArray(release) ? release : release?.sets;
  const releaseId = release?._meta?.releaseId ?? release?.releaseId;
  if (schemaVersion !== 1 || !Array.isArray(source)) return [];
  if (source.length && !validReleaseId(releaseId)) return [];
  const seen = new Set();
  const exampleIds = new Set();
  return source.map(normalizedSet).filter((set) => {
    if (
      !set
      || seen.has(set.id)
      || set.members.some((member) => exampleIds.has(member.exampleId))
    ) return false;
    seen.add(set.id);
    set.members.forEach((member) => exampleIds.add(member.exampleId));
    return true;
  });
}

const RELEASE_SPACE_SETS = normalizeStoryboardSpaceSetRelease(spaceSetRelease);

export const STORYBOARD_SPACE_SETS = Object.freeze(RELEASE_SPACE_SETS);

export const STORYBOARD_SPACE_SET_EXAMPLES = Object.freeze(
  RELEASE_SPACE_SETS.flatMap((set) => set.members
    .filter((member) => member.exampleId && member.thumb)
    .map((member) => Object.freeze({
      id: member.exampleId,
      cutType: member.cutType,
      shot: member.shot,
      direction: member.direction,
      gender: set.gender,
      applicableClothingTypes: set.applicableClothingTypes,
      thumb: member.thumb,
      assetUrl: member.allUrl,
      variants: Object.freeze(['all', 'pose']),
      rank: member.order,
      mood: set.tone,
      setOnly: true,
      spaceSetId: set.id,
    }))),
);

const SET_BY_ID = new Map(STORYBOARD_SPACE_SETS.map((set) => [set.id, set]));

export function storyboardSpaceSetById(id) {
  return SET_BY_ID.get(id) || null;
}

export function isStoryboardSpaceSetEligible(set, { gender = null, clothingType = null } = {}) {
  return !!set
    && !!gender
    && set.gender === gender
    && (!clothingType || (
      set.setApplicableClothingTypes || set.applicableClothingTypes
    ).includes(clothingType));
}

export function storyboardSpaceSetsFor({ gender = null, clothingType = null } = {}) {
  return STORYBOARD_SPACE_SETS.filter((set) => isStoryboardSpaceSetEligible(set, {
    gender, clothingType,
  }));
}

export function withStoryboardSpaceSetExamples(catalogs) {
  if (!catalogs || !STORYBOARD_SPACE_SET_EXAMPLES.length) return catalogs;
  const current = Array.isArray(catalogs.genExamples) ? catalogs.genExamples : [];
  const currentIds = new Set(current.map((example) => example.id));
  const additions = STORYBOARD_SPACE_SET_EXAMPLES.filter((example) => !currentIds.has(example.id));
  return additions.length ? { ...catalogs, genExamples: [...current, ...additions] } : catalogs;
}

export function spaceSetGroupId(setId, uniqueId) {
  if (!validReleaseId(setId) || !GROUP_INSTANCE.test(uniqueId) || uniqueId.includes('__')) {
    throw new Error('invalid production space-set group id');
  }
  return `${GROUP_PREFIX}${setId}__${uniqueId}`;
}

export function spaceSetIdFromGroupId(groupId) {
  if (typeof groupId !== 'string' || !groupId.startsWith(GROUP_PREFIX)) return null;
  const parts = groupId.slice(GROUP_PREFIX.length).split('__');
  if (parts.length !== 2 || !validReleaseId(parts[0]) || !GROUP_INSTANCE.test(parts[1])) return null;
  const [id] = parts;
  return SET_BY_ID.has(id) ? id : null;
}

export function inferStoryboardSpaceSet(groupId) {
  const storedId = spaceSetIdFromGroupId(groupId);
  return storedId ? storyboardSpaceSetById(storedId) : null;
}
