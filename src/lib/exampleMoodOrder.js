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
const CLASSIFICATION_PRIORITY = Object.freeze([
  'heritage', 'resort', 'nature', 'cafe', 'indoor', 'city', 'other',
]);

const normalizeSearchText = (value) => String(value || '')
  .normalize('NFKD')
  .replace(/[\u0300-\u036f]/g, '')
  .toLocaleLowerCase('en');

const NORMALIZED_KEYWORDS = new Map(EXAMPLE_MOOD_BUCKETS.map((bucket) => [
  bucket.id,
  Object.freeze(bucket.keywords.map(normalizeSearchText)),
]));
const CLASSIFICATION_BUCKETS = Object.freeze(CLASSIFICATION_PRIORITY.map((id) => (
  EXAMPLE_MOOD_BUCKETS.find((bucket) => bucket.id === id)
)));

const searchableExampleText = (example) => normalizeSearchText(
  [example?.mood, example?.detailSubject, example?.id]
    .filter((value) => typeof value === 'string' && value.trim())
    .join(' '),
);

export function exampleMoodBucket(example) {
  const haystack = searchableExampleText(example);
  // night/travel/outdoor 같은 비장소 토큰도 현 카탈로그 분류에 쓰인다. 새 키워드는
  // 넓은 버킷의 오탐을 늘릴 수 있으므로 판정 우선순위와 실데이터 회귀를 함께 확인한다.
  return CLASSIFICATION_BUCKETS.find((bucket) => (
    NORMALIZED_KEYWORDS.get(bucket.id).some((keyword) => haystack.includes(keyword))
  )) || EXAMPLE_MOOD_BUCKETS[EXAMPLE_MOOD_BUCKETS.length - 1];
}

const compareText = (left, right) => (left < right ? -1 : left > right ? 1 : 0);

export function orderExamplesByMood(examples) {
  return (Array.isArray(examples) ? examples : [])
    .map((example, originalIndex) => ({
      example,
      bucketIndex: BUCKET_INDEX.get(exampleMoodBucket(example).id),
      rank: Number(example?.rank) || 0,
      id: String(example?.id),
      originalIndex,
    }))
    .sort((left, right) => left.bucketIndex - right.bucketIndex
      || left.rank - right.rank
      || compareText(left.id, right.id)
      || left.originalIndex - right.originalIndex)
    .map(({ example }) => example);
}
