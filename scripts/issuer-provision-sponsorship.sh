#!/usr/bin/env bash
# 협찬 동의 VC(fmsponsorship-v1) Issuer 프로비저닝 — 얇은 래퍼.
#
# namespace → vc-schema → issue-profile 로직(멱등 skip·TAS list_vc_plan 확인)은
# scripts/issuer-provision-facelicense.sh(-ecs.sh) 를 그대로 쓰고, 스키마 내용만 FL_* 로 바꿔 넘긴다.
# 라이선스(facelicense-v2) 리소스는 읽지도 고치지도 않는다(다른 namespace/schema/plan).
#
# 클레임 id 는 fm-holder IssueVcService.resolveSponsorship 의 userInfo 키와 같아야 한다.
# vcPlanId 는 fm-holder IssuePlan.FMSPONSORSHIP_V1 과 같아야 한다(정확히 20자).
#
# 사용:
#   로컬/docker 호스트: PG_USER=… bash scripts/issuer-provision-sponsorship.sh
#   운영(ECS exec)   : AWS_PROFILE=wearless AWS_REGION=us-east-1 bash scripts/issuer-provision-sponsorship-ecs.sh
#                      DRY_RUN=1 … → 현재 상태만 출력(쓰기 0).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export FL_LABEL="FmSponsorship v1"
export FL_NAMESPACE_ID=${SP_NAMESPACE_ID:-kr.wearless.fmsponsorship.v1}
export FL_NAMESPACE_NAME=${SP_NAMESPACE_NAME:-FmSponsorship v1}
export FL_NAMESPACE_REF=${SP_NAMESPACE_REF:-https://wearless.kr/schema/fmsponsorship-v1}
export FL_VC_SCHEMA=${SP_VC_SCHEMA:-fmsponsorship-v1}
export FL_VC_PLAN=${SP_VC_PLAN:-vcplanspons000000001}   # 정확히 20자 (varchar(20))
export FL_CLAIMS="model_did|Model DID;credential_id|Credential ID;consent_doc_version|Consent Document Version;participation_doc_sha256|Participation Notice SHA256;profile_doc_sha256|Profile Notice SHA256;consented_at|Consented At"
export FL_VC_TITLE="WEARLESS Sponsorship Consent"
export FL_VC_DESCRIPTION="WEARLESS model sponsorship-consent VC for FaceMarket."
export FL_PROFILE_DESCRIPTION="WEARLESS model sponsorship-consent issuance profile."
export FL_TAG="fmsponsorship-v1"
export FL_PLAN_KEY="sponsorship_plan"
export FL_REQUEST_PLAN="fmsponsorship-v1"

if [ "${SP_TARGET:-local}" = ecs ]; then
  exec bash "$HERE/issuer-provision-facelicense-ecs.sh" "$@"
fi
exec bash "$HERE/issuer-provision-facelicense.sh" "$@"
