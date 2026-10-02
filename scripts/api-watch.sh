#!/usr/bin/env bash
# api.wearless.kr 를 1초마다 찔러 상태코드·지연·엣지(cf-ray)를 찍고 200 이 아닌 응답 수를 센다.
# DNS 전환·롤링 배포·되돌리기 동안 띄워 둔다. Ctrl-C 로 끝내면 요약을 찍고,
# 200 아닌 응답이 0 이면 exit 0, 하나라도 있으면 exit 1.
#
# 사용: scripts/api-watch.sh [URL]    (기본 https://api.wearless.kr/healthz)
set -uo pipefail

URL="${1:-https://api.wearless.kr/healthz}"
total=0
bad=0

summary() {
  echo
  echo "요청 ${total}건 · 200 아닌 응답 ${bad}건"
  [ "$bad" -eq 0 ] && exit 0
  exit 1
}
trap summary INT TERM

while true; do
  out=$(curl -sS -o /dev/null --max-time 10 -D - \
        -w 'HTTPCODE=%{http_code} TIME=%{time_total}\n' "$URL" 2>&1) || true
  total=$((total + 1))  # 끝난 요청만 센다 — Ctrl-C 순간 날아가던 요청은 빼고
  code=$(sed -n 's/^HTTPCODE=\([0-9]*\).*/\1/p' <<<"$out")
  secs=$(sed -n 's/.* TIME=\([0-9.]*\)$/\1/p' <<<"$out")
  ray=$(grep -i '^cf-ray:' <<<"$out" | tr -d '\r' | awk '{print $2}')
  [ "${code:-000}" = "200" ] || bad=$((bad + 1))
  printf '%s  %s  %ss  cf-ray=%s  bad=%d\n' "$(date +%H:%M:%S)" "${code:-000}" "${secs:-?}" "${ray:--}" "$bad"
  sleep 1
done
