#!/usr/bin/env bash
# 로컬 QA — 모델 지원 검토 콘솔(admin.wearless.kr) 접근용 관리자 지정.
#
# 인증은 프로드 Supabase(JWKS)라 로컬 auth.users 는 비어 있고, 관리자 판정은 로컬
# public.profiles.role = 'admin' 로 한다(repo.is_admin). 로그인한 뒤 이 스크립트를 돌려
# 자신을 관리자로 올린다.
#
# 사용법:
#   scripts/qa_grant_admin.sh <user_id>        # 특정 user_id(=Supabase sub)를 admin 으로
#   scripts/qa_grant_admin.sh --applicants     # 지원서를 낸 모든 user_id 를 admin 으로(제출 1회 후)
#   scripts/qa_grant_admin.sh --list           # 현재 profiles.role 목록
#
# user_id 를 모르면: 로그인 후 지원서를 한 번 제출 → `--applicants` 로 일괄 지정하거나,
# 브라우저 콘솔에서 (await supabase.auth.getUser()).data.user.id 로 확인.

set -euo pipefail
PSQL="${PSQL:-/opt/homebrew/opt/libpq/bin/psql}"
DBURL="${DATABASE_URL:-postgresql://postgres:postgres@127.0.0.1:54322/postgres}"

grant() {
  # profiles.user_id 는 auth.users FK 다. 인증은 프로드 Supabase 라 로컬 auth.users 는
  # 비어 있으므로, 로그인한 sub 용 최소 shell 행을 먼저 만든 뒤 profiles 를 admin 으로 올린다.
  "$PSQL" "$DBURL" -v ON_ERROR_STOP=1 -c \
    "insert into auth.users (id, email) values ('$1', 'qa+$1@local')
       on conflict (id) do nothing;
     insert into public.profiles (user_id, role) values ('$1', 'admin')
       on conflict (user_id) do update set role = 'admin';"
  echo "✓ admin granted: $1"
}

case "${1:-}" in
  --list)
    "$PSQL" "$DBURL" -c "select user_id, role from public.profiles order by role"
    ;;
  --applicants)
    ids=$("$PSQL" "$DBURL" -t -A -c "select distinct user_id from public.fm_model_applications")
    [ -z "$ids" ] && { echo "지원서가 아직 없어요 — 먼저 /model/apply 에서 제출하세요."; exit 1; }
    while IFS= read -r id; do [ -n "$id" ] && grant "$id"; done <<< "$ids"
    ;;
  "" )
    echo "usage: $0 <user_id> | --applicants | --list"; exit 1
    ;;
  *)
    grant "$1"
    ;;
esac
