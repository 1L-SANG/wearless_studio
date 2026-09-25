#!/usr/bin/env bash
# 협찬 동의 VC(fmsponsorship-v1) Issuer 프로비저닝 — ECS(Fargate) 운영 환경용.
# 스키마 값은 scripts/issuer-provision-sponsorship.sh, 실행 경로는 scripts/issuer-provision-facelicense-ecs.sh.
#   AWS_PROFILE=wearless AWS_REGION=us-east-1 DRY_RUN=1 bash scripts/issuer-provision-sponsorship-ecs.sh
set -euo pipefail
SP_TARGET=ecs exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/issuer-provision-sponsorship.sh" "$@"
