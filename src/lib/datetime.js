/* =============================================================
   날짜·시각 표시 — **전부 한국 시간(Asia/Seoul) 고정.**

   서버는 시각을 절대시각(timestamptz)으로 저장하고 ISO 문자열로 내보낸다. 그걸 화면에
   그릴 때 시간대를 안 정하면 **보는 사람의 브라우저 시간대**가 눈금이 된다. 한국에서
   열면 맞으니 개발 중엔 안 드러나고, 공개 검증 페이지(/verify)처럼 해외에서도 열리는
   화면에서 조용히 다른 날짜가 나온다 — 라이선스 유효기간이 그런 값이다.

   흔한 오해 하나: `toLocaleDateString('ko-KR')` 의 'ko-KR' 은 **표기 형식**만 정하고
   시간대는 정하지 않는다. 시간대는 `timeZone` 옵션으로만 정해진다. 그래서 이 파일이 있다.

   `iso.slice(0, 10)` 도 같은 이유로 금지다 — 그건 UTC 날짜라 KST 00:00~08:59 에 생긴
   것이 전부 전날로 보인다. 콘솔 대시보드는 KST 로 집계하므로, 같은 화면 안에서 목록과
   집계 숫자가 어긋난다.

   서버도 커넥션 시간대를 KST 로 둔다(server/app/db.py). 그래도 화면은 **그걸 믿지
   않는다** — 여기서 시간대를 고정하면 서버가 어떤 오프셋을 보내든 결과가 같다.

   표기 모양은 기존 화면 그대로다. 이 파일이 바꾸는 것은 **눈금뿐**이다.
   ============================================================= */

export const SEOUL_TIME_ZONE = 'Asia/Seoul';

/* Intl.DateTimeFormat 생성은 공짜가 아니라(로캘 데이터 해석) 목록 한 줄마다 새로 만들면
   눈에 띄게 느려진다 — 모듈 수준에서 한 번만 만든다. */
const fmt = (locale, options) =>
  new Intl.DateTimeFormat(locale, { timeZone: SEOUL_TIME_ZONE, ...options });

// toLocaleDateString('ko-KR') 의 기본 구성과 같다 — "2026. 9. 8."
const DATE = fmt('ko-KR', { year: 'numeric', month: 'numeric', day: 'numeric' });
// en-CA 는 YYYY-MM-DD 로 낸다 — 표 정렬·키에 쓰기 좋은 모양.
const DATE_KEY = fmt('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' });
const DATE_TIME = fmt('ko-KR', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const CLOCK = fmt('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
const YEAR_MONTH = fmt('en-CA', { year: 'numeric', month: '2-digit' });

/* 못 읽는 값은 던지지 않고 null 로 떨어뜨린다. 호출부가 전부 화면을 그리는 중이라,
   여기서 던지면 날짜 한 칸 때문에 화면 전체가 사라진다.
   (`new Date('쓰레기')` 는 던지지 않고 Invalid Date 를 준다 — 기존 try/catch 들은 사실
   아무것도 못 잡고 있었고 화면에 "Invalid Date" 가 그대로 나갔다. 여기서는 호출부가 준
   fallback 이 나간다.) */
function toDate(value) {
  if (value == null || value === '') return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function render(formatter, value, fallback) {
  const date = toDate(value);
  return date ? formatter.format(date) : fallback;
}

/** "2026. 9. 8." — 사람이 읽는 날짜. */
export function seoulDate(value, fallback = '') {
  return render(DATE, value, fallback);
}

/** "2026-09-08" — 표·목록의 날짜 칸(정렬 가능한 모양). */
export function seoulDateKey(value, fallback = '-') {
  return render(DATE_KEY, value, fallback);
}

/** "9월 8일 11:24" — 날짜+시각. */
export function seoulDateTime(value, fallback = '') {
  return render(DATE_TIME, value, fallback);
}

/** "11:24" — 시각만. */
export function seoulClock(value, fallback = '--:--') {
  return render(CLOCK, value, fallback);
}

/** "2027.06" — "유효 ~2027.06" 같은 연월 표기. */
export function seoulYearMonth(value, fallback = '') {
  const text = render(YEAR_MONTH, value, null);
  return text === null ? fallback : text.replace('-', '.');
}
