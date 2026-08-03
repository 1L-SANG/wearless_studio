/* 강조 특징(sellingPoints) 칩 인라인 수정 — 순수 계산부.
   폼에서 분리한 이유는 modelSelection.js 와 같다: 커밋 규칙(삭제·중복·AI 표식)이
   화면 없이 검증되어야 한다. */

/* 보이지 않는 문자(zero-width, BOM)는 지운다 — 붙여넣기로 섞여 들어오면 눈에는 빈 칩인데
   문구가 있는 것으로 저장되고, 중복 판정도 빗나간다. */
const INVISIBLE = /[\u200B-\u200D\uFEFF\u2060]/g;

/** 저장·비교용 정규화. 조합형(NFD)·완성형(NFC) 한글이 섞이면 눈에 같은 글자가 다른
 *  문자열이 되어 중복 칩이 생긴다 — 둘 다 NFC 로 모은다. */
function normalize(value) {
  return String(value ?? '').replace(INVISIBLE, '').normalize('NFC').trim();
}

/** 칩 하나를 새 문구로 확정한다. 반환은 onChange 에 그대로 넘길 패치, 변경 없으면 null.
 *
 *  - 빈 문구 + allowDelete = 삭제. X 버튼과 같은 결과로 모은다(따로 배우지 않아도 되게).
 *  - 빈 문구 + !allowDelete = 취소. 포커스가 빠지는 것만으로 칩이 사라지면, 지울 의도가
 *    없던 클릭에서도 목록이 줄어들고 그 사이 인덱스가 밀려 엉뚱한 칩이 열린다.
 *  - 다른 칩과 같은 문구면 되돌린다 — 같은 특징이 두 칸 차지하는 걸 막는다.
 *  - 문구를 고치면 aiSuggestedPoints 에서 옛 문구를 뺀다: 셀러가 손댄 말은 더 이상
 *    AI 제안이 아니고, 남겨두면 'AI 제안' 표식만 사라진 유령 항목이 원장에 남는다.
 */
export function applySellingPointEdit({
  sellingPoints, aiSuggestedPoints, index, text, allowDelete = true,
}) {
  const points = Array.isArray(sellingPoints) ? sellingPoints : [];
  if (!Number.isInteger(index) || index < 0 || index >= points.length) return null;

  const ai = Array.isArray(aiSuggestedPoints) ? aiSuggestedPoints : [];
  const prev = points[index];
  const next = normalize(text);
  if (next === normalize(prev)) return null;

  const withoutPrev = ai.filter((p) => p !== prev);
  if (!next) {
    if (!allowDelete) return null;
    return {
      sellingPoints: points.filter((_, i) => i !== index),
      aiSuggestedPoints: withoutPrev,
    };
  }
  if (points.some((p, i) => i !== index && normalize(p) === next)) return null;

  return {
    sellingPoints: points.map((p, i) => (i === index ? next : p)),
    aiSuggestedPoints: withoutPrev,
  };
}
