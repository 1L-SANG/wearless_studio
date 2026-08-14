import publicCombinationTable from '../../data/genexamples_public_combinations.json' with { type: 'json' };
import { poseExampleDirectionCompatible } from './storyboardTaxonomy.js';
import { detailDirectionFromExample } from './storyboardExampleSelection.js';

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

/** 릴리스 계약의 thumb/all 경로 규칙으로 보드 카드용 1x/2x 소스를 만든다. */
export function generationExampleImageSources(example) {
  const thumb = typeof example?.thumb === 'string' ? example.thumb : '';
  const releasedAll = thumb.replace(
    /\/thumb\/([^/?]+)\.webp(?=([?#]|$))/,
    '/all/$1.png',
  );
  const full = typeof example?.assetUrl === 'string' && example.assetUrl
    ? example.assetUrl
    : (releasedAll !== thumb ? releasedAll : '');
  return {
    src: thumb || full,
    // 보드 카드는 작은 슬롯이므로 원본 PNG를 2x 후보로 내려받지 않는다.
    // 전용 2x WebP가 생기기 전까지 릴리스 썸네일 하나만 사용한다.
    srcSet: undefined,
    prewarm: thumb || full,
  };
}

export function paginateGenerationGalleryItems(items, pageSize = 6) {
  const source = Array.isArray(items) ? items : [];
  const size = Math.max(1, Number(pageSize) || 6);
  const pages = [];
  for (let index = 0; index < source.length; index += size) {
    pages.push(source.slice(index, index + size));
  }
  return pages.length ? pages : [[]];
}

function isPublishedAll(example) {
  return Array.isArray(example?.variants) && example.variants.includes('all');
}

function matchesProductAndGender(example, { clothingType, gender }) {
  return Array.isArray(example?.applicableClothingTypes)
    && example.applicableClothingTypes.includes(clothingType)
    && (example.cutType === 'product' ? example.gender == null : example.gender === gender);
}

function orderGenerationExamples(matched, { cutType, shot, limit = 6 }) {
  const maxItems = Math.min(matched.length, limit);
  const mixAxis = cutType === 'styling' ? 'mood'
    : cutType === 'product' && shot === 'detail' ? 'detailSubject'
      : null;
  if (!mixAxis) return [...matched].sort(byRankThenId).slice(0, maxItems);

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
  for (let rankIndex = 0; mixed.length < maxItems; rankIndex += 1) {
    let added = false;
    for (const examples of orderedBuckets) {
      if (examples[rankIndex]) {
        mixed.push(examples[rankIndex]);
        added = true;
        if (mixed.length === maxItems) break;
      }
    }
    if (!added) break;
  }
  return mixed;
}

export function selectGenerationExamples(catalog, {
  cutType, shot, clothingType, gender, spaceGroupId = null, direction = null,
  includeSetOnly = false, appendSetOnly = false,
}) {
  const flatCombinationPublished = isGenerationCombinationPublic({
    cutType, shot, clothingType, gender,
  });
  if (!flatCombinationPublished && !includeSetOnly && !appendSetOnly) return [];
  const matched = (catalog || []).filter((example) => (
    (!example?.setOnly || includeSetOnly || appendSetOnly)
    && (example?.setOnly || flatCombinationPublished)
    &&
    isPublishedAll(example)
    && example?.cutType === cutType
    && example?.shot === shot
    && matchesProductAndGender(example, { clothingType, gender })
    && (!spaceGroupId || (
      example.variants.includes('pose')
      && poseExampleDirectionCompatible(example, { cutType, direction })
    ))
  ));
  if (appendSetOnly) {
    const ordinary = matched.filter((example) => !example.setOnly);
    const setMembers = matched.filter((example) => example.setOnly);
    return [
      ...orderGenerationExamples(ordinary, { cutType, shot }),
      ...orderGenerationExamples(setMembers, { cutType, shot, limit: setMembers.length }),
    ];
  }
  return orderGenerationExamples(matched, { cutType, shot });
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

/* 서버(detail_page_job._example_repeat_indexes)와 같은 화면 전용 파생값. 저장하지 않는다.
   반복 규칙(2026-08-14 오너 확정):
   - 포즈 변주는 **같은 생성예시를 다른 색상으로 반복**할 때만 적용한다(컬러웨이 반복).
   - 같은 예시·같은 색상 반복(컷 복제)은 변주 없이 1장만 생성해 복제 위치에 그대로 복사
     — 생성 회피는 서버가 하고, 화면은 배지를 붙이지 않는 것으로 같은 규칙을 표현한다. */
export function repeatedAllExampleVariationIds(blocks, catalog = []) {
  if (!Array.isArray(blocks)) return new Set();
  const examplesById = new Map((catalog || []).map((example) => [example.id, example]));
  const firstColorByKey = new Map();
  const variationIds = new Set();

  for (const block of blocks) {
    const example = examplesById.get(block?.exampleId);
    if (
      block?.source !== 'ai'
      || !block.id
      || !example
      || !['styling', 'horizon', 'mirror'].includes(block.cutType)
      || block.spaceGroupId
      || (block.refScope || 'all') !== 'all'
      || (block.pose || 'auto') !== 'auto'
      || !poseExampleDirectionCompatible(example, {
        cutType: block.cutType,
        direction: block.direction,
      })
    ) continue;

    const section = block.sectionId || `role:${block.sectionRole || 'unknown'}`;
    const key = `${section}\u0000${block.exampleId}`;
    const color = block.colorId ?? null;
    if (!firstColorByKey.has(key)) {
      firstColorByKey.set(key, color);
      continue;
    }
    if (firstColorByKey.get(key) !== color) variationIds.add(block.id);
  }

  return variationIds;
}

// avoidByBlockId: { [blockId]: exampleId } — 예시 셔플이 "직전과 같은 예시"를 피하도록
// 블록별 회피 대상을 넘긴다. 후보가 그것뿐이면 회피를 포기하고 그대로 쓴다(빈 배정 방지).
export function assignGenerationExamples(blocks, {
  catalog, product, gender, onlyBlockIds = null, avoidByBlockId = null,
}) {
  if (!Array.isArray(blocks)) return { blocks, changed: false, assignedIds: [], protectedIds: [], missingIds: [] };
  const only = onlyBlockIds == null ? null : new Set(onlyBlockIds);
  const usage = new Map();
  const colorwayTemplates = new Map();

  // 추가 색상 페어는 색상마다 새 예시를 고르지 않는다. 풀샷끼리, 미디움샷끼리
  // 하나의 촬영 예시를 공유하고 실제 생성만 각 colorId의 상품 사진으로 수행한다.
  for (const block of blocks) {
    if (block?.source !== 'ai' || !block.colorwayGroupId || !block.exampleId) continue;
    const pool = candidatesForBlock(block, catalog, product, gender).slice(0, 3);
    const selected = pool.find((example) => example.id === block.exampleId);
    if (!selected) continue;
    const key = usageKey(block, product, gender);
    if (!colorwayTemplates.has(key) || block.exampleSelectionOrigin === 'user') {
      colorwayTemplates.set(key, selected);
    }
  }

  const countedColorwayTemplates = new Set();
  for (const block of blocks) {
    if (block?.source !== 'ai' || block.exampleSelectionOrigin !== 'auto' || !block.exampleId) continue;
    const pool = candidatesForBlock(block, catalog, product, gender).slice(0, 3);
    const slot = pool.findIndex((example) => example.id === block.exampleId);
    if (slot < 0) continue;
    const key = usageKey(block, product, gender);
    if (block.colorwayGroupId) {
      const template = colorwayTemplates.get(key);
      if (!template || template.id !== block.exampleId || countedColorwayTemplates.has(key)) continue;
      countedColorwayTemplates.add(key);
    }
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
    if (block.exampleChoice === 'manual') return block;
    const colorwayKey = block.colorwayGroupId ? usageKey(block, product, gender) : null;
    const colorwayTemplate = colorwayKey ? colorwayTemplates.get(colorwayKey) : null;
    if (block.exampleId) {
      if (
        colorwayTemplate
        && block.exampleSelectionOrigin === 'auto'
        && block.exampleId !== colorwayTemplate.id
      ) {
        changed = true;
        assignedIds.push(block.id);
        return {
          ...block,
          exampleId: colorwayTemplate.id,
          refScope: 'all',
          baseThumb: block.baseThumb ?? block.thumb ?? null,
          thumb: colorwayTemplate.thumb,
        };
      }
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
    let example = colorwayTemplate;
    if (!example) {
      const avoidId = avoidByBlockId ? avoidByBlockId[block.id] : null;
      const eligible = [];
      for (let index = 0; index < pool.length; index += 1) {
        if (!avoidId || pool[index].id !== avoidId) eligible.push(index);
      }
      const candidates = eligible.length ? eligible : pool.map((_item, index) => index);
      let slot = candidates[0];
      for (const index of candidates) {
        if (counts[index] < counts[slot]) slot = index;
      }
      example = pool[slot];
      counts[slot] += 1;
      if (colorwayKey) colorwayTemplates.set(colorwayKey, example);
    }
    changed = true;
    assignedIds.push(block.id);
    return {
      ...block,
      exampleId: example.id,
      exampleSelectionOrigin: 'auto',
      refScope: block.spaceGroupId ? 'pose' : 'all',
      baseThumb: block.baseThumb ?? block.thumb ?? null,
      thumb: example.thumb,
      // 디테일 컷 방향은 예시 라벨이 내부 결정(미기재=front) — 자동 배정도 동일 규칙
      ...(block.cutType === 'product' && block.shot === 'detail'
        ? { direction: detailDirectionFromExample(example) } : {}),
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
