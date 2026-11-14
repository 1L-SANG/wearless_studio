"""WO2 storage, locking, carry-over, and generation boundaries (no providers/DB)."""
import asyncio
import contextlib
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from app.agents import content_roles, page_style
from app.workers import detail_page_job as dpj
from conftest import auth_headers, FakeConn, patch_route_db
from storyboard_v4_cases import v4_blocks

_REAL_PAGE_STYLE_LOCKED = repo.page_style_locked


def style(**patch):
    return {**page_style.default_page_style(), "updatedAt": "2026-10-28T12:00:00Z", **patch}


def photo(**patch):
    return {"id": "photo", "source": "ai", "cutType": "horizon", "shot": "full", "direction": "front", **patch}


def board():
    return v4_blocks([photo(), photo(id="second", direction="back")])


def test_v4_preserves_runs_and_uses_shooting_role_instead_of_section_sort():
    blocks = v4_blocks([photo(cutType="product", shot="ghost"), photo(id="worn", sectionId="other", cutType="styling")])
    out = content_roles.canonicalize_storyboard(blocks)
    assert [b["id"] for b in out] == ["photo", "worn"]
    assert [b["sectionRole"] for b in out] == ["product", "styling"]


@pytest.mark.parametrize("broken,code", [
    (lambda b: [{**b[0], "taxonomyVersion": 3}], "storyboard_outdated"),
    (lambda b: [b[0], {**b[1], "pageSectionKey": "show.wear_fit"}], "mixed_storyboard_templates"),
    (lambda b: [b[0], {**b[1], "pageSectionKey": "emo.first", "pageSectionKind": "first", "pageSectionUsage": None, "pageDesignId": "P01-D09"}], "invalid_storyboard_section"),
    (lambda b: [b[0], {**b[1], "sectionId": "s2", "pageSectionKey": "emo.wear_2"}, {**b[0], "id": "again"}], "noncontiguous_storyboard_section"),
    (lambda b: [{**b[0], "pageDesignId": "T01-D07"}], "invalid_storyboard_design"),
])
def test_v4_rejects_invalid_structure(broken, code):
    with pytest.raises(ValueError, match=code):
        content_roles.canonicalize_storyboard(broken(board()))


def text(kind="intro", **patch):
    return {"id": "text", "source": "text", "taxonomyVersion": 4, "pageSectionKey": f"emo.{kind}",
        "sectionId": kind, "pageSectionKind": kind, "pageDesignId": {"intro": "T01-D07", "points": "PT-EMO-01"}[kind],
        "cutType": None, "contentRole": None, "sectionRole": None, "slotKey": None, **patch}


@pytest.mark.parametrize("key,value", [("cutType", "horizon"), ("exampleId", None), ("matchIds", []), ("refImages", []), ("spaceGroupId", "x"), ("direction", "front"), ("refScope", "all"), ("spaceSetMemberOrder", 1), ("previewThumb", "/example.png"), ("modelId", "model"), ("refAssetIds", [])])
def test_text_card_rejects_generation_fields(key, value):
    with pytest.raises(ValueError, match="invalid_storyboard_text_card"):
        content_roles.canonicalize_storyboard([text(**{key: value})])


def test_text_only_and_lead_position():
    assert content_roles.canonicalize_storyboard([text()])[0]["source"] == "text"
    image = {**board()[0], "pageSectionKey": "emo.points", "sectionId": "points", "pageSectionKind": "points", "pageSectionUsage": None, "pageDesignId": "PT-EMO-01"}
    with pytest.raises(ValueError, match="invalid_storyboard_text_card"):
        content_roles.canonicalize_storyboard([image, text("points")])
    with pytest.raises(ValueError, match="invalid_storyboard_text_card"):
        content_roles.canonicalize_storyboard([text(), {**image, "pageSectionKey": "emo.intro", "sectionId": "intro", "pageSectionKind": "intro", "pageDesignId": "T01-D07"}])


def test_v4_slot_recipe_reverts_and_clears_incompatible_example():
    first = {**board()[0], "pageSectionKey": "emo.first", "pageSectionKind": "first", "pageSectionUsage": None,
        "pageDesignId": "P01-D09", "slotKey": "first.hero", "cutType": "product", "shot": "detail", "exampleId": "stale", "thumb": "new", "baseThumb": "old"}
    out = content_roles.canonicalize_storyboard([first])[0]
    assert (out["cutType"], out["shot"], out["contentRole"]) == ("styling", "full", "hero")
    assert out["exampleId"] is None and out["thumb"] == "old"


def test_seller_photo_keeps_its_planned_slot_when_replacing_an_ai_photo():
    from app.agents.page_assembler import assemble
    own = {**board()[0], "source": "mine", "ownImages": ["/my-photo.png"],
        "pageSectionKey": "emo.first", "pageSectionKind": "first", "pageSectionUsage": None,
        "pageDesignId": "P01-D09", "slotKey": "first.hero"}
    canonical = content_roles.canonicalize_storyboard([own])
    assert canonical[0]["slotKey"] == "first.hero"
    images = [e for b in assemble(canonical, [], [], {}, False) for e in b["elements"] if e["type"] == "image"]
    assert len(images) == 1 and images[0]["src"] == "/my-photo.png"
    assert images[0]["sourceBlockId"] == own["id"]


@pytest.fixture
def analysis_db(monkeypatch):
    saved = {"pageStyle": style()}
    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={}))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(side_effect=lambda *_: deepcopy(saved)))
    monkeypatch.setattr(repo, "lock_page_style_project", AsyncMock())
    monkeypatch.setattr(repo, "page_style_locked", AsyncMock(return_value=False))

    async def save(_conn, _pid, patch, **_kwargs):
        saved.update(deepcopy(patch))
        return {"project_id": "p1", "locked": False, "payload": deepcopy(saved)}
    monkeypatch.setattr(repo, "save_analysis", save)
    return saved


@pytest.mark.parametrize("patch", [{"templateId": "bad"}, {"fontStyle": "bad"}, {"colorMode": "bad"}, {"version": 4}, {"catalogVersion": "old"}, {"updatedAt": False}, {"unknown": 1}])
def test_page_style_allowlist(client, make_token, analysis_db, patch):
    res = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token), json={"pageStyle": style(**patch)})
    assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_page_style"


@pytest.mark.parametrize("field,value", [("templateId", "show"), ("fontStyle", "serif"), ("colorMode", "point"), ("updatedAt", "2026-10-28T12:01:00Z")])
@pytest.mark.parametrize("source", ["cut", "job"])
def test_only_template_is_locked_after_mannequin(client, make_token, analysis_db, monkeypatch, field, value, source):
    real_locked = _REAL_PAGE_STYLE_LOCKED

    class Cursor:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def execute(self, sql, params):
            assert "mannequin_cuts" in sql and "jobs" in sql
            assert params == ("p1", "p1")
        async def fetchone(self): return {"locked": source in ("cut", "job")}

    class Conn:
        def cursor(self): return Cursor()

    monkeypatch.setattr(repo, "page_style_locked", lambda _conn, project_id: real_locked(Conn(), project_id))
    res = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token), json={"pageStyle": style(**{field: value})})
    if field == "templateId":
        assert res.status_code == 409 and res.json()["error"]["code"] == "page_style_locked"
        assert analysis_db["pageStyle"] == style()
    else:
        assert res.status_code == 200
        assert res.json()["pageStyle"] == style(**{field: value})
        assert analysis_db["pageStyle"] == style(**{field: value})


def test_same_style_saves_after_lock_and_unrelated_merge_preserves_it(client, make_token, analysis_db, monkeypatch):
    monkeypatch.setattr(repo, "page_style_locked", AsyncMock(return_value=True))
    for patch in [{"pageStyle": style()}, {"mdComment": "메모"}]:
        res = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token), json=patch)
        assert res.status_code == 200
        assert res.json()["pageStyle"] == style()


def test_legacy_missing_style_uses_default_template_for_the_lock(client, make_token, analysis_db, monkeypatch):
    analysis_db.pop("pageStyle")
    monkeypatch.setattr(repo, "page_style_locked", AsyncMock(return_value=True))
    res = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token), json={"pageStyle": style(fontStyle="serif")})
    assert res.status_code == 200 and res.json()["pageStyle"]["fontStyle"] == "serif"
    blocked = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token), json={"pageStyle": style(templateId="show")})
    assert blocked.status_code == 409


def test_last_page_style_is_user_scoped_and_has_a_default(client, make_token, monkeypatch):
    patch_route_db(monkeypatch, routes)
    read = AsyncMock(return_value=style(templateId="show"))
    monkeypatch.setattr(repo, "last_page_style", read)
    res = client.get("/v1/page-style/last", headers=auth_headers(make_token))
    assert res.status_code == 200 and res.json()["templateId"] == "show"
    assert isinstance(read.call_args.args[1], str)
    read.return_value = None
    assert client.get("/v1/page-style/last", headers=auth_headers(make_token)).json() == page_style.default_page_style()


@pytest.mark.parametrize("endpoint,method", [("storyboard", "put"), ("detail-page:generate", "post")])
def test_old_board_is_400_before_any_generation(client, make_token, monkeypatch, endpoint, method):
    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=[{"taxonomyVersion": 3}]))
    kwargs = {"json": [{"taxonomyVersion": 3}]} if method == "put" else {}
    res = getattr(client, method)(f"/v1/projects/p1/{endpoint}", headers=auth_headers(make_token), **kwargs)
    assert res.status_code == 400 and res.json()["error"]["code"] == "storyboard_outdated"


def test_generation_quote_excludes_text_and_matches_frontend_count(client, make_token, monkeypatch):
    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    blocks = [*board(), text()]
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=blocks))
    monkeypatch.setattr(repo, "get_editor_blocks", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={"clothingType": "top",
        "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}))
    monkeypatch.setattr(routes, "_outfit_generation_snapshot", AsyncMock(return_value={"version": 1, "slots": {}}))
    create = AsyncMock(return_value=({"id": "job", "payload": {}}, True))
    monkeypatch.setattr(repo, "create_job", create)
    monkeypatch.setattr(repo, "reserve_credits", AsyncMock(return_value=999))
    monkeypatch.setattr(routes, "_wake_dispatcher", lambda *_: None)
    monkeypatch.setattr(routes, "_prewarm_detail_worker", lambda *_: None)
    res = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert res.status_code == 202, res.text
    assert create.call_args.kwargs["metadata"]["aiCount"] == 2


def test_reanalysis_carries_style_into_analysis_job_result_and_done_event():
    class Cursor:
        def __init__(self): self.calls = []; self.rows = [{"id": "job"}, {"payload": {"pageStyle": style(templateId="show")}}]
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def execute(self, sql, params): self.calls.append((sql, params))
        async def fetchone(self): return self.rows.pop(0)
    class Conn:
        def __init__(self): self.cur = Cursor()
        def cursor(self): return self.cur
    conn = Conn()
    asyncio.run(repo.finalize_analyze_success(conn, job_id="job", lease_token="lease", user_id="u", project_id="p",
        clothing_type=None, analysis_payload={"fit": "regular"}, result={"data": {"fit": "regular"}}, metadata={}))
    insert = next(sql for sql, _ in conn.cur.calls if sql.startswith("insert into analyses"))
    assert "analyses.payload->'pageStyle'" in insert
    done = next(params for sql, params in conn.cur.calls if "insert into job_events" in sql)
    assert done[1].obj["data"]["pageStyle"]["templateId"] == "show"


def test_style_lock_checks_cuts_and_all_mannequin_job_states():
    class Cursor:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def execute(self, sql, params): self.sql, self.params = sql, params
        async def fetchone(self): return {"locked": True}
    class Conn:
        cur = Cursor()
        def cursor(self): return self.cur
    conn = Conn()
    assert asyncio.run(repo.page_style_locked(conn, "p"))
    assert "mannequin_cuts" in conn.cur.sql and "jobs" in conn.cur.sql
    assert "status" not in conn.cur.sql
