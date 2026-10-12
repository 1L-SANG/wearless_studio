export const OUTER_CLOSURE_VALUES = Object.freeze([
  'closed', 'openToChest', 'partial', 'openToWaist', 'open',
]);

const CAPTIONS = {
  closed: '전체 닫힘', openToChest: '가슴까지 열림', partial: '반만 열림',
  openToWaist: '배까지 열림', open: '전체 열림',
};
const KINDS = new Set(['zipper', 'button', 'snap', 'belt', 'none', 'unknown']);

export function normalizeOuterClosureState(value) {
  return OUTER_CLOSURE_VALUES.includes(value) ? value : 'open';
}

export function outerClosureRange(frontClosure) {
  if (!frontClosure || Array.isArray(frontClosure) || frontClosure.version !== 1
    || !KINDS.has(frontClosure.kind) || !['graded', 'binary', null].includes(frontClosure.range)) {
    return 'graded';
  }
  if (frontClosure.kind === 'none') return 'hidden';
  if (frontClosure.kind === 'zipper' || frontClosure.kind === 'unknown') return 'graded';
  return frontClosure.kind === 'belt' || frontClosure.range === 'binary' ? 'binary' : 'graded';
}

export function outerClosureDisplayValue(value, range) {
  const normalized = normalizeOuterClosureState(value);
  if (range !== 'binary') return normalized;
  return OUTER_CLOSURE_VALUES.indexOf(normalized) <= 2 ? 'closed' : 'open';
}

export function outerClosureCaption(value, range, catalogOptions) {
  if (range === 'hidden') return null;
  const displayed = outerClosureDisplayValue(value, range);
  return catalogOptions?.find((option) => option.value === displayed)?.caption || CAPTIONS[displayed];
}
