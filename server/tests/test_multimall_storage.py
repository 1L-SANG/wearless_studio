"""Per-mall defaults and the unchanged create_job protocol, without a database."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import inspect

import pytest
from psycopg import errors

from app import repo
from app.services.mall_defaults import normalize_mall_defaults, mall_defaults_view
from conftest import assert_query_binds


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.row = None
        self.rowcount = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, sql, params=None):
        assert_query_binds(sql, params)
        self.conn.queries.append((" ".join(sql.lower().split()), params))
        self.row = self.conn.respond(sql, params)

    async def fetchone(self):
        return deepcopy(self.row)


class Conn:
    def __init__(self, respond):
        self.respond = respond
        self.queries = []

    def cursor(self):
        return Cursor(self)

    @asynccontextmanager
    async def transaction(self):
        yield


def test_mall_defaults_partial_values_and_fee_validation():
    view = mall_defaults_view("zigzag", None)
    assert view["revision"] == 0 and view["shipping"] == {} and view["shippingDays"] == 2
    assert "addresses.returnAddressId" in view["missingItems"]
    row = normalize_mall_defaults("zigzag", {}, {
        "shipping": {"feeType": "CONDITIONAL_FREE", "baseFee": 3000, "conditionalAmount": 50000},
        "disclosure": {"country": " 대한민국 ", "manufacturer": "직접 제작"},
    })
    assert row["shipping_days"] == 2 and row["listing_mode"] == "HIDDEN"
    assert row["disclosure"]["country"] == "대한민국"
    assert "warrantyText" not in row["disclosure"]
    before = deepcopy(row)
    free = normalize_mall_defaults("zigzag", row, {"shipping": {"feeType": "FREE"}, "disclosure": {"manufacturer": ""}})
    assert free["shipping"] == {"feeType": "FREE", "baseFee": 0}
    assert "manufacturer" not in free["disclosure"] and row == before
    charged = normalize_mall_defaults("zigzag", row, {"shipping": {"feeType": "CHARGED"}})
    assert "conditionalAmount" not in charged["shipping"]
    assert normalize_mall_defaults("zigzag", {}, {"shipping": {"baseFee": 2_147_483_647}})["shipping"]["baseFee"] == 2_147_483_647
    for patch in (
        *({"shipping": {field: value}} for field in ("baseFee", "jejuFee", "isolatedFee", "conditionalAmount")
          for value in (True, False, 1.5, -1, 2_147_483_648, "3000", None)),
        {"shipping": {"feeType": "CONDITIONAL_FREE", "conditionalAmount": 0}},
        *({"returns": {field: value}} for field in ("totalFee", "partialFee", "exchangeFee")
          for value in (True, 1.5, -1, 2_147_483_648)),
        *({"shippingDays": value} for value in (0, 31, True, 2.5, None)),
        {"listingMode": "VISIBLE"}, {"taxType": "A"}, {"productOrigin": "KR"},
        {"shipping": []}, {"shipping": {"unknown": 1}}, {"disclosure": {"phone": None}},
        {"unknown": {}},
    ):
        with pytest.raises(ValueError):
            normalize_mall_defaults("zigzag", {}, patch)


@pytest.mark.parametrize("same_store", [False, True])
def test_store_change_clears_only_own_addresses(monkeypatch, same_store):
    defaults = {
        "zigzag": {"shop_id": "old-shop", "addresses": {"returnAddressId": "zigzag-return"}, "revision": 4,
                   "shipping": {"baseFee": 3000}, "disclosure": {"country": "한국"}},
        "cafe24": {"shop_id": "cafe-shop", "addresses": {}, "revision": 7, "tax_type": "FREE"},
    }
    original_other, original_own = deepcopy(defaults["cafe24"]), deepcopy(defaults["zigzag"])
    shop_id = "old-shop" if same_store else "new-shop"

    def respond(sql, params):
        if "insert into mall_connections" in sql:
            return {"id": "new-connection"}
        if "insert into mall_defaults" in sql:
            owner, mall, shop = params
            assert owner == "owner"
            assert "completed_items" not in sql and "revision = mall_defaults.revision + 1" in sql
            previous = defaults[mall]
            if previous["shop_id"] != shop:
                defaults[mall] = {**previous, "shop_id": shop, "addresses": {}, "revision": previous["revision"] + 1}

    async def open_account(*args):
        return False

    monkeypatch.setattr(repo, "user_account_purge_closed", open_account)
    conn = Conn(respond)
    asyncio.run(repo.upsert_mall_connection(conn, user_id="owner", mall="zigzag", shop_id=shop_id,
        shop_name="Store", key_hint=None, access_key="test", secret_key="test", permissions=[], kek="test"))
    assert defaults["cafe24"] == original_other
    assert all(defaults["zigzag"][key] == original_own[key] for key in ("shipping", "disclosure"))
    assert defaults["zigzag"]["revision"] == (4 if same_store else 5)
    assert defaults["zigzag"]["addresses"] == ({"returnAddressId": "zigzag-return"} if same_store else {})
    assert all("mall_sales_defaults" not in sql for sql, _ in conn.queries)
    locks = [params[0] for sql, params in conn.queries if "pg_advisory_xact_lock" in sql]
    assert locks == ["mall-account:owner", "mall:owner:zigzag", "mall:owner:zigzag", "mall-defaults:owner:zigzag"]
    # OAuth completion has the same store-change rule, without touching old completion columns.
    source = inspect.getsource(repo.save_cafe24_connection)
    assert "revision = mall_defaults.revision + 1" in source and "completed_items" not in source


def test_mall_defaults_reads_only_mall_table():
    def respond(sql, params):
        assert "from mall_defaults" in sql and params == ("owner", "zigzag")
        assert "shipping_days" in sql and "listing_mode" in sql and "completed_items" not in sql
        return {"shop_id": "store", "shipping": {"baseFee": 999}, "addresses": {}, "revision": 1}
    assert asyncio.run(repo.get_mall_defaults(Conn(respond), user_id="owner", mall="zigzag"))["shipping"]["baseFee"] == 999
    assert not hasattr(repo, "get_sales_defaults") and not hasattr(repo, "save_sales_defaults")


@pytest.mark.parametrize("kind", ["analyze", "sam_preprocess", "detail_page", "mannequin", "editor_image"])
@pytest.mark.parametrize("conflict", ["by_key", "colliding_key", "active", "unique", "gone"])
def test_non_mall_active_join_behavior_is_unchanged(kind, conflict):
    by_key, inserts = 0, 0
    expected = {"id": "original", "idempotency_key": "request", "payload": {}, "project_id": "project"}

    def respond(sql, params):
        nonlocal by_key, inserts
        query = " ".join(sql.lower().split())
        if "where idempotency_key = %s" in query:
            by_key += 1
            return expected if conflict == "by_key" or (conflict == "colliding_key" and by_key > 1) else None
        if "insert into jobs" in query:
            inserts += 1
            assert "on conflict do nothing" in query and params[5] == 17
            if conflict == "unique" and inserts == 1:
                raise errors.UniqueViolation("concurrent key")
            if conflict == "gone" and inserts == 2:
                return expected
            return None
        if "and kind = %s and status in" in query:
            assert "payload->>'mall'" not in query and params == ("project", "owner", kind)
            return None if conflict == "gone" else expected

    conn = Conn(respond)
    row, created = asyncio.run(repo.create_job(conn, user_id="owner", project_id="project", kind=kind,
        payload={}, idempotency_key="request", credits_reserved=17, metadata={}))
    assert row == expected and created is (conflict == "gone")
    queries = [sql for sql, _ in conn.queries]
    if conflict == "colliding_key":
        assert not any("and kind = %s and status in" in sql for sql in queries)
    if conflict == "by_key":
        assert not any("insert into jobs" in sql for sql in queries)
    else:
        assert "savepoint create_job_insert" in queries
        assert ("rollback to savepoint create_job_insert" in queries) is (conflict == "unique")
        assert inserts == (2 if conflict == "gone" else 1)
    assert not any(sql.startswith(("commit", "begin")) for sql in queries)
