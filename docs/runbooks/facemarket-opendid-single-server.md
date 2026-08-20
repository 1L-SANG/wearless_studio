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
cd "$REPO"
CUTOVER_DIR=$(mktemp -d "$CUTOVER_PARENT/opendid-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
EXPORT_DIR="$CUTOVER_DIR/export"
SOURCE_INVENTORY="$CUTOVER_DIR/source-inventory.txt"
```

`deploy/opendid/env.example`의 빈 필수값을 `/opt/opendid/opendid.env`에 채운다. 파일은 `root:opendid 0640`, `/opt/opendid/secrets`와 `/opt/opendid/state/holder`는 `opendid:opendid 0700`, 그 안의 모든 파일은 `0600`이어야 한다. source가 `/opt/opendid` 구조가 아니면 `OPENDID_ROOT`, volume/container 이름, DB 값을 운영 환경에서 명시한다.

다음이면 시작하지 않는다.

- 운영 변경 승인, source/target 접근 권한 또는 안전한 전송 경로가 없음
- target에 기존 OpenDID container, named volume, wallet/DID 또는 Holder 파일이 있음
- source DB·Besu·wallet·Holder를 같은 중지 구간에서 snapshot할 수 없음
- Linux host UTC/NTP preflight가 실패함

## 2. Source 동결과 export

### 2.1 쓰기 프로세스 중지

PostgreSQL은 dump를 위해 실행 상태로 두고 Holder, TAS, Issuer, CAS, legacy Orchestrator, Besu 순으로 중지한다. systemd가 아닌 기존 프로세스 관리자를 쓰는 source라면 같은 프로세스를 그 관리자에서 중지한다.

```bash
sudo systemctl stop fm-holder opendid-cas opendid-issuer opendid-tas
sudo docker stop opendid-besu-node
```

legacy Orchestrator가 실행 중이면 그 관리 대상 전체를 중지한 뒤 Orchestrator 자체도 종료한다. 다음 포트에 listener가 하나라도 남아 있으면 export하지 않는다.

```bash
# legacy Orchestrator가 source JVM을 관리할 때만 호출한다.
curl -fsS http://127.0.0.1:9001/shutdown/all >/dev/null
# 이어서 기존 supervisor에서 Orchestrator를 종료한다.

if ss -ltnH | awk '{print $4}' | grep -Eq ':(8090|8091|8094|8100|8545|8546|9001)$'; then
  echo 'REFUSING: OpenDID writer or Orchestrator is still listening' >&2
  exit 1
fi
```

### 2.2 inventory와 synchronized snapshot

inventory는 개수와 파일 존재 여부만 출력하며 wallet/PII 내용은 출력하지 않는다.

```bash
deploy/opendid/inventory-state.sh >"$SOURCE_INVENTORY"
deploy/opendid/export-state.sh "$EXPORT_DIR"
(cd "$EXPORT_DIR" && sha256sum -c SHA256SUMS)
(cd "$CUTOVER_DIR" && sha256sum source-inventory.txt >SOURCE-INVENTORY.sha256)
chmod 600 "$SOURCE_INVENTORY" "$CUTOVER_DIR/SOURCE-INVENTORY.sha256" "$EXPORT_DIR"/*
```

`EXPORT-MANIFEST.txt`의 `holder_data`와 source inventory의 FaceLicense/entity 개수를 작업 기록에 남긴다. 비밀번호, private key, wallet 내용, VC 본문은 열거나 복사하지 않는다.

### 2.3 source 보존과 전송

- source volume, wallet/DID, Holder data를 수정하거나 삭제하지 않는다.
- `docker compose down -v`, volume prune, 재프로비저닝을 실행하지 않는다.
- target lifecycle과 재시작 검증이 끝날 때까지 source를 rollback 가능한 정지 상태로 보존한다.
- `CUTOVER_DIR` 전체를 승인된 암호화 채널로 target의 소유자 전용 디렉터리에 전송한다. 실제 target 주소는 배포 시스템에서 주입하고 문서에 적지 않는다.
- target에서 `(cd "$CUTOVER_DIR/export" && sha256sum -c SHA256SUMS)`와 `(cd "$CUTOVER_DIR" && sha256sum -c SOURCE-INVENTORY.sha256)`를 다시 통과시킨다.

## 3. Target restore

`deploy/opendid/README.md`에 따라 OpenDID V2.0.0 JAR, Holder JAR, config, Compose, systemd unit과 `/opt/opendid/opendid.env`를 먼저 설치한다. restore는 fresh target만 허용한다.

```bash
cd "$REPO"
: "${CUTOVER_DIR:?set transferred cutover directory}"
EXPORT_DIR="$CUTOVER_DIR/export"
SOURCE_INVENTORY="$CUTOVER_DIR/source-inventory.txt"
set -a
. /opt/opendid/opendid.env
set +a

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

먼저 [OpenDID Besu 시계 런북](opendid-besu-clock.md)의 Linux UTC/NTP preflight를 통과시킨다. 그 다음 infra → TAS → Issuer/CAS → Holder 순서로 시작한다.

```bash
sudo systemctl daemon-reload
sudo systemctl start opendid-infra
sudo systemctl is-active --quiet opendid-infra
docker inspect -f '{{.State.Health.Status}}' postgre-opendid | grep -qx healthy
docker inspect -f '{{.State.Health.Status}}' opendid-besu-node | grep -qx healthy

sudo systemctl start opendid-tas
curl -fsS http://127.0.0.1:8090/actuator/health >/dev/null

sudo systemctl start opendid-issuer opendid-cas
curl -fsS http://127.0.0.1:8091/actuator/health >/dev/null
curl -fsS http://127.0.0.1:8094/actuator/health >/dev/null

sudo systemctl start fm-holder
curl -fsS http://127.0.0.1:8100/holder/health >/dev/null
```

## 5. 상태 일치 검증

### 5.1 DB와 FaceLicense

target inventory를 만들고 source와 상태 개수를 비교한다. FaceLicense는 namespace 1건, schema 1건, Issuer/TAS plan 합계 2건이어야 한다.

```bash
TARGET_INVENTORY=$(mktemp)
trap 'rm -f "$TARGET_INVENTORY"' EXIT
OPENDID_ROOT=/opt/opendid deploy/opendid/inventory-state.sh >"$TARGET_INVENTORY"
grep -qx 'facelicense_namespace_rows=1' "$TARGET_INVENTORY"
grep -qx 'facelicense_schema_rows=1' "$TARGET_INVENTORY"
grep -qx 'facelicense_plan_rows=2' "$TARGET_INVENTORY"
diff -u \
  <(grep -E '^(facelicense_(namespace|schema|plan)_rows|entity_rows|issuer_rows|cas_rows)=' "$SOURCE_INVENTORY") \
  <(grep -E '^(facelicense_(namespace|schema|plan)_rows|entity_rows|issuer_rows|cas_rows)=' "$TARGET_INVENTORY")
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

### 5.3 Orchestrator 미실행

Orchestrator unit이 설치·활성화되어 있지 않고 `:9001` listener가 없어야 한다.

```bash
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

- source/target checksum과 inventory 개수가 일치함
- PostgreSQL/Besu 및 네 Java 서비스 health가 성공함
- FaceLicense namespace/schema/plan과 chain/contract 검사가 성공함
- `:9001`이 닫혀 있음
- 별도 lifecycle smoke에서 issue → valid → revoke → revoked와 전체 재시작 후 상태 유지가 성공함

하나라도 실패하면 `OPENDID_HOLDER_URL`을 변경하지 않는다. target의 Java 서비스와 infra를 중지하고 로그에는 status/ID만 보존한다.

```bash
sudo systemctl stop fm-holder opendid-cas opendid-issuer opendid-tas opendid-infra
```

source rollback이 승인되면 target이 완전히 중지된 것을 확인한 뒤 source Besu를 먼저 시작해 healthy를 기다리고 TAS → Issuer/CAS → Holder 순으로 시작한다. source volume/archive는 운영 검증과 backup 보존 승인이 끝날 때까지 삭제하지 않는다.
