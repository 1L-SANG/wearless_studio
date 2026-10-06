// 병합 저장의 null은 최상위 키 삭제다. 배열과 객체는 받은 값 전체로 바꾼다.
export function mergeAnalysisPatch(base, patch) {
  const result = { ...(base || {}) };
  for (const [key, value] of Object.entries(patch || {})) {
    if (value === null) delete result[key];
    else if (value !== undefined) result[key] = value;
  }
  return result;
}
