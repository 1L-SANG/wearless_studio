import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { clientApiFetch, readClientResponse } from '../../lib/api/clientRequest.js';
import { toBytesUrl } from './editorAssetBytes.js';

const luminance = rgb => rgb.slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
  .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
const inkLuminance = hex => luminance([1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)));

// Same lower-decile policy as page_template_contrast.sample_overlay_style.
export function editedLineContrast(data) {
  const { light, dark } = pageTemplatesCatalog.overlayColors;
  if (!data?.length) return { color: light, shade: false };
  const scores = [light, dark].map(color => {
    const ink = inkLuminance(color), ratios = [];
    for (let i = 0; i < data.length; i += 4) {
      const bg = luminance(Array.from(data.slice(i, i + 3)));
      ratios.push((Math.max(bg, ink) + .05) / (Math.min(bg, ink) + .05));
    }
    ratios.sort((a, b) => a - b);
    return { color, score: ratios[Math.floor(ratios.length * .1)] };
  });
  const best = scores[1].score >= scores[0].score ? scores[1] : scores[0];
  return { color: best.color, shade: best.score < 3 };
}

// Browser line boxes, after font loading and wrapping, are the measurement source.
export function measureWrappedTextLines(node) {
  const doc = node.ownerDocument, walker = doc.createTreeWalker(node, 4), range = doc.createRange(), lines = [];
  let textNode;
  while ((textNode = walker.nextNode())) {
    for (let index = 0; index < textNode.textContent.length; index++) {
      const text = textNode.textContent[index];
      if (text === '\n') continue;
      range.setStart(textNode, index); range.setEnd(textNode, index + 1);
      const rect = range.getBoundingClientRect();
      if (!rect.height) continue;
      let line = lines.at(-1);
      if (!line || Math.abs(line.top - rect.top) > 1) {
        // Preserve intentionally blank lines as well as automatic wrapping.
        if (line) {
          const lineHeight = parseFloat(doc.defaultView.getComputedStyle(node).lineHeight);
          const scale = node.offsetWidth ? node.getBoundingClientRect().width / node.offsetWidth : 1;
          const gap = lineHeight > 0 ? Math.round((rect.top - line.top) / (lineHeight * scale)) - 1 : 0;
          for (let blank = 0; blank < gap; blank++) lines.push({ text: '', top: line.top, bottom: line.bottom, left: line.left, right: line.left });
        }
        line = { text: '', left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom }; lines.push(line);
      }
      line.text += text; line.left = Math.min(line.left, rect.left); line.right = Math.max(line.right, rect.right); line.bottom = Math.max(line.bottom, rect.bottom);
    }
  }
  range.detach?.();
  return lines;
}

async function readImage(src, signal) {
  const response = await clientApiFetch(toBytesUrl(src), { signal });
  if (!response.ok) throw new Error('contrast image unavailable');
  const blob = await readClientResponse(response, 'blob');
  const url = URL.createObjectURL(blob);
  try {
    return await new Promise((resolve, reject) => {
      const image = new Image();
      const abort = () => { image.src = ''; reject(new DOMException('Aborted', 'AbortError')); };
      const done = callback => value => { signal?.removeEventListener('abort', abort); callback(value); };
      image.onload = done(() => resolve(image)); image.onerror = done(reject);
      if (signal?.aborted) { abort(); return; }
      signal?.addEventListener('abort', abort, { once: true }); image.src = url;
    });
  } finally { URL.revokeObjectURL(url); }
}

export async function measureEditedTemplateContrast(node, { signal, loadImage = readImage } = {}) {
  const doc = node.ownerDocument;
  await doc.fonts?.ready;
  if (signal?.aborted || !node.isConnected) return null;
  const lines = measureWrappedTextLines(node), root = node.closest('.block-clip');
  if (!root || !lines.length) return null;
  const images = [...root.querySelectorAll('.el img')].filter(image => !image.closest('.el-template-overlay')
    && Boolean(image.compareDocumentPosition(node) & 4));
  const decoded = new Map(), output = [];
  for (const line of lines) {
    const match = images.filter(image => {
      const r = image.closest('.el').getBoundingClientRect();
      return r.left <= line.left && r.right >= line.right && r.top <= line.top && r.bottom >= line.bottom;
    }).at(-1);
    if (!match) {
      // An edited title may grow below its photograph onto the section surface.
      // Those lines still need readable ink, rather than the old photo's white.
      const surface = root.closest?.('.canvas-block') || root;
      const color = doc.defaultView.getComputedStyle(surface).backgroundColor || '';
      const rgb = color.match(/^rgba?\(([^)]+)\)/)?.[1].split(/[, /]+/).map(Number);
      const alpha = rgb?.[3] ?? 1;
      const pixel = rgb?.length >= 3 ? [...rgb.slice(0, 3).map(value => Math.round(value * alpha + 255 * (1 - alpha))), 255]
        : [255, 255, 255, 255];
      output.push({ text: line.text, ...editedLineContrast(new Uint8ClampedArray(pixel)) });
      continue;
    }
    const src = match.currentSrc || match.src;
    if (!decoded.has(src)) decoded.set(src, await loadImage(src, signal));
    if (signal?.aborted) return null;
    const image = decoded.get(src), bounds = match.getBoundingClientRect();
    const fit = doc.defaultView.getComputedStyle(match).objectFit;
    const scale = (fit === 'contain' ? Math.min : Math.max)(bounds.width / image.naturalWidth, bounds.height / image.naturalHeight);
    const left = bounds.left + (bounds.width - image.naturalWidth * scale) / 2;
    const top = bounds.top + (bounds.height - image.naturalHeight * scale) / 2;
    const x = Math.max(0, Math.floor((line.left - left) / scale)), y = Math.max(0, Math.floor((line.top - top) / scale));
    const right = Math.min(image.naturalWidth, Math.ceil((line.right - left) / scale));
    const bottom = Math.min(image.naturalHeight, Math.ceil((line.bottom - top) / scale));
    if (right <= x || bottom <= y) { output.push({ text: line.text, ...editedLineContrast(null) }); continue; }
    const canvas = doc.createElement('canvas'); canvas.width = right - x; canvas.height = bottom - y;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(image, x, y, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
    output.push({ text: line.text, ...editedLineContrast(ctx.getImageData(0, 0, canvas.width, canvas.height).data) });
    canvas.width = canvas.height = 0;
  }
  return output;
}
