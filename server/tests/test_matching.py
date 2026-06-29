import contextlib

import app.routes as routes
from app.services import matching


def _it(id, ct, gender, cb, so, active=True):
    return {"id": id, "clothing_type": ct, "gender": gender,
            "color_brightness": cb, "sort_order": so, "is_active": active}


def test_complementary_type():
    assert matching.complementary_type("top") == "bottom"
    assert matching.complementary_type("dress") == "bottom"
    assert matching.complementary_type("outer") == "bottom"
    assert matching.complementary_type("bottom") == "top"


def test_recommend_filters_type_and_sorts_by_brightness_then_sortorder():
    items = [
        _it("b1", "bottom", "women", 20, 2),
        _it("b2", "bottom", "women", 90, 5),
        _it("b3", "bottom", "women", 90, 1),  # 동률 → sort_order 우선
        _it("t1", "top", "women", 99, 1),     # 보색 아님 → 제외
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    assert [i["id"] for i in out] == ["b3", "b2", "b1"]


def test_recommend_gender_filter_allows_unisex():
    items = [
        _it("m", "bottom", "men", 50, 1),
        _it("w", "bottom", "women", 50, 2),
        _it("u", "bottom", "unisex", 50, 3),
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    assert {i["id"] for i in out} == {"w", "u"}


def test_recommend_excludes_inactive_and_applies_limit():
    items = [
        _it("a", "bottom", "women", 80, 1),
        _it("b", "bottom", "women", 70, 2),
        _it("c", "bottom", "women", 60, 3, active=False),
    ]
    out = matching.recommend(items, clothing_type="top", genders=[], limit=1)
    assert [i["id"] for i in out] == ["a"]


def test_recommend_missing_brightness_defaults_50():
    items = [
        {"id": "x", "clothing_type": "bottom", "gender": "women", "sort_order": 1, "is_active": True},
        _it("y", "bottom", "women", 90, 2),
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    assert [i["id"] for i in out] == ["y", "x"]  # 90 > 50(default)


# ---------- 엔드포인트 (DB·R2 격리: monkeypatch) ----------

class _FakeR2:
    def public_url(self, key):
        return f"https://img.example.com/{key}"


def _no_db(monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield None
    monkeypatch.setattr(routes, "get_conn", fake_conn)


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_match_candidates_shape_and_public_url(client, make_token, monkeypatch):
    # frozen dataclass → object.__setattr__ 로 테스트 주입
    object.__setattr__(client.app.state.settings, "r2_public_base", "https://img.example.com")
    monkeypatch.setattr(routes, "_r2", lambda request: _FakeR2())

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_list(conn):
        return [{"id": "match_women_bottom_01", "name": "블랙 슬랙스",
                 "clothing_type": "bottom", "gender": "women", "category": "슬랙스",
                 "color_name": "블랙", "color_group": "black", "style_tags": ["basic"],
                 "fit": "regular", "length": "full", "color_brightness": 0, "sort_order": 201,
                 "is_active": True, "image_key": "seed/matching/match_women_bottom_01.png",
                 "thumb_key": "seed/matching/thumb/match_women_bottom_01.png"}]

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "list_active_matching_items", fake_list)
    _no_db(monkeypatch)

    res = client.get(
        "/v1/projects/p1/analysis/match-candidates?clothingType=top&gender=women",
        headers=_auth(make_token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body[0]["thumb"] == "https://img.example.com/seed/matching/thumb/match_women_bottom_01.png"
    assert body[0]["selected"] is False
    assert body[0]["id"] == "match_women_bottom_01"


def test_match_candidates_failfast_without_public_base(client, make_token, monkeypatch):
    object.__setattr__(client.app.state.settings, "r2_public_base", None)

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    _no_db(monkeypatch)

    res = client.get(
        "/v1/projects/p1/analysis/match-candidates?clothingType=top",
        headers=_auth(make_token))
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "r2_public_base_missing"
