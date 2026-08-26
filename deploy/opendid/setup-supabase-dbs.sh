#!/usr/bin/env bash
# OpenDID 용 postgres 데이터베이스를 기존 Supabase 인스턴스에 만든다(새 RDS 없이 "얹기").
#
# OpenDID TA/Issuer/CAS 서버는 각자 별도 DATABASE(tas/issuer/cas)를 쓴다(엔트리포인트가
# jdbc:.../<db-name> 으로 접속). 테이블은 서버가 첫 부팅 때 스스로 만든다 — 여기서는 DB 만 만든다.
# fm-holder 는 파일 저장(holder.data-dir)이라 pg DB 가 필요 없다.
#
# ⚠️ Supabase **직결(direct connection, 5432)** URL 로 실행할 것. pgbouncer pooler(6543)는
#    CREATE DATABASE 를 못 하고, OpenDID 런타임 접속도 pooler 가 아니라 직결을 써야 한다
#    (엔트리포인트 HikariCP 를 서버당 3으로 묶어 인스턴스 커넥션 고갈을 막는다).
#
# 사용:
#   ./setup-supabase-dbs.sh 'postgresql://postgres:<pw>@db.<ref>.supabase.co:5432/postgres'
# 또는 PG* 환경변수(PGHOST/PGPORT/PGUSER/PGPASSWORD)를 세팅한 뒤 인자 없이 실행.
set -euo pipefail

CONN="${1:-}"
PSQL=(psql)
[ -n "$CONN" ] && PSQL=(psql "$CONN")

command -v psql >/dev/null || { echo "psql 이 필요합니다(brew install libpq / postgresql-client)"; exit 1; }

echo "== 대상 확인 =="
"${PSQL[@]}" -tAc "select 'connected to ' || current_database() || ' @ ' || inet_server_addr()" || {
  echo "접속 실패 — Supabase **직결(5432)** URL 인지 확인하세요(pooler 6543 아님)."; exit 1; }

for db in tas issuer cas; do
  exists="$("${PSQL[@]}" -tAc "select 1 from pg_database where datname='${db}'")"
  if [ "$exists" = "1" ]; then
    echo "== ${db}: 이미 있음(건너뜀) =="
  else
    echo "== ${db}: 생성 =="
    "${PSQL[@]}" -c "CREATE DATABASE ${db}"
  fi
done

echo "== 완료. 생성된 DB =="
"${PSQL[@]}" -tAc "select datname from pg_database where datname in ('tas','issuer','cas') order by 1"
echo
echo "다음: OPENDID_DB_HOST/PORT/USER/PASSWORD 시크릿을 이 **직결** 값으로 SSM 에 넣고,"
echo "OMNIONE 엔티티 프로비저닝(런북) → copilot svc deploy opendid 순으로 진행하세요."
