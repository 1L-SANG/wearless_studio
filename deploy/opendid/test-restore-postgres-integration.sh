#!/usr/bin/env bash
set -euo pipefail
umask 077

if [ "${OPENDID_RUN_POSTGRES_INTEGRATION:-0}" != 1 ]; then
  printf 'SKIP set OPENDID_RUN_POSTGRES_INTEGRATION=1 to run OpenDID export/restore proof\n'
  exit 0
fi

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
EXPORT="$ROOT/deploy/opendid/export-state.sh"
RESTORE="$ROOT/deploy/opendid/restore-state.sh"
command -v docker >/dev/null 2>&1 || { printf 'docker not found\n' >&2; exit 2; }
docker info >/dev/null 2>&1 || { printf 'docker daemon is unavailable\n' >&2; exit 2; }

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }
sha256_value() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi
}
volume_exists() { docker volume inspect "$1" >/dev/null 2>&1; }
network_exists() { docker network inspect "$1" >/dev/null 2>&1; }
container_exists() { docker inspect "$1" >/dev/null 2>&1; }
volume_label() { docker volume inspect -f "{{ index .Labels \"$2\" }}" "$1" 2>/dev/null || true; }
resource_label() { docker inspect -f "{{ index .Config.Labels \"$2\" }}" "$1" 2>/dev/null || true; }
network_label() { docker network inspect -f "{{ index .Labels \"$2\" }}" "$1" 2>/dev/null || true; }

tmp=$(mktemp -d "${TMPDIR:-/tmp}/opendid-pg-e2e.XXXXXX")
tmp=$(cd -P "$tmp" && pwd)
private_name=$(basename "$tmp")
token=$(printf '%s' "$private_name" | tr -cd '[:alnum:]' | tr '[:upper:]' '[:lower:]')
[ -n "$token" ] || fail "could not derive private resource token"

source_container="opendid-source-$token"
source_pg_volume="opendid_source_pg_$token"
source_besu_volume="opendid_source_besu_$token"
wrong_container="opendid-wrong-$token"
wrong_pg_volume="opendid_wrong_pg_$token"
wrong_besu_volume="opendid_wrong_besu_$token"
wrong_network="opendid-wrong-net-$token"
wrong_project="opendid-wrong-$token"
target_container="opendid-target-$token"
target_pg_volume="opendid_target_pg_$token"
target_besu_volume="opendid_target_besu_$token"
target_network="opendid-target-net-$token"
target_project="opendid-target-$token"
fixture_user=opendid_fixture_user
fixture_db=opendid_fixture_db
restore_user=opendid_restore_admin
correct_password=opendid-integration-correct-only
wrong_password=opendid-integration-wrong-only
runtime_owner=$(id -un)
runtime_group=$(id -gn)
wrong_pg_restore_token=''
wrong_besu_restore_token=''
target_pg_restore_token=''
target_besu_restore_token=''

remove_test_container() {
  local name=$1
  [ "$(resource_label "$name" opendid.test-token)" = "$token" ] || return 0
  docker rm -f "$name" >/dev/null 2>&1 || true
}
remove_test_volume() {
  local name=$1
  [ "$(volume_label "$name" opendid.test-token)" = "$token" ] || return 0
  docker volume rm "$name" >/dev/null 2>&1 || true
}
remove_restore_volume() {
  local name=$1 expected=$2
  [ -n "$expected" ] || return 0
  [ "$(volume_label "$name" opendid.restore-token)" = "$expected" ] || return 0
  docker volume rm "$name" >/dev/null 2>&1 || true
}
remove_test_network() {
  local name=$1
  [ "$(network_label "$name" opendid.test-token)" = "$token" ] || return 0
  docker network rm "$name" >/dev/null 2>&1 || true
}
cleanup() {
  local status=$?
  trap - EXIT
  set +e
  remove_test_container "$source_container"
  remove_test_container "$wrong_container"
  remove_test_container "$target_container"
  remove_test_network "$wrong_network"
  remove_test_network "$target_network"
  remove_test_volume "$source_pg_volume"
  remove_test_volume "$source_besu_volume"
  remove_restore_volume "$wrong_pg_volume" "$wrong_pg_restore_token"
  remove_restore_volume "$wrong_besu_volume" "$wrong_besu_restore_token"
  remove_restore_volume "$target_pg_volume" "$target_pg_restore_token"
  remove_restore_volume "$target_besu_volume" "$target_besu_restore_token"
  rm -rf -- "$tmp"
  exit "$status"
}
trap cleanup EXIT

for container in "$source_container" "$wrong_container" "$target_container"; do
  container_exists "$container" && fail "container name collision: $container"
done
for volume in "$source_pg_volume" "$source_besu_volume" "$wrong_pg_volume" "$wrong_besu_volume" "$target_pg_volume" "$target_besu_volume"; do
  volume_exists "$volume" && fail "volume name collision: $volume"
done
for network in "$wrong_network" "$target_network"; do
  network_exists "$network" && fail "network name collision: $network"
done
docker network create --label "opendid.test-token=$token" "$wrong_network" >/dev/null
[ "$(network_label "$wrong_network" opendid.test-token)" = "$token" ] || fail "wrong network ownership label mismatch"
docker network create --label "opendid.test-token=$token" "$target_network" >/dev/null
[ "$(network_label "$target_network" opendid.test-token)" = "$token" ] || fail "target network ownership label mismatch"

wait_ready() {
  local container=$1 user=$2 db=$3 attempt=0
  while [ "$attempt" -lt 60 ]; do
    docker exec "$container" pg_isready -U "$user" -d "$db" >/dev/null 2>&1 && return 0
    attempt=$((attempt + 1))
    sleep 1
  done
  return 1
}
write_compose() {
  local path=$1
  printf '%s\n' \
    'services:' \
    '  postgres-opendid:' \
    '    image: postgres:16.4' \
    '    container_name: "${OPENDID_POSTGRES_CONTAINER:?set OPENDID_POSTGRES_CONTAINER}"' \
    '    labels:' \
    '      opendid.test-token: "${OPENDID_TEST_TOKEN:?set OPENDID_TEST_TOKEN}"' \
    '    environment:' \
    '      POSTGRES_USER: "${OPENDID_POSTGRES_USER:?set OPENDID_POSTGRES_USER}"' \
    '      POSTGRES_PASSWORD: "${OPENDID_POSTGRES_PASSWORD:?set OPENDID_POSTGRES_PASSWORD}"' \
    '      POSTGRES_DB: "${OPENDID_POSTGRES_DB:?set OPENDID_POSTGRES_DB}"' \
    '    volumes:' \
    '      - postgre_opendid_data:/var/lib/postgresql/data' \
    '    networks:' \
    '      - opendid-test' \
    'volumes:' \
    '  postgre_opendid_data:' \
    '    name: "${OPENDID_POSTGRES_VOLUME:?set OPENDID_POSTGRES_VOLUME}"' \
    '    external: true' \
    '  besu_opendid_data:' \
    '    name: "${OPENDID_BESU_VOLUME:?set OPENDID_BESU_VOLUME}"' \
    '    external: true' \
    'networks:' \
    '  opendid-test:' \
    '    name: "${OPENDID_NETWORK:?set OPENDID_NETWORK}"' \
    '    external: true' \
    >"$path"
}
compose_env() {
  OPENDID_COMPOSE_FILE=$1 \
  OPENDID_ENV_FILE=$2 \
  OPENDID_POSTGRES_CONTAINER=$3 \
  OPENDID_BESU_CONTAINER=$4 \
  OPENDID_POSTGRES_VOLUME=$5 \
  OPENDID_BESU_VOLUME=$6 \
  OPENDID_NETWORK=$7 \
  COMPOSE_PROJECT_NAME=$8 \
  OPENDID_TEST_TOKEN=$token \
  OPENDID_POSTGRES_USER=$9 \
  OPENDID_POSTGRES_PASSWORD=${10} \
  OPENDID_POSTGRES_DB=${11} \
  OPENDID_OWNER=$runtime_owner \
  OPENDID_GROUP=$runtime_group \
  OPENDID_ROOT=${12} \
  OPENDID_HOLDER_DATA_DIR=${13} \
  "$RESTORE" "${14}" --apply
}

source_root="$tmp/source-root"
source_holder="$source_root/state/holder"
mkdir -p "$source_root/jars/TA" "$source_root/jars/Issuer" \
  "$source_root/shells/Besu/TA" "$source_root/shells/Besu/Issuer" "$source_holder"
printf 'ta-wallet-fixture\n' >"$source_root/jars/TA/tas.wallet"
printf 'issuer-did-fixture\n' >"$source_root/jars/Issuer/issuer.did"
printf 'ta-chain-fixture\n' >"$source_root/shells/Besu/TA/blockchain.properties"
printf 'issuer-chain-fixture\n' >"$source_root/shells/Besu/Issuer/blockchain.properties"
printf 'ca-chain-fixture\n' >"$source_root/shells/Besu/blockchain.properties"
printf 'besu-config-fixture\n' >"$source_root/shells/Besu/besu.dat"
printf 'holder-fixture\n' >"$source_holder/wallet-state.bin"

fake_bin="$tmp/fake-bin"
mkdir "$fake_bin"
printf '%s\n' '#!/usr/bin/env bash' 'exit 3' >"$fake_bin/systemctl"
chmod 700 "$fake_bin/systemctl"

docker volume create --label "opendid.test-token=$token" "$source_pg_volume" >/dev/null
[ "$(volume_label "$source_pg_volume" opendid.test-token)" = "$token" ] || fail "source PostgreSQL volume ownership label mismatch"
docker volume create --label "opendid.test-token=$token" "$source_besu_volume" >/dev/null
[ "$(volume_label "$source_besu_volume" opendid.test-token)" = "$token" ] || fail "source Besu volume ownership label mismatch"
source_pg_env="$tmp/source-postgres.env"
printf 'POSTGRES_USER=%s\nPOSTGRES_PASSWORD=%s\nPOSTGRES_DB=%s\n' \
  "$fixture_user" "$correct_password" "$fixture_db" >"$source_pg_env"
chmod 600 "$source_pg_env"
docker run -d --name "$source_container" --label "opendid.test-token=$token" \
  --env-file "$source_pg_env" \
  -v "$source_pg_volume:/var/lib/postgresql/data" postgres:16.4 >/dev/null
[ "$(resource_label "$source_container" opendid.test-token)" = "$token" ] || fail "source container ownership label mismatch"
wait_ready "$source_container" "$fixture_user" "$fixture_db" || fail "source PostgreSQL did not become ready"
docker exec "$source_container" psql -X -v ON_ERROR_STOP=1 -U "$fixture_user" -d "$fixture_db" \
  -c 'CREATE TABLE restore_fixture(id integer PRIMARY KEY); INSERT INTO restore_fixture VALUES (7);' >/dev/null
docker run --rm -v "$source_besu_volume:/data" alpine:3.20 \
  sh -eu -c 'mkdir -p /data/chain && printf "besu-volume-fixture\n" >/data/chain/block.dat'

archive="$tmp/archive"
PATH="$fake_bin:$PATH" \
OPENDID_POSTGRES_CONTAINER="$source_container" \
OPENDID_BESU_CONTAINER="opendid-source-besu-$token" \
OPENDID_POSTGRES_VOLUME="$source_pg_volume" \
OPENDID_BESU_VOLUME="$source_besu_volume" \
OPENDID_POSTGRES_USER="$fixture_user" \
OPENDID_ROOT="$source_root" \
OPENDID_HOLDER_DATA_DIR="$source_holder" \
"$EXPORT" "$archive" >"$tmp/export.out"
archive_checksum_before=$(sha256_value "$archive/SHA256SUMS")

compose_file="$tmp/infra.compose.yml"
env_file="$tmp/opendid.env"
write_compose "$compose_file"
printf '\n' >"$env_file"

wrong_root="$tmp/wrong-root"
wrong_holder="$wrong_root/state/holder"
if compose_env "$compose_file" "$env_file" "$wrong_container" "opendid-wrong-besu-$token" \
  "$wrong_pg_volume" "$wrong_besu_volume" "$wrong_network" "$wrong_project" \
  "$fixture_user" "$wrong_password" "$fixture_db" "$wrong_root" "$wrong_holder" "$archive" \
  >"$tmp/wrong.out" 2>"$tmp/wrong.err"; then
  fail "restore accepted an incorrect final PostgreSQL password"
fi
wrong_pg_restore_token=$(volume_label "$wrong_pg_volume" opendid.restore-token)
wrong_besu_restore_token=$(volume_label "$wrong_besu_volume" opendid.restore-token)
container_exists "$wrong_container" && fail "wrong-password restore left its PostgreSQL container"
volume_exists "$wrong_pg_volume" && fail "wrong-password restore left its PostgreSQL volume"
volume_exists "$wrong_besu_volume" && fail "wrong-password restore left its Besu volume"
[ ! -e "$wrong_root/secrets" ] || fail "wrong-password restore installed identity files"
if ! grep -q 'restored PostgreSQL bridge login failed' "$tmp/wrong.err"; then
  sed -n '1,80p' "$tmp/wrong.err" >&2
  fail "wrong-password failure did not reach bridge authentication"
fi
if network_exists "$wrong_network"; then
  [ "$(network_label "$wrong_network" opendid.test-token)" = "$token" ] || fail "wrong restore network ownership mismatch"
  remove_test_network "$wrong_network"
fi

target_root="$tmp/target-root"
target_holder="$target_root/state/holder"
compose_env "$compose_file" "$env_file" "$target_container" "opendid-target-besu-$token" \
  "$target_pg_volume" "$target_besu_volume" "$target_network" "$target_project" \
  "$fixture_user" "$correct_password" "$fixture_db" "$target_root" "$target_holder" "$archive" \
  >"$tmp/restore.out"
target_pg_restore_token=$(volume_label "$target_pg_volume" opendid.restore-token)
target_besu_restore_token=$(volume_label "$target_besu_volume" opendid.restore-token)
case "$target_pg_restore_token" in opendid-restore.*) : ;; *) fail "target PostgreSQL volume lacks restore ownership token" ;; esac
[ "$target_besu_restore_token" = "$target_pg_restore_token" ] || fail "target volume restore tokens differ"

cmp "$source_root/jars/TA/tas.wallet" "$target_root/secrets/TA/tas.wallet" >/dev/null || fail "TA wallet was not normalized"
cmp "$source_root/jars/Issuer/issuer.did" "$target_root/secrets/Issuer/issuer.did" >/dev/null || fail "Issuer DID was not normalized"
cmp "$source_root/shells/Besu/blockchain.properties" "$target_root/secrets/CA/blockchain.properties" >/dev/null || fail "CA blockchain config was not normalized"
cmp "$source_holder/wallet-state.bin" "$target_holder/wallet-state.bin" >/dev/null || fail "Holder data did not round-trip"
source_besu_hash=$(docker run --rm -v "$source_besu_volume:/data:ro" alpine:3.20 sha256sum /data/chain/block.dat | awk '{print $1}')
target_besu_hash=$(docker run --rm -v "$target_besu_volume:/data:ro" alpine:3.20 sha256sum /data/chain/block.dat | awk '{print $1}')
[ "$source_besu_hash" = "$target_besu_hash" ] || fail "Besu data checksum changed"

OPENDID_POSTGRES_CONTAINER="$target_container" \
OPENDID_POSTGRES_VOLUME="$target_pg_volume" \
OPENDID_BESU_VOLUME="$target_besu_volume" \
OPENDID_NETWORK="$target_network" \
OPENDID_TEST_TOKEN="$token" \
COMPOSE_PROJECT_NAME="$target_project" \
OPENDID_POSTGRES_USER="$fixture_user" \
OPENDID_POSTGRES_PASSWORD="$correct_password" \
OPENDID_POSTGRES_DB="$fixture_db" \
docker compose -f "$compose_file" --env-file "$env_file" up -d postgres-opendid >/dev/null
wait_ready "$target_container" "$fixture_user" "$fixture_db" || fail "restored PostgreSQL did not become ready"
client_env="$tmp/postgres-client.env"
printf 'PGPASSWORD=%s\n' "$correct_password" >"$client_env"
chmod 600 "$client_env"
target_ip=$(docker inspect -f "{{with index .NetworkSettings.Networks \"$target_network\"}}{{.IPAddress}}{{end}}" "$target_container")
[ -n "$target_ip" ] || fail "restored PostgreSQL has no target network address"
client_psql() {
  docker run --rm --network "$target_network" --env-file "$client_env" postgres:16.4 \
    psql -X -v ON_ERROR_STOP=1 -At -h "$target_ip" -U "$fixture_user" -d "$fixture_db" "$@"
}
[ "$(client_psql -c 'SELECT id FROM restore_fixture;')" = 7 ] || fail "fixture row was not restored"
[ "$(client_psql -c "SELECT rolcanlogin::text || ':' || (rolpassword IS NULL)::text FROM pg_authid WHERE rolname='$restore_user';")" = false:true ] \
  || fail "bootstrap role was not locked"

OPENDID_POSTGRES_CONTAINER="$target_container" \
OPENDID_POSTGRES_VOLUME="$target_pg_volume" \
OPENDID_BESU_VOLUME="$target_besu_volume" \
OPENDID_NETWORK="$target_network" \
OPENDID_TEST_TOKEN="$token" \
COMPOSE_PROJECT_NAME="$target_project" \
OPENDID_POSTGRES_USER="$fixture_user" \
OPENDID_POSTGRES_PASSWORD="$correct_password" \
OPENDID_POSTGRES_DB="$fixture_db" \
docker compose -f "$compose_file" --env-file "$env_file" restart postgres-opendid >/dev/null
wait_ready "$target_container" "$fixture_user" "$fixture_db" || fail "restored PostgreSQL did not recover after restart"
target_ip=$(docker inspect -f "{{with index .NetworkSettings.Networks \"$target_network\"}}{{.IPAddress}}{{end}}" "$target_container")
[ "$(client_psql -c 'SELECT id FROM restore_fixture;')" = 7 ] || fail "fixture row was not persistent after restart"
[ "$(sha256_value "$archive/SHA256SUMS")" = "$archive_checksum_before" ] || fail "source archive was mutated"

printf 'PASS actual export -> wrong-password refusal -> restore -> restart using PostgreSQL 16.4\n'
