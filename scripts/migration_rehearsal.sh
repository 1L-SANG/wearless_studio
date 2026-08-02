#!/usr/bin/env bash
# Phase 3 P0-C 9/N — migration rehearsal on a DISPOSABLE database.
#
# 운영 DB 를 절대 건드리지 않는다. 로컬 supabase 컨테이너 안에 **새 DB 를 만들어**
# 거기에만 적용하고, 끝나면 지운다. 로컬 `postgres` DB 의 데이터도 손대지 않는다.
#
#   ./scripts/migration_rehearsal.sh          적용 + 검증 + 정리
#   KEEP=1 ./scripts/migration_rehearsal.sh   검증 후 DB 를 남긴다(사후 조사용)
set -euo pipefail

CONTAINER=${CONTAINER:-supabase_db_wearless_studio}
# 이름은 스크립트가 만든다. 밖에서 받은 이름을 그대로 DROP 하면 남의 DB 를 지운다.
DB_PREFIX="rehearsal_p3_"
DB_SUFFIX=${DB_SUFFIX:-$$}
DB="${DB_PREFIX}${DB_SUFFIX}"
case "$DB" in
  ${DB_PREFIX}[A-Za-z0-9_]*) : ;;
  *) echo "REFUSING: 안전하지 않은 DB 이름 ($DB)"; exit 2 ;;
esac
CREATED=0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIG="$ROOT/supabase/migrations"

psql_root() { docker exec -i "$CONTAINER" psql -U postgres -d postgres -v ON_ERROR_STOP=1 "$@"; }
psql_db()   { docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ON_ERROR_STOP=1 "$@"; }
q()         { docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" 2>&1 || true; }

fail=0
ok()   { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)) || true; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }

# 운영 DB 로 잘못 붙는 사고 방지 — 컨테이너 로컬이 아니면 즉시 중단.
# "사설 IP 니까 로컬"은 근거가 못 된다 — VPN 너머 운영도 사설 대역일 수 있다.
# 컨테이너가 이 머신의 docker 데몬에 있고, 이름이 로컬 supabase 규약이며, 운영에만
# 있는 흔적(대량 데이터)이 없다는 세 가지를 함께 본다.
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "REFUSING: 컨테이너를 찾을 수 없어요 ($CONTAINER)"; exit 2
fi
case "$CONTAINER" in
  supabase_db_*) : ;;
  *) echo "REFUSING: 로컬 supabase 컨테이너 이름이 아니에요 ($CONTAINER)"; exit 2 ;;
esac
prod_marker=$(docker exec "$CONTAINER" psql -U postgres -d postgres -tAc \
  "select coalesce((select count(*) from pg_database where datname in ('postgres')),0)" 2>/dev/null || echo "")
if [ -z "$prod_marker" ]; then
  echo "REFUSING: 컨테이너 postgres 에 붙지 못했어요"; exit 2
fi
users=$(docker exec "$CONTAINER" psql -U postgres -d postgres -tAc \
  "select count(*) from auth.users" 2>/dev/null || echo 0)
if [ "${users:-0}" -gt 1000 ]; then
  echo "REFUSING: 사용자 $users 명 — 운영으로 보이는 DB 입니다"; exit 2
fi

echo "== 0. disposable DB 준비 ($DB @ $CONTAINER) =="
# 이미 있으면 지우지 않는다 — 그 DB 가 무엇인지 우리가 모른다. 유일한 이름을 새로 찾는다.
for _try in 1 2 3 4 5; do
  exists=$(psql_root -tAc "select 1 from pg_database where datname = '$DB'" || true)
  [ -z "$exists" ] && break
  DB="${DB_PREFIX}${DB_SUFFIX}_${_try}"
  echo "  이름 충돌 회피 → $DB"
done
if [ -n "${exists:-}" ] && [ "$DB" = "${DB_PREFIX}${DB_SUFFIX}_5" ]; then
  echo "REFUSING: 유일한 DB 이름을 못 만들었어요"; exit 2
fi
psql_root -c "create database \"$DB\"" >/dev/null
CREATED=1

# 이 실행이 만든 DB 만 정리한다. 실패·중단에도 반드시 돈다.
cleanup() {
  local code=$?
  if [ "$CREATED" = "1" ] && [ "${KEEP:-0}" != "1" ]; then
    psql_root -c "drop database if exists \"$DB\" (force)" >/dev/null 2>&1 \
      && echo "disposable DB 삭제됨 ($DB)"
  elif [ "$CREATED" = "1" ]; then
    echo "DB 유지: $DB"
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

# supabase 가 제공하는 것들(이 레포의 migration 이 만들지 않는 전제) — 최소 대역.
psql_db <<'SQL' >/dev/null
create schema if not exists auth;
create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text,
  -- 20260613041903 의 signup 트리거가 이 필드를 읽는다. 없으면 insert 가 죽는다.
  raw_user_meta_data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create or replace function auth.uid() returns uuid language sql stable
  as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role; end if;
end $$;
SQL
echo "  auth 대역 준비 완료"
# 여기까지 못 왔으면 set -e 로 이미 죽는다 — migration 은 시작조차 하지 않는다.
psql_db -tAc "select 1 from auth.users limit 0" >/dev/null

echo
echo "== 1. 빈 DB 에 전체 적용 (순서대로) =="
applied=0
for f in "$MIG"/*.sql; do
  name=$(basename "$f")
  if out=$(psql_db < "$f" 2>&1); then
    applied=$((applied + 1))
    printf '  ok    %s\n' "$name"
  else
    bad "apply $name"
    printf '%s\n' "$out" | tail -5 | sed 's/^/        /'
    echo "== 첫 실패에서 중단 (남은 migration 은 의미 없음) =="
    break
  fi
done
echo "  적용됨: $applied / $(ls "$MIG"/*.sql | wc -l | tr -d ' ')"

echo
echo "== 2. 전체 재실행(멱등) =="
# Phase 3(20260801 이후)은 재실행 가능해야 한다. 그 이전 migration 의 비멱등성은
# 이번 작업이 만든 게 아니고 supabase 는 적용분을 추적하므로 운영 위험이 아니다.
legacy_fail=0; p3_fail=0
for f in "$MIG"/*.sql; do
  name=$(basename "$f")
  if ! out=$(psql_db < "$f" 2>&1); then
    if [[ "$name" > "20260801" ]]; then
      p3_fail=$((p3_fail + 1)); printf '  FAIL  rerun %s\n' "$name"
      printf '%s\n' "$out" | grep -i error | head -2 | sed 's/^/        /' || true
    else
      legacy_fail=$((legacy_fail + 1)); printf '  (pre-existing) %s\n' "$name"
    fi
  fi
done
check "Phase 3 migration 전체 재실행 가능" "$p3_fail" "0"
echo "  참고: Phase 3 이전 비멱등 migration $legacy_fail 건 (이번 작업 범위 밖)"

echo
echo "== 3. 스키마 계약 =="
check "edit_review_events 존재" \
  "$(q "select to_regclass('public.edit_review_events') is not null")" "t"
check "project_id FK = RESTRICT" \
  "$(q "select confdeltype from pg_constraint where conname='edit_review_events_project_id_fkey'")" "r"
check "edit_session_id FK = RESTRICT" \
  "$(q "select confdeltype from pg_constraint where conname='edit_review_events_edit_session_id_fkey'")" "r"
for col in wardrobe_image_id output_id actor_id; do
  check "$col FK = SET NULL" \
    "$(q "select confdeltype from pg_constraint c join pg_attribute a
            on a.attrelid=c.conrelid and a.attnum=c.conkey[1]
          where c.conrelid='public.edit_review_events'::regclass and a.attname='$col'")" "n"
done
check "decision CHECK" \
  "$(q "select count(*) from pg_constraint where conrelid='public.edit_review_events'::regclass
        and contype='c' and pg_get_constraintdef(oid) like '%accepted%rejected%'")" "1"
check "idempotency partial unique index" \
  "$(q "select indexdef like '%WHERE (idempotency_key IS NOT NULL)%'
        from pg_indexes where indexname='edit_review_events_idempotency'")" "t"
check "RLS 활성" \
  "$(q "select relrowsecurity from pg_class where oid='public.edit_review_events'::regclass")" "t"
check "owner-select 정책" \
  "$(q "select count(*) from pg_policies where tablename='edit_review_events'")" "1"
check "append-only 트리거(row-level)" \
  "$(q "select tgtype & 1 = 1 from pg_trigger where tgname='edit_review_events_append_only'")" "t"
check "edit_sessions status CHECK" \
  "$(q "select count(*)>0 from pg_constraint where conrelid='public.edit_sessions'::regclass
        and contype='c' and pg_get_constraintdef(oid) like '%review_required%'")" "t"
check "approved_baselines partial unique" \
  "$(q "select count(*)>0 from pg_indexes where tablename='approved_baselines'
        and indexdef like '%superseded_at IS NULL%'")" "t"

echo
echo "== 4. 행 동작 =="
seed_out=$(psql_db <<'SQL' 2>&1
insert into auth.users (id, email) values
  ('11111111-1111-4111-8111-111111111111', 'a@x.test'),
  ('22222222-2222-4222-8222-222222222222', 'b@x.test');
insert into public.projects (id, user_id, title)
  values ('33333333-3333-4333-8333-333333333333',
          '11111111-1111-4111-8111-111111111111', 'rehearsal');
insert into public.assets (id, user_id, project_id, source, r2_bucket, r2_key, mime_type)
  values ('55555555-5555-4555-8555-555555555555',
          '11111111-1111-4111-8111-111111111111',
          '33333333-3333-4333-8333-333333333333', 'upload', 'b', 'k/1.png', 'image/png');
insert into public.edit_sessions
  (id, project_id, edit_type, requested_adjustments, locked_invariants, allowed_scope,
   status, created_by, completed_at, source_kind, source_asset_id)
  values ('44444444-4444-4444-8444-444444444444',
          '33333333-3333-4333-8333-333333333333', 'BACKGROUND_ONLY',
          '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, 'review_required',
          '11111111-1111-4111-8111-111111111111', now(),
          'editor_asset', '55555555-5555-4555-8555-555555555555');
SQL
)
echo "$seed_out" | grep -i error | head -3 | sed 's/^/        /' || true
check "edit_session seed" \
  "$(q "select count(*) from public.edit_sessions")" "1"

q "insert into public.edit_review_events
     (project_id, edit_session_id, actor_id, decision, idempotency_key)
   values ('33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444',
           '11111111-1111-4111-8111-111111111111','accepted','k1')" >/dev/null
check "이벤트 1건 삽입" "$(q "select count(*) from public.edit_review_events")" "1"

dup=$(q "insert into public.edit_review_events
           (project_id, edit_session_id, actor_id, decision, idempotency_key)
         values ('33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444',
                 '11111111-1111-4111-8111-111111111111','rejected','k1')
         on conflict (edit_session_id, idempotency_key)
           where idempotency_key is not null do nothing
         returning id" 2>&1)
check "같은 키 재삽입 = 0행(트랜잭션 생존)" \
  "$(echo "$dup" | grep -cE '^[0-9]+$' || true)" "0"
check "행은 여전히 1건" "$(q "select count(*) from public.edit_review_events")" "1"

nokey1=$(q "insert into public.edit_review_events
              (project_id, edit_session_id, decision) values
              ('33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444','rejected')
            returning id" 2>&1)
nokey2=$(q "insert into public.edit_review_events
              (project_id, edit_session_id, decision) values
              ('33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444','rejected')
            returning id" 2>&1)
check "키 없는 행은 인덱스 밖(둘 다 저장)" \
  "$([ -n "$nokey1" ] && [ -n "$nokey2" ] && [ "$nokey1" != "$nokey2" ] && echo t || echo f)" "t"

upd=$(q "update public.edit_review_events set decision='rejected' where idempotency_key='k1'" 2>&1)
check "UPDATE 차단" "$(echo "$upd" | grep -c 'append-only' || true)" "1"
del=$(q "delete from public.edit_review_events where idempotency_key='k1'" 2>&1)
check "DELETE 차단" "$(echo "$del" | grep -c 'append-only' || true)" "1"

# (a) 프로젝트 소유자가 아닌 검수자 — 계정이 사라져도 "검수가 있었다"는 남아야 한다.
q "insert into public.edit_review_events
     (project_id, edit_session_id, actor_id, decision, idempotency_key)
   values ('33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444',
           '22222222-2222-4222-8222-222222222222','accepted','k-other')" >/dev/null
before_actor=$(q "select count(*) from public.edit_review_events")
otherdel=$(q "delete from auth.users where id='22222222-2222-4222-8222-222222222222'" 2>&1)
if echo "$otherdel" | grep -qi 'append-only'; then
  bad "actor set null 이 append-only 트리거에 막힘 (FK 파생 예외가 안 먹음)"
elif echo "$otherdel" | grep -qi 'violates'; then
  bad "비소유 actor 삭제가 막힘: $(echo "$otherdel" | head -1)"
else
  check "검수자 계정 삭제 후에도 이력 보존" \
    "$(q "select count(*) from public.edit_review_events")" "$before_actor"
  check "그 행의 actor_id 만 null 로" \
    "$(q "select actor_id is null from public.edit_review_events where idempotency_key='k-other'")" "t"
  check "판단 자체는 그대로" \
    "$(q "select decision from public.edit_review_events where idempotency_key='k-other'")" "accepted"
fi

# (b) 프로젝트 **소유자** 삭제 = 계정 hard delete. RESTRICT 라 막힌다 — 의도된 대가다.
#     같은 성질의 선례(credit_ledger)가 이미 있는지 함께 잰다.
ownerdel=$(q "delete from auth.users where id='11111111-1111-4111-8111-111111111111'" 2>&1)
check "소유자 hard delete 차단(감사 이력 보존)" \
  "$(echo "$ownerdel" | grep -ci 'violates foreign key' || true)" "1"
echo "        차단 주체: $(echo "$ownerdel" | grep -o 'constraint "[^"]*"' | head -1 || true)"
q "insert into auth.users (id, email) values ('66666666-6666-4666-8666-666666666666','c@x.test')" >/dev/null
ledger_ins=$(q "insert into public.credit_ledger
  (user_id, action_key, delta, balance_after, available_after)
  values ('66666666-6666-4666-8666-666666666666', 'grant', 10, 10, 10)" 2>&1)
if echo "$ledger_ins" | grep -qi error; then echo "        (선례 측정 실패: $ledger_ins)"; fi
ledgerdel=$(q "delete from auth.users where id='66666666-6666-4666-8666-666666666666'" 2>&1)
if echo "$ledgerdel" | grep -qi 'violates\|append-only'; then
  echo "        선례: credit_ledger 도 같은 이유로 계정 hard delete 를 막는다 (신규 제약 아님)"
else
  echo "        주의: credit_ledger 는 계정 삭제를 막지 않는다 — 이번 RESTRICT 가 새 차단 클래스다"
fi

sdel=$(q "delete from public.edit_sessions where id='44444444-4444-4444-8444-444444444444'" 2>&1)
check "edit_session hard delete = RESTRICT" "$(echo "$sdel" | grep -ci 'violates foreign key' || true)" "1"
pdel=$(q "delete from public.projects where id='33333333-3333-4333-8333-333333333333'" 2>&1)
check "project hard delete = RESTRICT" "$(echo "$pdel" | grep -ci 'violates foreign key' || true)" "1"

echo
echo "== 5. 동시 동일 키 insert (실 커넥션 2개) =="
race() {
  docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "
    begin;
    select pg_sleep(0.4);
    insert into public.edit_review_events
      (project_id, edit_session_id, decision, idempotency_key)
    values ('33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444',
            'accepted','race-$1')
    on conflict (edit_session_id, idempotency_key)
      where idempotency_key is not null do nothing
    returning id;
    select 'alive-after-conflict' as probe;
    commit;" 2>&1
}
before=$(q "select count(*) from public.edit_review_events")
race k >"$ROOT/.rehearsal_a.log" 2>&1 &
race k >"$ROOT/.rehearsal_b.log" 2>&1 &
wait
after=$(q "select count(*) from public.edit_review_events")
check "동시 동일 키 → 행 1건만 증가" "$((after - before))" "1"
check "충돌 쪽 트랜잭션 생존" \
  "$(cat "$ROOT/.rehearsal_a.log" "$ROOT/.rehearsal_b.log" | grep -c 'alive-after-conflict' || true)" "2"
rm -f "$ROOT/.rehearsal_a.log" "$ROOT/.rehearsal_b.log"

echo
echo "== 6. legacy row 조회 fallback =="
# Phase 3 컬럼이 아직 없던 시절의 행처럼 — edit_session 이 없는 wardrobe_images.
q "insert into public.wardrobe_images (project_id, asset_id, ai)
   values ('33333333-3333-4333-8333-333333333333',
           '55555555-5555-4555-8555-555555555555', false)" >/dev/null 2>&1
check "edit_session_id 없는 legacy row 조회" \
  "$(q "select count(*) from public.wardrobe_images where edit_session_id is null")" "1"
check "qc_status 컬럼 nullable" \
  "$(q "select is_nullable from information_schema.columns
        where table_name='wardrobe_images' and column_name='qc_status'")" "YES"

echo
echo "== 7. RLS 소유권 (authenticated 역할로) =="
q "grant usage on schema public to authenticated" >/dev/null
q "grant select on public.edit_review_events, public.projects to authenticated" >/dev/null
as_user() {
  docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "
    begin;
    set local role authenticated;
    set local request.jwt.claim.sub = '$1';
    $2
    commit;" 2>&1 | grep -vE '^(BEGIN|SET|COMMIT|ROLLBACK)' | grep -v '^[[:space:]]*$' | head -1 || true
}
check "타 사용자에게는 0건" \
  "$(as_user 99999999-9999-4999-8999-999999999999 'select count(*) from public.edit_review_events;')" "0"
rls_own=$(as_user 11111111-1111-4111-8111-111111111111 'select count(*) from public.edit_review_events;')
check "소유자에게는 보임(0 아님)" \
  "$([ "${rls_own:-0}" -gt 0 ] 2>/dev/null && echo t || echo f)" "t"
ins=$(as_user 11111111-1111-4111-8111-111111111111 "insert into public.edit_review_events (project_id, edit_session_id, decision) values ('33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444','accepted');")
check "authenticated 직접 INSERT 차단" \
  "$(echo "$ins" | grep -ci 'permission denied\|violates row-level' || true)" "1"

echo
echo "== 결과 =="
if [ "$fail" = 0 ]; then echo "ALL PASS"; else echo "FAILURES: $fail"; fi
exit "$fail"
