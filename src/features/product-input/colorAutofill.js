const DEFAULT_SWATCH_LABELS = Object.freeze({
  white: '화이트',
  gray: '그레이',
  black: '블랙',
  ivory: '아이보리',
  beige: '베이지',
  brown: '브라운',
  red: '레드',
  yellow: '옐로우',
  green: '그린',
  blue: '블루',
  navy: '네이비',
  pink: '핑크',
  purple: '퍼플',
});

const hasText = (value) => typeof value === 'string' && value.trim().length > 0;

/**
 * AG-01 제안을 색상 그룹에 채운다. 셀러가 이미 정한 값은 절대 덮지 않는다.
 * 변경할 값이 없으면 입력 colors 배열을 그대로 반환해 불필요한 저장을 막는다.
 */
export function autofillColorGroups(colors, suggestions, swatchColors = []) {
  if (!Array.isArray(colors) || !Array.isArray(suggestions) || !suggestions.length) return colors;

  const suggestionByGroupId = new Map(
    suggestions
      .filter((suggestion) => suggestion && typeof suggestion.colorGroupId === 'string')
      .map((suggestion) => [suggestion.colorGroupId, suggestion]),
  );
  const swatchLabels = {
    ...DEFAULT_SWATCH_LABELS,
    ...Object.fromEntries((swatchColors || []).map((swatch) => [swatch.id, swatch.label])),
  };
  let changed = false;
  const next = colors.map((color) => {
    const suggestion = suggestionByGroupId.get(color?.id);
    if (!suggestion) return color;

    const swatchId = color.swatchId || suggestion.swatchId || undefined;
    const suggestedName = hasText(suggestion.colorName)
      ? suggestion.colorName.trim()
      : swatchLabels[suggestion.swatchId];
    const name = hasText(color.name) ? color.name : (suggestedName || color.name);
    if (swatchId === color.swatchId && name === color.name) return color;

    changed = true;
    return { ...color, swatchId, name };
  });

  return changed ? next : colors;
}

