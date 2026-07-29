import publicCombinationTable from '../../data/genexamples_public_combinations.json' with { type: 'json' };
import { poseExampleDirectionCompatible } from './storyboardTaxonomy.js';

const PUBLIC_COMBINATIONS = Object.freeze(publicCombinationTable.combinations.map(Object.freeze));
const PUBLIC_KEYS = new Set(PUBLIC_COMBINATIONS.map((combination) => combinationKey(combination)));

function normalizedGender(cutType, gender) {
  return cutType === 'product' ? null : gender;
}

export function combinationKey({ cutType, shot, clothingType, gender }) {
  return [cutType, shot, clothingType, normalizedGender(cutType, gender) ?? 'any'].join(':');
}

export function publicGenerationExampleCombinations() {
  return PUBLIC_COMBINATIONS;
}

export function isGenerationCombinationPublic(condition) {
  return PUBLIC_KEYS.has(combinationKey(condition));
}

export function hasPublicGenerationExamplesForCut({ cutType, clothingType, gender, shots }) {
  return (shots || []).some((shot) => isGenerationCombinationPublic({
    cutType, shot, clothingType, gender,
  }));
}

const compareText = (left, right) => (left < right ? -1 : left > right ? 1 : 0);
const byRankThenId = (left, right) => (
  (Number(left.rank) || 0) - (Number(right.rank) || 0)
  || compareText(String(left.id), String(right.id))
);

function isPublishedAll(example) {
  return Array.isArray(example?.variants) && example.variants.includes('all');
}

function matchesProductAndGender(example, { clothingType, gender }) {
  return Array.isArray(example?.applicableClothingTypes)
    && example.applicableClothingTypes.includes(clothingType)
    && (example.cutType === 'product' ? example.gender == null : example.gender === gender);
}

export function selectGenerationExamples(catalog, {
  cutType, shot, clothingType, gender, spaceGroupId = null, direction = null,
}) {
  if (!isGenerationCombinationPublic({ cutType, shot, clothingType, gender })) return [];
  const matched = (catalog || []).filter((example) => (
    isPublishedAll(example)
    && example?.cutType === cutType
    && example?.shot === shot
    && matchesProductAndGender(example, { clothingType, gender })
    && (!spaceGroupId || (
      example.variants.includes('pose')
      && poseExampleDirectionCompatible(example, { cutType, direction })
    ))
  ));
  const mixAxis = cutType === 'styling' ? 'mood'
    : cutType === 'product' && shot === 'detail' ? 'detailSubject'
      : null;
  if (!mixAxis) return [...matched].sort(byRankThenId).slice(0, 6);

  const buckets = new Map();
  for (const example of matched) {
    const key = String(example[mixAxis] || '');
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(example);
  }
  const orderedBuckets = [...buckets.entries()]
    .sort(([left], [right]) => compareText(left, right))
    .map(([, examples]) => examples.sort(byRankThenId));
  const mixed = [];
  for (let rankIndex = 0; mixed.length < 6; rankIndex += 1) {
    let added = false;
    for (const examples of orderedBuckets) {
      if (examples[rankIndex]) {
        mixed.push(examples[rankIndex]);
        added = true;
        if (mixed.length === 6) break;
      }
    }
    if (!added) break;
  }
  return mixed;
}

function candidatesForBlock(block, catalog, product, gender) {
  return selectGenerationExamples(catalog, {
    cutType: block.cutType,
    shot: block.shot,
    clothingType: product?.clothingType,
    gender,
    spaceGroupId: block.spaceGroupId,
    direction: block.direction,
  });
}

function usageKey(block, product, gender) {
  return combinationKey({
    cutType: block.cutType,
    shot: block.shot,
    clothingType: product?.clothingType,
    gender,
  });
}

export function assignGenerationExamples(blocks, { catalog, product, gender, onlyBlockIds = null }) {
  if (!Array.isArray(blocks)) return { blocks, changed: false, assignedIds: [], protectedIds: [], missingIds: [] };
  const only = onlyBlockIds == null ? null : new Set(onlyBlockIds);
  const usage = new Map();

  for (const block of blocks) {
    if (block?.source !== 'ai' || block.exampleSelectionOrigin !== 'auto' || !block.exampleId) continue;
    const pool = candidatesForBlock(block, catalog, product, gender).slice(0, 3);
    const slot = pool.findIndex((example) => example.id === block.exampleId);
    if (slot < 0) continue;
    const key = usageKey(block, product, gender);
    if (!usage.has(key)) usage.set(key, [0, 0, 0]);
    usage.get(key)[slot] += 1;
  }

  let changed = false;
  const assignedIds = [];
  const protectedIds = [];
  const missingIds = [];
  const next = blocks.map((block) => {
    if (!block || block.source !== 'ai') return block;
    if (only && !only.has(block.id)) return block;
    if (block.exampleId) {
      if (block.exampleSelectionOrigin === 'auto' || block.exampleSelectionOrigin === 'user') return block;
      changed = true;
      protectedIds.push(block.id);
      return { ...block, exampleSelectionOrigin: 'user' };
    }
    if (block.exampleSelectionOrigin != null) return block;

    const pool = candidatesForBlock(block, catalog, product, gender).slice(0, 3);
    if (!pool.length) {
      missingIds.push(block.id);
      return block;
    }
    const key = usageKey(block, product, gender);
    if (!usage.has(key)) usage.set(key, [0, 0, 0]);
    const counts = usage.get(key);
    let slot = 0;
    for (let index = 1; index < pool.length; index += 1) {
      if (counts[index] < counts[slot]) slot = index;
    }
    counts[slot] += 1;
    const example = pool[slot];
    changed = true;
    assignedIds.push(block.id);
    return {
      ...block,
      exampleId: example.id,
      exampleSelectionOrigin: 'auto',
      refScope: block.spaceGroupId ? 'pose' : 'all',
      baseThumb: block.baseThumb ?? block.thumb ?? null,
      thumb: example.thumb,
    };
  });
  return { blocks: changed ? next : blocks, changed, assignedIds, protectedIds, missingIds };
}

export function storedExampleConditionStatus(example, { cutType, clothingType, gender }) {
  if (!example) return 'unknown';
  if (!isPublishedAll(example)) return 'unknown';
  if (example.cutType !== cutType) return 'changed';
  return matchesProductAndGender(example, { clothingType, gender }) ? 'valid' : 'changed';
}

export function directionBadgeLabel(direction) {
  return { front: '정면', side: '사이드', back: '뒷면' }[direction] || '방향 없음';
}

export function exampleSelectionFingerprintFields(block) {
  const automatic = block?.exampleSelectionOrigin === 'auto';
  return {
    exampleId: automatic ? null : (block?.exampleId ?? null),
    exampleSelectionOrigin: block?.exampleId && !automatic ? 'user' : null,
    refScope: automatic ? null : (block?.refScope ?? null),
  };
}

export function shouldMarkStoryboardDirty({ autoAssignment = false } = {}) {
  return !autoAssignment;
}
