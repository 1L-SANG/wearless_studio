import { NEEDS_INPUT, NOTICE_FIELDS } from './presets/infoPresets.js';

// 캔버스에서 직접 고친 글자도 내려받으므로 폼의 옛 info.fields가 아닌 출력 요소를 읽는다.
export function missingNoticeFields(blocks = []) {
  const labels = new Set();
  for (const block of blocks) {
    if (block?.kind !== 'info' || block.infoType !== 'required_notice') continue;
    const texts = (block.elements || []).filter((el) => el.type === 'text');
    const fieldLabels = new Set((block.info?.fields || NOTICE_FIELDS).map((field) => field.label));
    for (const [index, value] of texts.entries()) {
      if (value.hidden || value.system || String(value.text ?? '').trim() !== NEEDS_INPUT) continue;
      if ((value.opacity ?? 1) * (value.style?.opacity ?? 1) === 0) continue;
      // 고시 표의 같은 행 왼쪽이 항목명이다. 레이어 순서가 바뀌어도 행은 유지된다.
      const rowLabel = texts.find((el) => el !== value && el.y === value.y && el.x < value.x);
      // 개별 드래그는 요소 순서를 바꾸지 않는다. 항목명인 경우에만 이전 글자를 사용한다.
      const previous = texts[index - 1];
      const label = rowLabel || (fieldLabels.has(previous?.text) ? previous : null);
      labels.add(String(label?.text || '').trim() || '고시정보 항목');
    }
  }
  return [...labels];
}
