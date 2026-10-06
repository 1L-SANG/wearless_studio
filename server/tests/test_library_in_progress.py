"""만드는 중 목록 SQL/HTTP 계약. 외부 DB 없이 메모리 SQLite에서 조건과 정렬을 실행한다.

LATERAL은 상관 스칼라 조회로 옮기고 JSON 함수 이름/UUID 형식만 번역한다.
Postgres 실행 계획과 실제 인덱스 비용은 검증하지 않는다.
"""
import asyncio
import json
import re
import sqlite3
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app import repo, routes
from conftest import auth_headers, assert_query_binds, patch_route_db
from test_library_cover_thumb import _FakeR2


class _ListConn:
    def __init__(self):
        self.calls = []
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.create_function("greatest", -1, lambda *args: max(x for x in args if x is not None))
        self.db.create_function("regexp", 2, lambda pattern, value: bool(value and re.search(pattern, value, re.I)))
        self.db.executescript("""
            create table projects(id text primary key, user_id text, status text, deleted_at text,
                updated_at text, storyboard text, editor_blocks text, selected_mannequin_id text);
            create table products(project_id text, name text, colors text);
            create table analyses(project_id text, payload text);
            create table assets(id text primary key, deleted_at text, r2_key text, source text, metadata text);
            create table mannequin_cuts(project_id text, asset_id text, candidate text, version integer);
            create table jobs(id text, project_id text, user_id text, kind text, status text, result text,
                created_at text, updated_at text);
        """)

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, sql, params):
        assert_query_binds(sql, params)
        self.calls.append((sql, params))
        query = sql
        query = query.replace("select kinds.kind, latest.summary", "select kinds.kind, latest.summary as summary")
        query = query.replace("from (values ('mannequin'), ('detail_page')) kinds(kind)",
                              "from (select 'mannequin' as kind union all select 'detail_page') kinds")
        required = []
        lateral = re.compile(r"(left )?join lateral \((.*?)\) (\w+) on true", re.S)
        while match := lateral.search(query):
            optional, body, alias = match.groups()
            query = query[:match.start()] + query[match.end():]
            if not optional:
                required.append(f"(select * from ({body})) is not null")
            query = re.sub(rf"\b{alias}\.(\w+)", lambda m: f"(select {m[1]} from ({body}))", query)
            required = [re.sub(rf"\b{alias}\.(\w+)", lambda m: f"(select {m[1]} from ({body}))", x)
                        for x in required]
        query = query.replace("where pr.user_id = %s", "where " + " and ".join(required) + " and pr.user_id = %s")
        query = re.sub(r"with ordinality col\(value, position\)", "col", query)
        query = query.replace("col.position", "col.key")
        query = re.sub(r"\bim\s*->", "im.value->", query)
        query = re.sub(r"im.value->>'id' ~\*\s*('[^']+')", r"regexp(\1, im.value->>'id')", query)
        for old, new in (("jsonb_array_elements", "json_each"), ("jsonb_array_length", "json_array_length"),
                         ("jsonb_typeof", "json_type"), ("jsonb_build_object", "json_object"), ("btrim", "trim")):
            query = query.replace(old, new)
        query = re.sub(r"::(?:text|jsonb|uuid)", "", query).replace("%s", "?")
        self.result = self.db.execute(query, params)

    async def fetchall(self):
        rows = [dict(row) for row in self.result.fetchall()]
        for row in rows:
            for key in ("latest_mannequin_job", "latest_detail_page_job", "cover_metadata", "summary"):
                if row.get(key) is not None:
                    row[key] = json.loads(row[key])
        return rows

    def project(self, *, signal=None, photo=True, asset_deleted=False, status="draft", user="u1",
                deleted=None, name="", updated="2026-10-01", colors=None, selected=None, blocks=None):
        pid, aid = str(uuid4()), str(uuid4())
        if colors is None:
            colors = [{"images": [{"slot": "Front", "id": aid}]}] if photo else []
        self.db.execute("insert into projects values (?, ?, ?, ?, ?, ?, ?, ?)",
                        (pid, user, status, deleted, updated, json.dumps([{}] if signal == "storyboard" else []),
                         json.dumps(blocks or []), selected))
        self.db.execute("insert into products values (?, ?, ?)", (pid, name, json.dumps(colors)))
        self.db.execute("insert into assets values (?, ?, ?, 'upload', '{}')",
                        (aid, "deleted" if asset_deleted else None, "photo.png"))
        if signal in ("mannequin", "detail_page"):
            self.job(pid, signal, user=user)
        if signal == "cut":
            self.db.execute("insert into mannequin_cuts values (?, ?, 'A', 1)", (pid, aid))
        return pid, aid

    def job(self, pid, kind, *, status="done", user="u1", created="2026-10-01", updated="2026-10-01", error=None):
        jid = str(uuid4())
        self.db.execute("insert into jobs values (?, ?, ?, ?, ?, ?, ?, ?)",
                        (jid, pid, user, kind, status, json.dumps({"errorCode": error}), created, updated))
        return jid

    def rows(self):
        return asyncio.run(repo.list_in_progress(self, "u1"))


def test_pristine_and_analysis_only_drafts_do_not_qualify():
    conn = _ListConn()
    conn.project(photo=False)
    pid, _ = conn.project()
    conn.db.execute("insert into analyses values (?, ?)", (pid, json.dumps({"fit": "regular"})))
    assert conn.rows() == []
    assert "analyses" not in conn.calls[0][0]


@pytest.mark.parametrize("signal", ["storyboard", "mannequin", "cut", "detail_page"])
def test_each_progress_signal_qualifies_with_a_front_asset(signal):
    conn = _ListConn()
    pid, aid = conn.project(signal=signal)
    row, = conn.rows()
    assert row["id"] == pid and row["title"] == "새 상품"
    assert row["cover"] == f"/v1/assets/{aid}/file"


@pytest.mark.parametrize("kind", ["no_photo", "deleted_asset", "missing_asset", "malformed_asset"])
def test_front_photo_must_have_a_non_deleted_asset(kind):
    conn = _ListConn()
    pid, aid = conn.project(signal="storyboard", photo=kind != "no_photo", asset_deleted=kind == "deleted_asset")
    if kind == "missing_asset":
        conn.db.execute("delete from assets where id = ?", (aid,))
    if kind == "malformed_asset":
        conn.db.execute("update products set colors = ? where project_id = ?",
                        (json.dumps([{"images": [{"slot": "Front", "id": "bad-id"}]}]), pid))
    assert conn.rows() == []


@pytest.mark.parametrize("base", [False, True])
def test_base_color_else_first_color_cannot_borrow_another_colors_photo(base):
    conn = _ListConn()
    pid, aid = conn.project(signal="storyboard")
    colors = [{"images": []}, {"isBase": base, "images": [{"slot": "Front", "id": aid}]}]
    conn.db.execute("update products set colors = ? where project_id = ?", (json.dumps(colors), pid))
    assert [row["id"] for row in conn.rows()] == ([pid] if base else [])


@pytest.mark.parametrize("override", [{"status": "done"}, {"user": "other"}, {"deleted": "deleted"}])
def test_done_deleted_and_other_users_are_excluded(override):
    conn = _ListConn()
    conn.project(signal="storyboard", **override)
    assert conn.rows() == []
    assert conn.calls[0][1] == ("u1",)


def test_single_query_limits_twenty_and_sorts_by_project_or_latest_job_activity():
    conn = _ListConn()
    pids = [conn.project(signal="storyboard", updated=f"2026-10-{i:02d}")[0] for i in range(1, 23)]
    conn.job(pids[0], "analyze", updated="2026-11-01")
    conn.job(pids[1], "detail_page", user="other", updated="2026-12-01")
    rows = conn.rows()
    assert len(rows) == 20 and len(conn.calls) == 1
    assert rows[0]["id"] == pids[0] and rows[0]["updated_at"] == "2026-11-01"
    assert [row["id"] for row in rows[1:]] == list(reversed(pids[3:]))


@pytest.mark.parametrize("status,blocks,selected,cut,step", [
    ("pending", [], "A-1", True, "generating"),
    ("running", [{}], "A-1", True, "generating"),
    ("error", [], "A-1", True, "failed"),
    ("cancelled", [], None, False, "failed"),
    ("error", [{}], "A-1", True, "mannequin"),
    ("cancelled", [{}], None, False, "storyboard"),
    ("done", [], "A-1", True, "mannequin"),
    (None, [], "A-1", True, "mannequin"),
    (None, [], "A-99", True, "storyboard"),
    (None, [], "A-1", False, "storyboard"),
    (None, [], None, True, "storyboard"),
])
def test_step_mapping(status, blocks, selected, cut, step):
    conn = _ListConn()
    pid, _ = conn.project(signal="cut" if cut else "storyboard", selected=selected, blocks=blocks)
    if status:
        conn.job(pid, "detail_page", status=status)
    row, = conn.rows()
    assert row["step"] == step


def test_latest_summaries_use_creation_order_and_error_code_not_old_job_updates():
    conn = _ListConn()
    pid, _ = conn.project(signal="storyboard", name="상품명")
    conn.job(pid, "mannequin", created="2026-10-01", updated="2026-11-01", status="error", error="old")
    latest = conn.job(pid, "mannequin", created="2026-10-02", status="cancelled", error="cancelled")
    row, = conn.rows()
    assert row["latest_mannequin_job"] == {"id": latest, "status": "cancelled", "errorCode": "cancelled"}
    assert row["latest_detail_page_job"] is None and row["title"] == "상품명"


def test_latest_project_jobs_query_scopes_both_kinds_to_user_and_project():
    conn = _ListConn()
    pid, _ = conn.project(signal="storyboard")
    conn.job(pid, "mannequin", created="2026-10-01", updated="2026-12-01")
    jid = conn.job(pid, "mannequin", created="2026-10-02", status="error", error="quality_failed")
    conn.job(pid, "detail_page", user="other", created="2026-11-01")
    conn.job("other-project", "detail_page", created="2026-11-01")
    jobs = asyncio.run(repo.get_latest_project_jobs(conn, "u1", pid))
    assert jobs == {"mannequin": {"id": jid, "status": "error", "errorCode": "quality_failed"}, "detailPage": None}
    assert len(conn.calls) == 1 and conn.calls[0][1] == ("u1", pid)


@pytest.mark.parametrize("view", ["", "?view=library", "?view=unknown", "?view=in_progress"])
def test_route_selects_only_new_view_and_preserves_existing_list_shape(client, make_token, monkeypatch, view):
    patch_route_db(monkeypatch, routes)
    common = {"id": "p1", "title": "상품", "cover": "/v1/assets/a1/file", "updated_at": "2026-10-01T00:00:00Z",
              "cover_r2_key": "cut.png", "cover_source": "ai", "cover_metadata": {"facemarket_real_derived": False}}
    library = AsyncMock(return_value=[{**common, "clothing_type": None, "block_count": 1, "status": "done"}])
    progress = AsyncMock(return_value=[{**common, "step": "storyboard", "latest_mannequin_job": None,
                                       "latest_detail_page_job": None}])
    monkeypatch.setattr(repo, "list_library", library)
    monkeypatch.setattr(repo, "list_in_progress", progress)
    client.app.state.r2 = _FakeR2()
    response = client.get(f"/v1/projects{view}", headers=auth_headers(make_token))
    assert response.status_code == 200, response.text
    row, = response.json()
    assert row["cover"].endswith("width=440,quality=80,format=auto,fit=cover/cut.png")
    if view == "?view=in_progress":
        library.assert_not_awaited()
        assert set(row) == {"id", "title", "cover", "updatedAt", "step", "latestMannequinJob", "latestDetailPageJob"}
    else:
        progress.assert_not_awaited()
        assert set(row) == {"id", "title", "cover", "updatedAt", "clothingType", "blockCount", "status"}


@pytest.mark.parametrize("jobs", [
    {"mannequin": None, "detailPage": None},
    {"mannequin": {"id": "m1", "status": "running", "errorCode": None},
     "detailPage": {"id": "d1", "status": "error", "errorCode": "failed"}},
])
def test_project_detail_exposes_latest_jobs(client, make_token, monkeypatch, jobs):
    patch_route_db(monkeypatch, routes)
    project = {"id": "p1", "status": "draft", "title": "", "compose_mode": "basic", "copywriting": True,
               "selected_mannequin_id": None, "adjust_count": 0,
               "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value=project))
    latest = AsyncMock(return_value=jobs)
    monkeypatch.setattr(repo, "get_latest_project_jobs", latest)
    response = client.get("/v1/projects/p1", headers=auth_headers(make_token))
    assert response.status_code == 200, response.text
    assert response.json()["latestJobs"] == jobs
    assert latest.call_args.args[1:] == ("user-1", "p1")


def test_project_detail_owner_failure_does_not_read_jobs(client, make_token, monkeypatch):
    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value=None))
    latest = AsyncMock()
    monkeypatch.setattr(repo, "get_latest_project_jobs", latest)
    assert client.get("/v1/projects/p1", headers=auth_headers(make_token)).status_code == 404
    latest.assert_not_awaited()
