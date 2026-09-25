#!/usr/bin/env bash
# FaceLicense v2 Issuer 프로비저닝 — **ECS(Fargate) 운영 환경용**.
#
# scripts/issuer-provision-facelicense.sh 와 같은 리소스(namespace → vc-schema → issue-profile)를
# 같은 본문으로 만든다. 차이는 실행 경로 하나뿐이다:
#   - 원본은 배포 호스트의 docker postgres(psql) + localhost Issuer admin API 를 전제한다.
#   - 운영 opendid 는 Fargate 태스크 + RDS 라 psql 도 docker 도 없다. 대신 `aws ecs execute-command`
#     로 태스크 안의 curl 을 빌려 쓰고, 존재 확인은 psql 대신 Issuer/TAS admin GET 으로 한다.
# 멱등: 각 리소스는 이미 있으면 건너뛴다. 기존 v1(kr.wearless.facelicense/facelicense/
# vcplanface0000000001)은 읽기만 하고 절대 고치지 않는다(런북: v1 서명 크리덴셜 보존).
#
# 사용:
#   AWS_PROFILE=wearless AWS_REGION=us-east-1 bash scripts/issuer-provision-facelicense-ecs.sh
#   DRY_RUN=1 … → 현재 상태만 출력(쓰기 0).
# 전제: session-manager-plugin, python3, opendid 태스크가 1대 떠 있을 것(exec: true).
set -euo pipefail

CLUSTER=${ECS_CLUSTER:-}
TASK=${ECS_TASK:-}
CONTAINER=${ECS_CONTAINER:-opendid}
SERVICE_HINT=${ECS_SERVICE_HINT:-opendid}
ISSUER=${ISSUER_ADMIN:-http://localhost:8091/issuer/admin/v1}
TAS=${TAS_ADMIN:-http://localhost:8090/list/admin/v1}
DRY_RUN=${DRY_RUN:-0}

# v1 signed credentials keep their original namespace/schema. Never rewrite them.
NS_ID_STR=${FL_NAMESPACE_ID:-kr.wearless.facelicense.v2}
NS_NAME=${FL_NAMESPACE_NAME:-FaceLicense v2}
NS_REF=${FL_NAMESPACE_REF:-https://wearless.kr/schema/facelicense-v2}
VC_SCHEMA=${FL_VC_SCHEMA:-facelicense-v2}
VC_PLAN=${FL_VC_PLAN:-vcplanface0000000002}   # 정확히 20자 (varchar(20))

# 스키마 내용 — 기본값 = FaceLicense v2. 다른 VC(협찬 등)는 얇은 래퍼가 FL_* 를 바꿔 이 스크립트를 재사용한다.
# FL_CLAIMS = "claim_id|Caption;..." (전부 type=text/format=plain/location=inline/required=true)
LABEL=${FL_LABEL:-FaceLicense v2}
CLAIMS=${FL_CLAIMS:-model_did|Model DID;license_id|License ID;issued_at|Issued At;face_image_digest|Face Image Digest;agreement_version|Agreement Version;consent_doc_version|Consent Document Version}
VC_TITLE=${FL_VC_TITLE:-WEARLESS Face License}
VC_DESC=${FL_VC_DESCRIPTION:-WEARLESS model face-license VC for FaceMarket.}
PROFILE_DESC=${FL_PROFILE_DESCRIPTION:-WEARLESS model face-license issuance profile.}
TAG=${FL_TAG:-facelicense-v2}
PLAN_KEY=${FL_PLAN_KEY:-facelicense_plan}
REQUEST_PLAN=${FL_REQUEST_PLAN:-facelicense-v2}

if [ "${#VC_PLAN}" -ne 20 ]; then
  echo "ERROR: vcPlanId '$VC_PLAN' 는 ${#VC_PLAN}자 — issue_profile.vc_plan_id 는 varchar(20) 필수." >&2
  exit 1
fi

# FL_CLAIMS → namespace items JSON. id/caption 은 JSON 에 그대로 박히므로 안전한 글자만 허용.
claim_items_json() {
  local out="" entry id caption
  local IFS=';'
  for entry in $CLAIMS; do
    id=${entry%%|*}; caption=${entry#*|}
    [[ "$id" =~ ^[a-z0-9_]+$ ]] && [[ "$caption" =~ ^[A-Za-z0-9\ ]+$ ]] && [ "$id" != "$entry" ] || {
      echo "ERROR: FL_CLAIMS 항목 '$entry' 형식 오류 (claim_id|Caption)" >&2; return 1; }
    [ -z "$out" ] || out+=","
    out+="{\"id\":\"$id\",\"caption\":\"$caption\",\"type\":\"text\",\"format\":\"plain\",\"hideValue\":false,\"location\":\"inline\",\"required\":true}"
  done
  [ -n "$out" ] || { echo "ERROR: FL_CLAIMS 가 비었다" >&2; return 1; }
  printf '[%s]' "$out"
}
ITEMS_JSON=$(claim_items_json)

# --- 태스크 자동 탐색 -------------------------------------------------------------
if [ -z "$CLUSTER" ]; then
  CLUSTER=$(aws ecs list-clusters --query "clusterArns[?contains(@,'wearless')]|[0]" --output text)
fi
if [ -z "$TASK" ]; then
  SERVICE=$(aws ecs list-services --cluster "$CLUSTER" --query "serviceArns[?contains(@,'$SERVICE_HINT')]|[0]" --output text)
  TASK=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "${SERVICE##*/}" --desired-status RUNNING \
           --query 'taskArns[0]' --output text)
fi
[ -n "$TASK" ] && [ "$TASK" != "None" ] || { echo "ERROR: opendid RUNNING 태스크가 없다 — 먼저 깨워라(desired=1)." >&2; exit 1; }
echo "==> $LABEL Issuer 프로비저닝 via ECS exec (cluster=${CLUSTER##*/} task=${TASK##*/} dry_run=$DRY_RUN)"

# --- 원격 curl --------------------------------------------------------------------
# execute-command 출력은 세션 배너(첫 줄 + 빈 줄)와 "Exiting session…" 꼬리가 붙는다 → 벗겨서 본문만.
remote() {
  aws ecs execute-command --cluster "$CLUSTER" --task "$TASK" --container "$CONTAINER" \
    --interactive --command "$1" 2>&1 | sed '1,/^$/d' | sed '/^Exiting session with sessionId/d'
}
# 세션이 가끔 중간에 끊긴다("Cannot perform start session: EOF") → 완전한 JSON 이 올 때까지 최대 5회.
rget() {
  local out n
  for n in 1 2 3 4 5; do
    # 한 줄짜리 긴 JSON(TAS 응답)은 세션이 ~1KB 에서 끊긴다 → 원격에서 줄을 나눠 받는다.
    # JSON 문자열엔 raw 개행이 없으니 로컬에서 개행을 전부 지우면 원문과 같다.
    out=$(remote "sh -c \"curl -s '$1' | fold -w 300\"")
    if printf '%s' "$out" | python3 -c '
import json,sys
raw=sys.stdin.read().replace("\r","").replace("\n",""); i=raw.find("{")
sys.exit(1 if i<0 else 0) if i<0 else None
json.JSONDecoder().raw_decode(raw[i:])' 2>/dev/null; then
      printf '%s' "$out"; return 0
    fi
    echo "    (세션 끊김 — GET 재시도 $n/5)" >&2; sleep 2
  done
  echo "ERROR: GET $1 — 5회 모두 불완전한 응답" >&2; return 1
}
# POST 본문은 base64 로 실어 원격 sh 의 따옴표 지옥을 피한다. 응답 코드만 돌려받는다.
rpost() {
  local url=$1 body=$2 b64
  b64=$(printf '%s' "$body" | base64 | tr -d '\n')
  remote "sh -c 'echo $b64 | base64 -d | curl -s -X POST \"$url\" -H \"Content-Type: application/json\" --data-binary @- -o /dev/null -w %{http_code}'" | tr -d '[:space:]'
}
# JSON 파싱은 로컬 python3 (태스크 이미지엔 jq/python 없음).
jid() {  # jid <json> <field> <value> → content[] 중 field==value 인 항목의 id (없으면 빈 문자열)
  # 세션 출력은 CRLF 이고 앞뒤에 배너/"Cannot perform start session: EOF" 같은 꼬리가 붙는다 →
  # 첫 '{' 부터 raw_decode 로 완전한 객체 하나만 읽고 나머지는 무시한다.
  python3 -c '
import json,sys
raw=sys.stdin.read().replace("\r","").replace("\n",""); i=raw.find("{")
if i<0: sys.exit(0)
obj,_=json.JSONDecoder().raw_decode(raw[i:])
for x in obj.get("content",[]):
    if str(x.get(sys.argv[1]))==sys.argv[2]:
        print(x.get("id")); break
' "$2" "$3" <<<"$1"
}

# 1/3 namespace (POST body = SchemaClaims: {namespace, items[]}, 각 claim type=text/format=plain/location=inline)
NS_ID=$(jid "$(rget "$ISSUER/namespaces?searchKey=namespaceId&searchValue=$NS_ID_STR&size=100")" namespaceId "$NS_ID_STR")
if [ -z "$NS_ID" ]; then
  echo "--> 1/3 namespace 생성: $NS_ID_STR"
  if [ "$DRY_RUN" = 1 ]; then echo "    (dry-run) skip"; else
  code=$(rpost "$ISSUER/namespaces" "{
    \"namespace\": {\"id\":\"$NS_ID_STR\",\"name\":\"$NS_NAME\",\"ref\":\"$NS_REF\"},
    \"items\": $ITEMS_JSON
  }")
  [ "$code" = 200 ] || [ "$code" = 201 ] || echo "    경고: namespace POST http='$code' — 세션이 끊겼을 수 있어 재조회로 판정한다" >&2
  NS_ID=$(jid "$(rget "$ISSUER/namespaces?searchKey=namespaceId&searchValue=$NS_ID_STR&size=100")" namespaceId "$NS_ID_STR")
  echo "    namespace id=$NS_ID"
  fi
else
  echo "--> 1/3 namespace 이미 존재 (id=$NS_ID) — skip"
fi
[ "$DRY_RUN" = 1 ] && [ -z "$NS_ID" ] && { echo "(dry-run) 이후 단계는 namespace 생성 뒤에 결정된다."; exit 0; }
[ -n "$NS_ID" ] || { echo "ERROR: namespace 생성/조회 실패 — Issuer 로그 확인" >&2; exit 1; }

# 2/3 vc-schema (POST body = VcSchemaReqDto{namespaces:[nsId], vcSchemaId, ...})
VS_ID=$(jid "$(rget "$ISSUER/vc-schemas?searchKey=vcSchemaId&searchValue=$VC_SCHEMA&size=100")" vcSchemaId "$VC_SCHEMA")
if [ -z "$VS_ID" ]; then
  echo "--> 2/3 vc-schema 생성: $VC_SCHEMA (namespace=$NS_ID)"
  if [ "$DRY_RUN" = 1 ]; then echo "    (dry-run) skip"; exit 0; fi
  code=$(rpost "$ISSUER/vc-schemas" "{
    \"namespaces\": [$NS_ID],
    \"vcSchemaId\": \"$VC_SCHEMA\",
    \"title\": \"$VC_TITLE\",
    \"description\": \"$VC_DESC\",
    \"language\": \"ko\",
    \"version\": \"2.0\"
  }")
  [ "$code" = 200 ] || [ "$code" = 201 ] || echo "    경고: vc-schema POST http='$code' — 세션이 끊겼을 수 있어 재조회로 판정한다" >&2
  VS_ID=$(jid "$(rget "$ISSUER/vc-schemas?searchKey=vcSchemaId&searchValue=$VC_SCHEMA&size=100")" vcSchemaId "$VC_SCHEMA")
  echo "    vc_schema id=$VS_ID"
else
  echo "--> 2/3 vc-schema 이미 존재 (id=$VS_ID) — skip"
fi
[ -n "$VS_ID" ] || { echo "ERROR: vc_schema 생성/조회 실패 — Issuer 로그 확인" >&2; exit 1; }

# 3/3 issue-profile (POST body = CreateIssueProfileReqDto; initiateType 소문자 issuer_init → enum ISSUER_INIT)
# issue-profiles 검색은 title 만 지원 → 전체 목록에서 vcPlanId 로 고른다.
IP_ID=$(jid "$(rget "$ISSUER/issue-profiles?size=200")" vcPlanId "$VC_PLAN")
if [ -z "$IP_ID" ]; then
  echo "--> 3/3 issue-profile 생성: $VC_PLAN (vc_schema=$VS_ID)"
  if [ "$DRY_RUN" = 1 ]; then echo "    (dry-run) skip"; exit 0; fi
  code=$(rpost "$ISSUER/issue-profiles" "{
    \"vcPlanId\": \"$VC_PLAN\",
    \"title\": \"$VC_TITLE\",
    \"description\": \"$PROFILE_DESC\",
    \"vcSchemaId\": $VS_ID,
    \"language\": \"ko\",
    \"endpoints\": [\"http://127.0.0.1\"],
    \"cipher\": \"AES-256-CBC\",
    \"curve\": \"Secp256r1\",
    \"padding\": \"PKCS5\",
    \"initiateType\": \"issuer_init\",
    \"tags\": [\"$TAG\"],
    \"zkpEnabled\": false
  }")
  [ "$code" = 200 ] || [ "$code" = 201 ] || echo "    경고: issue-profile POST http='$code' — 세션이 끊겼을 수 있어 재조회로 판정한다" >&2
  IP_ID=$(jid "$(rget "$ISSUER/issue-profiles?size=200")" vcPlanId "$VC_PLAN")
  echo "    issue_profile id=$IP_ID (list_vc_plan 자동 등록 → TAS offer/propose 가능)"
else
  echo "--> 3/3 issue-profile 이미 존재 (id=$IP_ID) — skip"
fi
[ -n "$IP_ID" ] || { echo "ERROR: issue_profile 생성/조회 실패 — Issuer 로그 확인" >&2; exit 1; }

echo "==> 검증"
echo "  namespace     : $NS_ID ok"
echo "  vc_schema     : $VS_ID ok"
echo "  issue_profile : $IP_ID ok"
# list_vc_plan 은 TAS(List Community). issue-profile POST 가 여기에 자동 등록한다.
LVP=$(jid "$(rget "$TAS/vc-plans/list?size=200")" vcPlanId "$VC_PLAN")
[ -n "$LVP" ] || { echo "${PLAN_KEY}=missing"; exit 1; }
echo "${PLAN_KEY}=present"
echo "완료. plan=$VC_PLAN 로 $LABEL VC 발급 가능."
echo "홀더: POST /holder/models/{id}/issue-vc  body={\"plan\":\"$REQUEST_PLAN\",\"claims\":{...}}"
