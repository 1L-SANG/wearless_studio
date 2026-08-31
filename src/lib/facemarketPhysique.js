/* =============================================================
   lib/facemarketPhysique — 등록 위저드 physique(체형·키) 스텝 전용 UI 상수.
   값(value)은 server/app/facemarket_physique.py(단일 소스)의 enum과 정확히
   일치해야 한다 — 라벨(한국어)만 여기 소유, 값은 절대 임의로 바꾸지 않는다.
   ============================================================= */

// 키 구간 — 성별별 6구간. value 는 백엔드 HEIGHT_BUCKETS 와 문자 그대로 일치.
export const HEIGHT_BUCKETS = Object.freeze({
  male: Object.freeze([
    { value: 'm_lt170', label: '170cm 미만' },
    { value: 'm_170_175', label: '170–175cm' },
    { value: 'm_175_180', label: '175–180cm' },
    { value: 'm_180_185', label: '180–185cm' },
    { value: 'm_185_190', label: '185–190cm' },
    { value: 'm_gte190', label: '190cm 이상' },
  ]),
  female: Object.freeze([
    { value: 'f_lt155', label: '155cm 미만' },
    { value: 'f_155_160', label: '155–160cm' },
    { value: 'f_160_165', label: '160–165cm' },
    { value: 'f_165_170', label: '165–170cm' },
    { value: 'f_170_175', label: '170–175cm' },
    { value: 'f_gte175', label: '175cm 이상' },
  ]),
});

// 체형 7종 — 성별 무관 공용 목록. value 는 백엔드 BODY_TYPES 와 문자 그대로 일치.
export const BODY_TYPES = Object.freeze([
  { value: 'delicate', label: '여리여리' },
  { value: 'slim', label: '마름' },
  { value: 'regular', label: '보통' },
  { value: 'plump', label: '통통' },
  { value: 'toned', label: '잔잔한 근육' },
  { value: 'bulk', label: '벌크업' },
  { value: 'glamorous', label: '글래머러스' },
]);

// 성별별로 보여줄 체형 목록. **값은 위 BODY_TYPES(서버 enum)에서만 고른다** — 목록을
// 나누는 건 UI 뿐이고, 새 값을 만들면 서버 validate_physique 가 거절한다.
// 이미지는 /models/physique/{gender}/{value}.webp 규약. 파일이 없으면 카드가 텍스트 칩으로
// 되돌아가므로, 나중에 사진만 그 경로에 떨구면 코드 수정 없이 이미지 선택으로 바뀐다.
const BODY_TYPE_LABEL = Object.freeze(
  Object.fromEntries(BODY_TYPES.map((b) => [b.value, b.label])),
);
const BODY_TYPES_BY_GENDER = Object.freeze({
  male: Object.freeze(['slim', 'regular', 'toned', 'bulk', 'plump']),
  female: Object.freeze(['delicate', 'slim', 'regular', 'plump', 'glamorous']),
});

export function bodyTypeOptions(gender) {
  const values = BODY_TYPES_BY_GENDER[gender];
  // 성별을 아직 모르면(OACX 미제공·미선택) 7종 전부 — 고를 수단 자체를 뺏지 않는다.
  if (!values) return BODY_TYPES.map((b) => ({ ...b, image: null }));
  return values.map((value) => ({
    value,
    label: BODY_TYPE_LABEL[value],
    image: `/models/physique/${gender}/${value}.webp`,
  }));
}

// gender('male'|'female')로 보여줄 키 구간을 좁힌다. gender 가 없으면(OACX 신원에서 아직
// 못 받았거나 null) 남녀 통합 목록을 보여준다 — 선택 자체가 잘못된 값을 만들진 않는다
// (서버 validate_physique 가 gender 불일치를 다시 막는다).
export function heightBucketOptions(gender) {
  if (gender === 'male') return HEIGHT_BUCKETS.male;
  if (gender === 'female') return HEIGHT_BUCKETS.female;
  return [...HEIGHT_BUCKETS.male, ...HEIGHT_BUCKETS.female];
}
