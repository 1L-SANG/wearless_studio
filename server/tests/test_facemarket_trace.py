"""관리자 출처 추적 라우트(POST /v1/facemarket/admin/trace) — 2026-09-26.

핵심 계약:
  1. 관리자만. 판정이 이미지 바이트를 읽기 **전에** 끝난다.
  2. 워터마크 코드가 원장과 맞으면 그 배포본이 확실한 후보(high) — 셀러는 마스킹.
  3. 워터마크가 없어도 pHash 로 컷·배포본을 후보로 낸다(medium/low).
  4. 감사 원장에는 해시 앞 12자·요약만. 이미지 바이트는 어디에도 안 남는다.
"""
import contextlib
import io
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import admin_guard
from app import facemarket_trace as T
from app.main import create_app
from app.services import fm_fingerprint, fm_publication_mark
from conftest import make_settings
from scripts.fm_trace_robustness import procedural_photo, synthetic_page

PUB_ID = "aaaaaaaa-0000-4000-8000-000000000001"
OUT_ID = "bbbbbbbb-0000-4000-8000-000000000002"
CODE = 0x2468ACE1
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _png(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _jpeg(im, q=75) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q)
    return buf.getvalue()


@pytest.fixture(scope="module")
def world():
    page = synthetic_page(2000, seed=12, blocks=6)
    marked = fm_publication_mark.mark_publication(_png(page), "long_png", CODE)
    assert marked.wm_status == "embedded"
    cut = procedural_photo(1024, 1536, 777)
    rows = [{"id": f"f{i}", "publication_id": PUB_ID, "output_record_id": None,
             "kind": fp["kind"], "region_y0": fp["region_y0"], "region_y1": fp["region_y1"],
             "phash": fm_fingerprint.to_signed(fp["phash"]),
             "dhash": fm_fingerprint.to_signed(fp["dhash"])}
            for i, fp in enumerate(marked.fingerprints)]
    ch = fm_fingerprint.safe_image_hashes(_png(cut))
    rows.append({"id": "fc", "publication_id": None, "output_record_id": OUT_ID, "kind": "cut",
                 "region_y0": None, "region_y1": None,
                 "phash": fm_fingerprint.to_signed(ch["phash"]),
                 "dhash": fm_fingerprint.to_signed(ch["dhash"])})
    return {"page": Image.open(io.BytesIO(marked.data)).convert("RGB"), "cut": cut, "rows": rows}


class Cur:
    def __init__(self, store):
        self.store = store
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        self.store["sql"].append(s)
        if s.startswith("select id::text as id, wm_code from fm_publication_records"):
            codes = params[0]
            self._rows = [{"id": PUB_ID, "wm_code": CODE}] if CODE in codes else []
        elif s.startswith("select id::text as id, publication_id::text"):
            self._rows = self.store["rows"]
        elif s.startswith("select p.id::text as id, p.project_id::text"):
            self._rows = [{
                "id": PUB_ID, "project_id": "proj-1", "seller_id": "seller-1",
                "model_id": "model-1", "license_ref": "lic-1", "kind": "long_png",
                "created_at": NOW, "revoked_at": None, "wm_status": "embedded",
                "c2pa_status": "signed", "chain_status": "confirmed",
                "project_title": "니트 상세", "model_name": "김*연",
                "license_status": "active", "license_valid_until": None,
                "seller_email": "seller.kim@example.com", "seller_name": "김셀러",
            }] if PUB_ID in params[0] else []
        elif s.startswith("select r.id::text as id, r.asset_id::text"):
            self._rows = [{
                "id": OUT_ID, "asset_id": "asset-9", "job_id": "job-9", "project_id": "proj-2",
                "seller_id": "seller-2", "model_id": "model-1", "license_ref": "lic-1",
                "created_at": NOW, "project_title": "셔츠", "model_name": "김*연",
                "license_status": None, "license_valid_until": None,
                "seller_email": "x@y.kr", "seller_name": None,
            }] if OUT_ID in params[0] else []
        else:
            raise AssertionError(f"unexpected SQL: {s[:120]}")

    async def fetchall(self):
        return self._rows


class Conn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return Cur(self.store)

    async def commit(self):
        self.store["commits"] += 1

    async def rollback(self):
        return None


@pytest.fixture()
def trace(keypair, monkeypatch, world):
    _priv, public_key = keypair
    app = create_app(make_settings(
        facemarket_enabled=True, fm_provenance_enabled=True,
        fm_provenance_token_secret="s", public_web_origin="https://ai.wearless.kr",
    ))
    app.state.jwt_key_resolver = lambda _t: public_key
    store = {"rows": world["rows"], "sql": [], "commits": 0, "audits": [], "admin": True,
             "admin_checked_before_read": None}

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield Conn(store)

    async def fake_require_admin(conn, user_id, request):
        if not store["admin"]:
            raise admin_guard.forbidden()

    async def fake_audit(conn, **kw):
        store["audits"].append(kw)

    monkeypatch.setattr(T, "get_conn", fake_get_conn)
    monkeypatch.setattr(T.admin_guard, "require_admin", fake_require_admin)
    monkeypatch.setattr(T.admin_guard, "write_audit", fake_audit)
    return TestClient(app), store


def _post(client, make_token, data: bytes, name="found.jpg", mime="image/jpeg"):
    return client.post(
        "/v1/facemarket/admin/trace",
        files={"image": (name, data, mime)},
        headers={"Authorization": f"Bearer {make_token(sub='admin-1')}"},
    )


def test_watermarked_strip_is_a_high_confidence_hit_with_masked_seller(trace, make_token, world):
    client, store = trace
    small = world["page"].resize((860, round(world["page"].height * 860 / 2000)), Image.LANCZOS)
    crop = small.crop((0, 900, 860, 1900))
    r = _post(client, make_token, _jpeg(crop, 70))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["watermark"]["status"] == "matched"
    assert body["watermark"]["code"] == f"{CODE:08x}"
    assert body["watermark"]["publicationId"] == PUB_ID
    top = body["candidates"][0]
    assert top["confidence"] == "high" and top["publicationId"] == PUB_ID
    assert top["evidence"]["watermark"] is True
    assert top["evidence"]["phashDistance"] is not None      # 지문도 같은 배포본을 가리킨다
    assert top["seller"] == {"id": "seller-1", "emailMasked": "se***@example.com",
                             "nameMasked": "김*러"}
    assert top["model"]["displayName"] == "김*연"
    assert top["license"]["status"] == "active"
    assert top["verifyUrl"] == f"https://ai.wearless.kr/verify/p/{PUB_ID}"
    assert "seller.kim@example.com" not in r.text
    # 감사 원장 — 요약만, 바이트 없음
    (audit,) = store["audits"]
    assert audit["action"] == "facemarket.trace" and audit["target_id"] == PUB_ID
    assert set(audit["after"]) == {"sha256Prefix", "watermark", "candidates",
                                   "publicationIds", "outputRecordIds"}
    assert len(audit["after"]["sha256Prefix"]) == 12
    assert store["commits"] == 1


def test_unwatermarked_cut_is_found_by_phash(trace, make_token, world):
    client, store = trace
    cut = world["cut"].resize((600, 900), Image.LANCZOS)
    r = _post(client, make_token, _jpeg(cut, 80))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["watermark"]["status"] == "not_found"
    top = body["candidates"][0]
    assert top["target"] == "cut" and top["outputRecordId"] == OUT_ID
    assert top["confidence"] in ("medium", "low")
    assert top["evidence"]["matchedKind"] == "cut"
    assert top["license"]["status"] == "deleted"          # 라이선스 행이 사라진 원장
    assert top["verifyUrl"] is None and top["assetId"] == "asset-9"


def test_unrelated_image_has_no_candidates(trace, make_token):
    client, _store = trace
    r = _post(client, make_token, _jpeg(procedural_photo(860, 1100, 4242)))
    assert r.status_code == 200, r.text
    assert r.json()["candidates"] == []
    assert r.json()["watermark"]["status"] == "not_found"


def test_non_admin_is_refused_before_bytes_are_used(trace, make_token, monkeypatch):
    client, store = trace
    store["admin"] = False
    called = []
    monkeypatch.setattr(T, "_analyze", lambda data: called.append(1))
    r = _post(client, make_token, b"whatever")
    assert r.status_code == 403
    assert called == [] and store["audits"] == []


def test_invalid_image_is_400_and_oversize_is_413(trace, make_token, monkeypatch):
    client, _store = trace
    r = _post(client, make_token, b"not an image at all", name="x.png", mime="image/png")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"
    monkeypatch.setattr(T, "MAX_TRACE_BYTES", 10)
    r = _post(client, make_token, b"x" * 11)
    assert r.status_code == 413


def test_rank_candidates_orders_watermark_then_distance():
    def m(pub=None, out=None, d=0, dd=0, kind="strip"):
        return {"row": {"publication_id": pub, "output_record_id": out, "kind": kind,
                        "region_y0": 0 if kind == "strip" else None, "region_y1": 800},
                "phash_distance": d, "dhash_distance": dd,
                "query": {"kind": "window", "y0": 0, "y1": 800}}
    ranked = T.rank_candidates(
        [m(pub="p2", d=0), m(pub="p2", d=3), m(out="c1", d=6, kind="cut"), m(pub="p3", d=9)],
        ["p1"])
    assert [(c["target_id"], c["confidence"]) for c in ranked] == [
        ("p1", "high"), ("p2", "medium"), ("c1", "low"), ("p3", "low")]
    assert ranked[1]["matched_fingerprints"] == 2 and ranked[1]["phash_distance"] == 0


def test_mask_email():
    assert T._mask_email("seller.kim@example.com") == "se***@example.com"
    assert T._mask_email(None) is None and T._mask_email("nope") is None


def test_trace_route_absent_without_provenance_flag(keypair, make_token):
    app = create_app(make_settings(facemarket_enabled=True, fm_provenance_enabled=False))
    app.state.jwt_key_resolver = lambda _t: keypair[1]
    with TestClient(app) as client:
        r = _post(client, make_token, b"x")
    assert r.status_code == 404
