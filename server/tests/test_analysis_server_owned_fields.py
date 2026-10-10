"""analysis 저장이 AI 파생 필드를 이월하는지 (codex 리뷰 2026-07-31 CRITICAL 반영).

`save_analysis` 는 최상위 키를 원자 병합한다. 누락된 서버 파생 키는 보존하고,
해시로 봉인된 근거는 클라이언트가 보내도 바꾸지 않는다.

프론트 shape 에도 필드를 넣었지만(shapes.js), 구버전 클라·부분 payload·직접 API 호출까지
막으려면 서버가 최종 방어선이어야 한다.
"""
import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from conftest import auth_headers, patch_route_db
from conftest import assert_query_binds


class _Cur:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        assert_query_binds(sql, params)
        self.owner.calls.append((sql, params))
        self._sql = sql

    async def fetchone(self):
        if "insert into analyses" in self._sql:
            _pid, payload, *options = self.owner.calls[-1][1]
            previous = self.owner.stored or {}
            if "jsonb_each(analyses.payload)" in self._sql:
                assert "previous.key = any(%s::text[]) and not (excluded.payload ? previous.key)" in self._sql
                self.owner.saved = {**payload.obj, **{k: v for k, v in previous.items()
                                                     if k in options[0] and k not in payload.obj}}
            else:
                assert "analyses.payload || excluded.payload" in self._sql
                self.owner.saved = {**previous, **payload.obj}
                if options:
                    assert "- %s::text[]" in self._sql
                    for key in options[0]:
                        self.owner.saved.pop(key, None)
            self.owner.stored = self.owner.saved
            return {"project_id": "p1", "payload": self.owner.saved, "locked": self.owner.locked}
        return {"payload": self.owner.stored} if self.owner.stored is not None else None


class _Conn:
    def __init__(self, stored=None, locked=False):
        self.stored, self.calls, self.saved = stored, [], None
        self.locked = locked

    def cursor(self):
        return _Cur(self)

    async def commit(self):
        pass


def _save(stored, incoming, **options):
    conn = _Conn(stored)
    asyncio.run(repo.save_analysis(conn, "p1", incoming, **options))
    return conn.saved


@pytest.mark.parametrize("merge", [False, True])
@pytest.mark.parametrize("incoming", [None, {"version": 1, "kind": "none", "range": None}])
def test_front_closure_is_immutable_for_client_merge_and_replacement(merge, incoming):
    original = {"version": 1, "kind": "zipper", "range": "graded", "secondary": None,
                "evidenceSlots": ["Front"], "note": None}
    assert "frontClosure" not in _save(None, {"frontClosure": incoming}, merge=merge, delete_nulls=merge)
    assert _save({"frontClosure": original}, {"frontClosure": incoming},
                 merge=merge, delete_nulls=merge)["frontClosure"] == original
    assert _save({"frontClosure": original}, {"fit": "regular"}, merge=merge)["frontClosure"] == original


def test_detail_recommendations_cannot_be_created_or_replaced_by_client():
    original = {"version": 1, "status": "ready", "candidates": [{"id": "server"}]}
    forged = {"version": 1, "status": "ready", "candidates": [{"id": "client"}]}
    assert "detailRecommendations" not in _save(None, {"detailRecommendations": forged})
    assert _save({"detailRecommendations": original}, {"detailRecommendations": forged})["detailRecommendations"] == original
    assert _save({"detailRecommendations": original}, {"fit": "regular"})["detailRecommendations"] == original


def test_garment_color_evidence_is_server_owned_and_cannot_be_forged():
    original = {"version": 1, "colors": [{"colorId": "blue", "observedHex": "#92b4c7"}]}
    forged = {"version": 1, "colors": [{"colorId": "blue", "observedHex": "#ff0000"}]}
    assert "garmentColorEvidence" not in _save(None, {"garmentColorEvidence": forged})
    assert _save({"garmentColorEvidence": original}, {"garmentColorEvidence": forged})["garmentColorEvidence"] == original
    assert _save({"garmentColorEvidence": original}, {"fit": "regular"})["garmentColorEvidence"] == original


def test_atomic_merge_preserves_lazy_color_without_a_stale_read():
    conn = _Conn({"garmentColorEvidence": {"version": 1, "measured": "lazy"}})
    asyncio.run(repo.save_analysis(conn, "p1", {"fit": "regular"}))
    assert len(conn.calls) == 1
    assert "analyses.payload || excluded.payload" in conn.calls[0][0]
    assert conn.saved["garmentColorEvidence"] == {"version": 1, "measured": "lazy"}


def test_source_mirrored_carried_when_client_omits_it():
    saved = _save({"sourceMirrored": True, "fit": "over"}, {"fit": "regular"})
    assert saved["sourceMirrored"] is True   # 이월됨
    assert saved["fit"] == "regular"          # 셀러 편집은 그대로 반영


def test_feature_copy_carried_when_client_omits_it():
    """생성 잡이 써 둔 featureCopy 도 같은 이월 대상 — REPLACE 저장에 사라지면 안 된다."""
    saved = _save(
        {"featureCopy": [{"point": "하이웨이스트", "desc": "허리선이 높아 다리가 더 길어 보입니다."}], "fit": "over"},
        {"fit": "regular"},
    )
    assert saved["featureCopy"] == [{"point": "하이웨이스트", "desc": "허리선이 높아 다리가 더 길어 보입니다."}]  # 이월됨
    assert saved["fit"] == "regular"          # 셀러 편집은 그대로 반영


def test_client_value_wins_when_present():
    """이월은 '누락 시'에만 — 클라가 명시한 값을 서버가 덮으면 편집이 안 먹는다."""
    saved = _save({"sourceMirrored": True}, {"sourceMirrored": False, "fit": "slim"})
    assert saved["sourceMirrored"] is False


def test_no_carry_when_no_previous_analysis():
    saved = _save(None, {"fit": "regular"})
    assert "sourceMirrored" not in saved


def test_immutable_contract_is_filtered_even_when_all_keys_are_supplied():
    full = {k: None for k in repo._SERVER_OWNED_ANALYSIS_KEYS}
    conn = _Conn({"sourceMirrored": True})
    asyncio.run(repo.save_analysis(conn, "p1", full))
    assert len(conn.calls) == 1
    assert not set(repo._IMMUTABLE_SERVER_OWNED_ANALYSIS_KEYS) & conn.calls[0][1][1].obj.keys()


def test_missing_keys_are_preserved_without_a_previous_lookup():
    conn = _Conn({"sourceMirrored": True})
    asyncio.run(repo.save_analysis(conn, "p1", {"fit": "regular"}))
    assert len(conn.calls) == 1
    assert conn.saved == {"sourceMirrored": True, "fit": "regular"}


def test_stale_writer_of_one_key_keeps_another_writers_change():
    conn = _Conn({"fit": "slim", "brandUseCategory": "old"})
    asyncio.run(repo.save_analysis(conn, "p1", {"brandUseCategory": "new"}))
    asyncio.run(repo.save_analysis(conn, "p1", {"fit": "regular"}))
    assert conn.saved == {"fit": "regular", "brandUseCategory": "new"}


def test_arrays_and_fit_profile_are_replaced_whole_and_null_is_saved():
    saved = _save({"matchClothing": [{"id": "old"}], "fitProfile": {"axes": {}, "matchingFit": {}},
                   "brandUseCategory": "old"},
                  {"matchClothing": [], "fitProfile": {"axes": {"fit": "regular"}},
                   "brandUseCategory": None})
    assert saved == {"matchClothing": [], "fitProfile": {"axes": {"fit": "regular"}},
                     "brandUseCategory": None}


@pytest.mark.parametrize("merge", [False, True])
def test_locked_column_and_payload_cannot_be_changed_by_analysis_patch(merge):
    conn = _Conn({"locked": True, "fit": "slim"}, locked=True)
    row = asyncio.run(repo.save_analysis(conn, "p1", {"locked": False, "fit": "regular"},
                                         merge=merge, delete_nulls=merge))
    assert row["locked"] is True and row["payload"]["locked"] is True
    assert "locked =" not in conn.calls[0][0]
    assert "locked" not in _save(None, {"locked": True}, merge=merge, delete_nulls=merge)


@pytest.mark.parametrize("merge", [False, True])
@pytest.mark.parametrize("key", repo._IMMUTABLE_SERVER_OWNED_ANALYSIS_KEYS)
@pytest.mark.parametrize("incoming", [None, {"forged": True}])
def test_client_cannot_remove_or_replace_server_evidence_in_either_mode(merge, key, incoming):
    original = {"server": True}
    assert _save({key: original}, {key: incoming}, merge=merge, delete_nulls=merge)[key] == original
    assert key not in _save(None, {key: incoming}, merge=merge, delete_nulls=merge)


def test_legacy_replace_drops_omitted_user_keys_keeps_server_keys_and_stores_null():
    original = {"obsolete": "remove", "fit": "slim", **{k: {"server": True} for k in repo._SERVER_OWNED_ANALYSIS_KEYS}}
    saved = _save(original, {"fit": "regular", "brandUseCategory": None}, merge=False)
    assert saved == {"fit": "regular", "brandUseCategory": None,
                     **{k: {"server": True} for k in repo._SERVER_OWNED_ANALYSIS_KEYS}}
    assert _save({"sourceMirrored": True}, {"sourceMirrored": False}, merge=False) == {"sourceMirrored": False}
    assert _save({"sourceMirrored": True}, {"sourceMirrored": None}, merge=False) == {"sourceMirrored": None}


def test_client_merge_null_deletes_only_top_level_keys_and_replaces_arrays_and_profile():
    saved = _save({"fit": "slim", "brandUseCategory": "old", "measurementsUnknown": False,
                   "matchClothing": [{"id": "old"}], "fitProfile": {"matchingFit": {}}},
                  {"brandUseCategory": None, "measurementsUnknown": None, "matchClothing": [],
                   "fitProfile": {"axes": {"fit": None}}}, merge=True, delete_nulls=True)
    assert saved == {"fit": "slim", "matchClothing": [], "fitProfile": {"axes": {"fit": None}}}
    assert _save({"fitProfile": {}}, {"fitProfile": None}, merge=True, delete_nulls=True) == {}
    assert _save(None, {"brandUseCategory": None}, merge=True, delete_nulls=True) == {}


@pytest.mark.parametrize("key", repo._SERVER_OWNED_ANALYSIS_KEYS)
def test_client_merge_null_cannot_remove_server_owned_keys(key):
    assert _save({key: {"server": True}}, {key: None}, merge=True, delete_nulls=True) == {key: {"server": True}}


@pytest.mark.parametrize("query,keeps_other", [("", False), ("?merge=0", False), ("?merge=1", True)])
def test_client_route_defaults_to_replace_and_requires_opt_in_to_merge(
    client, make_token, monkeypatch, query, keeps_other,
):
    conn = _Conn({"fit": "slim", "other": "keep only in merge", "sourceMirrored": True})
    @contextlib.asynccontextmanager
    async def connection(_request):
        yield conn
    monkeypatch.setattr(routes, "get_conn", connection)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={}))
    response = client.patch(f"/v1/projects/p1/analysis{query}", headers=auth_headers(make_token),
                            json={"fit": "tight", "brandUseCategory": None})
    assert response.status_code == 200, response.text
    expected = {"projectId": "p1", "fit": "slim", "sourceMirrored": True}
    expected.update({"other": "keep only in merge"} if keeps_other else {"brandUseCategory": None})
    assert response.json() == expected


def test_merge_route_keeps_existing_validation_before_the_write(client, make_token, monkeypatch):
    save = AsyncMock()
    monkeypatch.setattr(repo, "save_analysis", save)
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token),
                            json={"brandUseCategory": "invalid"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_brand_use_category"
    save.assert_not_awaited()


def test_confirmed_gpt_product_evidence_is_carried_when_client_omits_it():
    contract = {"schemaVersion": 1, "contractSha256": "server-owned"}
    saved = _save(
        {"confirmedGptProductEvidence": contract, "fit": "over"},
        {"fit": "regular"},
    )
    assert saved["confirmedGptProductEvidence"] == contract
    assert saved["fit"] == "regular"


def test_client_cannot_replace_or_create_confirmed_gpt_product_evidence():
    server_contract = {"schemaVersion": 1, "contractSha256": "server-owned"}
    client_contract = {"schemaVersion": 1, "contractSha256": "tampered"}
    saved = _save(
        {"confirmedGptProductEvidence": server_contract},
        {"fit": "regular", "confirmedGptProductEvidence": client_contract},
    )
    assert saved["confirmedGptProductEvidence"] == server_contract

    without_previous = _save(
        None,
        {"fit": "regular", "confirmedGptProductEvidence": client_contract},
    )
    assert "confirmedGptProductEvidence" not in without_previous


def test_signed_public_evidence_is_rebound_to_uploaded_product_before_save(
    client, make_token, monkeypatch
):
    patch_route_db(monkeypatch, routes)
    client.app.state.r2 = type(
        "R2", (), {"get_bytes": staticmethod(lambda key: b"\x89PNG-bytes")}
    )()
    saved = []
    contract = {"schemaVersion": 1, "contractSha256": "server-contract"}

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_product(conn, project_id):
        return {
            "colors": [{
                "isBase": True,
                "images": [{"slot": "Front", "id": "asset-front"}],
            }]
        }

    async def fake_asset(conn, user_id, asset_id):
        return {"r2_key": "seller/front.png", "mime_type": "image/png"}

    async def fake_save(conn, project_id, value):
        saved.append((project_id, value))

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(routes.repo, "save_confirmed_gpt_product_evidence", fake_save)
    monkeypatch.setattr(
        routes.product_evidence_contract,
        "verify_handoff",
        lambda value, secret: contract,
    )
    monkeypatch.setattr(
        routes.product_evidence_contract,
        "source_binding_matches",
        lambda value, images, slots: value == contract
        and images == [(b"\x89PNG-bytes", "image/png")]
        and slots == ["Front"],
    )

    response = client.post(
        "/v1/projects/p1/analysis/confirmed-gpt-evidence:promote",
        headers=auth_headers(make_token),
        json={"signed": "handoff"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"promoted": True}
    assert saved == [("p1", contract)]


@pytest.mark.parametrize("junk", ["false", 0, None, "true", 1])
def test_prompt_block_requires_strict_true(junk):
    """소비부는 truthiness 가 아니라 `is True` — 저장 API 는 임의 JSON 을 받는다.

    문자열 "false" 가 블록을 켜면 멀쩡한 사진을 좌우로 뒤집는다.
    """
    from app.agents.prompts import build_mirrored_source_block
    assert build_mirrored_source_block({"sourceMirrored": junk}) == ""
    assert build_mirrored_source_block({"sourceMirrored": True}) != ""
