import asyncio

from app import repo


class _Cursor:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_list_active_matching_items_uses_two_explicit_security_arms():
    rows = [{"id": "curated"}, {"id": "custom"}]
    cursor = _Cursor(rows=rows)
    result = asyncio.run(repo.list_active_matching_items(
        _Conn(cursor), "owner-1", "project-1"
    ))

    sql, params = cursor.calls[0]
    assert result == rows
    assert "union all" in sql.lower()
    assert "mi.owner_user_id is null and mi.project_id is null" in sql.lower()
    assert "thb.source = 'seed'" in sql.lower()
    assert "thb.visibility = 'public'" in sql.lower()
    assert "p.user_id = %s" in sql.lower()
    assert "mi.owner_user_id = %s and mi.project_id = %s" in sql.lower()
    assert "thb.source = 'upload'" in sql.lower()
    assert "img.source = 'derived'" in sql.lower()
    assert params == ("owner-1", "owner-1", "project-1")


def test_matching_asset_owner_gate_hides_other_custom_scope():
    cursor = _Cursor(row=None)
    result = asyncio.run(repo.get_matching_item_asset(
        _Conn(cursor), "custom-known-id", "owner-1", "project-1"
    ))

    sql, params = cursor.calls[0]
    assert result is None
    assert "owner_user_id is null or (owner_user_id = %s and project_id = %s)" in sql.lower()
    assert params == ("custom-known-id", "owner-1", "project-1")


def test_matching_metadata_owner_gate_hides_other_custom_scope():
    cursor = _Cursor(row=None)
    result = asyncio.run(repo.get_matching_item_metadata(
        _Conn(cursor), "custom-known-id", "owner-1", "project-1"
    ))

    sql, params = cursor.calls[0]
    assert result is None
    assert "owner_user_id is null or (owner_user_id = %s and project_id = %s)" in sql.lower()
    assert params == ("custom-known-id", "owner-1", "project-1")


def test_uploaded_asset_gate_is_one_ordered_query_with_all_four_conditions():
    cursor = _Cursor(rows=[])
    asset_ids = ["00000000-0000-0000-0000-000000000001"]
    asyncio.run(repo.get_uploaded_assets_for_project(
        _Conn(cursor), "owner-1", "project-1", asset_ids
    ))

    sql, params = cursor.calls[0]
    lowered = sql.lower()
    assert "unnest(%s::uuid[]) with ordinality" in lowered
    assert "a.user_id = %s" in lowered
    assert "a.project_id = %s" in lowered
    assert "a.source = 'upload'" in lowered
    assert "a.deleted_at is null" in lowered
    assert "order by requested.ord" in lowered
    assert params == (asset_ids, "owner-1", "project-1")


def test_reused_soft_deleted_grid_refreshes_source_asset_metadata():
    cursor = _Cursor(row={"id": "grid-id"})
    source_ids = ["source-new-1", "source-new-2"]
    result = asyncio.run(repo.insert_custom_grid_asset(
        _Conn(cursor),
        asset_id="grid-id",
        user_id="owner-1",
        project_id="project-1",
        bucket="wearless",
        key="derived/grid.jpg",
        size=123,
        checksum="checksum",
        source_asset_ids=source_ids,
    ))

    sql, params = cursor.calls[0]
    lowered = sql.lower()
    assert result == {"id": "grid-id"}
    assert "metadata = excluded.metadata" in lowered
    assert "checksum = excluded.checksum" in lowered
    assert "where assets.user_id = excluded.user_id" in lowered
    assert params[-1].obj["sourceAssetIds"] == source_ids
