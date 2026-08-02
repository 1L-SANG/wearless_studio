"""Phase 3 P0-C 9/N 보정 — 리허설 스크립트가 남의 DB 를 지우지 않는가.

이 스크립트는 DROP DATABASE 를 하는 유일한 코드다. 검증 로직이 아무리 좋아도
잘못된 DB 를 지우면 그걸로 끝이라, 파괴 경로만 따로 고정한다.
"""

import pathlib
import re
import subprocess

import pytest

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
    # glob 이 아니라 앵커된 정규식으로 조인다.
    assert 'grep -Eq "^${DB_PREFIX}[A-Za-z0-9_]+$"' in s
    assert "REFUSING: 안전하지 않은 DB 이름" in s


def test_an_existing_database_is_never_dropped_at_startup():
    """시작할 때 같은 이름이 있으면 그 DB 가 무엇인지 우리가 모른다."""
    s = src()
    # cleanup() 정의는 실행이 아니라 종료 훅이라 제외한다.
    startup = s[:s.index("cleanup() {")]
    assert "drop database" not in startup.lower()
    assert "이름 충돌 회피" in startup


def test_every_drop_targets_a_psql_identifier_variable():
    """식별자를 셸 문자열로 이어 붙이지 않는다 — psql 이 인용하게 맡긴다."""
    drops = [m.group(0) for m in re.finditer(r"drop database[^\n]*", src())]
    assert drops
    for d in drops:
        assert ':"db"' in d, d


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


# ── 적대 suffix (9/N 최종 보정) ─────────────────────────────────────────────
# 이 스크립트는 DROP DATABASE 를 한다. 이름을 만드는 경로에 남의 입력이 닿으면
# 그 순간 임의 DB 삭제가 된다. 그러니 적대 입력에서 CREATE/DROP 이 0회여야 한다.

HOSTILE = [
    'x"; drop database postgres; --',
    "x'; drop database postgres; --",
    "postgres",
    "a b",
    "a\nb",
    "$(whoami)",
    "`id`",
    "a;b",
    "../../etc",
    "",
    "-",
    "a-b",
]


@pytest.mark.parametrize("suffix", HOSTILE)
def test_hostile_suffix_never_reaches_create_or_drop(suffix, tmp_path):
    """docker 를 가짜로 바꿔 호출을 기록한다 — 실제 DB 는 건드리지 않는다."""
    import os
    import subprocess as sp
    fake = tmp_path / "bin"
    fake.mkdir()
    calls = tmp_path / "calls.log"
    (fake / "docker").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {calls}\n'
        'if [ "$1" = "inspect" ]; then exit 0; fi\n'
        # stdin 으로 들어오는 SQL 도 남긴다.
        f'cat >> {calls} 2>/dev/null\n'
        "exit 0\n")
    (fake / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}",
           "REHEARSAL_DB_SUFFIX": suffix}
    r = sp.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True,
               timeout=60)
    log = calls.read_text() if calls.exists() else ""
    assert "create database" not in log.lower(), f"{suffix!r} 로 CREATE 가 실행됨"
    assert "drop database" not in log.lower(), f"{suffix!r} 로 DROP 이 실행됨"
    assert r.returncode != 0
    assert "REFUSING" in (r.stdout + r.stderr)


def test_the_suffix_regex_is_a_full_anchor_not_a_glob():
    s = src()
    assert "grep -Eq '^[A-Za-z0-9_]+$'" in s
    # case 글로브로 검증하던 흔적이 남아 있으면 안 된다.
    assert "${DB_PREFIX}[A-Za-z0-9_]*)" not in s


def test_the_default_name_takes_no_external_input():
    s = src()
    assert 'DB_SUFFIX="$(_rand)"' in s
    assert "uuidgen" in s and "/dev/urandom" in s
    assert "DB_SUFFIX=${DB_SUFFIX:-$$}" not in s


def test_identifiers_go_through_psql_variables():
    s = src()
    assert 'create database :"db";' in s
    assert 'drop database if exists :"db" (force);' in s
    assert "datname = :'db';" in s          # 리터럴도 변수로
    # 셸 문자열을 SQL 에 이어 붙이던 흔적이 없어야 한다.
    assert '\\"$DB\\"' not in s
    assert "'$DB'" not in s


def test_psql_variables_are_fed_through_stdin_not_dash_c():
    """psql -c 는 변수 보간을 하지 않는다 — -c 로 넘기면 :\"db\" 가 그대로 SQL 에 간다."""
    s = src()
    assert "psql_root_sql()" in s
    assert '-c \'create database' not in s
