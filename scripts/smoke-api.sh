#!/usr/bin/env bash
# 새 리전 API 가 실제로 살아 있는지 DNS 컷오버 **전에** 확인한다.
# ALB DNS 로 직접 때리되 Host 헤더는 api.wearless.kr 로 보낸다 — 그래야 실제 컷오버
# 이후와 같은 경로(호스트 기반 리스너 룰 + TLS SNI)를 지난다.
#
# 사용: scripts/smoke-api.sh <alb-dns-name>
set -uo pipefail

ALB="${1:?ALB DNS name (예: wearless-xxx.us-east-1.elb.amazonaws.com)}"
HOST="${HOST:-api.wearless.kr}"
fail=0

check() {  # name expected_status path
  local name="$1" want="$2" path="$3"
  local got
  got=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 \
        --resolve "${HOST}:443:$(dig +short "$ALB" | head -1)" \
        "https://${HOST}${path}" 2>/dev/null) || got="curl_error"
  if [ "$got" = "$want" ]; then
    echo "  ok   ${name}  (${got})"
  else
    echo "  FAIL ${name}  expected ${want}, got ${got}"
    fail=$((fail + 1))
  fi
}

echo "대상 ALB: $ALB  (Host: $HOST)"

# 상수 응답 헬스체크 — ALB 타깃이 healthy 여야 200.
check "healthz"            200 "/healthz"
# 인증 필수 라우트가 라우터에 등록돼 있는지. 토큰이 없으니 401 이 정답이다
# (404 면 라우터 미등록 = 플래그/기동 문제, 502·503 이면 태스크가 안 떴다).
check "me/ping 무인증 401"  401 "/v1/me/ping"
# FaceMarket 라우터는 FACEMARKET_ENABLED 가 켜져야 존재한다. 401 = 등록됨.
check "facemarket 라우터"   401 "/v1/facemarket/models"
# 결제 라우터는 항상 등록된다. POST 전용이라 GET 은 405 — 404 가 아니면 등록된 것이다
# (서울 프로덕션에서 실측 확인: 405).
check "payments 라우터"     405 "/v1/payments/toss/checkout"

echo
if [ "$fail" -eq 0 ]; then
  echo "통과 — 컷오버 가능. Cloudflare 에서 ${HOST} CNAME 을 ${ALB} 로 바꾼다."
else
  echo "실패 ${fail}건 — 컷오버 금지. copilot-aws svc logs --name api --env use1 로 확인."
  exit 1
fi
