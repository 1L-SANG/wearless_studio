from types import SimpleNamespace

import pytest

from app import facemarket
from payout_helpers import PUBLICATION_ID, Conn, client_for, patch_db
from test_facemarket_usage_controls import _settlement

BASE = f"/v1/facemarket/model/publications/{PUBLICATION_ID}/preview-url"


def auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_preview_route_exists(keypair, make_token):
    assert client_for(keypair).get(BASE, headers=auth(make_token)).status_code == 503


@pytest.mark.parametrize("kind", ["long_png", "block_png"])
@pytest.mark.parametrize("revoked", [None, "2026-09-10"])
def test_owner_can_preview_even_revoked_with_ten_minute_signature(kind, revoked, keypair, make_token, monkeypatch):
    conn = Conn([{"kind": kind, "r2_key": "private-signed-object", "revoked_at": revoked}])
    patch_db(monkeypatch, facemarket, conn)
    client = client_for(keypair)
    calls = []

    def preview_url(key, expires):
        calls.append((key, expires))
        return "https://preview.test/temporary"

    client.app.state.r2 = SimpleNamespace(preview_url=preview_url)
    response = client.get(BASE, headers=auth(make_token))
    assert response.status_code == 200
    assert response.json() == {"url": "https://preview.test/temporary", "expiresIn": 600}
    assert response.headers["cache-control"] == "no-store"
    assert calls == [("private-signed-object", 600)]
    sql, params = conn.executed[0]
    assert "m.user_id = %s" in sql and "m.id = p.model_id" in sql
    assert params == (PUBLICATION_ID, "user-1")
    assert "revoked_at is null" not in sql


@pytest.mark.parametrize("row", [None, {"kind": "zip", "r2_key": "key"}, {"kind": "long_png", "r2_key": None}])
def test_foreign_missing_zip_and_unsigned_are_hidden(row, keypair, make_token, monkeypatch):
    conn = Conn([row])
    patch_db(monkeypatch, facemarket, conn)
    client = client_for(keypair)
    client.app.state.r2 = SimpleNamespace(preview_url=lambda *_args: pytest.fail("must not sign"))
    response = client.get(BASE, headers=auth(make_token))
    assert response.status_code == 404
    assert "key" not in response.json()


def test_invalid_id_and_anonymous_do_not_query(keypair, make_token, monkeypatch):
    conn = Conn()
    patch_db(monkeypatch, facemarket, conn)
    client = client_for(keypair)
    assert client.get(BASE).status_code == 401
    assert client.get(BASE.replace(PUBLICATION_ID, "invalid"), headers=auth(make_token)).status_code == 404
    assert conn.executed == []


def test_settlements_link_latest_owned_publication_without_private_fields(keypair, make_token, monkeypatch):
    conn = Conn([[{**_settlement(), "publication_id": PUBLICATION_ID, "project_id": "project",
                   "r2_key": "private", "seller_id": "private", "image_sha256": "private"}]])
    patch_db(monkeypatch, facemarket, conn)
    response = client_for(keypair).get("/v1/facemarket/settlements", headers=auth(make_token))
    assert response.status_code == 200
    row = response.json()[0]
    assert row["publicationId"] == PUBLICATION_ID
    assert row["projectId"] == "project"
    assert not ({"r2Key", "sellerId", "imageSha256", "r2_key"} & row.keys())
    sql, _ = conn.executed[0]
    assert "left join lateral" in sql
    assert "pub.project_id = j.project_id" in sql
    assert "pub.model_id = m.id" in sql
    assert "pub.kind = 'long_png'" in sql and "pub.revoked_at is null" in sql
    assert "pub.r2_key is not null" in sql
    assert "order by pub.created_at desc, pub.id desc limit 1" in sql
