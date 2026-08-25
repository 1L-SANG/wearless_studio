#!/usr/bin/env bash
# OpenDID single-container entrypoint — TA + Issuer + CAS + fm-holder 를 한 컨테이너에서
# localhost 로 띄운다(서버끼리 localhost:8090/8091/8094/8100 로 호출하므로). besu 는 없다 —
# 체인은 OmniOne Chain(외부 RPC). 상태(pg·wallet)는 컨테이너 밖(RDS·Secrets)이라 이 컨테이너는
# stateless → Fargate Spot + scale-to-zero 안전.
#
# 필수 env:
#   OPENDID_DB_HOST OPENDID_DB_PORT OPENDID_DB_USER OPENDID_DB_PASSWORD   (RDS; DB명 tas/issuer/cas 고정)
#   OMNIONE_CHAIN_RPC_URL OMNIONE_CHAIN_ID OMNIONE_DID_CONTRACT_ADDRESS
#   OMNIONE_TAS_KEY OMNIONE_ISSUER_KEY  (서버별 regist 역할 키 — OmniOne 은 역할이 주소별이라 분리 필수)
#   WALLET_SECRETS_DIR   (tas.wallet issuer.wallet cas.wallet wallet.wallet 가 있는 마운트 경로 — Secrets/EFS)
#   WALLET_PASSWORD      (파일 월렛 공통 비번)
#   FM_HOLDER_PEPPER
# 선택: JVM_HEAP(각 서버 -Xmx, 기본 384m), *_PORT override
set -euo pipefail

JARS=/opt/opendid/jars
J() { echo "java -Xms128m -Xmx${JVM_HEAP:-384m} -jar"; }
: "${OPENDID_DB_HOST:?}" "${OPENDID_DB_USER:?}" "${OPENDID_DB_PASSWORD:?}"
: "${OMNIONE_CHAIN_RPC_URL:?}" "${OMNIONE_DID_CONTRACT_ADDRESS:?}" "${OMNIONE_TAS_KEY:?}" "${OMNIONE_ISSUER_KEY:?}"
: "${WALLET_SECRETS_DIR:?}" "${WALLET_PASSWORD:?}"
DBPORT="${OPENDID_DB_PORT:-5432}"
CHAINID="${OMNIONE_CHAIN_ID:-201210}"
RUN=/run/opendid
mkdir -p "$RUN"

# 1) 체인 properties 를 서버별로 렌더 — 같은 컨트랙트·RPC 지만 evm.contract.privateKey 는 그 서버의
#    regist 역할 키(OmniOne 은 Tas/Issuer 역할이 서로 다른 주소에 부여됨). CAS 는 조회만이라 TAS 키 재사용.
render_bc() {  # $1=outfile $2=privkey
  cat > "$1" <<EOF
evm.network.url=${OMNIONE_CHAIN_RPC_URL}
evm.chainId=${CHAINID}
evm.gas.limit=10000000
evm.gas.price=0
evm.connection.timeout=10000
evm.contract.address=${OMNIONE_DID_CONTRACT_ADDRESS}
evm.contract.privateKey=$2
EOF
  chmod 600 "$1"
}
render_bc "$RUN/bc-tas.properties"    "${OMNIONE_TAS_KEY}"
render_bc "$RUN/bc-issuer.properties" "${OMNIONE_ISSUER_KEY}"
render_bc "$RUN/bc-cas.properties"    "${OMNIONE_TAS_KEY}"

# 2) 공통 Spring override — dev 프로파일(실 config) + RDS 데이터소스 + 체인/월렛 파일경로.
common() {  # $1=db-name  $2=wallet-file  $3=bc-file
  echo "--spring.profiles.active=dev \
--spring.datasource.url=jdbc:postgresql://${OPENDID_DB_HOST}:${DBPORT}/$1 \
--spring.datasource.username=${OPENDID_DB_USER} \
--spring.datasource.password=${OPENDID_DB_PASSWORD} \
--blockchain.file-path=$3 \
--wallet.file-path=${WALLET_SECRETS_DIR}/$2 \
--wallet.password=${WALLET_PASSWORD}"
}

pids=()
launch() {  # $1=name $2=jar $3=port $4=extra-args
  echo "[opendid] launching $1 on :$3"
  $(J) "$JARS/$2" --server.port="$3" $4 > "$RUN/$1.log" 2>&1 &
  pids+=($!)
}

launch tas    did-ta-server-2.0.0.jar     "${TAS_PORT:-8090}"    "$(common tas    tas.wallet    $RUN/bc-tas.properties)"
launch issuer did-issuer-server-2.0.0.jar "${ISSUER_PORT:-8091}" "$(common issuer issuer.wallet $RUN/bc-issuer.properties)"
launch cas    did-ca-server-2.0.0.jar     "${CAS_PORT:-8094}"    "$(common cas    cas.wallet    $RUN/bc-cas.properties)"
# fm-holder: config 가 env(${FM_...})로 wallet 경로·pw·pepper 를 읽고, tas/issuer/cas URL 은
# 이미 localhost 고정(한 컨테이너라 그대로 맞다) — CLI override 불필요, env 만 세팅해 넘긴다.
echo "[opendid] launching fm-holder on :${HOLDER_PORT:-8100}"
export FM_WALLET_PROVIDER_PATH="${WALLET_SECRETS_DIR}/wallet.wallet"
export FM_CAS_PROVIDER_PATH="${WALLET_SECRETS_DIR}/cas.wallet"
export FM_WALLET_PROVIDER_PW="${WALLET_PASSWORD}"
export FM_CAS_PROVIDER_PW="${WALLET_PASSWORD}"
export FM_HOLDER_PEPPER="${FM_HOLDER_PEPPER:?}"
$(J) "$JARS/fm-holder-0.1.0.jar" --server.port="${HOLDER_PORT:-8100}" > "$RUN/fm-holder.log" 2>&1 &
pids+=($!)

# 3) holder(:8100) 헬스 대기 = 스택 준비 신호(오토스케일러가 이 포트로 깨움 확인).
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:${HOLDER_PORT:-8100}/actuator/health" >/dev/null 2>&1; then
    echo "[opendid] stack ready (holder healthy)"; break
  fi
  sleep 2
done

# 4) 로그 tail + 프로세스 감시: 하나라도 죽으면 컨테이너 종료(Fargate 가 새 태스크로 교체).
tail -F "$RUN"/*.log &
wait -n "${pids[@]}"
echo "[opendid] a server exited — shutting down container for replacement"
kill "${pids[@]}" 2>/dev/null || true
exit 1
