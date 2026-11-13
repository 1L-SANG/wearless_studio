import { waitForEditedContrast } from './editorContrastPending.js';
import { getFontEmbedCSS } from 'html-to-image';

// html-to-image filters @font-face by the supplied subtree. Collect the whole
// page once, including inherited/variable-based families, before capturing blocks.
export async function getPageFontEmbedCSS(blockNodes, { embed = getFontEmbedCSS } = {}) {
  const doc = blockNodes[0]?.ownerDocument;
  if (!doc) return '';
  await doc.fonts?.ready;
  await waitForEditedContrast(blockNodes);
  const families = new Set();
  const visit = node => {
    const family = doc.defaultView.getComputedStyle(node).fontFamily || node.style?.fontFamily || '';
    family.split(',').map(value => value.trim().replace(/^["']|["']$/g, '')).filter(Boolean).forEach(value => families.add(value));
    Array.from(node.children || []).forEach(visit);
  };
  blockNodes.forEach(visit);
  if (!families.size) return '';
  // Detached probe: no DOM mutation/layout, just the family union used by the
  // library's font-rule filter. Both weights of a used family remain available.
  const probe = doc.createElement('span');
  probe.style.fontFamily = [...families].map(name => `"${name}"`).join(', ');
  return embed(probe);
}
