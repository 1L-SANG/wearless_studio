const WIDTHS = [4, 2, 2];

export function nextBirthdateSegments(segments, index, input) {
  const digits = String(input).replace(/\D/g, '');
  if (input && !digits) return { segments: [...segments], focusIndex: index };
  if (digits.length === 8) {
    return { segments: [digits.slice(0, 4), digits.slice(4, 6), digits.slice(6, 8)], focusIndex: 2 };
  }
  const next = [...segments];
  next[index] = digits.slice(0, WIDTHS[index]);
  return { segments: next, focusIndex: next[index].length === WIDTHS[index] ? Math.min(index + 1, 2) : index };
}

export function backspaceBirthdateSegments(segments, index) {
  const next = [...segments];
  const focusIndex = !next[index] && index > 0 ? index - 1 : index;
  next[focusIndex] = next[focusIndex].slice(0, -1);
  return { segments: next, focusIndex };
}

export function birthdateFromSegments(segments) {
  return segments.every((part, index) => new RegExp(`^\\d{${WIDTHS[index]}}$`).test(part))
    ? segments.join('-') : '';
}

export function isAdultBirthdate(value, today = new Date()) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  if (year < 1000 || date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return false;
  const age = today.getFullYear() - year
    - (today.getMonth() + 1 < month || (today.getMonth() + 1 === month && today.getDate() < day) ? 1 : 0);
  return age >= 19;
}
