const PHOTO_ROW_KINDS = new Set(['twocol', 'threecol', 'grid2x2', 'colorcmp']);
const UNSUPPORTED_KINDS = new Set(['info', 'size', 'care', 'ai-notice']);

// ADR-0019의 칸 판정. 자동 채움 뒤 남은 콘티 번호만으로 계획된 칸으로 보지 않는다.
export function editorSlotKind(block, element, storyboard = []) {
  if (!block || !element || element.type !== 'image' || block.hidden || element.hidden
    || block.auto || UNSUPPORTED_KINDS.has(block.kind)) return 'unsupported';
  if (element.src) return 'filled';
  if (element.genPending) return 'pending';
  if (element.genFailed) return 'planned';

  const source = storyboard.find((entry) => entry.id === element.sourceBlockId);
  const pageGenerated = !block.templateId && (PHOTO_ROW_KINDS.has(block.kind)
    || (block.contentRole && block.contentRole !== 'custom'));
  return source?.source === 'ai' && pageGenerated ? 'planned' : 'unplanned';
}
