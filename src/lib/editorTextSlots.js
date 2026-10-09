import { HOOK_TITLE_LIMITS, HOOK_TITLE_METRICS } from './hookTitleMetrics.js';

export const CAPTION_SIZE = 44;
export const CAPTION_LINE_HEIGHT = 60;
export const CAPTION_CHARS_PER_LINE = 20;
export const CAPTION_GAP = 32;

export const TAG_SIZE = 28;
export const TAG_HEIGHT = 38;
export const TAG_SHIFT = 64;

export const ROW_LABEL_SIZE = 30;
export const ROW_LABEL_HEIGHT = 42;

export const CAPTION_STYLE = Object.freeze({
  size: CAPTION_SIZE,
  weight: 600,
  color: '#0e0d14',
  lineHeight: CAPTION_LINE_HEIGHT,
});

export const TAG_STYLE = Object.freeze({
  size: TAG_SIZE,
  weight: 700,
  tracking: 3,
  color: '#9a9aa2',
  lineHeight: TAG_HEIGHT,
});

export const ROW_LABEL_STYLE = Object.freeze({
  size: ROW_LABEL_SIZE,
  weight: 600,
  color: '#0e0d14',
  align: 'center',
  lineHeight: ROW_LABEL_HEIGHT,
});

const characterCount = (value) => Array.from(String(value ?? '')).length;

export function captionLineCount(value) {
  return String(value ?? '').split('\n').reduce(
    (total, line) => total + Math.max(1, Math.ceil(characterCount(line) / CAPTION_CHARS_PER_LINE)),
    0,
  );
}

export function captionHeight(value) {
  return captionLineCount(value) * CAPTION_LINE_HEIGHT;
}

export function hookTitleLayout(value) {
  const title = String(value ?? '').normalize('NFC')
    .replace(/[ \t\r\n\f\v\u00a0\u3000]+/g, ' ').replace(/^ +| +$/g, '');
  const characters = Array.from(title);
  const { maxSize, minSize, lineBudgetPx, ellipsis } = HOOK_TITLE_LIMITS;
  const metrics = HOOK_TITLE_METRICS;
  const budget = lineBudgetPx * metrics.unitsPerEm;
  const widthOf = (character) => {
    const code = character.codePointAt(0);
    if ((code >= 0xac00 && code <= 0xd7a3) || (code >= 0x3131 && code <= 0x318e)) return metrics.hangul;
    return metrics.ascii[character] ?? metrics.extra[character] ?? metrics.fallback;
  };
  const fit = (width) => Math.min(maxSize, Math.floor(budget / Math.max(1, width)));
  const finish = (lines) => {
    const size = fit(Math.max(...lines.map((line) => Array.from(line).reduce((sum, character) => sum + widthOf(character), 0))));
    const lineHeight = Math.floor((size * 6 + 2) / 5);
    return { text: lines.join('\n'), lines: lines.length, size, lineHeight, h: lines.length * lineHeight };
  };
  const widths = [0];
  for (const character of characters) widths.push(widths.at(-1) + widthOf(character));

  const opening = Array.from('([{【〔「『（［');
  const closing = Array.from(')]}】〕」』）］');
  const stack = [];
  const inside = new Set();
  characters.forEach((character, index) => {
    if (opening.includes(character)) {
      stack.push({ character, index });
    } else {
      const kind = closing.indexOf(character);
      if (kind < 0) return;
      for (let top = stack.length - 1; top >= 0; top -= 1) {
        if (stack[top].character !== opening[kind]) continue;
        for (let inner = stack[top].index + 1; inner < index; inner += 1) inside.add(inner);
        stack.length = top;
        break;
      }
    }
  });

  const breaks = [];
  for (let index = 1; index < characters.length; index += 1) {
    if (inside.has(index - 1) || inside.has(index)) continue;
    if (characters[index] === ' ') {
      breaks.push({ end: index, start: index + 1 });
    } else if (characters[index - 1] !== ' '
      && (closing.includes(characters[index - 1]) || opening.includes(characters[index])
        || ['/', '·', ','].includes(characters[index - 1]))) {
      breaks.push({ end: index, start: index });
    }
  }

  const totalWidth = widths.at(-1);
  let best = { lines: [title], size: fit(totalWidth), difference: 0, index: 0 };
  for (const { end, start } of breaks) {
    const leftWidth = widths[end];
    const rightWidth = totalWidth - widths[start];
    const size = fit(Math.max(leftWidth, rightWidth));
    const difference = Math.abs(leftWidth - rightWidth);
    if (size > best.size || (size === best.size && best.lines.length === 2
      && (difference < best.difference || (difference === best.difference && end < best.index)))) {
      best = {
        lines: [characters.slice(0, end).join(''), characters.slice(start).join('')],
        size, difference, index: end,
      };
    }
  }
  if (best.size >= minSize) return finish(best.lines);

  const minimumBudget = Math.floor(budget / minSize);
  const ellipsisWidth = widthOf(ellipsis);
  const truncate = (start, end) => {
    if (end == null) {
      end = start;
      while (end < characters.length && widths[end + 1] - widths[start] + ellipsisWidth <= minimumBudget) end += 1;
    }
    return characters.slice(start, end).join('').replace(/ +$/, '') + ellipsis;
  };
  const firstBreak = breaks.filter(({ end }) => widths[end] <= minimumBudget).at(-1);
  if (!firstBreak) return finish([truncate(0)]);
  const secondBreak = breaks.filter(({ end }) => end > firstBreak.start
    && widths[end] - widths[firstBreak.start] + ellipsisWidth <= minimumBudget).at(-1);
  return finish([
    characters.slice(0, firstBreak.end).join(''),
    truncate(firstBreak.start, secondBreak?.end),
  ]);
}

export function hookTitleStyle(color, { size, lineHeight }) {
  return {
    font: 'Pretendard',
    size,
    weight: 700,
    color,
    align: 'center',
    lineHeight,
  };
}
