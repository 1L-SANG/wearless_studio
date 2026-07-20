#!/usr/bin/env bash
# FaceMarket 온체인 정산 프로드 시크릿 프로비저닝 (P0 — copilot/api/manifest.yml secrets 블록과 짝).
#
# 왜 스크립트인가: FM_CHAIN_RPC_URL 은 쿼리스트링에 게이트웨이 auth 토큰이 박혀 있어 URL 전체가
# 시크릿이고, FM_CHAIN_PRIVATE_KEY 는 정산 컨트랙트 owner 개인키다. 값을 셸 히스토리·채팅에
# 노출하지 않도록 server/.env.local 에서 직접 읽어 SSM 에 넣는다.
#
# 실행: ./scripts/fm-chain-secrets.sh
# 선행: server/.env.local 에 FM_CHAIN_RPC_URL / FM_CHAIN_PRIVATE_KEY 존재, AWS profile 'wearless'.
# 순서: 이 스크립트 실행 → manifest 커밋 push (deploy-server.yml 이 copilot svc deploy).
#      순서 어기면 ECS 태스크가 SSM 파라미터를 못 찾아 기동 실패한다.
set -euo pipefail

PROFILE=wearless
REGION=ap-northeast-2
PREFIX=/copilot/wearless/prod/secrets

cd "$(dirname "$0")/.."
[[ -f server/.env.local ]] || { echo "server/.env.local 없음" >&2; exit 1; }
set -a; source server/.env.local; set +a

for NAME in FM_CHAIN_RPC_URL FM_CHAIN_PRIVATE_KEY; do
  VAL="${!NAME:-}"
  [[ -n "$VAL" ]] || { echo "$NAME 이 server/.env.local 에 없음" >&2; exit 1; }
  if aws ssm put-parameter --profile "$PROFILE" --region "$REGION" \
      --name "$PREFIX/$NAME" --type SecureString --value "$VAL" \
      --tags Key=copilot-application,Value=wearless Key=copilot-environment,Value=prod \
      --output text --query Version >/dev/null 2>&1; then
    echo "created $PREFIX/$NAME"
  else
    # 이미 존재 → 값 갱신 + 태그 보정 (put-parameter 는 --overwrite 와 --tags 동시 불가)
    aws ssm put-parameter --profile "$PROFILE" --region "$REGION" \
      --name "$PREFIX/$NAME" --type SecureString --value "$VAL" \
      --overwrite --output text --query Version >/dev/null
    aws ssm add-tags-to-resource --profile "$PROFILE" --region "$REGION" \
      --resource-type Parameter --resource-id "$PREFIX/$NAME" \
      --tags Key=copilot-application,Value=wearless Key=copilot-environment,Value=prod
    echo "updated $PREFIX/$NAME"
  fi
done

echo "done — 이제 manifest 변경을 main 에 push 하면 deploy-server.yml 이 배포한다."
