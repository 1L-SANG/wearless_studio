import asyncio
import re

from app import repo


class _Cursor:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []
        self.calls = []
        self.raw_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        self.raw_calls.append((sql, params))

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _sql_without_comments(sql: str) -> str:
    """`-- 주석` 을 제거하고 공백을 정규화한다. 주석 문구가 조건절로 오인되지 않게."""
    return " ".join(re.sub(r"--[^\n]*", " ", sql).split()).lower()


def _arm(sql: str, index: int) -> str:
    arms = _sql_without_comments(sql).split("union all")
    assert len(arms) == 2, "curated/custom 두 arm"
    return arms[index]


def _join_conditions(arm: str, alias: str) -> str:
    """`join assets <alias> on ...` 의 조건부만 잘라낸다(다음 join/where 직전까지).

    SQL 전체 문자열 검색이 아니라 해당 조인의 조건만 본다 — 다른 arm·다른 별칭의
    조건이 섞여 통과하는 일이 없게.
    """
    head = f"join assets {alias} on "
    start = arm.index(head) + len(head)
    tail = arm[start:]
    stops = [m.start() for m in re.finditer(r"\b(?:left join|join|where)\b", tail)]
    return tail[: (stops[0] if stops else len(tail))]


# 2026-08-13 리뷰 C1 회귀 — 누끼 워커가 thumbnail_asset_id 를 source='derived' 파생 컷으로
# 갈아끼운다. 커스텀 arm 썸네일 조인이 'upload' 만 받으면 누끼 성공 순간 조인이 깨져
# "내 옷" 카드가 목록에서 통째로 사라진다(카드 소멸 + 선택 해제).
def test_custom_arm_thumbnail_join_survives_cutout_asset_swap():
    cursor = _Cursor(rows=[])
    asyncio.run(repo.list_active_matching_items(_Conn(cursor), "owner-1", "project-1"))
    raw_sql = cursor.raw_calls[0][0]

    custom_thumb = _join_conditions(_arm(raw_sql, 1), "thb")
    assert "'upload'" in custom_thumb, "등록 직후 원본 썸네일"
    assert "'derived'" in custom_thumb, "누끼 스왑 뒤 파생 썸네일"
    assert "source = 'upload'" not in custom_thumb, "파생을 배제하는 옛 등호 조건 금지"
    assert "thb.visibility = 'private'" in custom_thumb, "소유자 스코프는 유지"
    assert "thb.deleted_at is null" in custom_thumb

    custom_image = _join_conditions(_arm(raw_sql, 1), "img")
    assert "'derived'" in custom_image

    # 큐레이션 arm 은 여전히 seed/public 만 — 커스텀 완화가 새어 나가지 않았는지.
    curated_thumb = _join_conditions(_arm(raw_sql, 0), "thb")
    assert "thb.source = 'seed'" in curated_thumb
    assert "'derived'" not in curated_thumb


def test_custom_matching_item_exposes_image_metadata_under_the_shared_key():
    # _matching_item_to_api 와 삭제 경로가 함께 읽는 키 이름이 하나여야 한다(리뷰 M7).
    cursor = _Cursor(row=None)
    asyncio.run(repo.get_custom_matching_item(_Conn(cursor), "owner-1", "project-1"))
    sql = _sql_without_comments(cursor.raw_calls[0][0])
    assert "img.metadata as image_meta" in sql
    assert "as image_metadata" not in sql


# 2026-08-14 재리뷰 I-B — 디스패처는 프로세스당 직렬이고 recover_stale_leases 는 running 만
# 건드린다. 나이 상한이 없으면 앞선 잡에 밀린 pending 하나가 카드를 무기한 스켈레톤에 가둔다.
def test_active_cutout_job_query_bounds_stuck_pending_jobs_by_age():
    cursor = _Cursor(row={"active": True})
    active = asyncio.run(repo.has_active_matching_cutout_job(_Conn(cursor), "project-1"))

    sql, params = cursor.calls[0]
    lowered = sql.lower()
    assert active is True
    assert "kind = 'matching_cutout'" in lowered
    assert "status in ('pending', 'running')" in lowered
    assert "created_at > now() - (%s * interval '1 minute')" in lowered
    assert params == ("project-1", repo.MATCHING_CUTOUT_ACTIVE_WINDOW_MINUTES)
    assert repo.MATCHING_CUTOUT_ACTIVE_WINDOW_MINUTES == 10


def test_custom_asset_cleanup_covers_worker_derived_cutouts():
    # 리뷰 I4 — 누끼 파생 컷도 "내 옷 삭제" 한 번에 회수돼야 한다.
    cursor = _Cursor(rows=[])
    asyncio.run(repo.soft_delete_unreferenced_custom_assets(
        _Conn(cursor), "owner-1", "project-1", ["asset-1"]))
    sql = _sql_without_comments(cursor.raw_calls[0][0])
    for purpose in ("custom_match_source", "custom_match_grid", "custom_match_cutout"):
        assert f"'{purpose}'" in sql


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
    assert "thb.source in ('upload', 'derived')" in sql.lower()
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
