#!/usr/bin/env bash
# FaceLicense Issuer 프로비저닝 (선택과제1 — 커스텀 VC). MDL 과 동일 방식으로 dev Issuer(:8091)
# admin API(permitAll)에 namespace → vc-schema → issue-profile 을 등록한다. issue-profile POST 는
# 발급 프로필을 저장할 뿐 아니라 List Community(TAS)의 list_vc_plan 에도 자동 등록하므로,
# 이 스크립트만으로 offer/propose-issue-vc(TAS) → issue-vc(Issuer) 전 구간이 발급 가능해진다.
#
# dev-DB(besu+postgres) 리셋 후 재실행해도 안전(멱등): 각 리소스는 이미 있으면 건너뛴다.
# 실측 근거: MDL 은 namespace `org.iso.18013.5.1` + vc_schema `mdl` + issue_profile
# `vcplanid000000000001` 로 프로비저닝돼 실 VC 가 발급된다(.omc/research/flowb-issuevc-findings.md).
#
# 주의: vc_plan_id 컬럼 = varchar(20). FaceLicense plan 은 정확히 20자여야 한다(vcplanface0000000001).
set -euo pipefail

ADMIN=${ISSUER_ADMIN:-http://localhost:8091/issuer/admin/v1}
PGC=${PG_CONTAINER:-postgre-opendid}
PGUSER=${PG_USER:-${OPENDID_POSTGRES_USER:-}}
PGDB=${PG_DB:-issuer}

NS_ID_STR=${FL_NAMESPACE_ID:-kr.wearless.facelicense}
NS_NAME=${FL_NAMESPACE_NAME:-FaceLicense}
NS_REF=${FL_NAMESPACE_REF:-https://wearless.kr/schema/facelicense}
VC_SCHEMA=${FL_VC_SCHEMA:-facelicense}
VC_PLAN=${FL_VC_PLAN:-vcplanface0000000001}   # 정확히 20자 (varchar(20))

[ -n "$PGUSER" ] || { echo "PG_USER=missing" >&2; exit 1; }

q() {
  local db=$1 sql=$2
  shift 2
  printf '%s\n' "$sql" | docker exec -i "$PGC" psql -X -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$db" "$@" -tA 2>/dev/null | tr -d '[:space:]'
}
q_issuer() { q "$PGDB" "$@"; }
q_tas() { q tas "$@"; }

if [ "${#VC_PLAN}" -ne 20 ]; then
  echo "ERROR: vcPlanId '$VC_PLAN' 는 ${#VC_PLAN}자 — issue_profile.vc_plan_id 는 varchar(20) 필수." >&2
  exit 1
fi

echo "==> FaceLicense Issuer 프로비저닝 (admin=$ADMIN, db=$PGC/$PGDB)"

# 1/3 namespace (POST body = SchemaClaims: {namespace, items[]}, 각 claim type=text/format=plain/location=inline)
NS_ID=$(q_issuer "SELECT id FROM namespace WHERE namespace_id = :'namespace_id';" -v "namespace_id=$NS_ID_STR")
if [ -z "$NS_ID" ]; then
  echo "--> 1/3 namespace 생성: $NS_ID_STR"
  curl -sf -X POST "$ADMIN/namespaces" -H 'Content-Type: application/json' -d "{
    \"namespace\": {\"id\":\"$NS_ID_STR\",\"name\":\"$NS_NAME\",\"ref\":\"$NS_REF\"},
    \"items\": [
      {\"id\":\"allowed_use\",\"caption\":\"Allowed Use\",\"type\":\"text\",\"format\":\"plain\",\"hideValue\":false,\"location\":\"inline\",\"required\":false},
      {\"id\":\"forbidden_use\",\"caption\":\"Forbidden Use\",\"type\":\"text\",\"format\":\"plain\",\"hideValue\":false,\"location\":\"inline\",\"required\":false},
      {\"id\":\"unit_price\",\"caption\":\"Unit Price\",\"type\":\"text\",\"format\":\"plain\",\"hideValue\":false,\"location\":\"inline\",\"required\":false},
      {\"id\":\"license_valid_until\",\"caption\":\"License Valid Until\",\"type\":\"text\",\"format\":\"plain\",\"hideValue\":false,\"location\":\"inline\",\"required\":false},
      {\"id\":\"face_image_digest\",\"caption\":\"Face Image Digest\",\"type\":\"text\",\"format\":\"plain\",\"hideValue\":false,\"location\":\"inline\",\"required\":false},
      {\"id\":\"model_name\",\"caption\":\"Model Name\",\"type\":\"text\",\"format\":\"plain\",\"hideValue\":false,\"location\":\"inline\",\"required\":false}
    ]
  }" >/dev/null
  NS_ID=$(q_issuer "SELECT id FROM namespace WHERE namespace_id = :'namespace_id';" -v "namespace_id=$NS_ID_STR")
  echo "    namespace id=$NS_ID"
else
  echo "--> 1/3 namespace 이미 존재 (id=$NS_ID) — skip"
fi
[ -n "$NS_ID" ] || { echo "ERROR: namespace id 조회 실패" >&2; exit 1; }

# 2/3 vc-schema (POST body = VcSchemaReqDto{namespaces:[nsId], vcSchemaId, ...})
VS_ID=$(q_issuer "SELECT id FROM vc_schema WHERE vc_schema_id = :'vc_schema';" -v "vc_schema=$VC_SCHEMA")
if [ -z "$VS_ID" ]; then
  echo "--> 2/3 vc-schema 생성: $VC_SCHEMA (namespace=$NS_ID)"
  curl -sf -X POST "$ADMIN/vc-schemas" -H 'Content-Type: application/json' -d "{
    \"namespaces\": [$NS_ID],
    \"vcSchemaId\": \"$VC_SCHEMA\",
    \"title\": \"WEARLESS Face License\",
    \"description\": \"WEARLESS model face-license VC for FaceMarket.\",
    \"language\": \"ko\",
    \"version\": \"1.0\"
  }" >/dev/null
  VS_ID=$(q_issuer "SELECT id FROM vc_schema WHERE vc_schema_id = :'vc_schema';" -v "vc_schema=$VC_SCHEMA")
  echo "    vc_schema id=$VS_ID"
else
  echo "--> 2/3 vc-schema 이미 존재 (id=$VS_ID) — skip"
fi
[ -n "$VS_ID" ] || { echo "ERROR: vc_schema id 조회 실패" >&2; exit 1; }

# 3/3 issue-profile (POST body = CreateIssueProfileReqDto; initiateType 소문자 issuer_init → enum ISSUER_INIT)
IP_ID=$(q_issuer "SELECT id FROM issue_profile WHERE vc_plan_id = :'vc_plan';" -v "vc_plan=$VC_PLAN")
if [ -z "$IP_ID" ]; then
  echo "--> 3/3 issue-profile 생성: $VC_PLAN (vc_schema=$VS_ID)"
  curl -sf -X POST "$ADMIN/issue-profiles" -H 'Content-Type: application/json' -d "{
    \"vcPlanId\": \"$VC_PLAN\",
    \"title\": \"WEARLESS Face License\",
    \"description\": \"WEARLESS model face-license issuance profile.\",
    \"vcSchemaId\": $VS_ID,
    \"language\": \"ko\",
    \"endpoints\": [\"http://127.0.0.1\"],
    \"cipher\": \"AES-256-CBC\",
    \"curve\": \"Secp256r1\",
    \"padding\": \"PKCS5\",
    \"initiateType\": \"issuer_init\",
    \"tags\": [\"facelicense\"],
    \"zkpEnabled\": false
  }" >/dev/null
  IP_ID=$(q_issuer "SELECT id FROM issue_profile WHERE vc_plan_id = :'vc_plan';" -v "vc_plan=$VC_PLAN")
  echo "    issue_profile id=$IP_ID (list_vc_plan 자동 등록 → TAS offer/propose 가능)"
else
  echo "--> 3/3 issue-profile 이미 존재 (id=$IP_ID) — skip"
fi
[ -n "$IP_ID" ] || { echo "ERROR: issue_profile id 조회 실패" >&2; exit 1; }

echo "==> 검증"
echo "  namespace     : $(q_issuer "SELECT id||' ok' FROM namespace WHERE id = :'id';" -v "id=$NS_ID")"
echo "  vc_schema     : $(q_issuer "SELECT id||' ok' FROM vc_schema WHERE id = :'id';" -v "id=$VS_ID")"
echo "  issue_profile : $(q_issuer "SELECT id||' ok' FROM issue_profile WHERE id = :'id';" -v "id=$IP_ID")"
# list_vc_plan 은 tas DB(List Community). issue-profile POST 가 여기에 자동 등록한다.
LVP=$(q_tas "SELECT count(*) FROM list_vc_plan WHERE vc_plan_id = :'vc_plan';" -v "vc_plan=$VC_PLAN" || true)
[ "${LVP:-0}" -gt 0 ] || { echo "facelicense_plan=missing"; exit 1; }
echo "facelicense_plan=present"
echo "완료. plan=$VC_PLAN 로 FaceLicense VC 발급 가능."
echo "홀더: POST /holder/models/{id}/issue-vc  body={\"plan\":\"facelicense\",\"claims\":{...}}"
