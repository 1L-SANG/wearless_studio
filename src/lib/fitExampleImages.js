/* 핏 예시 타일 이미지 매핑 (fit-profile UI)
   - 예시 = 셀러 옷과 무관한 "중립 마네킹 예시"(우리가 생성 검증 때 만든 이미지의 큐레이션).
   - 파일 규칙: public/assets/fit-examples/{category}-{gender|any}-{axis}-{value}.jpg
   - 이미지가 없는 조합은 null 반환 → UI는 텍스트 전용 타일로 폴백(절대 깨지지 않게).
   - 갭(추가 생성 백로그): top-men semi_over · pants-men slim/straight ·
     skirt length(mini/midi/long) · dress a_line/midi · outer regular/semi_over/basic. */

const BASE = '/assets/fit-examples';

// 존재하는 파일만 등록 (빌드타임 검증 대신 명시 목록 — 오탈자 방지)
const FILES = new Set([
  'top-women-fit-tight', 'top-women-fit-slim', 'top-women-fit-regular',
  'top-women-fit-semi_over', 'top-women-fit-over',
  'top-women-length-ultra_crop', 'top-women-length-crop', 'top-women-length-basic', 'top-women-length-long',
  'top-men-fit-slim', 'top-men-fit-regular', 'top-men-fit-over',
  'pants-women-cut-skinny', 'pants-women-cut-slim', 'pants-women-cut-straight',
  'pants-women-cut-bootcut', 'pants-women-cut-wide',
  'pants-men-cut-tapered', 'pants-men-cut-relaxed', 'pants-men-cut-semi_wide', 'pants-men-cut-wide',
  'pants-any-length-above_ankle', 'pants-any-length-ankle', 'pants-any-length-below_ankle',
  'skirt-women-silhouette-h_line', 'skirt-women-silhouette-a_line', 'skirt-women-silhouette-mermaid',
  'dress-women-silhouette-h_line', 'dress-women-silhouette-fit_and_flare', 'dress-women-silhouette-mermaid',
  'dress-women-length-mini', 'dress-women-length-long',
  'outer-any-fit-slim', 'outer-any-fit-over',
  'outer-any-length-crop_short', 'outer-any-length-long',
]);

/** 예시 이미지 경로. 성별 전용 → 공용(any) 순으로 찾고, 없으면 null (텍스트 타일 폴백). */
export function fitExampleImage(category, gender, axis, value) {
  if (!category || !axis || !value) return null;
  const g = gender === 'men' ? 'men' : 'women';
  for (const gg of [g, 'any']) {
    const key = `${category}-${gg}-${axis}-${value}`;
    if (FILES.has(key)) return `${BASE}/${key}.jpg`;
  }
  return null;
}
