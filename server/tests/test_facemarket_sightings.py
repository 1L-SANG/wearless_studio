"""모델 제보("내 얼굴 찾기 신고") 라우트 — 2026-09-27.

핵심 계약:
  1. 등록된 모델 본인만. 모델 확인·하루 한도가 이미지 바이트를 읽기 **전에** 끝난다.
  2. 올린 이미지는 관리자 추적과 같은 대조(trace_image)를 거쳐 발견 원장에 남는다 — 매칭이 없어도.
  3. 모델에게는 접수 사실만 돌려준다. 셀러·배포본 정보는 응답에 없다.
  4. 이미지 바이트는 저장하지 않는다. 감사 원장엔 해시 앞자리·요약만.
"""
import contextlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import facemarket_sightings as S
from app.main import create_app
from conftest import make_settings

MODEL_ID = "dddddddd-0000-4000-8000-000000000004"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)


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
        if s.startswith("select id::text as id from fm_models where user_id"):
            self._rows = [{"id": MODEL_ID}] if self.store["model"] else []
        elif s.startswith("select count(*) as n from fm_trace_findings"):
            self._rows = [{"n": self.store["today"]}]
        elif s.startswith("select id::text as id, status, created_at, report_page_url"):
            self._rows = self.store["mine"]
        else:
            raise AssertionError(f"unexpected SQL: {s[:120]}")

    async def fetchone(self):
        return self._rows[0] if self._rows else None

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
def sight(keypair, monkeypatch):
    _priv, public_key = keypair
    app = create_app(make_settings(
        facemarket_enabled=True, fm_provenance_enabled=True,
        fm_provenance_token_secret="s", public_web_origin="https://ai.wearless.kr",
    ))
    app.state.jwt_key_resolver = lambda _t: public_key
    store = {"model": True, "today": 0, "mine": [], "sql": [], "commits": 0, "audits": [],
             "traced": [], "recorded": []}

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield Conn(store)

    async def fake_trace(conn, data, *, origin, rows=None):
        store["traced"].append(data)
        if data == b"bad":
            raise ValueError("invalid image")
        return {"image": {"sha256": "ab" * 32, "sha256Prefix": "ab" * 6},
                "watermark": {"status": "not_found"},
                "candidates": [{"target": "cut", "outputRecordId": "o1",
                                "model": {"id": MODEL_ID}, "seller": {"id": "seller-9"},
                                "confidence": "medium", "evidence": {"watermark": False}}]}

    async def fake_record(conn, **kw):
        store["recorded"].append(kw)
        return {"id": "finding-1", "matched": True}

    async def fake_audit(conn, **kw):
        store["audits"].append(kw)

    monkeypatch.setattr(S, "get_conn", fake_get_conn)
    monkeypatch.setattr(S.facemarket_trace, "trace_image", fake_trace)
    monkeypatch.setattr(S.fm_trace_findings, "record_model_report", fake_record)
    monkeypatch.setattr(S.admin_guard, "write_audit", fake_audit)
    return TestClient(app), store


def _post(client, make_token, data=b"img", **form):
    return client.post(
        "/v1/facemarket/me/sightings",
        files={"image": ("found.jpg", data, "image/jpeg")},
        data=form,
        headers={"Authorization": f"Bearer {make_token(sub='model-user-1')}"},
    )


def test_model_report_is_traced_recorded_and_audited(sight, make_token):
    client, store = sight
    r = _post(client, make_token, pageUrl=" https://shop.example/item/1 ", note=" 제 얼굴이에요 ")
    assert r.status_code == 201, r.text
    assert r.json() == {"id": "finding-1", "status": "received"}
    assert "seller-9" not in r.text and "o1" not in r.text      # 모델에게 셀러·원장 정보 없음
    (rec,) = store["recorded"]
    assert rec["reporter_model_id"] == MODEL_ID and rec["reporter_user_id"] == "model-user-1"
    assert rec["page_url"] == "https://shop.example/item/1" and rec["note"] == "제 얼굴이에요"
    assert rec["image_sha256"] == "ab" * 32 and len(rec["candidates"]) == 1
    (audit,) = store["audits"]
    assert audit["action"] == "facemarket.sighting_report" and audit["target_id"] == MODEL_ID
    assert audit["after"] == {"sha256Prefix": "ab" * 6, "candidates": 1, "findingId": "finding-1"}
    assert store["commits"] == 1


def test_non_model_is_refused_before_bytes_are_read(sight, make_token):
    client, store = sight
    store["model"] = False
    r = _post(client, make_token)
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    assert store["traced"] == [] and store["recorded"] == []


def test_daily_limit_is_checked_before_bytes(sight, make_token):
    client, store = sight
    store["today"] = S.DAILY_REPORT_LIMIT
    r = _post(client, make_token)
    assert r.status_code == 429 and r.json()["error"]["code"] == "report_limit"
    assert store["traced"] == []


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x/y", "https://" + "a" * 600,
                                 "not a url"])
def test_page_url_must_be_http(sight, make_token, url):
    client, store = sight
    r = _post(client, make_token, pageUrl=url)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_page_url"
    assert store["traced"] == []


def test_invalid_image_is_400_and_nothing_recorded(sight, make_token):
    client, store = sight
    r = _post(client, make_token, data=b"bad")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"
    assert store["recorded"] == [] and store["commits"] == 0


def test_oversize_is_413(sight, make_token, monkeypatch):
    client, store = sight
    monkeypatch.setattr(S, "MAX_REPORT_BYTES", 10)
    r = _post(client, make_token, data=b"x" * 11)
    assert r.status_code == 413 and store["traced"] == []


def test_model_sees_own_reports_with_plain_status(sight, make_token):
    client, store = sight
    store["mine"] = [
        {"id": "f2", "status": "misuse", "created_at": NOW, "report_page_url": "https://a"},
        {"id": "f1", "status": "new", "created_at": NOW, "report_page_url": None},
    ]
    r = client.get("/v1/facemarket/me/sightings",
                   headers={"Authorization": f"Bearer {make_token(sub='model-user-1')}"})
    assert r.status_code == 200, r.text
    assert r.json() == {"items": [
        {"id": "f2", "status": "misuse", "createdAt": NOW.isoformat(), "pageUrl": "https://a"},
        {"id": "f1", "status": "new", "createdAt": NOW.isoformat(), "pageUrl": None},
    ]}
    assert r.headers["cache-control"] == "no-store"


def test_sighting_route_absent_without_provenance_flag(keypair, make_token):
    app = create_app(make_settings(facemarket_enabled=True, fm_provenance_enabled=False))
    app.state.jwt_key_resolver = lambda _t: keypair[1]
    with TestClient(app) as client:
        r = client.get("/v1/facemarket/me/sightings",
                       headers={"Authorization": f"Bearer {make_token(sub='u')}"})
    assert r.status_code == 404
