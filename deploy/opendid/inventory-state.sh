#!/usr/bin/env bash
set -euo pipefail

POSTGRES_CONTAINER=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
BESU_CONTAINER=${OPENDID_BESU_CONTAINER:-opendid-besu-node}
POSTGRES_VOLUME=${OPENDID_POSTGRES_VOLUME:-postgre_opendid_data}
BESU_VOLUME=${OPENDID_BESU_VOLUME:-besu_opendid_data}
POSTGRES_VOLUME_FALLBACKS=${OPENDID_POSTGRES_VOLUME_FALLBACKS:-postgre_postgre_opendid_data}
BESU_VOLUME_FALLBACKS=${OPENDID_BESU_VOLUME_FALLBACKS:-besu_besu_opendid_data}
POSTGRES_USER=${OPENDID_POSTGRES_USER:-${OPENDID_DB_USER:-postgres}}
POSTGRES_DB=${OPENDID_POSTGRES_DB:-${OPENDID_DB_NAME:-postgres}}
OPENDID_ROOT=${OPENDID_ROOT:-/opt/opendid}
SECRETS_DIR=${OPENDID_SECRETS_DIR:-$OPENDID_ROOT/secrets}
CONFIG_DIR=${OPENDID_CONFIG_DIR:-$OPENDID_ROOT/config}
HOLDER_DATA_DIR=${OPENDID_HOLDER_DATA_DIR:-$OPENDID_ROOT/state/holder}

docker_image() { docker inspect -f '{{.Config.Image}}' "$1" 2>/dev/null || true; }
resolve_volume() {
  local volume=$1
  shift || true
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    printf '%s\n' "$volume"
    return 0
  fi
  for volume in "$@"; do
    if docker volume inspect "$volume" >/dev/null 2>&1; then
      printf '%s\n' "$volume"
      return 0
    fi
  done
  return 1
}
count_files() { [ -d "$1" ] && find "$1" -type f "$@" 2>/dev/null | wc -l | tr -d ' ' || echo 0; }
psql_value() {
  docker exec -i "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "$1" 2>/dev/null | head -1 || true
}

if ! command -v docker >/dev/null 2>&1; then
  echo 'docker=absent'
else
  pg_image=$(docker_image "$POSTGRES_CONTAINER")
  besu_image=$(docker_image "$BESU_CONTAINER")
  printf 'postgres_container=%s\n' "$([ -n "$pg_image" ] && echo present || echo absent)"
  [ -n "$pg_image" ] && printf 'postgres_image=%s\n' "$pg_image"
  printf 'besu_container=%s\n' "$([ -n "$besu_image" ] && echo present || echo absent)"
  [ -n "$besu_image" ] && printf 'besu_image=%s\n' "$besu_image"
  if pg_volume=$(resolve_volume "$POSTGRES_VOLUME" $POSTGRES_VOLUME_FALLBACKS); then
    printf 'postgres_volume=present\npostgres_volume_name=%s\n' "$pg_volume"
  else
    printf 'postgres_volume=absent\n'
  fi
  if besu_volume=$(resolve_volume "$BESU_VOLUME" $BESU_VOLUME_FALLBACKS); then
    printf 'besu_volume=present\nbesu_volume_name=%s\n' "$besu_volume"
  else
    printf 'besu_volume=absent\n'
  fi

  if [ -n "$pg_image" ]; then
    version=$(psql_value "select current_setting('server_version');")
    [ -n "$version" ] && printf 'postgres_version=%s\n' "$version"
    db_lines=$(docker exec -i "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d postgres -tA -F '|' -c \
      "select datname, pg_database_size(datname) from pg_database where datistemplate=false order by datname;" 2>/dev/null || true)
    if [ -n "$db_lines" ]; then
      printf '%s\n' "$db_lines" | while IFS='|' read -r db size _; do
          [ -n "$db" ] || continue
          tables=$(docker exec -i "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$db" -tA -c \
            "select count(*) from information_schema.tables where table_schema='public';" 2>/dev/null | head -1 || true)
          printf 'db=%s size_bytes=%s public_tables=%s\n' "$db" "$size" "${tables:-unknown}"
        done
    else
      printf 'db_metadata=unknown\n'
    fi
    fl_schema=$(psql_value "select count(*) from information_schema.schemata where lower(schema_name) like '%facelicense%';")
    fl_namespace=$(psql_value "select count(*) from information_schema.tables where lower(table_name) like '%namespace%';")
    fl_plan=$(psql_value "select count(*) from information_schema.tables where lower(table_name) like '%plan%';")
    entity_count=$(psql_value "select count(*) from information_schema.tables where lower(table_name) like '%entity%';")
    issuer_count=$(psql_value "select count(*) from information_schema.tables where lower(table_name) like '%issuer%';")
    cas_count=$(psql_value "select count(*) from information_schema.tables where lower(table_name) like '%cas%';")
    printf 'facelicense_schema_count=%s\n' "${fl_schema:-unknown}"
    printf 'facelicense_namespace_table_count=%s\n' "${fl_namespace:-unknown}"
    printf 'facelicense_plan_table_count=%s\n' "${fl_plan:-unknown}"
    printf 'entity_table_count=%s\n' "${entity_count:-unknown}"
    printf 'issuer_table_count=%s\n' "${issuer_count:-unknown}"
    printf 'cas_table_count=%s\n' "${cas_count:-unknown}"
  fi
fi

wallets=$(count_files "$SECRETS_DIR" \( -name '*.wallet' -o -name '*.zkpwallet' \))
dids=$(count_files "$SECRETS_DIR" -name '*.did')
blockchain=$(count_files "$SECRETS_DIR" -name 'blockchain.properties')
configs=$(count_files "$CONFIG_DIR")
printf 'wallet_files=%s\n' "$wallets"
printf 'did_files=%s\n' "$dids"
printf 'blockchain_config_files=%s\n' "$blockchain"
printf 'app_config_files=%s\n' "$configs"
if [ -d "$HOLDER_DATA_DIR" ] && [ -n "$(find "$HOLDER_DATA_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]; then
  printf 'holder_data=present\n'
else
  printf 'holder_data=missing\n'
fi
