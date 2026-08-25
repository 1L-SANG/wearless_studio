#!/usr/bin/env bash
# inventory-state.sh — read-only inventory of the OpenDID source state.
#
# Prints ONLY non-sensitive facts needed to plan the single-server move:
#   - container / volume existence + image versions
#   - database names, sizes, public-table counts
#   - FaceLicense namespace / schema / plan counts
#   - entity / issuer / CAS row counts
#   - wallet / DID / config file EXISTENCE (never contents)
#   - Holder data existence
#
# It NEVER prints passwords, private keys, PII, wallet contents, or any row data.
# It is strictly read-only: no docker exec writes, no volume mounts other than
# the running containers' own read paths, no filesystem writes.
#
# Env overrides (source-server specific):
#   OPENDID_PG_CONTAINER    (default postgre-opendid)
#   OPENDID_BESU_CONTAINER  (default opendid-besu-node)
#   OPENDID_PG_VOLUME       (default postgre_postgre_opendid_data)
#   OPENDID_BESU_VOLUME     (default besu_besu_opendid_data)
#   OPENDID_PG_SUPERUSER    (default: auto-detect from POSTGRES_USER, then omn/opendid/postgres)
#   OPENDID_SECRETS_DIR     (default /opt/opendid/secrets)
#   OPENDID_CONFIG_DIR      (default /opt/opendid/config)
#   OPENDID_HOLDER_DATA_DIR (default /opt/opendid/state/holder)
#   FL_NAMESPACE_ID / FL_VC_SCHEMA / FL_VC_PLAN
set -euo pipefail

PG_CONTAINER="${OPENDID_PG_CONTAINER:-postgre-opendid}"
BESU_CONTAINER="${OPENDID_BESU_CONTAINER:-opendid-besu-node}"
PG_VOLUME="${OPENDID_PG_VOLUME:-postgre_postgre_opendid_data}"
BESU_VOLUME="${OPENDID_BESU_VOLUME:-besu_besu_opendid_data}"
SECRETS_DIR="${OPENDID_SECRETS_DIR:-/opt/opendid/secrets}"
CONFIG_DIR="${OPENDID_CONFIG_DIR:-/opt/opendid/config}"
HOLDER_DATA_DIR="${OPENDID_HOLDER_DATA_DIR:-/opt/opendid/state/holder}"

FL_NS="${FL_NAMESPACE_ID:-kr.wearless.facelicense}"
FL_SCHEMA="${FL_VC_SCHEMA:-facelicense}"
FL_PLAN="${FL_VC_PLAN:-vcplanface0000000001}"

have_docker() { command -v docker >/dev/null 2>&1; }
container_exists() { docker inspect "$1" >/dev/null 2>&1; }
container_running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" = "true" ]; }
container_image() { docker inspect -f '{{.Config.Image}}' "$1" 2>/dev/null || echo "unknown"; }
volume_exists() { docker volume inspect "$1" >/dev/null 2>&1; }

SU=""
detect_pg_superuser() {
  [ -n "$SU" ] && return 0
  if [ -n "${OPENDID_PG_SUPERUSER:-}" ]; then SU="$OPENDID_PG_SUPERUSER"; return 0; fi
  local env_user cand
  env_user="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$PG_CONTAINER" 2>/dev/null | sed -n 's/^POSTGRES_USER=//p' | head -n1)"
  for cand in "$env_user" omn opendid postgres; do
    [ -n "$cand" ] || continue
    if docker exec "$PG_CONTAINER" psql -U "$cand" -d postgres -tAc 'SELECT 1' >/dev/null 2>&1; then
      SU="$cand"; return 0
    fi
  done
  return 1
}

psql_scalar() { # db query -> single scalar; trims leading/trailing space only; silent on error
  docker exec "$PG_CONTAINER" psql -U "$SU" -d "$1" -tAc "$2" 2>/dev/null \
    | tr -d '\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}
regclass_exists() { # db relation
  [ "$(psql_scalar "$1" "SELECT to_regclass('$2') IS NOT NULL")" = "t" ]
}
guarded_count() { # db relation where-clause(optional)
  local db="$1" rel="$2" where="${3:-}"
  regclass_exists "$db" "public.$rel" || { echo "0 (table absent)"; return 0; }
  local n
  n="$(psql_scalar "$db" "SELECT count(*) FROM \"$rel\"${where:+ WHERE $where}")"
  echo "${n:-?}"
}

echo "OpenDID source-state inventory (read-only)"
echo "generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "note: no passwords, private keys, PII, or wallet/DID contents are shown."
echo

if ! have_docker; then
  echo "docker: NOT AVAILABLE — container/DB inventory skipped."
else
  echo "== Containers =="
  for pair in "postgres:$PG_CONTAINER" "besu:$BESU_CONTAINER"; do
    role="${pair%%:*}"; name="${pair#*:}"
    if container_exists "$name"; then
      run="no"; container_running "$name" && run="yes"
      printf "  %-9s %-22s present=yes running=%-3s image=%s\n" "$role" "$name" "$run" "$(container_image "$name")"
    else
      printf "  %-9s %-22s present=no\n" "$role" "$name"
    fi
  done
  echo
  echo "== Volumes =="
  for pair in "pg:$PG_VOLUME" "besu:$BESU_VOLUME"; do
    role="${pair%%:*}"; name="${pair#*:}"
    if volume_exists "$name"; then printf "  %-5s %-32s present=yes\n" "$role" "$name"
    else printf "  %-5s %-32s present=no\n" "$role" "$name"; fi
  done
  echo

  if container_exists "$PG_CONTAINER" && container_running "$PG_CONTAINER" && detect_pg_superuser; then
    echo "== Databases (name / size / public_tables) — superuser=$SU =="
    while IFS= read -r db; do
      db="$(printf '%s' "$db" | tr -d '[:space:]')"
      [ -n "$db" ] || continue
      size="$(psql_scalar "$db" "SELECT pg_size_pretty(pg_database_size('$db'))")"
      tcount="$(psql_scalar "$db" "SELECT count(*) FROM pg_tables WHERE schemaname='public'")"
      printf "  %-12s %-10s %s tables\n" "$db" "${size:-?}" "${tcount:-?}"
    done < <(docker exec "$PG_CONTAINER" psql -U "$SU" -d postgres -tAc \
              "SELECT datname FROM pg_database WHERE datistemplate=false ORDER BY datname" 2>/dev/null)
    echo
    echo "== Entity / Issuer / CAS counts =="
    printf "  tas.entity    : %s\n" "$(guarded_count tas entity)"
    printf "  issuer.issuer : %s\n" "$(guarded_count issuer issuer)"
    printf "  cas.cas       : %s\n" "$(guarded_count cas cas)"
    echo
    echo "== FaceLicense (issuer db) =="
    printf "  namespace  %-30s : %s\n" "$FL_NS"     "$(guarded_count issuer namespace     "namespace_id='$FL_NS'")"
    printf "  vc_schema  %-30s : %s\n" "$FL_SCHEMA"  "$(guarded_count issuer vc_schema     "vc_schema_id='$FL_SCHEMA'")"
    printf "  plan       %-30s : %s\n" "$FL_PLAN"    "$(guarded_count issuer issue_profile "vc_plan_id='$FL_PLAN'")"
    echo
  else
    echo "== Databases =="
    echo "  postgres not running (or superuser undetected) — DB inventory skipped."
    echo
  fi
fi

echo "== Secrets / config / Holder (existence only) =="
if [ -d "$SECRETS_DIR" ]; then
  echo "  secrets dir $SECRETS_DIR : present"
  for sub in TA Issuer CA Wallet; do
    d="$SECRETS_DIR/$sub"
    if [ -d "$d" ]; then
      n="$(find "$d" -type f 2>/dev/null | wc -l | tr -d ' ')"
      printf "    %-8s present (%s files)\n" "$sub" "$n"
    else
      printf "    %-8s absent\n" "$sub"
    fi
  done
else
  echo "  secrets dir $SECRETS_DIR : absent"
fi
if [ -d "$CONFIG_DIR" ]; then
  n="$(find "$CONFIG_DIR" -maxdepth 1 -name '*.yml' -type f 2>/dev/null | wc -l | tr -d ' ')"
  echo "  config dir $CONFIG_DIR : present ($n yml files)"
else
  echo "  config dir $CONFIG_DIR : absent"
fi
if [ -d "$HOLDER_DATA_DIR" ] && [ -n "$(ls -A "$HOLDER_DATA_DIR" 2>/dev/null)" ]; then
  echo "  holder data $HOLDER_DATA_DIR : present"
else
  echo "  holder data $HOLDER_DATA_DIR : missing"
fi
