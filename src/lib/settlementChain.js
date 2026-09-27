/* =============================================================
   정산 체인 대조 표시 규칙 (2026-09-26) — 셀러 영수증·모델 정산 내역·관리자 체인 검증이
   같은 말을 하게 하는 순수 함수 모음. 화면(React)도 네트워크도 모른다.

   🔴 판정은 서버가 eth_call 로 방금 읽은 결과(verdict)에서만 나온다. 조회가 실패하면
      "일치"도 "불일치"도 아닌 **조회 실패**다 — 여기서 성공을 지어내는 경로가 없어야 한다.
   ============================================================= */

/** "0x1234ab…cd9f" — 영수증·표에 들어가는 짧은 해시. 원문은 복사 버튼·title 로 준다. */
export function shortHash(value, head = 6, tail = 4) {
  const text = String(value || '').trim();
  if (!text) return '-';
  const body = text.startsWith('0x') ? text.slice(2) : text;
  if (body.length <= head + tail) return text;
  return `0x${body.slice(0, head)}…${body.slice(-tail)}`;
}

const VERDICTS = {
  match: { label: '일치', tone: 'ok', sentence: '체인에 기록된 값과 우리 장부가 모두 같아요.' },
  mismatch: { label: '불일치', tone: 'bad', sentence: '체인 값과 우리 장부가 다른 칸이 있어요.' },
  not_found: { label: '체인에 기록 없음', tone: 'bad', sentence: '이 결제 번호로 체인에 기록된 정산을 찾지 못했어요.' },
};

/** 서버 응답 → 배지. verdict 가 없거나 모르는 값이면 판정하지 않는다(null). */
export function chainVerdict(result) {
  if (!result || typeof result !== 'object') return null;
  return VERDICTS[result.verdict] || null;
}

const CLOCK = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
});

/** "14:03:22 체인에서 읽음" — 방금 읽었다는 사실을 초 단위로. */
export function chainCheckedLabel(checkedAt) {
  const date = new Date(checkedAt);
  if (!checkedAt || Number.isNaN(date.getTime())) return '';
  return `${CLOCK.format(date)} 체인에서 읽음`;
}

const won = value => `${Number(value).toLocaleString('ko-KR')}원`;

/** 비교 표 한 칸의 값. 체인에 없으면 '-'. */
export function chainFieldValue(field, side) {
  const value = field?.[side];
  if (value === null || value === undefined || value === '') return '-';
  if (['total', 'model', 'platform', 'ops'].includes(field.key)) return won(value);
  if (field.key === 'block') return `#${Number(value).toLocaleString('ko-KR')}`;
  if (field.key === 'modelRef') return shortHash(value);
  return String(value);
}

const ERRORS = {
  chain_rpc_failed: '체인 노드에서 응답을 받지 못했어요. 잠시 후 다시 눌러 주세요.',
  chain_unavailable: '체인 연결이 설정되지 않아 지금은 조회할 수 없어요.',
  not_found: '이 정산 기록을 찾을 수 없어요.',
};

/** 조회 실패 문구. 성공처럼 들리는 말은 쓰지 않는다. */
export function chainCheckError(error) {
  const code = error?.code || error?.body?.error?.code || error?.data?.error?.code;
  if (code && ERRORS[code]) return ERRORS[code];
  if (error?.status === 502) return ERRORS.chain_rpc_failed;
  if (error?.status === 503) return ERRORS.chain_unavailable;
  if (error?.status === 404) return ERRORS.not_found;
  return '체인 조회에 실패했어요. 잠시 후 다시 눌러 주세요.';
}
