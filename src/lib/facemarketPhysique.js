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

// gender('male'|'female')로 보여줄 키 구간을 좁힌다. gender 가 없으면(OACX 신원에서 아직
// 못 받았거나 null) 남녀 통합 목록을 보여준다 — 선택 자체가 잘못된 값을 만들진 않는다
// (서버 validate_physique 가 gender 불일치를 다시 막는다).
export function heightBucketOptions(gender) {
  if (gender === 'male') return HEIGHT_BUCKETS.male;
  if (gender === 'female') return HEIGHT_BUCKETS.female;
  return [...HEIGHT_BUCKETS.male, ...HEIGHT_BUCKETS.female];
}
