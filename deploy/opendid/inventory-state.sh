#!/usr/bin/env bash
set -euo pipefail

POSTGRES_CONTAINER=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
BESU_CONTAINER=${OPENDID_BESU_CONTAINER:-opendid-besu-node}
POSTGRES_VOLUME=${OPENDID_POSTGRES_VOLUME:-postgre_opendid_data}
BESU_VOLUME=${OPENDID_BESU_VOLUME:-besu_opendid_data}
POSTGRES_VOLUME_FALLBACKS=${OPENDID_POSTGRES_VOLUME_FALLBACKS:-postgre_postgre_opendid_data}
BESU_VOLUME_FALLBACKS=${OPENDID_BESU_VOLUME_FALLBACKS:-besu_besu_opendid_data}
POSTGRES_USER=${OPENDID_POSTGRES_USER:-${OPENDID_DB_USER:-}}
POSTGRES_DB=${OPENDID_POSTGRES_DB:-${OPENDID_DB_NAME:-}}
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
container_env_value() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$POSTGRES_CONTAINER" 2>/dev/null |
    sed -n "s/^$1=//p" | head -1
}
count_files() {
  local dir=$1
  shift
  local count=0
  if [ -d "$dir" ]; then
    while IFS= read -r -d '' _file; do
      count=$((count + 1))
    done < <(find "$dir" -type f "$@" -print0 2>/dev/null)
  fi
  printf '%s\n' "$count"
}
psql_value() {
  docker exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "$1" 2>/dev/null | head -1 || true
}
psql_value_db() {
  docker exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$1" -tA -c "$2" 2>/dev/null | head -1
}
count_rows_exact() {
  local dbs=$1
  shift
  [ -n "$dbs" ] || { printf 'unknown\n'; return; }
  local total=0 found=0 db spec target_db table where sql count reg
  while IFS= read -r db; do
    [ -n "$db" ] || continue
    for spec in "$@"; do
      target_db=''
      case "$spec" in
        *:*) target_db=${spec%%:*}; spec=${spec#*:} ;;
      esac
      [ -z "$target_db" ] || [ "$target_db" = "$db" ] || continue
      table=${spec%%|*}
      where=''
      [ "$table" != "$spec" ] && where=${spec#*|}
      if reg=$(psql_value_db "$db" "select to_regclass('public.$table');"); then
        [ -n "$reg" ] || continue
      else
        printf 'unknown\n'
        return
      fi
      sql="select count(*) from public.$table"
      [ -n "$where" ] && sql="$sql where $where"
      if count=$(psql_value_db "$db" "$sql;"); then
        :
      else
        printf 'unknown\n'
        return
      fi
      if printf '%s' "$count" | grep -Eq '^[0-9]+$'; then
        total=$((total + count))
        found=1
      else
        printf 'unknown\n'
        return
      fi
    done
  done <<EOF
$dbs
EOF
  [ "$found" = "1" ] && printf '%s\n' "$total" || printf '0\n'
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
    [ -n "$POSTGRES_USER" ] || POSTGRES_USER=$(container_env_value POSTGRES_USER)
    [ -n "$POSTGRES_DB" ] || POSTGRES_DB=$(container_env_value POSTGRES_DB)
    POSTGRES_USER=${POSTGRES_USER:-postgres}
    POSTGRES_DB=${POSTGRES_DB:-postgres}
    version=$(psql_value "select current_setting('server_version');")
    [ -n "$version" ] && printf 'postgres_version=%s\n' "$version"
    db_lines=$(docker exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F '|' -c \
      "select datname, pg_database_size(datname) from pg_database where datistemplate=false order by datname;" 2>/dev/null || true)
    if [ -n "$db_lines" ]; then
      db_names=$(printf '%s\n' "$db_lines" | cut -d '|' -f 1)
      printf '%s\n' "$db_lines" | while IFS='|' read -r db size _; do
          [ -n "$db" ] || continue
          tables=$(docker exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$db" -tA -c \
            "select count(*) from information_schema.tables where table_schema='public';" 2>/dev/null | head -1 || true)
          printf 'db=%s size_bytes=%s public_tables=%s\n' "$db" "$size" "${tables:-unknown}"
        done
    else
      db_names=''
      printf 'db_metadata=unknown\n'
    fi
    printf 'facelicense_namespace_rows=%s\n' "$(count_rows_exact "$db_names" "issuer:namespace|namespace_id='kr.wearless.facelicense'")"
    printf 'facelicense_schema_rows=%s\n' "$(count_rows_exact "$db_names" "issuer:vc_schema|vc_schema_id='facelicense'")"
    printf 'facelicense_plan_rows=%s\n' "$(count_rows_exact "$db_names" "issuer:issue_profile|vc_plan_id='vcplanface0000000001'" "tas:list_vc_plan|vc_plan_id='vcplanface0000000001'")"
    printf 'entity_rows=%s\n' "$(count_rows_exact "$db_names" tas:entity)"
    printf 'issuer_rows=%s\n' "$(count_rows_exact "$db_names" issuer:issuer)"
    printf 'cas_rows=%s\n' "$(count_rows_exact "$db_names" cas:cas cas:ca)"
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
