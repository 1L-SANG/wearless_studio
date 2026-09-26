"""지문 백필 스크립트 — 기본 dry-run 은 DB 에 쓰지 않는다, --apply 만 insert(2026-09-26).

prod 에 돌리지 않는다. 가짜 커넥션·R2 로 분기만 잠근다.
"""
import io

from scripts import fm_fingerprint_backfill as B
from scripts.fm_trace_robustness import procedural_photo


def _png(seed, w=900, h=1300):
    buf = io.BytesIO()
    procedural_photo(w, h, seed).save(buf, "PNG")
    return buf.getvalue()


class Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        if s.startswith("select r.id::text as id"):
            self.rows = self.conn.cuts
        elif s.startswith("select p.id::text as id"):
            self.rows = self.conn.pubs
        else:
            self.conn.writes.append((s, params))

    def executemany(self, sql, seq):
        self.conn.writes.extend((" ".join(sql.split()).lower(), p) for p in seq)

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, cuts, pubs):
        self.cuts, self.pubs, self.writes, self.commits = cuts, pubs, [], 0

    def cursor(self):
        return Cur(self)

    def commit(self):
        self.commits += 1


class R2:
    def __init__(self, objects):
        self.objects = objects

    def get_bytes(self, key):
        return self.objects[key]


def _world():
    objects = {"cut/1.png": _png(1), "cut/2.png": b"broken", "pub/1.png": _png(2, 1000, 2600)}
    conn = Conn(
        cuts=[{"id": "o1", "r2_bucket": None, "r2_key": "cut/1.png"},
              {"id": "o2", "r2_bucket": None, "r2_key": "cut/2.png"},
              {"id": "o3", "r2_bucket": None, "r2_key": "cut/missing.png"}],
        pubs=[{"id": "p1", "kind": "long_png", "r2_key": "pub/1.png"}],
    )
    return conn, R2(objects)


def test_dry_run_computes_but_never_writes():
    conn, r2 = _world()
    assert B.backfill_cuts(conn, lambda _b: r2, limit=10, apply=False) == (1, 2)
    ok, failed = B.backfill_publications(conn, r2, limit=10, apply=False)
    assert (ok, failed) == (1, 0)
    assert conn.writes == [] and conn.commits == 0


def test_apply_inserts_idempotently():
    conn, r2 = _world()
    B.backfill_cuts(conn, lambda _b: r2, limit=10, apply=True)
    B.backfill_publications(conn, r2, limit=10, apply=True)
    cut_writes = [w for w in conn.writes if "values (%s, 'cut'" in w[0]]
    pub_writes = [w for w in conn.writes if "(publication_id, kind" in w[0]]
    assert [w[1][0] for w in cut_writes] == ["o1"]
    assert pub_writes and all(w[1][0] == "p1" for w in pub_writes)
    assert all("on conflict do nothing" in w[0] for w in conn.writes)
    assert {w[1][1] for w in pub_writes} == {"publication", "strip"}
