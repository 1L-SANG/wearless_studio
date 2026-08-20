#!/usr/bin/env bash
set -euo pipefail

if [ "${OPENDID_RUN_POSTGRES_INTEGRATION:-0}" != 1 ]; then
  printf 'SKIP set OPENDID_RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL 16.4 restore proof\n'
  exit 0
fi

command -v docker >/dev/null 2>&1 || { printf 'docker not found\n' >&2; exit 2; }
suffix="$$"
source_container="opendid-pg-source-$suffix"
target_container="opendid-pg-target-$suffix"
source_volume="opendid_pg_source_$suffix"
target_volume="opendid_pg_target_$suffix"
fixture_user=opendid_fixture_user
fixture_db=opendid_fixture_db
restore_user=opendid_restore_admin
test_password=test-only
tmp="$(mktemp -d "${TMPDIR:-/tmp}/opendid-pg-integration.XXXXXX")"

cleanup() {
  case "$source_container" in opendid-pg-source-[0-9]*) : ;; *) exit 2 ;; esac
  case "$target_container" in opendid-pg-target-[0-9]*) : ;; *) exit 2 ;; esac
  case "$source_volume" in opendid_pg_source_[0-9]*) : ;; *) exit 2 ;; esac
  case "$target_volume" in opendid_pg_target_[0-9]*) : ;; *) exit 2 ;; esac
  docker rm -f "$source_container" "$target_container" >/dev/null 2>&1 || true
  docker volume rm "$source_volume" "$target_volume" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

wait_ready() {
  local container=$1 user=$2 db=$3 attempt=0
  while [ "$attempt" -lt 60 ]; do
    docker exec "$container" pg_isready -U "$user" -d "$db" >/dev/null 2>&1 && return 0
    attempt=$((attempt + 1))
    sleep 1
  done
  return 1
}

docker volume create "$source_volume" >/dev/null
docker run -d --name "$source_container" \
  -e POSTGRES_USER="$fixture_user" -e POSTGRES_PASSWORD="$test_password" -e POSTGRES_DB="$fixture_db" \
  -v "$source_volume:/var/lib/postgresql/data" postgres:16.4 >/dev/null
wait_ready "$source_container" "$fixture_user" "$fixture_db"
docker exec "$source_container" psql -X -v ON_ERROR_STOP=1 -U "$fixture_user" -d "$fixture_db" \
  -c 'CREATE TABLE restore_fixture(id integer PRIMARY KEY); INSERT INTO restore_fixture VALUES (7);' >/dev/null
docker exec "$source_container" pg_dumpall -U "$fixture_user" >"$tmp/postgres.dump.sql"
docker rm -f "$source_container" >/dev/null

docker volume create "$target_volume" >/dev/null
docker run -d --name "$target_container" \
  -e POSTGRES_USER="$restore_user" -e POSTGRES_PASSWORD="$test_password" -e POSTGRES_DB=postgres \
  -v "$target_volume:/var/lib/postgresql/data" postgres:16.4 >/dev/null
wait_ready "$target_container" "$restore_user" postgres
docker exec -i "$target_container" psql -X -v ON_ERROR_STOP=1 -U "$restore_user" -d postgres \
  <"$tmp/postgres.dump.sql" >/dev/null
state=$(docker exec "$target_container" psql -X -v ON_ERROR_STOP=1 -At -U "$restore_user" -d postgres \
  -c "ALTER ROLE \"$restore_user\" NOLOGIN PASSWORD NULL; SELECT rolcanlogin::text || ':' || (rolpassword IS NULL)::text FROM pg_authid WHERE rolname='$restore_user';" | tail -1)
[ "$state" = false:true ]
[ "$(docker exec "$target_container" psql -X -At -U "$fixture_user" -d "$fixture_db" -c 'SELECT id FROM restore_fixture;')" = 7 ]

docker rm -f "$target_container" >/dev/null
docker run -d --name "$target_container" \
  -e POSTGRES_USER="$fixture_user" -e POSTGRES_PASSWORD="$test_password" -e POSTGRES_DB="$fixture_db" \
  -v "$target_volume:/var/lib/postgresql/data" postgres:16.4 >/dev/null
wait_ready "$target_container" "$fixture_user" "$fixture_db"
[ "$(docker exec "$target_container" psql -X -At -U "$fixture_user" -d "$fixture_db" -c 'SELECT id FROM restore_fixture;')" = 7 ]
[ "$(docker exec "$target_container" psql -X -At -U "$fixture_user" -d postgres \
  -c "SELECT rolcanlogin::text || ':' || (rolpassword IS NULL)::text FROM pg_authid WHERE rolname='$restore_user';")" = false:true ]
if docker exec "$target_container" psql -X -At -U "$restore_user" -d postgres -c 'SELECT 1;' >/dev/null 2>&1; then
  printf 'bootstrap role still accepts login\n' >&2
  exit 1
fi

printf 'PASS PostgreSQL 16.4 dump restore, role lock, operational login, and restart persistence\n'
