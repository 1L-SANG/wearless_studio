"""Actual detail jobs publish only a connected mall's public shop name."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app import repo
from app.workers import detail_page_job as dpj
from conftest import FakeConn, fake_worker_app, make_settings, worker_job


class MallConnectionDB(FakeConn):
    """Run the real owner-bound repository read without a database or provider."""
    def __init__(self, rows, error_on):
        self.rows, self.error_on, self.queries = rows, error_on, []

    @asynccontextmanager
    async def connection(self):
        yield self

    @asynccontextmanager
    async def cursor(self):
        yield self

    async def execute(self, sql, params):
        assert "from mall_connections" in sql
        assert "where user_id = %s and mall = %s and status = 'connected'" in sql
        self.queries.append(params)
        if params[1] == self.error_on:
            raise RuntimeError("mall lookup unavailable")
        self.params = params

    async def fetchone(self):
        user_id, mall = self.params
        return next((row for row in self.rows if row["user_id"] == user_id
                     and row["mall"] == mall and row["status"] == "connected"), None)


def connection(mall, name, **overrides):
    return {"id": f"connection-{mall}", "user_id": "job-owner", "mall": mall,
            "status": "connected", "shop_id": "shop-id", "shop_name": name,
            "key_hint": "private-hint", "permissions": ["products:write"],
            "connected_at": None, "last_registered_at": None, "revoked_at": None,
            "token_expires_at": None, "scopes": ["private-scope"],
            "refresh_expires_at": datetime.now(timezone.utc) + timedelta(days=1), **overrides}


def run_job(monkeypatch, rows=(), *, frame=True, error_on=None, cafe24_ready=True, mall_connect_enabled=True):
    board = [{"id": "photo", "taxonomyVersion": 4, "source": "mine", "ownImages": ["/seller-photo.jpg"],
              "sectionId": "wear", "pageSectionKey": "emo.wear_1", "pageSectionKind": "wear",
              "pageSectionUsage": "styling", "slotKey": None,
              "pageDesignId": "WEAR-STYLING-FRAME-COLUMN-01" if frame else "P02-D01"}]
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"copywriting": False}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={"name": "코튼 셔츠", "colors": []}))
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=board))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(return_value={}))
    account_lookup = AsyncMock(return_value={"name": "계정 개인 실명", "email": "private@example.test"})
    monkeypatch.setattr(repo, "get_account", account_lookup)
    monkeypatch.setattr(dpj.page_part_copy, "generate", AsyncMock(return_value={}))
    monkeypatch.setattr(dpj, "_emit", AsyncMock())
    monkeypatch.setattr(dpj, "_gen_cuts", AsyncMock(return_value=([], [], 0, [], None, [])))
    finalize = AsyncMock(return_value={"editor_blocks": [], "available": 1})
    monkeypatch.setattr(repo, "finalize_detail_page_success", finalize)
    failure = AsyncMock(return_value=True)
    monkeypatch.setattr(repo, "finalize_detail_page_failure", failure)
    assembled = {}
    real_assemble = dpj.page_assembler.assemble

    def assemble(*args, **kwargs):
        assembled.update(kwargs)
        return real_assemble(*args, **kwargs)

    monkeypatch.setattr(dpj.page_assembler, "assemble", assemble)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b", cafe24_enabled=cafe24_ready,
        cafe24_client_id="client", cafe24_client_secret="secret", mall_keys_kek="test-kek",
        mall_connect_enabled=mall_connect_enabled))
    db = MallConnectionDB(rows, error_on)
    app.state.pool = db
    job = {**worker_job(), "user_id": "job-owner"}
    asyncio.run(dpj.run_detail_page_job(app, job))
    assert not failure.called, failure.call_args
    assert finalize.await_count == 1
    account_lookup.assert_not_called()
    assert "profile" not in assembled
    blocks = finalize.call_args.kwargs["editor_blocks"]
    footers = [el["text"] for block in blocks for el in block.get("elements", []) if el.get("copyRole") == "who"]
    return db.queries, assembled, footers


def test_actual_frame_job_prefers_connected_cafe24_shop_name_and_passes_only_trimmed_scalar(monkeypatch):
    queries, assembled, footers = run_job(monkeypatch, [connection("zigzag", "지그재그 상점"),
                                                       connection("cafe24", "  공식 상점  ")])
    assert queries == [("job-owner", "cafe24")]
    assert assembled["shop_name"] == "공식 상점"
    assert footers == ["공식 상점"]
    assert not any(key in assembled for key in ("key_hint", "permissions", "scopes", "shop_id"))


@pytest.mark.parametrize("name", [None, "", "   ", 123, False, {}, ["상점"]])
def test_empty_or_nonstring_priority_shop_name_falls_back_to_connected_zigzag(monkeypatch, name):
    queries, assembled, footers = run_job(monkeypatch, [connection("cafe24", name), connection("zigzag", "  다음 상점  ")])
    assert queries == [("job-owner", "cafe24"), ("job-owner", "zigzag")]
    assert assembled["shop_name"] == "다음 상점"
    assert footers == ["다음 상점"]


@pytest.mark.parametrize("rows", [[], [connection("cafe24", "끊긴 상점", status="disconnected")],
    [connection("zigzag", "다른 사용자의 상점", user_id="another-owner")],
    [connection("cafe24", " "), connection("zigzag", None)]])
def test_no_active_owned_shop_name_omits_footer_without_using_personal_account(monkeypatch, rows):
    queries, assembled, footers = run_job(monkeypatch, rows)
    assert queries == [("job-owner", "cafe24"), ("job-owner", "zigzag")]
    assert "shop_name" not in assembled
    assert footers == []


@pytest.mark.parametrize("expires_at", [None, datetime.now(timezone.utc) - timedelta(seconds=1)])
def test_expired_or_missing_cafe24_refresh_expiry_uses_zigzag(monkeypatch, expires_at):
    _, assembled, footers = run_job(monkeypatch, [connection("cafe24", "만료 상점", refresh_expires_at=expires_at),
                                                connection("zigzag", "현재 상점")])
    assert assembled["shop_name"] == "현재 상점"
    assert footers == ["현재 상점"]


def test_cafe24_unavailable_uses_existing_zigzag_connection_even_when_zigzag_connect_disabled(monkeypatch):
    _, assembled, footers = run_job(monkeypatch, [connection("cafe24", "준비 안 된 상점"),
                                                connection("zigzag", "현재 상점")], cafe24_ready=False)
    assert assembled["shop_name"] == "현재 상점"
    assert footers == ["현재 상점"]


@pytest.mark.parametrize("error_on", ["cafe24", "zigzag"])
def test_any_connection_read_failure_omits_footer_and_preserves_completed_job(monkeypatch, error_on):
    queries, assembled, footers = run_job(monkeypatch, [connection("zigzag", "공개 상점")], error_on=error_on)
    assert queries[-1] == ("job-owner", error_on)
    assert "shop_name" not in assembled
    assert footers == []


def test_nonframe_job_does_not_query_malls_or_publish_account_profile(monkeypatch):
    queries, assembled, footers = run_job(monkeypatch, [connection("cafe24", "공개 상점")], frame=False)
    assert queries == []
    assert "shop_name" not in assembled
    assert footers == []


def test_disabled_mall_connections_skip_existing_rows_and_complete_without_footer(monkeypatch):
    queries, assembled, footers = run_job(monkeypatch, [connection("cafe24", "기존 공식 상점"),
                                                       connection("zigzag", "기존 지그재그 상점")],
                                         mall_connect_enabled=False)
    assert queries == []
    assert "shop_name" not in assembled
    assert footers == []
