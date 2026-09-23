import publicCombinationTable from '../../data/genexamples_public_combinations.json' with { type: 'json' };
import { filterExamplesForModel } from './identityScope.js';
import { exampleDirectionFamilyMatches, poseExampleDirectionCompatible } from './storyboardTaxonomy.js';
import { detailDirectionFromExample } from './storyboardExampleSelection.js';
import {
  exampleMoodBucket,
  EXAMPLE_MOOD_BUCKETS,
  orderExamplesByMood,
} from './exampleMoodOrder.js';

const PUBLIC_COMBINATIONS = Object.freeze(publicCombinationTable.combinations.map(Object.freeze));
const PUBLIC_KEYS = new Set(PUBLIC_COMBINATIONS.map((combination) => combinationKey(combination)));

function normalizedGender(cutType, gender) {
  return cutType === 'product' ? null : gender;
}

export function combinationKey({ cutType, shot, clothingType, gender }) {
  return [cutType, shot, clothingType, normalizedGender(cutType, gender) ?? 'any'].join(':');
}

export function isGenerationCombinationPublic(condition) {
  return PUBLIC_KEYS.has(combinationKey(condition));
}

const compareText = (left, right) => (left < right ? -1 : left > right ? 1 : 0);
const MOOD_BUCKET_ORDER = new Map(EXAMPLE_MOOD_BUCKETS.map((bucket, index) => [bucket.id, index]));
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

function matchesSharedEligibility(example, { clothingType, gender, allowSetOnly = false }) {
  return (!example?.setOnly || allowSetOnly)
    && isPublishedAll(example)
    && matchesProductAndGender(example, { clothingType, gender });
}

function orderGenerationExamples(matched, {
  cutType, shot, limit = 6, groupByMood = ['styling', 'horizon'].includes(cutType),
  mixAxis = cutType === 'product' && shot === 'detail' ? 'detailSubject' : null,
}) {
  const maxItems = Math.min(matched.length, limit);
  if (!mixAxis) {
    const ordered = groupByMood ? orderExamplesByMood(matched) : [...matched].sort(byRankThenId);
    return ordered.slice(0, maxItems);
  }

  const buckets = new Map();
  for (const example of matched) {
    const key = mixAxis === 'moodBucket'
      ? exampleMoodBucket(example).id
      : String(example[mixAxis] || '');
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(example);
  }
  const orderedBuckets = [...buckets.entries()]
    .sort(([left], [right]) => mixAxis === 'moodBucket'
      ? MOOD_BUCKET_ORDER.get(left) - MOOD_BUCKET_ORDER.get(right)
      : compareText(left, right))
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

function flatCombinationIsPublished({ cutType, shot, clothingType, gender }) {
  return isGenerationCombinationPublic({ cutType, shot, clothingType, gender });
}

function matchesMainSelection(example, options, flatCombinationPublished) {
  const {
    cutType, shot, clothingType, gender, spaceGroupId, direction, sideStyle = null,
    includeSetOnly, appendSetOnly,
  } = options;
  return matchesSharedEligibility(example, {
    clothingType, gender, allowSetOnly: includeSetOnly || appendSetOnly,
  })
    && (example?.setOnly || flatCombinationPublished)
    && example?.cutType === cutType
    && example?.shot === shot
    && (!spaceGroupId || (
      example.variants.includes('pose')
      && poseExampleDirectionCompatible(example, { cutType, direction, sideStyle })
    ));
}

function matchesMirrorSelection(example, { clothingType, gender }) {
  return matchesSharedEligibility(example, { clothingType, gender })
    && example?.cutType === 'mirror'
    && isGenerationCombinationPublic({
      cutType: 'mirror', shot: example.shot, clothingType, gender,
    });
}

export function hasSelectableGenerationExamples(catalog, rawOptions) {
  const options = {
    spaceGroupId: null,
    direction: null,
    includeSetOnly: false,
    appendSetOnly: false,
    appendMirror: false,
    ...rawOptions,
  };
  const flatCombinationPublished = flatCombinationIsPublished(options);
  if (!flatCombinationPublished && !options.includeSetOnly && !options.appendSetOnly) return false;
  const source = Array.isArray(catalog) ? catalog : [];
  if (source.some((example) => matchesMainSelection(example, options, flatCombinationPublished))) {
    return true;
  }
  return options.appendSetOnly && options.appendMirror && options.cutType === 'styling'
    && source.some((example) => matchesMirrorSelection(example, options));
}

/* 갤러리·자동배정은 **카드와 같은 방향 가족**(정면·사선·옆모습·뒷면)을 **앞에** 둔다.
   숨기지는 않는다 — 셀러는 분위기 예시에서 아무거나 고를 수 있어야 한다(2026-09-22 오너).
   2026-09-22 운영 실측: 사선 카드에 정면 예시가 붙어 갤러리가 여섯 방향을 한 통에 섞어 보였다.
   같은 방향이 먼저 오면 자동배정(앞에서 셋을 뽑는다)도 자연히 같은 방향을 고른다. */
function partitionByDirectionFamily(list, { cutType, direction, sideStyle }) {
  if (!direction || !['styling', 'horizon'].includes(cutType)) return { same: list, rest: [] };
  const same = [];
  const rest = [];
  for (const example of list) {
    (exampleDirectionFamilyMatches(example, { direction, sideStyle }) ? same : rest).push(example);
  }
  return { same, rest };
}

/* 스튜디오(호리존)는 방향마다 **한 가지 촬영**만 보이면 된다 — 같은 방향에 낱개 예시가 있으면
   같은 그림이 두 장 뜨던 공간 묶음 멤버를 갤러리에서 뺀다(2026-09-22 오너: "똑같은 사진 2개").
   멤버는 공간 묶음을 고를 때 그대로 쓰이고, 낱개가 없는 방향에서는 여전히 갤러리를 채운다.
   스냅(스타일링)은 장소가 다 다른 게 값이라 그대로 둔다. */
function directionFamilyKey(example) {
  const direction = example?.direction || null;
  if (direction !== 'side') return direction;
  return example?.sideStyle === 'threeQuarter' ? 'side:threeQuarter' : 'side:profile';
}

function dedupeHorizonSetMembers(setMembers, ordinary, cutType) {
  if (cutType !== 'horizon') return setMembers;
  const covered = new Set(ordinary.map(directionFamilyKey).filter(Boolean));
  if (!covered.size) return setMembers;
  return setMembers.filter((example) => !covered.has(directionFamilyKey(example)));
}

export function selectGenerationExamples(catalog, rawOptions) {
  const options = {
    spaceGroupId: null,
    direction: null,
    includeSetOnly: false,
    appendSetOnly: false,
    appendMirror: false,
    mixMoodBuckets: false,
    ...rawOptions,
  };
  const {
    cutType, shot, clothingType, gender, includeSetOnly, appendSetOnly,
    appendMirror, mixMoodBuckets,
  } = options;
  const flatCombinationPublished = flatCombinationIsPublished(options);
  if (!flatCombinationPublished && !includeSetOnly && !appendSetOnly) return [];
  const source = Array.isArray(catalog) ? catalog : [];
  const matched = source.filter((example) => (
    matchesMainSelection(example, options, flatCombinationPublished)
  ));
  // 같은 방향 → 나머지 순서로 **그룹마다** 정렬·제한한다. 한 번에 정렬하면 6장 제한이 같은
  // 방향을 잘라낼 수 있고, 정렬 뒤에 나누면 무드 순서가 그룹 안에서 깨진다.
  const grouped = (list, opts) => {
    const { same, rest } = partitionByDirectionFamily(list, options);
    return [...orderGenerationExamples(same, opts), ...orderGenerationExamples(rest, opts)];
  };
  if (appendSetOnly) {
    const ordinary = matched.filter((example) => !example.setOnly);
    const setMembers = dedupeHorizonSetMembers(
      matched.filter((example) => example.setOnly), ordinary, cutType,
    );
    const mirrorExamples = appendMirror && cutType === 'styling'
      ? source.filter((example) => (
        matchesMirrorSelection(example, options)
        // 거울 예시는 소수이므로 현재 full/medium 탭과 무관하게 모두 마지막에 둔다.
      )).sort(byRankThenId)
      : [];
    return [
      ...grouped(ordinary, { cutType, shot }),
      ...grouped(setMembers, { cutType, shot, limit: setMembers.length, groupByMood: false }),
      ...mirrorExamples,
    ];
  }
  return grouped(matched, {
    cutType, shot, mixAxis: mixMoodBuckets ? 'moodBucket' : undefined,
  });
}

/** 방향 칩을 바꿨을 때 **자동배정** 예시가 새 방향과 안 맞으면 같은 방향 예시로 갈아 끼운다.
    돌려주는 건 블록 패치(없으면 null). 셀러가 직접 고른(origin=user) 예시와 장소세트 멤버는
    건드리지 않는다 — 그건 "변경됨" 표시가 맞는 자리다. assignGenerationExamples 가 교체 때
    쓰는 필드(exampleId·refScope·thumb·baseThumb)를 그대로 쓴다. */
export function repickExampleForDirection(block, catalog, {
  clothingType, gender, identityKind = null, direction, sideStyle = null,
}) {
  if (!block || block.source !== 'ai' || block.spaceGroupId) return null;
  if (block.exampleSelectionOrigin !== 'auto' || !block.exampleId) return null;
  const source = Array.isArray(catalog) ? catalog : [];
  const current = source.find((example) => example.id === block.exampleId);
  if (current && exampleDirectionFamilyMatches(current, { direction, sideStyle })) return null;
  const pool = filterExamplesForModel(selectGenerationExamples(source, {
    cutType: block.cutType, shot: block.shot, clothingType, gender,
    direction, sideStyle, mixMoodBuckets: block.cutType === 'styling',
  }), identityKind, { ...block, direction, sideStyle });
  const next = pool.find((example) => exampleDirectionFamilyMatches(example, { direction, sideStyle }));
  if (!next || next.id === block.exampleId) return null;
  return {
    exampleId: next.id,
    refScope: 'all',
    baseThumb: block.baseThumb ?? block.thumb ?? null,
    thumb: next.thumb,
  };
}

/** 이 컷을 '다시 뽑을' 수 있는가 — 후보가 2개 이상이어야 다른 예시로 바뀔 수 있다.
    후보가 0·1개면 셔플을 눌러도 영영 아무 일도 안 난다. 그때는 버튼을 아예 안 보여
    주는 게 정직하다(2026-08-17 검증: 확장형 추가색상 컷이 늘 무반응이었다). */
export function canRerollGenerationExample(block, { catalog, product, gender }) {
  if (!block?.exampleId) return false;
  return candidatesForBlock(block, catalog || [], product, gender).length > 1;
}

function candidatesForBlock(block, catalog, product, gender, identityKind = null) {
  // 이 모델로 만들 수 없는 예시는 후보에서 아예 뺀다(판정은 서버 규칙 표 — lib/identityScope.js).
  return filterExamplesForModel(selectGenerationExamples(catalog, {
    cutType: block.cutType,
    shot: block.shot,
    clothingType: product?.clothingType,
    gender,
    spaceGroupId: block.spaceGroupId,
    direction: block.direction,
    sideStyle: block.sideStyle ?? null,
    mixMoodBuckets: block.cutType === 'styling',
  }), identityKind, block);
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
        sideStyle: block.sideStyle ?? null,
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
  catalog, product, gender, onlyBlockIds = null, avoidByBlockId = null, identityKind = null,
}) {
  if (!Array.isArray(blocks)) return { blocks, changed: false, assignedIds: [], protectedIds: [], missingIds: [] };
  const only = onlyBlockIds == null ? null : new Set(onlyBlockIds);
  const usage = new Map();
  const colorwayTemplates = new Map();

  // 추가 색상 페어는 색상마다 새 예시를 고르지 않는다. 풀샷끼리, 미디움샷끼리
  // 하나의 촬영 예시를 공유하고 실제 생성만 각 colorId의 상품 사진으로 수행한다.
  for (const block of blocks) {
    if (block?.source !== 'ai' || !block.colorwayGroupId || !block.exampleId) continue;
    const pool = candidatesForBlock(block, catalog, product, gender, identityKind).slice(0, 3);
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
    const pool = candidatesForBlock(block, catalog, product, gender, identityKind).slice(0, 3);
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

    const pool = candidatesForBlock(block, catalog, product, gender, identityKind).slice(0, 3);
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

export function storedExampleConditionStatus(example, {
  cutType, blockCutType = cutType, clothingType, gender, includeMirror = false,
}) {
  if (!example) return 'unknown';
  if (!isPublishedAll(example)) return 'unknown';
  if (example.cutType !== cutType
    && !(cutType === 'styling' && example.cutType === 'mirror'
      && (blockCutType === 'mirror' || includeMirror))) return 'changed';
  return matchesProductAndGender(example, { clothingType, gender }) ? 'valid' : 'changed';
}

export function directionBadgeLabel(direction) {
  return { front: '정면', side: '사이드', back: '뒷면' }[direction] || '방향 없음';
}

/** 예시 카드 배지 — 방향 가족 4개(정면·사선·옆모습·뒷면). 미기재 side 는 옆모습이다. */
export function exampleDirectionFamilyLabel(example) {
  const direction = example?.direction;
  if (direction === 'front') return '정면';
  if (direction === 'back') return '뒷면';
  if (direction === 'side') return example?.sideStyle === 'threeQuarter' ? '사선' : '옆모습';
  return '방향 없음';
}

/** 갤러리 섹션 정의 — 셀러가 보는 방향 4가지. 순서는 정면 → 사선 → 옆모습 → 뒷면. */
export const GENERATION_DIRECTION_SECTIONS = Object.freeze([
  Object.freeze({ key: 'front', label: '정면', direction: 'front', sideStyle: null }),
  Object.freeze({ key: 'threeQuarter', label: '사선', direction: 'side', sideStyle: 'threeQuarter' }),
  Object.freeze({ key: 'profile', label: '옆모습', direction: 'side', sideStyle: 'profile' }),
  Object.freeze({ key: 'back', label: '뒷면', direction: 'back', sideStyle: null }),
]);

const OTHER_SECTION_KEY = 'other';

/** 갤러리를 **방향 가족별 묶음**으로 자른다 — 정면 사진은 정면 칸, 사선은 사선 칸에 모인다.
    카드가 보고 있는 방향의 묶음이 맨 앞이라 갤러리를 열면 그 방향 사진부터 나온다.
    방향이 없는 예시(아직 라벨이 안 붙은 스냅)는 마지막 '기타' 묶음에 남는다 — 숨기지 않는다.
    어떤 묶음도 잠그지 않는다: 셀러는 어느 방향 사진이든 골라 쓸 수 있다(2026-09-22 오너). */
export function groupGenerationExamplesByDirection(list, { direction = null, sideStyle = null } = {}) {
  const source = Array.isArray(list) ? list : [];
  const buckets = new Map(GENERATION_DIRECTION_SECTIONS.map((section) => [section.key, []]));
  const other = [];
  for (const example of source) {
    const section = GENERATION_DIRECTION_SECTIONS.find((item) => (
      exampleDirectionFamilyMatches(example, { direction: item.direction, sideStyle: item.sideStyle })
    ));
    (section ? buckets.get(section.key) : other).push(example);
  }
  const current = direction
    ? GENERATION_DIRECTION_SECTIONS.find((item) => (
      item.direction === direction
      && (direction !== 'side' || item.sideStyle === (sideStyle === 'threeQuarter' ? 'threeQuarter' : 'profile'))
    ))
    : null;
  const ordered = current
    ? [current, ...GENERATION_DIRECTION_SECTIONS.filter((item) => item.key !== current.key)]
    : [...GENERATION_DIRECTION_SECTIONS];
  const sections = ordered
    .map((section) => ({ key: section.key, label: section.label, examples: buckets.get(section.key) }))
    .filter((section) => section.examples.length);
  if (other.length) sections.push({ key: OTHER_SECTION_KEY, label: '기타', examples: other });
  return sections;
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
