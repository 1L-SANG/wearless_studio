export const EXAMPLE_MOOD_BUCKETS = Object.freeze([
  Object.freeze({
    id: 'cafe',
    label: '카페',
    keywords: Object.freeze(['cafe', 'café', 'coffee', 'bakery']),
  }),
  Object.freeze({
    id: 'indoor',
    label: '집·실내',
    keywords: Object.freeze([
      'home', 'bedroom', 'room', 'indoor', 'lounge', 'terrace', 'workshop',
      'interior', 'atelier', 'library', 'living', 'reading', 'counter',
      'record-store', 'laundromat', 'passage',
      // 현재 호리존 릴리스는 mood가 비어 있어 id의 촬영 공간 토큰으로 분류한다.
      'studio', 'horizon',
    ]),
  }),
  Object.freeze({
    id: 'city',
    label: '거리·도심',
    keywords: Object.freeze([
      'city', 'urban', 'street', 'alley', 'storefront', 'building', 'office',
      'corridor', 'stairs', 'plaza', 'roadside', 'sidewalk', 'rooftop', 'market',
      'crosswalk', 'night',
      'neighborhood', 'wall', 'doorway', 'corner', 'courtyard', 'loadingzone',
      'convenience', 'garage', 'court', 'steps', 'car-wash', 'outdoor',
    ]),
  }),
  Object.freeze({
    id: 'nature',
    label: '공원·자연',
    keywords: Object.freeze([
      'park', 'green', 'leafy', 'riverside', 'garden', 'botanical',
    ]),
  }),
  Object.freeze({
    id: 'resort',
    label: '해변·리조트',
    keywords: Object.freeze(['coastal', 'beach', 'pool', 'resort', 'travel']),
  }),
  Object.freeze({
    id: 'heritage',
    label: '전통·헤리티지',
    keywords: Object.freeze(['heritage', 'traditional']),
  }),
  Object.freeze({ id: 'other', label: '기타', keywords: Object.freeze([]) }),
]);

const BUCKET_INDEX = new Map(EXAMPLE_MOOD_BUCKETS.map((bucket, index) => [bucket.id, index]));

const searchableExampleText = (example) => (
  [example?.mood, example?.detailSubject, example?.id]
    .filter((value) => typeof value === 'string' && value.trim())
    .join(' ')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('en')
);

export function exampleMoodBucket(example) {
  const haystack = searchableExampleText(example);
  return EXAMPLE_MOOD_BUCKETS.find((bucket) => (
    bucket.keywords.some((keyword) => haystack.includes(
      keyword.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('en'),
    ))
  )) || EXAMPLE_MOOD_BUCKETS[EXAMPLE_MOOD_BUCKETS.length - 1];
}

const compareText = (left, right) => (left < right ? -1 : left > right ? 1 : 0);

export function compareGenerationExamplesByMood(left, right) {
  const leftBucket = exampleMoodBucket(left);
  const rightBucket = exampleMoodBucket(right);
  return BUCKET_INDEX.get(leftBucket.id) - BUCKET_INDEX.get(rightBucket.id)
    || (Number(left?.rank) || 0) - (Number(right?.rank) || 0)
    || compareText(String(left?.id), String(right?.id));
}

export function orderExamplesByMood(examples) {
  return [...(Array.isArray(examples) ? examples : [])].sort(compareGenerationExamplesByMood);
}
