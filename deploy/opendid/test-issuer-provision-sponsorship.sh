#!/usr/bin/env bash
# 협찬 동의 VC(fmsponsorship-v1) 프로비저닝 래퍼 계약 테스트 — 네트워크·DB·AWS 없이 가짜 CLI 로.
#   bash deploy/opendid/test-issuer-provision-sponsorship.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL="$ROOT/scripts/issuer-provision-sponsorship.sh"
ECS="$ROOT/scripts/issuer-provision-sponsorship-ecs.sh"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/issuer-provision-sponsorship-test.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

fakebin="$tmp/bin"
mkdir -p "$fakebin"
fail=0
ok() { printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; fail=$((fail + 1)); }
want_grep() { grep -Eq "$1" "$2" && ok "$3" || bad "$3"; }
want_no_grep() { ! grep -Eq "$1" "$2" && ok "$3" || bad "$3"; }
py() { python3 - "$@"; }

# --- 가짜 docker(psql) / curl: 상태 디렉터리에 리소스 파일이 있으면 "존재" ---------------
cat >"$fakebin/docker" <<'SH'
#!/usr/bin/env python3
import os, pathlib, sys
state = pathlib.Path(os.environ["STATE"])
q = sys.stdin.read()
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("docker-sql " + " ".join(q.split()) + "\n")
# 값은 psql -v 바인드(:'var')로만 가야 한다 — SQL 본문에 끼어들면 실패.
unsafe = os.environ.get("UNSAFE_VALUE")
if unsafe and unsafe in q:
    sys.exit(7)
if "list_vc_plan" in q:
    print(os.environ.get("FAKE_LVP") or (1 if (state / "issue-profiles").exists() else 0))
else:
    r = "namespaces" if "FROM namespace" in q else "vc-schemas" if "FROM vc_schema" in q else "issue-profiles"
    print(7 if (state / r).exists() else "")
SH
cat >"$fakebin/curl" <<'SH'
#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ["STATE"])
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("curl " + " ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
body = json.loads(args[args.index("-d") + 1])
url = next(a for a in args if a.startswith("http"))
(state / url.rsplit("/", 1)[-1]).write_text(json.dumps(body))
SH

# --- 가짜 aws: ECS exec 안의 curl GET/POST 를 흉내(세션 배너·꼬리 포함) --------------------
cat >"$fakebin/aws" <<'SH'
#!/usr/bin/env python3
import base64, json, os, pathlib, re, sys
state = pathlib.Path(os.environ["STATE"])
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("aws " + " ".join(args) + "\n")
sub = args[1]
if sub == "list-clusters": print("arn:aws:ecs:us-east-1:1:cluster/wearless-use1"); sys.exit()
if sub == "list-services": print("arn:aws:ecs:us-east-1:1:service/wearless-use1/opendid"); sys.exit()
if sub == "list-tasks": print("arn:aws:ecs:us-east-1:1:task/wearless-use1/abc"); sys.exit()
assert sub == "execute-command", args
cmd = args[args.index("--command") + 1]
print("The Session Manager plugin was installed successfully.\n")
if "-X POST" in cmd:
    b64 = re.search(r"echo (\S+) \| base64 -d", cmd).group(1)
    url = re.search(r'-X POST \\?"([^"\\]+)', cmd).group(1)
    (state / url.rsplit("/", 1)[-1]).write_text(base64.b64decode(b64).decode())
    print("201")
else:
    url = re.search(r"curl -s '([^']+)'", cmd).group(1)
    path = url.split("?")[0].rsplit("/", 1)[-1]
    def load(r):
        f = state / r
        return json.loads(f.read_text()) if f.exists() else None
    content = []
    if path == "namespaces" and load("namespaces"):
        content = [{"id": 11, "namespaceId": load("namespaces")["namespace"]["id"]}]
    elif path == "vc-schemas" and load("vc-schemas"):
        content = [{"id": 12, "vcSchemaId": load("vc-schemas")["vcSchemaId"]}]
    elif path == "issue-profiles" and load("issue-profiles"):
        content = [{"id": 13, "vcPlanId": load("issue-profiles")["vcPlanId"]}]
    elif path == "list" and load("issue-profiles") and os.environ.get("FAKE_LVP") != "0":
        content = [{"id": 1, "vcPlanId": load("issue-profiles")["vcPlanId"]}]
    print(json.dumps({"content": content}))
print("\nExiting session with sessionId: fake-123")
SH
chmod +x "$fakebin"/*

export PATH="$fakebin:$PATH"
export FAKE_LOG="$tmp/fake.log"
unset DRY_RUN FAKE_LVP UNSAFE_VALUE
# 호출자 환경의 라이선스 FL_* 가 새면 안 된다(래퍼가 덮어써야 한다).
export FL_NAMESPACE_ID=kr.wearless.facelicense.v2 FL_VC_PLAN=vcplanface0000000002 FL_TAG=facelicense-v2

check_bodies() {  # check_bodies <state> <label>
  py "$1" <<'PY' && ok "$2: namespace·schema·profile 본문이 협찬 스키마" || bad "$2: namespace·schema·profile 본문이 협찬 스키마"
import json, pathlib, sys
s = pathlib.Path(sys.argv[1])
ns = json.loads((s / "namespaces").read_text())
vs = json.loads((s / "vc-schemas").read_text())
ip = json.loads((s / "issue-profiles").read_text())
assert ns["namespace"] == {"id": "kr.wearless.fmsponsorship.v1", "name": "FmSponsorship v1",
                           "ref": "https://wearless.kr/schema/fmsponsorship-v1"}, ns
assert [i["id"] for i in ns["items"]] == ["model_did", "credential_id", "consent_doc_version",
    "participation_doc_sha256", "profile_doc_sha256", "consented_at"], ns["items"]
assert all(i["required"] and i["type"] == "text" and i["location"] == "inline" for i in ns["items"])
assert vs["vcSchemaId"] == "fmsponsorship-v1" and vs["version"] == "2.0" and "Sponsorship" in vs["title"], vs
assert ip["vcPlanId"] == "vcplanspons000000001" and len(ip["vcPlanId"]) == 20, ip
assert ip["tags"] == ["fmsponsorship-v1"] and ip["initiateType"] == "issuer_init", ip
PY
}

# 1) 로컬: 빈 Issuer → 3개 생성 + TAS 플랜 확인
export STATE="$tmp/local"; mkdir -p "$STATE"
if PG_USER=omn "$LOCAL" >"$tmp/local.out" 2>&1; then ok 'local: 생성 경로 성공'; else bad 'local: 생성 경로 성공'; cat "$tmp/local.out"; fi
check_bodies "$STATE" local
want_grep '^sponsorship_plan=present$' "$tmp/local.out" 'local: sponsorship_plan=present'
want_grep 'fmsponsorship-v1' "$tmp/local.out" 'local: 홀더 plan 안내가 fmsponsorship-v1'
want_no_grep 'facelicense' "$tmp/local.out" 'local: 라이선스 리소스를 건드리지 않는다'

# 2) 로컬 재실행: 전부 skip(멱등, POST 0회)
: >"$FAKE_LOG"
PG_USER=omn "$LOCAL" >"$tmp/local2.out" 2>&1 && ok 'local: 재실행 성공' || bad 'local: 재실행 성공'
[ "$(grep -c '이미 존재' "$tmp/local2.out")" = 3 ] && ok 'local: 3단계 모두 skip' || bad 'local: 3단계 모두 skip'
want_no_grep '^curl ' "$FAKE_LOG" 'local: 재실행은 POST 하지 않는다'

# 3) 로컬: 위험한 override 값은 SQL 본문에 끼어들지 않고(-v 바인드만), TAS 플랜 없으면 실패
export UNSAFE_VALUE="spons' OR '1'='1"
: >"$FAKE_LOG"
SP_NAMESPACE_ID="$UNSAFE_VALUE" SP_VC_SCHEMA="$UNSAFE_VALUE" PG_USER=omn "$LOCAL" >"$tmp/unsafe.out" 2>&1 \
  && ok 'local: 위험한 값도 바인드 변수로만' || bad 'local: 위험한 값도 바인드 변수로만'
grep -F "docker-sql" "$FAKE_LOG" | grep -qF "$UNSAFE_VALUE" && bad 'local: 위험한 값이 SQL 본문에 없다' || ok 'local: 위험한 값이 SQL 본문에 없다'
unset UNSAFE_VALUE
FAKE_LVP=0 PG_USER=omn "$LOCAL" >"$tmp/missing.out" 2>&1 \
  && bad 'local: TAS 플랜 없으면 nonzero' || ok 'local: TAS 플랜 없으면 nonzero'
want_grep '^sponsorship_plan=missing$' "$tmp/missing.out" 'local: missing 보고'
want_no_grep '발급 가능' "$tmp/missing.out" 'local: missing 이면 발급 가능 문구 없음'

# 4) 로컬: vcPlanId 20자 강제
SP_VC_PLAN=vcplanspons00000001 PG_USER=omn "$LOCAL" >"$tmp/len.out" 2>&1 \
  && bad 'local: 19자 plan 거절' || ok 'local: 19자 plan 거절'

# 5) ECS DRY_RUN: 빈 Issuer 여도 쓰기 0
export STATE="$tmp/ecs-dry"; mkdir -p "$STATE"; : >"$FAKE_LOG"
DRY_RUN=1 "$ECS" >"$tmp/dry.out" 2>&1 && ok 'ecs dry-run: 성공 종료' || { bad 'ecs dry-run: 성공 종료'; cat "$tmp/dry.out"; }
want_grep 'FmSponsorship v1 Issuer 프로비저닝 via ECS' "$tmp/dry.out" 'ecs dry-run: 협찬 라벨'
want_grep '\(dry-run\) skip' "$tmp/dry.out" 'ecs dry-run: skip 표시'
want_no_grep 'X POST' "$FAKE_LOG" 'ecs dry-run: POST 없음'
[ -z "$(ls -A "$STATE")" ] && ok 'ecs dry-run: 상태 변화 없음' || bad 'ecs dry-run: 상태 변화 없음'

# 6) ECS 실행: 생성 → 검증, 재실행은 skip
DRY_RUN=0 "$ECS" >"$tmp/ecs.out" 2>&1 && ok 'ecs: 생성 경로 성공' || { bad 'ecs: 생성 경로 성공'; cat "$tmp/ecs.out"; }
check_bodies "$STATE" ecs
want_grep '^sponsorship_plan=present$' "$tmp/ecs.out" 'ecs: sponsorship_plan=present'
: >"$FAKE_LOG"
DRY_RUN=0 "$ECS" >"$tmp/ecs2.out" 2>&1 && ok 'ecs: 재실행 성공' || bad 'ecs: 재실행 성공'
want_no_grep 'X POST' "$FAKE_LOG" 'ecs: 재실행은 POST 하지 않는다'
FAKE_LVP=0 DRY_RUN=0 "$ECS" >"$tmp/ecs3.out" 2>&1 && bad 'ecs: TAS 플랜 없으면 nonzero' || ok 'ecs: TAS 플랜 없으면 nonzero'
want_grep '^sponsorship_plan=missing$' "$tmp/ecs3.out" 'ecs: missing 보고'

[ "$fail" -eq 0 ]
