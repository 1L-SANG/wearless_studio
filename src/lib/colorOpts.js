/* =============================================================
   lib/colorOpts — 색상 옵션(원 색 + 이름) 파생의 단일 규칙.

   콘티보드와 에디터가 같은 색상을 서로 다른 근거로 표시하던 버그(오너 8/15:
   "의류 인스펙터의 색상 원이 색상 이름과 매칭이 안 된다")의 구조적 원인이 규칙 복제였다.
   원(hex)은 swatchId 로, 이름(label)은 product.colors[].name 으로 뽑히면서 사용자가
   스와치를 고른 순간 둘이 갈라졌다 — 이름은 시드 잔재('블랙'), 원은 실제 선택(아이보리).

   그래서 label 도 hex 와 같은 근거(swatchId)를 1순위로 쓴다. 이름을 직접 입력받는
   기능이 생기면 그때는 name 이 1순위가 되어야 하고, hex 도 name→hex 로 함께 뒤집어야
   한다(한쪽만 바꾸면 같은 불일치가 반대 방향으로 재발한다).

   순수 함수 — node --test 에서 직접 import 한다.
   ============================================================= */

/** 색상 하나의 표시 이름. swatchId(사용자가 실제 고른 스와치) → 저장된 이름 → 순번. */
export function colorLabelOf(color, catalogs, index = 0) {
  const swatch = (catalogs?.swatchColors || []).find((s) => s.id === color?.swatchId);
  return swatch?.label || color?.name?.trim() || `색상 ${index + 1}`;
}

/** product.colors → [{id, label, hex}]. hexOf 는 호출부의 hexFor(스와치 우선 해석)를 주입. */
export function buildColorOpts(colors, catalogs, hexOf) {
  return (colors || []).map((color, index) => ({
    id: color.id,
    label: colorLabelOf(color, catalogs, index),
    hex: hexOf(color),
  }));
}

/** 사진이 있거나 기준 색상인 것만 — 옵션 UI 에 실제로 노출할 목록. */
export function visibleColorOpts(allColorOpts, colors) {
  return allColorOpts.filter((_option, index) => (
    ((colors || [])[index]?.images || []).length || (colors || [])[index]?.isBase
  ));
}
