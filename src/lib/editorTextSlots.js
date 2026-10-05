export const CAPTION_SIZE = 44;
export const CAPTION_LINE_HEIGHT = 60;
export const CAPTION_CHARS_PER_LINE = 20;
export const CAPTION_GAP = 32;

export const TAG_SIZE = 28;
export const TAG_HEIGHT = 38;
export const TAG_SHIFT = 64;

export const ROW_LABEL_SIZE = 30;
export const ROW_LABEL_HEIGHT = 42;

export const HOOK_TITLE_SIZE = 60;
export const HOOK_TITLE_LINE_HEIGHT = 72;
export const HOOK_TITLE_CHARS_PER_LINE = 14;
export const HOOK_TITLE_MAX_LINES = 2;

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
  const title = String(value ?? '').trim().replace(/\s+/g, ' ');
  if (characterCount(title) <= HOOK_TITLE_CHARS_PER_LINE) {
    return { text: title, lines: 1, h: HOOK_TITLE_LINE_HEIGHT };
  }

  const words = title.split(' ');
  let lines;
  if (words.length > 1) {
    let best = null;
    for (let index = 1; index < words.length; index += 1) {
      const left = words.slice(0, index).join(' ');
      const right = words.slice(index).join(' ');
      const leftLength = characterCount(left);
      const rightLength = characterCount(right);
      const overflow = Math.max(0, leftLength - HOOK_TITLE_CHARS_PER_LINE)
        + Math.max(0, rightLength - HOOK_TITLE_CHARS_PER_LINE);
      const score = overflow * 1000 + Math.abs(leftLength - rightLength);
      if (!best || score < best.score) best = { left, right, score };
    }
    lines = [best.left, best.right];
  } else {
    const characters = Array.from(title);
    const splitAt = Math.ceil(characters.length / 2);
    lines = [characters.slice(0, splitAt).join(''), characters.slice(splitAt).join('')];
  }

  return {
    text: lines.join('\n'),
    lines: HOOK_TITLE_MAX_LINES,
    h: HOOK_TITLE_LINE_HEIGHT * HOOK_TITLE_MAX_LINES,
  };
}

export function hookTitleStyle(color) {
  return {
    font: 'Pretendard',
    size: HOOK_TITLE_SIZE,
    weight: 700,
    color,
    align: 'center',
    lineHeight: HOOK_TITLE_LINE_HEIGHT,
    maxLines: HOOK_TITLE_MAX_LINES,
  };
}

export function textLineClampStyle(style, editing) {
  const maxLines = Number(style?.maxLines);
  if (editing || !Number.isFinite(maxLines) || maxLines < 1) return {};
  return {
    display: '-webkit-box',
    WebkitBoxOrient: 'vertical',
    WebkitLineClamp: maxLines,
    overflow: 'hidden',
  };
}
