"""커넥션풀이 죽은 커넥션을 그대로 내주지 않는지."""

from psycopg_pool import AsyncConnectionPool

from app import db


def test_pool_checks_connections_before_handing_them_out():
    """Supabase 풀러(Supavisor)는 NLB 뒤 여러 노드로 떠 있고, 우리 커넥션은 그중 한 노드에
    고정된다. 그 노드가 교체되면 풀이 들고 있던 커넥션이 한꺼번에 죽는다 —
    2026-09-09 프로덕션에서 12분간 그랬다(`SSL connection has been closed unexpectedly`).

    check 훅이 없으면 풀은 죽은 커넥션을 **검사 없이 내주고**, 쓰는 쪽이 처음 execute 에서
    터진다. 디스패처·스윕이 각자 예외를 던지고 그게 전부 알림으로 나간다. 훅이 있으면
    풀이 대여 전에 걸러 조용히 새로 맺는다(대여마다 왕복 1회가 값이다).
    """
    pool = db.create_pool("postgresql://u:p@localhost:5432/postgres")
    assert pool._check is AsyncConnectionPool.check_connection
