import { templateSectionName } from '../../lib/pageTemplates/slots.js';
import { getBlockExportElements } from './editorGeometry.js';

export function getAiDraftSectionNames(blocks = []) {
  const names = new Set();
  for (const block of blocks) {
    if (getBlockExportElements(block).some(element => !element.hidden
      && element.type === 'text' && element.aiDraft === true && String(element.text ?? '').trim())) {
      names.add(templateSectionName(block));
    }
  }
  return [...names];
}

export function formatAiDraftSectionNames(names) {
  return names.length > 3 ? `${names.slice(0, 2).join(', ')} 외 ${names.length - 2}곳` : names.join(', ');
}
