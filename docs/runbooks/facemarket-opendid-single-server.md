# 런북 — FaceMarket OpenDID 단일 서버 이전

기존 PostgreSQL·Besu·entity wallet/DID·Holder 상태를 한 시점으로 묶어 Server 3으로 이전한다. TAS·Issuer·CAS·Holder는 Server 3에서만 실행하고 Orchestrator는 운영하지 않는다.

이 런북은 승인된 배포 창, source/target 접근 권한, 암호화된 전송 경로, 운영 비밀값을 갖춘 작업자만 실행한다. 실제 host/IP, 비밀번호, private key, VC 본문은 명령·터미널 로그·티켓에 남기지 않는다. Server 1의 `OPENDID_HOLDER_URL` 변경은 아래 검증이 모두 끝난 뒤 별도 cutover 단계에서만 한다.

## 1. 변수와 사전 조건

저장소 root와 암호화된 작업 디렉터리를 운영 환경 값으로 지정한다. `CUTOVER_PARENT`는 source 상태와 독립된 파일시스템이어야 한다.

```bash
set -euo pipefail
umask 077
: "${REPO:?set repository root}"
: "${CUTOVER_PARENT:?set encrypted archive parent}"
: "${SOURCE_ENV:?set source OpenDID environment file}"
cd "$REPO"
set -a
. "$SOURCE_ENV"
set +a
OPENDID_POSTGRES_CONTAINER=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
: "${OPENDID_POSTGRES_USER:?set source PostgreSQL user}"
: "${OPENDID_ISSUER_DB:?set source Issuer database}"
CUTOVER_DIR=$(mktemp -d "$CUTOVER_PARENT/opendid-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
EXPORT_DIR="$CUTOVER_DIR/export"
SOURCE_INVENTORY="$CUTOVER_DIR/source-inventory.txt"
SOURCE_ISSUER_STATE="$CUTOVER_DIR/source-issuer-state.txt"
```

`deploy/opendid/env.example`의 빈 필수값을 `/opt/opendid/opendid.env`에 채운다. 파일은 `root:opendid 0640`, `/opt/opendid/secrets`와 `/opt/opendid/state/holder`는 `opendid:opendid 0700`, 그 안의 모든 파일은 `0600`이어야 한다. source가 `/opt/opendid` 구조가 아니면 `OPENDID_ROOT`, volume/container 이름, DB 값을 운영 환경에서 명시한다.

다음이면 시작하지 않는다.

- 운영 변경 승인, source/target 접근 권한 또는 안전한 전송 경로가 없음
- target에 기존 OpenDID container, named volume, wallet/DID 또는 Holder 파일이 있음
- source DB·Besu·wallet·Holder를 같은 중지 구간에서 snapshot할 수 없음
- Linux host UTC/NTP preflight가 실패함

## 2. Source 동결과 export

### 2.1 쓰기 프로세스 중지

PostgreSQL은 dump를 위해 실행 상태로 둔다. 아래 두 경로 중 source의 실제 관리 방식 하나만 선택한다. 공통 순서는 Java writer 중지 → Orchestrator 종료와 `:9001` 폐쇄 → Besu graceful stop이다.

Linux systemd와 macOS legacy source가 서로 다른 기본 도구를 사용하므로 각 경로의 bounded wait helper를 따로 정의한다.

```bash
wait_linux_ports_closed() {
  local pattern=$1
  local attempt=0
  while [ "$attempt" -lt 60 ]; do
    if ! ss -ltnH | awk '{print $4}' | grep -Eq "$pattern"; then
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  echo "REFUSING: listeners did not close: $pattern" >&2
  return 1
}

wait_legacy_ports_closed() {
  local attempt=0 port open
  while [ "$attempt" -lt 60 ]; do
    open=0
    for port in "$@"; do
      lsof -nP -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && open=1
    done
    [ "$open" = 0 ] && return 0
    sleep 2
    attempt=$((attempt + 1))
  done
  echo 'REFUSING: legacy OpenDID listeners did not close' >&2
  return 1
}

stop_legacy_listener() {
  local port=$1 pids pid
  pids=$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  [ -z "$pids" ] && return 0
  for pid in $pids; do
    case "$pid" in *[!0-9]*) echo 'REFUSING: non-numeric listener PID' >&2; return 1 ;; esac
  done
  kill $pids
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}
```

#### A. systemd-managed source

네 unit이 모두 설치된 것을 먼저 확인한다. 하나라도 없으면 이 경로를 실행하지 않고 legacy 경로로 간다.

```bash
command -v systemctl >/dev/null
command -v ss >/dev/null
for unit in fm-holder opendid-tas opendid-issuer opendid-cas; do
  systemctl cat "$unit.service" >/dev/null || {
    echo "REFUSING: $unit.service is not systemd-managed" >&2
    exit 1
  }
done

sudo systemctl stop fm-holder opendid-cas opendid-issuer opendid-tas
for unit in fm-holder opendid-tas opendid-issuer opendid-cas; do
  ! systemctl is-active --quiet "$unit.service" || exit 1
done

if systemctl is-active --quiet opendid-orchestrator.service; then
  sudo systemctl stop opendid-orchestrator.service
fi
wait_linux_ports_closed ':(8090|8091|8094|8100|9001)$'

sudo docker stop --time 60 opendid-besu-node >/dev/null
test "$(docker inspect -f '{{.State.Running}}' opendid-besu-node)" = false
wait_linux_ports_closed ':(8545|8546)$'
SOURCE_FREEZE_VERIFIED=systemd
```

#### B. legacy Orchestrator-managed source

이 저장소의 legacy 개발 경로처럼 Holder가 별도 JVM이고 Orchestrator가 entity JVM을 관리할 때 사용한다. `lsof`로 해당 listener의 PID만 종료하며 PostgreSQL은 건드리지 않는다.

```bash
command -v lsof >/dev/null
curl -fsS http://127.0.0.1:9001/ >/dev/null

stop_legacy_listener 8100
wait_legacy_ports_closed 8100

curl -fsS http://127.0.0.1:9001/shutdown/all >/dev/null
wait_legacy_ports_closed 8090 8091 8094

stop_legacy_listener 9001
wait_legacy_ports_closed 9001

sudo docker stop --time 60 opendid-besu-node >/dev/null
test "$(docker inspect -f '{{.State.Running}}' opendid-besu-node)" = false
wait_legacy_ports_closed 8545 8546
SOURCE_FREEZE_VERIFIED=legacy
```

`deploy/opendid/export-state.sh`는 systemd-managed source에서 unit 상태를 확인하고 `lsof`가 있으면 writer port도 함께 확인한다. systemd가 없는 legacy source에서는 `lsof`로 writer port 폐쇄를 확인하며, 둘 다 없으면 export를 거부한다.

어느 경로든 최종 확인에서 Java/OpenDID/Besu listener가 남거나 Besu container가 실행 중이면 export하지 않는다. PostgreSQL container는 계속 실행 중이어야 한다.

```bash
: "${SOURCE_FREEZE_VERIFIED:?run exactly one source freeze path}"
test "$(docker inspect -f '{{.State.Running}}' opendid-besu-node)" = false
test "$(docker inspect -f '{{.State.Running}}' "$OPENDID_POSTGRES_CONTAINER")" = true
```

### 2.2 inventory와 synchronized snapshot

inventory는 개수와 파일 존재 여부만 출력하며 wallet/PII 내용은 출력하지 않는다.

```bash
deploy/opendid/inventory-state.sh >"$SOURCE_INVENTORY"

record_issuer_state() {
  local output=$1
  {
    docker exec -i "$OPENDID_POSTGRES_CONTAINER" psql -X -v ON_ERROR_STOP=1 \
      -U "$OPENDID_POSTGRES_USER" -d "$OPENDID_ISSUER_DB" -At <<'SQL'
select 'vc_rows=' || count(*) from vc;
select 'vc_status=' || coalesce(status, '<null>') || ' count=' || count(*) from vc group by status order by status nulls first;
select 'revoke_rows=' || count(*) from revoke_vc;
select 'revoke_status=' || coalesce(status, '<null>') || ' count=' || count(*) from revoke_vc group by status order by status nulls first;
SQL
    printf 'vc_id_status_sha256='
    docker exec "$OPENDID_POSTGRES_CONTAINER" psql -X -v ON_ERROR_STOP=1 \
      -U "$OPENDID_POSTGRES_USER" -d "$OPENDID_ISSUER_DB" -At \
      -c "select coalesce(vc_id, '<null>') || E'\\x1f' || coalesce(status, '<null>') from vc order by vc_id nulls first, status nulls first, id;" \
      | sha256 | awk '{print $1}'
    printf 'revoke_vc_id_status_sha256='
    docker exec "$OPENDID_POSTGRES_CONTAINER" psql -X -v ON_ERROR_STOP=1 \
      -U "$OPENDID_POSTGRES_USER" -d "$OPENDID_ISSUER_DB" -At \
      -c "select coalesce(vc_id, '<null>') || E'\\x1f' || coalesce(status, '<null>') from revoke_vc order by vc_id nulls first, status nulls first, id;" \
      | sha256 | awk '{print $1}'
  } >"$output"
  chmod 600 "$output"
}

record_issuer_state "$SOURCE_ISSUER_STATE"
deploy/opendid/export-state.sh "$EXPORT_DIR"
(cd "$EXPORT_DIR" && sha256 -c SHA256SUMS)
(cd "$CUTOVER_DIR" && sha256 source-inventory.txt source-issuer-state.txt >SOURCE-STATE.sha256)
chmod 600 "$SOURCE_INVENTORY" "$SOURCE_ISSUER_STATE" "$CUTOVER_DIR/SOURCE-STATE.sha256" "$EXPORT_DIR"/*
```

`source-issuer-state.txt`에는 VC/revoke 전체 행 수, status별 개수, `vc_id + status` 정렬 digest만 들어간다. 기존 행 수를 하드코딩하지 않고 이 source 기록을 이전의 기준으로 사용한다. `EXPORT-MANIFEST.txt`의 `holder_data`와 source inventory의 FaceLicense/entity 개수도 함께 보존한다. 비밀번호, private key, wallet 내용, VC ID/본문은 출력하거나 복사하지 않는다.

### 2.3 source 보존과 전송

- source volume, wallet/DID, Holder data를 수정하거나 삭제하지 않는다.
- `docker compose down -v`, volume prune, 재프로비저닝을 실행하지 않는다.
- target lifecycle과 재시작 검증이 끝날 때까지 source를 rollback 가능한 정지 상태로 보존한다.
- `CUTOVER_DIR` 전체를 승인된 암호화 채널로 target의 소유자 전용 디렉터리에 전송한다. 실제 target 주소는 배포 시스템에서 주입하고 문서에 적지 않는다.
- target에서 `(cd "$CUTOVER_DIR/export" && sha256sum -c SHA256SUMS)`와 `(cd "$CUTOVER_DIR" && sha256sum -c SOURCE-STATE.sha256)`를 다시 통과시킨다.

## 3. Target restore

`deploy/opendid/README.md`에 따라 OpenDID V2.0.0 JAR, Holder JAR, config, Compose, systemd unit과 `/opt/opendid/opendid.env`를 먼저 설치한다. restore는 fresh target만 허용한다.

```bash
set -euo pipefail
umask 077
: "${REPO:?set repository root on target}"
: "${CUTOVER_DIR:?set transferred cutover directory}"
cd "$REPO"
EXPORT_DIR="$CUTOVER_DIR/export"
SOURCE_INVENTORY="$CUTOVER_DIR/source-inventory.txt"
SOURCE_ISSUER_STATE="$CUTOVER_DIR/source-issuer-state.txt"
set -a
. /opt/opendid/opendid.env
set +a
OPENDID_POSTGRES_CONTAINER=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
: "${OPENDID_POSTGRES_USER:?set target PostgreSQL user}"
: "${OPENDID_ISSUER_DB:?set target Issuer database}"
(cd "$CUTOVER_DIR" && sha256sum -c SOURCE-STATE.sha256)

sudo --preserve-env=OPENDID_POSTGRES_USER,OPENDID_POSTGRES_PASSWORD,OPENDID_POSTGRES_DB,OPENDID_POSTGRES_VOLUME,OPENDID_BESU_VOLUME \
  deploy/opendid/restore-state.sh "$EXPORT_DIR"
sudo --preserve-env=OPENDID_POSTGRES_USER,OPENDID_POSTGRES_PASSWORD,OPENDID_POSTGRES_DB,OPENDID_POSTGRES_VOLUME,OPENDID_BESU_VOLUME \
  deploy/opendid/restore-state.sh "$EXPORT_DIR" --apply
```

첫 명령은 checksum과 restore 계획만 검증한다. `--apply`는 checksum 불일치, 기존 volume/container, stale identity 파일을 발견하면 쓰기 전에 거부한다. 실패 후 임의로 target을 정리해 재시도하지 말고 원인을 확인한다.

복원 후 비밀 파일의 소유권·권한을 내용 출력 없이 확인한다.

```bash
test -z "$(sudo find /opt/opendid/secrets /opt/opendid/state/holder -type f ! -perm 0600 -print -quit)"
test -z "$(sudo find /opt/opendid/secrets /opt/opendid/state/holder -type f \( ! -user opendid -o ! -group opendid \) -print -quit)"
```

## 4. Target 기동과 health

먼저 [OpenDID Besu 시계 런북](opendid-besu-clock.md)의 Linux UTC/NTP preflight를 통과시킨다. Server 3 private IP를 `/opt/opendid/opendid.env`의 `FM_HOLDER_BIND_ADDRESS`에 설정하고, host firewall과 Security Group에서 Server 1만 그 주소의 `8100`에 접근하도록 허용한다. PostgreSQL, Besu, TAS, Issuer, CAS는 loopback 전용이다. 그 다음 infra → TAS → Issuer/CAS → Holder 순서로 시작한다.

```bash
set -euo pipefail
: "${SERVER3_PRIVATE_BIND_ADDRESS:?set approved Server 3 private IP}"
set -a
. /opt/opendid/opendid.env
set +a
: "${FM_HOLDER_HMAC_SECRET:?set Holder HMAC secret in /opt/opendid/opendid.env}"
: "${FM_HOLDER_BIND_ADDRESS:?set Holder private bind in /opt/opendid/opendid.env}"
test "$FM_HOLDER_BIND_ADDRESS" = "$SERVER3_PRIVATE_BIND_ADDRESS"

wait_http() {
  local url=$1 name=$2
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 "$url" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "FAILED: $name readiness timed out: $url" >&2
  return 1
}

sudo systemctl daemon-reload
sudo systemctl start opendid-infra
sudo systemctl is-active --quiet opendid-infra
docker inspect -f '{{.State.Health.Status}}' postgre-opendid | grep -qx healthy
docker inspect -f '{{.State.Health.Status}}' opendid-besu-node | grep -qx healthy

sudo systemctl start opendid-tas
wait_http http://127.0.0.1:8090/actuator/health TAS

sudo systemctl start opendid-issuer opendid-cas
wait_http http://127.0.0.1:8091/actuator/health Issuer
wait_http http://127.0.0.1:8094/actuator/health CAS

sudo systemctl start fm-holder
wait_http http://${FM_HOLDER_BIND_ADDRESS}:8100/holder/health Holder
```

Holder는 non-templated unit 하나와 data directory 하나만 사용한다. 새 jar/config 배포 시 `systemctl stop fm-holder`가 끝난 뒤 `systemctl start fm-holder`를 실행한다. rolling overlap, 두 번째 Holder, `flock`, 분산 lock은 nonce cleanup에 프로세스 간 조정이 생기기 전까지 금지한다.

위 systemd 기동이 모두 끝난 뒤 target에서는 managed smoke만 실행한다. 이 모드는 기존 서비스를 health-check하고 Holder만 한 번 재시작한다. Java를 직접 실행하거나 Docker/PostgreSQL/Besu를 중지·시작하지 않는다.

```bash
sudo --preserve-env=FM_HOLDER_HMAC_SECRET,FM_HOLDER_BIND_ADDRESS \
  env OPENDID_SMOKE_MODE=managed deploy/opendid/smoke.sh
```

성공 출력은 `holder_unsigned=blocked`, `holder_valid=valid`, `holder_revoked=revoked`, `restart_holder_revoked=revoked`, `smoke_result=ok` 집계만 보존한다. 전체 infra 재시작과 power-loss 검증은 별도 승인된 운영 창에서만 수행한다.

## 5. 상태 일치 검증

### 5.1 DB, FaceLicense, VC/revoke history

target inventory를 만들고 source의 모든 DB 이름·public table 수를 비교한다. 크기는 logical restore 뒤 달라질 수 있으므로 비교에서 제외한다. FaceLicense는 namespace 1건, schema 1건, Issuer/TAS plan 합계 2건이어야 한다.

```bash
TARGET_INVENTORY=$(mktemp)
TARGET_ISSUER_STATE=$(mktemp)
SOURCE_DB_SHAPE=$(mktemp)
TARGET_DB_SHAPE=$(mktemp)
trap 'rm -f "$TARGET_INVENTORY" "$TARGET_ISSUER_STATE" "$SOURCE_DB_SHAPE" "$TARGET_DB_SHAPE"' EXIT
OPENDID_ROOT=/opt/opendid deploy/opendid/inventory-state.sh >"$TARGET_INVENTORY"

db_shape() {
  awk '/^db=/ {
    db=$1; tables=""
    for (i=1; i<=NF; i++) if ($i ~ /^public_tables=/) tables=$i
    if (db !~ /^db=.+/ || tables !~ /^public_tables=[0-9]+$/) exit 2
    print db, tables
    count++
  } END { if (count == 0) exit 2 }' "$1" | LC_ALL=C sort
}

db_shape "$SOURCE_INVENTORY" >"$SOURCE_DB_SHAPE"
db_shape "$TARGET_INVENTORY" >"$TARGET_DB_SHAPE"
diff -u "$SOURCE_DB_SHAPE" "$TARGET_DB_SHAPE"
grep -qx 'facelicense_namespace_rows=1' "$TARGET_INVENTORY"
grep -qx 'facelicense_schema_rows=1' "$TARGET_INVENTORY"
grep -qx 'facelicense_plan_rows=2' "$TARGET_INVENTORY"
diff -u \
  <(grep -E '^(facelicense_(namespace|schema|plan)_rows|entity_rows|issuer_rows|cas_rows)=' "$SOURCE_INVENTORY") \
  <(grep -E '^(facelicense_(namespace|schema|plan)_rows|entity_rows|issuer_rows|cas_rows)=' "$TARGET_INVENTORY")

record_issuer_state() {
  local output=$1
  {
    docker exec -i "$OPENDID_POSTGRES_CONTAINER" psql -X -v ON_ERROR_STOP=1 \
      -U "$OPENDID_POSTGRES_USER" -d "$OPENDID_ISSUER_DB" -At <<'SQL'
select 'vc_rows=' || count(*) from vc;
select 'vc_status=' || coalesce(status, '<null>') || ' count=' || count(*) from vc group by status order by status nulls first;
select 'revoke_rows=' || count(*) from revoke_vc;
select 'revoke_status=' || coalesce(status, '<null>') || ' count=' || count(*) from revoke_vc group by status order by status nulls first;
SQL
    printf 'vc_id_status_sha256='
    docker exec "$OPENDID_POSTGRES_CONTAINER" psql -X -v ON_ERROR_STOP=1 \
      -U "$OPENDID_POSTGRES_USER" -d "$OPENDID_ISSUER_DB" -At \
      -c "select coalesce(vc_id, '<null>') || E'\\x1f' || coalesce(status, '<null>') from vc order by vc_id nulls first, status nulls first, id;" \
      | sha256sum | awk '{print $1}'
    printf 'revoke_vc_id_status_sha256='
    docker exec "$OPENDID_POSTGRES_CONTAINER" psql -X -v ON_ERROR_STOP=1 \
      -U "$OPENDID_POSTGRES_USER" -d "$OPENDID_ISSUER_DB" -At \
      -c "select coalesce(vc_id, '<null>') || E'\\x1f' || coalesce(status, '<null>') from revoke_vc order by vc_id nulls first, status nulls first, id;" \
      | sha256sum | awk '{print $1}'
  } >"$output"
  chmod 600 "$output"
}

record_issuer_state "$TARGET_ISSUER_STATE"
diff -u "$SOURCE_ISSUER_STATE" "$TARGET_ISSUER_STATE"
```

### 5.2 Besu chain과 contract

다음 검사는 TA/Issuer/CA 설정의 chain ID·contract address가 서로 같고, Besu 응답의 chain ID가 일치하며, 해당 address에 code가 존재하는지만 출력한다. 주소와 private key는 출력하지 않는다.

```bash
sudo python3 - <<'PY'
import json
import pathlib
import urllib.request

paths = [
    pathlib.Path('/opt/opendid/secrets/TA/blockchain.properties'),
    pathlib.Path('/opt/opendid/secrets/Issuer/blockchain.properties'),
    pathlib.Path('/opt/opendid/secrets/CA/blockchain.properties'),
]

def properties(path):
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values

configs = [properties(path) for path in paths]
chain_ids = {int(config['evm.chainId']) for config in configs}
contracts = {config['evm.contract.address'].lower() for config in configs}
if len(chain_ids) != 1 or len(contracts) != 1:
    raise SystemExit('chain_contract=mismatched_config')

def rpc(method, params):
    body = json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1}).encode()
    request = urllib.request.Request('http://127.0.0.1:8545', body, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.load(response)
    if 'error' in result:
        raise SystemExit('chain_contract=rpc_error')
    return result['result']

contract = next(iter(contracts))
if int(rpc('eth_chainId', []), 16) != next(iter(chain_ids)):
    raise SystemExit('chain_contract=chain_id_mismatch')
if rpc('eth_getCode', [contract, 'latest']) in ('0x', '0x0', None):
    raise SystemExit('chain_contract=missing_code')
print('chain_contract=ok')
PY
```

### 5.3 기존 VC 온체인 metadata 전수 검증

DB 동일성 비교를 먼저 끝낸 뒤 Issuer `vc`의 모든 기존 행을 OpenDID V2 contract의 view 함수 `getVcmetaData(string)`으로 조회한다. JSON-RPC `eth_call`만 사용하므로 transaction, nonce, revoke 행을 만들지 않는다. 출력은 집계뿐이며 VC ID, VC 본문, RPC 응답은 출력하지 않는다. DB와 온체인 status는 각각 `ACTIVE` 또는 `REVOKED`로 같아야 하며 다른 상태와 조회 오류는 실패다.

`verify-vcmeta.py`는 먼저 selector, 단일 dynamic string 인자 encoder, 반환 struct의 다섯 번째 `status` decoder를 고정 fixture로 self-check한다. 하나라도 어긋나면 Besu 조회 전에 중단한다.

```bash
export OPENDID_POSTGRES_CONTAINER OPENDID_POSTGRES_USER OPENDID_ISSUER_DB
sudo --preserve-env=OPENDID_POSTGRES_CONTAINER,OPENDID_POSTGRES_USER,OPENDID_ISSUER_DB \
  deploy/opendid/verify-vcmeta.py
```

`eth_call`은 모델 wallet을 쓰지 않으므로 `holder_data=missing`이어도 이 조회는 가능하다. 그러나 기존 모델 명의의 revoke 서명은 여전히 불가능하다.

### 5.4 Orchestrator 미실행

Orchestrator unit은 inactive/disabled 상태이고 `:9001` listener가 없어야 한다. unit이 아예 없어도 두 `systemctl` 검사는 통과한다.

```bash
command -v ss >/dev/null
! systemctl is-active --quiet opendid-orchestrator.service
! systemctl is-enabled --quiet opendid-orchestrator.service
! ss -ltnH | awk '{print $4}' | grep -Eq ':9001$'
```

## 6. Holder data 공백 규칙

`EXPORT-MANIFEST.txt`가 `holder_data=missing`이면 DB와 Besu를 통한 기존 VC lifecycle 조회는 가능할 수 있지만, 과거 모델 wallet 개인키가 없으므로 Holder는 그 모델 명의의 revoke 서명을 만들 수 없다.

- 기존 FaceMarket/OpenDID 레코드를 자동 삭제하지 않는다.
- 기존 `vc_id`를 null 처리, 교체, 재연결 또는 임의 재발급하지 않는다.
- 기존 VC의 revoke 가능성이 복구됐다고 표시하지 않는다.
- 새 모델 wallet부터 `/opt/opendid/state/holder`에 영속화하고 backup 대상에 포함한다.
- 이 제한을 제품·보안 책임자가 수용하지 않으면 API cutover를 중지하고 wallet backup을 찾는다.

## 7. 완료·중단·rollback

다음이 모두 참이어야 Server 1 cutover 단계로 넘긴다.

- source/target checksum, 모든 DB 이름·table 수, VC/revoke status 개수와 digest가 일치함
- PostgreSQL/Besu 및 네 Java 서비스 health가 성공함
- FaceLicense namespace/schema/plan과 chain/contract 검사가 성공함
- 모든 기존 VC의 DB status와 read-only 온체인 status가 일치함
- `:9001`이 닫혀 있음
- 별도 lifecycle smoke에서 issue → valid → revoke → revoked와 전체 재시작 후 상태 유지가 성공함

하나라도 실패하면 `OPENDID_HOLDER_URL`을 변경하지 않는다. target의 Java 서비스와 infra를 중지하고 작업 기록에는 aggregate status와 digest만 보존한다.

```bash
sudo systemctl stop fm-holder opendid-cas opendid-issuer opendid-tas opendid-infra
```

source rollback이 승인되면 target이 완전히 중지된 것을 확인한 뒤 source Besu를 먼저 시작해 healthy를 기다리고 TAS → Issuer/CAS → Holder 순으로 시작한다. source volume/archive는 운영 검증과 backup 보존 승인이 끝날 때까지 삭제하지 않는다.
