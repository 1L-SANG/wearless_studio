const WIDTHS = [4, 2, 2];

export function nextBirthdateSegments(segments, index, input) {
  const digits = String(input).replace(/\D/g, '');
  if (input && !digits) return { segments: [...segments], focusIndex: index };
  if (digits.length === 8) {
    return { segments: [digits.slice(0, 4), digits.slice(4, 6), digits.slice(6, 8)], focusIndex: 2 };
  }
  const next = [...segments];
  next[index] = digits.slice(0, WIDTHS[index]);
  // 두 자리가 될 수 없는 월(2~9)과 일(4~9) 한 자리는 앞에 0을 붙여 바로 다음 칸으로 넘긴다.
  if ((index === 1 && /^[2-9]$/.test(next[index])) || (index === 2 && /^[4-9]$/.test(next[index]))) next[index] = `0${next[index]}`;
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

export function isCalendarDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  return year >= 1000 && date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day;
}

export function isAdultBirthdate(value, today = new Date()) {
  if (!isCalendarDate(value)) return false;
  const [year, month, day] = value.split('-').map(Number);
  const age = today.getFullYear() - year
    - (today.getMonth() + 1 < month || (today.getMonth() + 1 === month && today.getDate() < day) ? 1 : 0);
  return age >= 19;
}

// 생년월일 칸 아래 안내를 고른다. 달력과 나이 문제는 세 칸이 다 찬 뒤에만 알린다.
export function birthdateProblem(segments, today = new Date()) {
  const [year = '', month = '', day = ''] = segments;
  if (month.length === 2 && day.length === 2 && year.length >= 1 && year.length <= 3) return 'year';
  const value = birthdateFromSegments(segments);
  if (!value) return null;
  if (!isCalendarDate(value)) return 'calendar';
  return isAdultBirthdate(value, today) ? null : 'minor';
}
