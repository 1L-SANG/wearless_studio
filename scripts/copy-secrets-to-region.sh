#!/usr/bin/env bash
# Copilot SSM 시크릿을 리전/환경 사이로 복사한다.
#
# 값은 절대 출력하지 않는다 — 이름과 성공/실패만 찍는다. Copilot 은 태그로 파라미터
# 소유권을 판정하므로(copilot-application/-environment) 태그를 반드시 같이 단다.
# raw put-parameter 로 만든 태그 없는 파라미터는 태스크가 읽지 못한다(2026-07-17 실경험).
#
# 사용: scripts/copy-secrets-to-region.sh <src-region> <src-env> <dst-region> <dst-env>
#   예: scripts/copy-secrets-to-region.sh ap-northeast-2 prod us-east-1 use1
set -euo pipefail

SRC_REGION="${1:?src region}"; SRC_ENV="${2:?src env}"
DST_REGION="${3:?dst region}"; DST_ENV="${4:?dst env}"
APP="${APP:-wearless}"
PROFILE="${AWS_PROFILE:-wearless}"

SRC_PREFIX="/copilot/${APP}/${SRC_ENV}/secrets/"
DST_PREFIX="/copilot/${APP}/${DST_ENV}/secrets/"

echo "복사: ${SRC_REGION}${SRC_PREFIX}  ->  ${DST_REGION}${DST_PREFIX}"

names=$(aws ssm get-parameters-by-path \
  --profile "$PROFILE" --region "$SRC_REGION" \
  --path "$SRC_PREFIX" --recursive \
  --query 'Parameters[].Name' --output text | tr '\t' '\n')

[ -z "$names" ] && { echo "원본에 파라미터가 없다 — 경로를 확인할 것"; exit 1; }

copied=0
for name in $names; do
  short="${name#"$SRC_PREFIX"}"
  dst="${DST_PREFIX}${short}"

  # --with-decryption 값은 변수에만 담고 어디에도 echo 하지 않는다.
  value=$(aws ssm get-parameter --profile "$PROFILE" --region "$SRC_REGION" \
            --name "$name" --with-decryption --query 'Parameter.Value' --output text)

  aws ssm put-parameter --profile "$PROFILE" --region "$DST_REGION" \
    --name "$dst" --type SecureString --value "$value" --overwrite >/dev/null

  # put-parameter --overwrite 는 태그를 지운다. 항상 다시 단다.
  aws ssm add-tags-to-resource --profile "$PROFILE" --region "$DST_REGION" \
    --resource-type Parameter --resource-id "$dst" \
    --tags "Key=copilot-application,Value=${APP}" \
           "Key=copilot-environment,Value=${DST_ENV}" >/dev/null

  unset value
  echo "  ok  ${short}"
  copied=$((copied + 1))
done

echo "완료: ${copied}개"
echo "확인: aws ssm get-parameters-by-path --region ${DST_REGION} --path ${DST_PREFIX} --query 'length(Parameters)'"
