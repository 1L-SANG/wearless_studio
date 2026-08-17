"""analysis 저장이 AI 파생 필드를 이월하는지 (codex 리뷰 2026-07-31 CRITICAL 반영).

`save_analysis` 는 REPLACE 시맨틱이라 들어온 payload 가 곧 새 전문이다. 프론트는
`defaultAnalysisShape()` 기반 full payload 를 PATCH 하므로, shape 에 없는 서버 파생 키는
셀러가 소재·핏을 **한 번만 수정해도** 사라진다. sourceMirrored 가 사라지면 거울 셀카의
반전된 로고가 그대로 생성 컷에 남는다 — 조용한 회귀라 더 위험하다.

프론트 shape 에도 필드를 넣었지만(shapes.js), 구버전 클라·부분 payload·직접 API 호출까지
막으려면 서버가 최종 방어선이어야 한다.
"""
import asyncio

import pytest

from app import repo, routes
from conftest import auth_headers, patch_route_db


class _Cur:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.owner.calls.append((sql, params))
        self._sql = sql

    async def fetchone(self):
        if "insert into analyses" in self._sql:
            _pid, payload, locked = self.owner.calls[-1][1]
            self.owner.saved = payload.obj  # psycopg Json 래퍼
            return {"project_id": "p1", "payload": payload.obj, "locked": locked}
        return {"payload": self.owner.stored} if self.owner.stored is not None else None


class _Conn:
    def __init__(self, stored=None):
        self.stored, self.calls, self.saved = stored, [], None

    def cursor(self):
        return _Cur(self)


def _save(stored, incoming):
    conn = _Conn(stored)
    asyncio.run(repo.save_analysis(conn, "p1", incoming))
    return conn.saved


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


def test_immutable_contract_forces_previous_lookup_even_when_all_keys_are_supplied():
    """클라가 전문을 보내도 해시 계약은 서버의 기존 값을 확인해야 한다."""
    full = {k: None for k in repo._SERVER_OWNED_ANALYSIS_KEYS}
    conn = _Conn({"sourceMirrored": True})
    asyncio.run(repo.save_analysis(conn, "p1", full))
    assert any("select" in sql.lower() for sql, _ in conn.calls)


def test_previous_lookup_happens_when_a_key_is_missing():
    """반대 방향 — 키가 빠졌으면 반드시 이전 payload 를 읽어 이월해야 한다."""
    conn = _Conn({"sourceMirrored": True})
    asyncio.run(repo.save_analysis(conn, "p1", {"fit": "regular"}))
    assert any("select" in sql.lower() for sql, _ in conn.calls)


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
