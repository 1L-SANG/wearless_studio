import asyncio
from copy import deepcopy

import pytest

from app import repo, routes
from conftest import assert_query_binds, auth_headers, patch_route_db
from test_analysis_server_owned_fields import _Conn

KEY = "garmentColorSliders"
ZERO = {"chroma": 0, "warmth": 0, "lightness": 0}
VALID = {**ZERO, "warmth": -100}
PRODUCT = {"colors": [{"id": "keep"}, {"id": "zero"}]}


@pytest.mark.parametrize("query", ["", "?merge=true"])
@pytest.mark.parametrize("incoming,expected", [
    ({"version": 1, "colors": {"keep": {**VALID, "targetLinear": [1, 1, 1]}, "zero": ZERO,
                               "unknown": VALID}}, {"version": 1, "colors": {"keep": VALID}}),
    ({"version": 1, "colors": {"keep": {**ZERO, "chroma": 101}}}, None),
    ({"version": 1, "colors": {"keep": {**ZERO, "chroma": True}}}, None),
    ({"version": 1, "colors": []}, None),
    ({"version": True, "colors": {}}, None),
])
def test_analysis_patch_validates_client_sliders(client, make_token, monkeypatch, query, incoming, expected):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value=PRODUCT))
    conn = _Conn({KEY: {"version": 1, "colors": {"keep": VALID}}, "fit": "regular"})

    @asynccontextmanager
    async def connection(_request):
        yield conn

    monkeypatch.setattr(routes, "get_conn", connection)
    response = client.patch("/v1/projects/p1/analysis" + query, headers=auth_headers(make_token),
                            json={KEY: incoming, "fit": "slim"})
    assert response.status_code == 200, response.text
    assert conn.saved["fit"] == "slim"
    if expected is None:
        assert KEY not in conn.saved
    else:
        assert conn.saved[KEY] == expected


def test_reanalysis_keeps_client_sliders_in_analysis_and_result():
    class Conn:
        analysis = {KEY: {"version": 1, "colors": {"keep": VALID}}, "fit": "regular"}

        def cursor(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, sql, params=None):
            assert_query_binds(sql, params)
            if sql.startswith("select id from jobs"):
                self.result = {"id": "job"}
            if sql.startswith("insert into analyses"):
                assert "then jsonb_build_object('garmentColorSliders', analyses.payload->'garmentColorSliders')" in sql
                self.analysis = {**params[1].obj, KEY: self.analysis[KEY]}
                self.result = {"payload": self.analysis}

        async def fetchone(self):
            return self.result

    conn = Conn()
    before = deepcopy(conn.analysis[KEY])
    result = asyncio.run(repo.finalize_analyze_success(
        conn, job_id="job", lease_token="lease", user_id="user", project_id="project",
        clothing_type=None, analysis_payload={"fit": "slim"}, result={"data": {"fit": "slim"}}, metadata={},
    ))
    assert conn.analysis == {KEY: before, "fit": "slim"}
    assert result["result"]["data"] == {KEY: before, "fit": "slim"}
