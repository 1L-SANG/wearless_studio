#!/usr/bin/env bash
# `copilot svc deploy --detach` 다음에 그 서비스 스택이 끝날 때까지 직접 기다린다.
#
# 왜: Copilot v1.34.1 의 진행 표시는 2026-09-28 부터 끝나지 않을 수 있다. CloudFormation 이 ECS 서비스에
# "Eventual consistency check initiated" UPDATE_IN_PROGRESS 이벤트를 하나 더 보내면 Copilot 이 그걸 새
# 배포로 착각해 기다리다가, 1시간 뒤 OIDC 자격증명이 만료되며 ExpiredToken 으로 실패한다(2026-10-02 두 번
# 실측 — 스택은 이미 UPDATE_COMPLETE). Copilot 은 2026-06-12 지원 종료라 공식 수정이 없다.
#
# CloudFormation 의 ECS Service 업데이트는 ECS 배포가 안정될 때까지(서킷브레이커 포함) 끝나지 않으므로
# 스택이 UPDATE_COMPLETE 면 롤링도 끝난 것이다. 롤백·실패로 끝나면 빨간불로 끝낸다.
#
# 사용: scripts/ci/wait-copilot-stack.sh <stack-name> <배포 직전 UTC 시각 YYYY-MM-DDTHH:MM:SS>
set -uo pipefail

STACK="${1:?stack name (예: wearless-use1-api)}"
STARTED="${2:?배포 직전 UTC 시각 (date -u +%Y-%m-%dT%H:%M:%S)}"
REGION="${STACK_REGION:-us-east-1}"   # 앱 메타데이터는 서울, 환경 리소스는 us-east-1
MAX_POLLS="${MAX_POLLS:-240}"          # 15초 × 240 = 60분
NOOP_POLLS="${NOOP_POLLS:-8}"          # 2분 동안 갱신이 시작되지 않으면 변경 없음으로 본다

stale=0
for ((i = 1; i <= MAX_POLLS; i++)); do
  out=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
        --query 'Stacks[0].[StackStatus,LastUpdatedTime]' --output text 2>&1) || {
    echo "describe-stacks 실패(재시도): $out"; sleep 15; continue; }
  status=$(awk '{print $1}' <<<"$out")
  updated=$(awk '{print $2}' <<<"$out")
  case "$status" in
    *ROLLBACK*|*FAILED*)
      echo "::error::$STACK 이 $status 로 끝났다 — 배포 실패(이벤트: aws cloudformation describe-stack-events --region $REGION --stack-name $STACK)"
      exit 1 ;;
    *_IN_PROGRESS)
      echo "$(date -u +%H:%M:%S) $STACK $status"
      stale=0 ;;
    UPDATE_COMPLETE|CREATE_COMPLETE)
      # 실행 직후엔 직전 배포의 완료 상태가 잠깐 보인다 — 이번 배포가 만든 갱신인지 시각으로 가린다.
      if [[ "$updated" > "$STARTED" ]]; then
        echo "$STACK $status ($updated)"
        exit 0
      fi
      stale=$((stale + 1))
      if (( stale >= NOOP_POLLS )); then
        echo "$STACK 갱신이 시작되지 않았다 — 변경 없음으로 본다($status, 마지막 갱신 $updated)"
        exit 0
      fi ;;
    *)
      echo "$(date -u +%H:%M:%S) $STACK $status" ;;
  esac
  sleep 15
done
echo "::error::$STACK 이 $((MAX_POLLS * 15 / 60))분 안에 끝나지 않았다"
exit 1
