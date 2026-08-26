#!/usr/bin/env bash
# 서울(ap-northeast-2) 스택 철거. us-east-1 컷오버가 **끝난 뒤에만** 돌린다.
#
# copilot env delete 는 환경 스택(VPC·ALB·클러스터·서비스)만 지운다. ECR 리포지토리와
# KMS 키·아티팩트 버킷은 앱 스택셋의 리전 인스턴스에 있어서 따로 지워야 한다 —
# 서울 ECR 에만 48GB 가 남아 있었다.
#
# 사용: scripts/teardown-seoul.sh            (검사만, 아무것도 지우지 않음)
#       scripts/teardown-seoul.sh --confirm  (실제 삭제)
set -uo pipefail

PROFILE="${AWS_PROFILE:-wearless}"
APP="${APP:-wearless}"
OLD_REGION=ap-northeast-2
OLD_ENV=prod
NEW_REGION=us-east-1
HOST=api.wearless.kr
CONFIRM="${1:-}"

blocked=0
note() { echo "  $*"; }

echo "== 사전 검사 =="

# 1. DNS 가 실제로 us-east-1 ALB 를 가리키는가. 이게 아니면 지우는 순간 서비스가 죽는다.
cname=$(dig +short "$HOST" | head -1)
if echo "$cname" | grep -q "$NEW_REGION"; then
  note "ok   DNS -> $cname"
else
  note "STOP DNS 가 아직 us-east-1 이 아니다: ${cname:-(응답 없음)}"
  blocked=1
fi

# 2. 새 리전 API 가 살아 있는가.
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://${HOST}/healthz" 2>/dev/null) || code=curl_error
if [ "$code" = "200" ]; then note "ok   https://${HOST}/healthz = 200"
else note "STOP healthz = ${code}"; blocked=1; fi

# 3. 서울에서 아직 도는 잡이 있는가. 있으면 지우는 순간 그 잡이 죽는다.
#    (환불은 되지만 셀러 입장에선 실패다 — 끝날 때까지 기다리는 편이 낫다.)
running=$(aws ecs describe-services --profile "$PROFILE" --region "$OLD_REGION" \
  --cluster "$(aws ecs list-clusters --profile "$PROFILE" --region "$OLD_REGION" --query 'clusterArns[0]' --output text)" \
  --services "$(aws ecs list-services --profile "$PROFILE" --region "$OLD_REGION" \
      --cluster "$(aws ecs list-clusters --profile "$PROFILE" --region "$OLD_REGION" --query 'clusterArns[0]' --output text)" \
      --query "serviceArns[?contains(@,'-api-Service')]|[0]" --output text)" \
  --query 'services[0].runningCount' --output text 2>/dev/null)
note "info 서울 api 태스크 ${running:-?}대 (0 으로 내린 뒤 지우는 것을 권장)"

# 4. opendid 이미지가 새 리전에 복사돼 있는가. 이 저장소로는 재빌드가 안 되므로
#    서울 ECR 이 유일본이면 삭제와 함께 영구 소실된다.
if aws ecr describe-images --profile "$PROFILE" --region "$NEW_REGION" \
     --repository-name wearless/opendid --query 'imageDetails[0].imageDigest' --output text >/dev/null 2>&1; then
  note "ok   opendid 이미지가 ${NEW_REGION} ECR 에 있다"
else
  note "STOP opendid 이미지가 ${NEW_REGION} 에 없다 — 지우면 복구 불가(레포로 재빌드 불가)"
  blocked=1
fi

echo
if [ "$blocked" -ne 0 ]; then
  echo "차단됨 — 위 STOP 항목을 먼저 해결할 것."
  exit 1
fi
if [ "$CONFIRM" != "--confirm" ]; then
  echo "검사 통과. 실제로 지우려면: $0 --confirm"
  echo
  echo "지울 대상:"
  echo "  1) copilot svc delete --env ${OLD_ENV}    (서비스별 — env delete 의 전제)"
  echo "  2) copilot env delete --name ${OLD_ENV}   (VPC·ALB·클러스터)"
  echo "  3) ${OLD_REGION} ECR 리포 4개            (약 48GB)"
  echo "  4) 앱 스택셋의 ${OLD_REGION} 인스턴스     (KMS 키·아티팩트 버킷)"
  echo "  5) ${OLD_REGION} SSM 시크릿              (us-east-1 에 복사본 있음)"
  exit 0
fi

echo "== 삭제 실행 =="

# 순서가 강제된다. `env delete` 는 그 환경에 서비스가 남아 있으면
#   ✘ service "api, opendid, sam2" still exist within the environment prod
# 로 **거부한다**(2026-08-26 실측). 예전 이 스크립트는 그 실패를 무시하고 다음 단계로
# 넘어가 ECR·SSM 을 먼저 지웠고, 환경은 남았는데 이미지도 시크릿도 없는 상태가 됐다.
# 그래서 서비스부터 지우고, 한 단계라도 실패하면 멈춘다.
#
# --env 를 반드시 준다. 빼면 **모든 환경**에서 그 서비스를 지운다 — 지금 서비스 중인
# us-east-1 이 함께 날아간다.
echo "-- 1) 서비스 (환경 한정)"
for n in api sam2 opendid detail-worker; do
  copilot-aws svc ls --app "$APP" 2>/dev/null | grep -q "^${n}\b" || continue
  echo "   $n"
  copilot-aws svc delete --app "$APP" --name "$n" --env "$OLD_ENV" --yes \
    || { echo "  STOP: $n 삭제 실패 — 여기서 멈춘다(뒤 단계가 복구 수단을 지운다)"; exit 1; }
done

echo "-- 2) 환경 스택"
copilot-aws env delete --app "$APP" --name "$OLD_ENV" --yes \
  || { echo "  STOP: 환경 삭제 실패 — ECR·SSM 은 건드리지 않고 멈춘다"; exit 1; }

echo "-- 3) ECR 리포"
for r in wearless/api wearless/sam2 wearless/opendid wearless/detail-worker; do
  aws ecr delete-repository --profile "$PROFILE" --region "$OLD_REGION" \
    --repository-name "$r" --force --query 'repository.repositoryName' --output text 2>/dev/null \
    || echo "  skip $r"
done

echo "-- 4) 스택셋 리전 인스턴스"
aws cloudformation delete-stack-instances --profile "$PROFILE" --region "$OLD_REGION" \
  --stack-set-name "${APP}-infrastructure" \
  --accounts "$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)" \
  --regions "$OLD_REGION" --no-retain-stacks \
  --operation-preferences FailureToleranceCount=0 --query 'OperationId' --output text 2>/dev/null \
  || echo "  skip (스택셋 인스턴스 없음)"

echo "-- 5) SSM 시크릿"
aws ssm get-parameters-by-path --profile "$PROFILE" --region "$OLD_REGION" \
  --path "/copilot/${APP}/${OLD_ENV}/secrets/" --recursive --query 'Parameters[].Name' --output text \
  | tr '\t' '\n' | while read -r n; do
      [ -z "$n" ] && continue
      aws ssm delete-parameter --profile "$PROFILE" --region "$OLD_REGION" --name "$n" 2>/dev/null \
        && echo "  deleted $(basename "$n")"
    done

echo
echo "완료. 확인: aws ce get-cost-and-usage 로 다음 청구 주기에 서울 비용이 0 인지 본다."
