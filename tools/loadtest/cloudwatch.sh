#!/usr/bin/env bash
# k6 실행 시간창의 서버 쪽 지표 — ECS api 태스크 CPU/메모리 + ALB 응답시간·5xx.
#   tools/loadtest/cloudwatch.sh <start ISO8601 UTC> <end ISO8601 UTC>
#   예: tools/loadtest/cloudwatch.sh 2026-09-22T11:00:00Z 2026-09-22T11:05:00Z
# 1분 해상도(CloudWatch 기본)라 30초짜리 스파이크는 평균에 묻힌다 — Maximum 도 같이 뽑는다.
set -euo pipefail
START=${1:?start}; END=${2:?end}
P=${AWS_PROFILE:-wearless}; R=us-east-1
CLUSTER=wearless-use1-Cluster-hYAqwp9HcIdj
SERVICE=wearless-use1-api-Service-QliObqw9TgGI
ALB=app/wearle-Publi-n7eZ27fYallL/af884ba3230772c6
TG=targetgroup/wearle-Targe-M9FXEDYQXDTP/e3a9cf283e94f549

q() { # namespace metric dims stat — stat 이 p50/p95 면 extended-statistics 로 간다
  local statflag=--statistics field="$4"
  case "$4" in p*) statflag=--extended-statistics; field="ExtendedStatistics.\"$4\"";; esac
  aws --profile "$P" --region "$R" cloudwatch get-metric-statistics \
    --namespace "$1" --metric-name "$2" --dimensions $3 \
    --start-time "$START" --end-time "$END" --period 60 $statflag "$4" \
    --query "sort_by(Datapoints,&Timestamp)[].[Timestamp,$field]" --output text 2>/dev/null \
    | awk -v n="$2" -v s="$4" '{printf "%-28s %-14s %s\n", n"("s")", substr($1,12,8), $2}'
}
echo "== ECS api ($START → $END) =="
q AWS/ECS CPUUtilization "Name=ClusterName,Value=$CLUSTER Name=ServiceName,Value=$SERVICE" Average
q AWS/ECS CPUUtilization "Name=ClusterName,Value=$CLUSTER Name=ServiceName,Value=$SERVICE" Maximum
q AWS/ECS MemoryUtilization "Name=ClusterName,Value=$CLUSTER Name=ServiceName,Value=$SERVICE" Maximum
echo "== ALB → api target =="
q AWS/ApplicationELB RequestCount "Name=LoadBalancer,Value=$ALB Name=TargetGroup,Value=$TG" Sum
q AWS/ApplicationELB TargetResponseTime "Name=LoadBalancer,Value=$ALB Name=TargetGroup,Value=$TG" p50
q AWS/ApplicationELB TargetResponseTime "Name=LoadBalancer,Value=$ALB Name=TargetGroup,Value=$TG" p95
q AWS/ApplicationELB HTTPCode_Target_5XX_Count "Name=LoadBalancer,Value=$ALB Name=TargetGroup,Value=$TG" Sum
q AWS/ApplicationELB HTTPCode_ELB_5XX_Count "Name=LoadBalancer,Value=$ALB" Sum
