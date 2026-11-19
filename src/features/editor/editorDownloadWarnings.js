import { NEEDS_INPUT, NOTICE_FIELDS } from './presets/infoPresets.js';
import { hasImageSource } from './editorImageSource.js';
import { canvasAiProgressVisible } from './canvasAiApply.js';

function isVisible(element) {
  return !element.hidden && !element.system
    && (element.opacity ?? 1) * (element.style?.opacity ?? 1) !== 0;
}

// 캔버스에서 직접 고친 글자도 내려받으므로 폼의 옛 info.fields가 아닌 출력 요소를 읽는다.
export function missingNoticeFields(blocks = []) {
  const labels = new Set();
  for (const block of blocks) {
    if (block?.kind !== 'info' || block.infoType !== 'required_notice') continue;
    const texts = (block.elements || []).filter((el) => el.type === 'text');
    const fieldLabels = new Set((block.info?.fields || NOTICE_FIELDS).map((field) => field.label));
    let previousWasValue = false;
    for (const [index, value] of texts.entries()) {
      // 드래그해도 순서는 유지된다. 앞 칸의 값이 항목명과 같을 때는 새 항목명으로 쓰지 않는다.
      const previous = texts[index - 1];
      const orderedLabel = !previousWasValue && fieldLabels.has(previous?.text) ? previous : null;
      previousWasValue = Boolean(orderedLabel);

      const text = String(value.text ?? '').trim();
      if (!isVisible(value) || (text && text !== NEEDS_INPUT)) continue;
      // 레이어 순서로 짝을 찾을 수 없으면 같은 행의 왼쪽 글자를 찾는다.
      const label = orderedLabel || texts.find((el) => el.y === value.y && el.x < value.x);
      if (label) labels.add(String(label.text || '').trim() || '고시정보 항목');
    }
  }
  return [...labels];
}

export function downloadChecks(blocks = [], canvasAiJobs) {
  const items = {
    failed: { kind: 'failed', count: 0 },
    empty: { kind: 'empty', count: 0 },
    notice: { kind: 'notice', count: 0 },
    pending: { kind: 'pending', count: 0 },
  };
  for (const block of blocks) {
    for (const element of block?.elements || []) {
      if (element.type !== 'image' || hasImageSource(element) || !isVisible(element)) continue;
      const job = canvasAiJobs?.get(element.id);
      const slotJob = job?.isSlot && job.blockId === block.id && canvasAiProgressVisible(job);
      // 'done'인 컷도 최종 이미지 주소가 들어올 때까지 기다린다.
      const failed = slotJob ? job.status === 'failed' : element.genPending === 'failed' || element.genFailed;
      const kind = failed ? 'failed' : slotJob || element.genPending ? 'pending' : 'empty';
      const item = items[kind];
      if (item.count === 0) item.blockId = block.id;
      item.count += 1;
    }
  }

  const labels = missingNoticeFields(blocks);
  if (labels.length > 0) {
    const firstBlock = blocks.find((block) => missingNoticeFields([block]).length > 0);
    items.notice = { kind: 'notice', count: labels.length, blockId: firstBlock.id, labels };
  }
  return Object.values(items).filter((item) => item.count > 0);
}
