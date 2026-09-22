"""정산 행 미리보기 — 발행본(long_png)이 있으면 그것, 없으면 그 잡이 만든 생성 컷.

운영에서 셀러가 상세페이지를 한 번도 내려받지 않아 fm_publication_records 가 0행이라
'사용된 페이지' 가 전부 빈 그림이었다(2026-09-22). 서버가 이미 가진 생성 컷으로 폴백한다.
"""

from types import SimpleNamespace

import pytest

from app import facemarket
from payout_helpers import Conn, client_for, patch_db

SETTLEMENT_ID = "55555555-5555-4555-8555-555555555555"
BASE = f"/v1/facemarket/model/settlements/{SETTLEMENT_ID}/preview-url"


def auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _client(keypair, monkeypatch, row, sign=None):
    conn = Conn([row])
    patch_db(monkeypatch, facemarket, conn)
    client = client_for(keypair)
    client.app.state.r2 = SimpleNamespace(
        preview_url=sign or (lambda *_args: pytest.fail("must not sign")))
    return client, conn


def test_publication_wins_over_cut(keypair, make_token, monkeypatch):
    calls = []
    client, conn = _client(
        keypair, monkeypatch,
        {"publication_key": "signed-page", "cut_key": "generated-cut"},
        sign=lambda key, expires: calls.append((key, expires)) or "https://preview.test/page",
    )
    response = client.get(BASE, headers=auth(make_token))
    assert response.status_code == 200
    assert response.json() == {
        "url": "https://preview.test/page", "expiresIn": 600, "source": "publication"}
    assert response.headers["cache-control"] == "no-store"
    assert calls == [("signed-page", 600)]
    sql, params = conn.executed[0]
    assert "m.user_id = %s" in sql and "st.id = %s" in sql
    assert params == (SETTLEMENT_ID, "user-1")


def test_falls_back_to_generated_cut_when_seller_never_published(keypair, make_token, monkeypatch):
    calls = []
    client, _conn = _client(
        keypair, monkeypatch,
        {"publication_key": None, "cut_key": "generated-cut"},
        sign=lambda key, expires: calls.append((key, expires)) or "https://preview.test/cut",
    )
    response = client.get(BASE, headers=auth(make_token))
    assert response.status_code == 200
    assert response.json() == {"url": "https://preview.test/cut", "expiresIn": 600, "source": "cut"}
    assert calls == [("generated-cut", 600)]


@pytest.mark.parametrize("row", [None, {"publication_key": None, "cut_key": None}])
def test_foreign_or_imageless_settlement_is_hidden(row, keypair, make_token, monkeypatch):
    client, _conn = _client(keypair, monkeypatch, row)
    response = client.get(BASE, headers=auth(make_token))
    assert response.status_code == 404
    assert "key" not in response.text


def test_invalid_id_and_anonymous_do_not_query(keypair, make_token, monkeypatch):
    conn = Conn()
    patch_db(monkeypatch, facemarket, conn)
    client = client_for(keypair)
    assert client.get(BASE).status_code == 401
    assert client.get(BASE.replace(SETTLEMENT_ID, "invalid"), headers=auth(make_token)).status_code == 404
    assert conn.executed == []


def test_storage_unconfigured_is_503(keypair, make_token, monkeypatch):
    conn = Conn([{"publication_key": None, "cut_key": "generated-cut"}])
    patch_db(monkeypatch, facemarket, conn)
    client = client_for(keypair)
    client.app.state.r2 = None
    assert client.get(BASE, headers=auth(make_token)).status_code == 503
