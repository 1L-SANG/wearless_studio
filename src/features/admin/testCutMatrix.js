/* 테스트컷 비교 표 — 보정(A·B·C) × 자리(확대1·확대2·전신1·전신2).

   보정 3종은 **같은 기준 원본 4장**에 얼굴만 다시 그린 것이라, 같은 자리끼리 줄을 맞춰야
   비교가 된다. 화면(AdminModels.jsx)은 이 표를 그리기만 한다. 상대 경로 import 도 없는
   순수 모듈이라 테스트가 vite 없이 바로 읽는다. */

export const TEST_CUT_SLOTS = [
  { key: 'closeup-0', kind: 'closeup', index: 0, label: '확대 1' },
  { key: 'closeup-1', kind: 'closeup', index: 1, label: '확대 2' },
  { key: 'fullbody-0', kind: 'fullbody', index: 0, label: '전신 1' },
  { key: 'fullbody-1', kind: 'fullbody', index: 1, label: '전신 2' },
];

/** cuts → { [finishCode]: [cut|null × 4] }. 자리 순서는 TEST_CUT_SLOTS, 같은 종류 안에서는 sort 순. */
export function testCutMatrix(cuts, finishCodes) {
  const matrix = {};
  for (const code of finishCodes) {
    const mine = (cuts || []).filter((cut) => (cut.skinFinish || 'prod') === code);
    const byKind = (kind) => mine
      .filter((cut) => cut.kind === kind)
      .sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0));
    const closeups = byKind('closeup');
    const fullbodies = byKind('fullbody');
    matrix[code] = TEST_CUT_SLOTS.map(({ kind, index }) => (
      (kind === 'closeup' ? closeups : fullbodies)[index] || null
    ));
  }
  return matrix;
}

/** 크게 보기에서 옆 보정으로 넘기기 — 같은 자리에서 컷이 있는 보정만 돈다. 없으면 null. */
export function neighborFinish(matrix, finishCodes, current, slotIndex, step) {
  const filled = finishCodes.filter((code) => matrix[code]?.[slotIndex]);
  if (filled.length === 0) return null;
  const at = filled.indexOf(current);
  if (at === -1) return filled[0];
  return filled[(at + step + filled.length) % filled.length];
}
