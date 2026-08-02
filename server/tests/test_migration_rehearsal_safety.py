"""Phase 3 P0-C 9/N 보정 — 리허설 스크립트가 남의 DB 를 지우지 않는가.

이 스크립트는 DROP DATABASE 를 하는 유일한 코드다. 검증 로직이 아무리 좋아도
잘못된 DB 를 지우면 그걸로 끝이라, 파괴 경로만 따로 고정한다.
"""

import pathlib
import re
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "migration_rehearsal.sh"


def src():
    return SCRIPT.read_text(encoding="utf-8")


def test_the_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_it_fails_fast():
    assert re.search(r"^set -euo pipefail$", src(), re.M)


def test_the_db_name_is_generated_not_taken_from_outside():
    s = src()
    assert 'DB="${DB_PREFIX}${DB_SUFFIX}"' in s
    # 외부 DB 변수를 그대로 쓰면 아무 DB 나 지울 수 있다.
    assert "DB=${DB:-" not in s


def test_only_the_prefixed_name_is_allowed():
    s = src()
    assert 'case "$DB" in' in s and "${DB_PREFIX}[A-Za-z0-9_]*" in s
    assert "REFUSING: 안전하지 않은 DB 이름" in s


def test_an_existing_database_is_never_dropped_at_startup():
    """시작할 때 같은 이름이 있으면 그 DB 가 무엇인지 우리가 모른다."""
    s = src()
    # cleanup() 정의는 실행이 아니라 종료 훅이라 제외한다.
    startup = s[:s.index("cleanup() {")]
    assert "drop database" not in startup.lower()
    assert "이름 충돌 회피" in startup


def test_every_drop_targets_a_quoted_identifier():
    for m in re.finditer(r"drop database[^\n]*", src()):
        assert '\\"$DB\\"' in m.group(0), m.group(0)


def test_cleanup_only_removes_what_this_run_created():
    s = src()
    assert "CREATED=0" in s and "CREATED=1" in s
    cleanup = s[s.index("cleanup() {"):s.index("trap cleanup")]
    assert '[ "$CREATED" = "1" ]' in cleanup
    assert "drop database" in cleanup


def test_cleanup_runs_on_failure_and_interrupt():
    assert "trap cleanup EXIT INT TERM" in src()


def test_keep_skips_the_drop():
    cleanup = src()[src().index("cleanup() {"):src().index("trap cleanup")]
    assert '"${KEEP:-0}" != "1"' in cleanup


def test_private_ip_alone_does_not_prove_local():
    s = src()
    assert "inet_server_addr" not in s          # 사설 IP 판정 제거
    assert 'docker inspect "$CONTAINER"' in s
    assert "supabase_db_*)" in s
    assert "운영으로 보이는 DB" in s


def test_a_populated_database_is_refused():
    """운영에는 사용자가 많다 — 그 흔적이 보이면 시작하지 않는다."""
    s = src()
    assert "select count(*) from auth.users" in s
    assert re.search(r'\$\{users:-0\}" -gt \d+', s)


def test_migrations_do_not_run_if_preparation_failed():
    s = src()
    prep_end = s.index("== 1. 빈 DB 에 전체 적용")
    prep = s[:prep_end]
    # 준비 확인 쿼리가 migration 루프보다 앞에 있고, set -e 로 실패 시 중단된다.
    assert "select 1 from auth.users limit 0" in prep


def test_the_seed_only_touches_the_rehearsal_database():
    s = src()
    body = s[s.index("== 4. 행 동작"):]
    assert "psql_root" not in body                 # 관리 DB 에 쓰지 않는다
