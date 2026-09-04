"""모델 본인이 자기 얼굴 사용 내역을 본다. 셀러 신원은 안 보인다.

모델에게 필요한 건 '몇 번 쓰였나'와 '체인에 기록됐나'다. 어느 셀러가 썼는지는
계약상 필요 없고, 노출하면 셀러 영업정보가 모델에게 새는 것이다.

Fix round 1(리뷰):
  · model_id 는 uuid 컬럼이라 쓰레기 입력이 그대로 쿼리에 들어가면 PG 캐스팅에서 터져 500이
    된다 — get_model_thumbnail·verify_license_public 선례대로 파싱 가드를 앞단에 둔다(가드가
    없으면 500이 그대로 CloudWatch→Slack 알림 소음이 된다). 별칭 표기(urn:uuid:… 등)는
    Python uuid.UUID() 는 통과시키지만 Postgres uuid_in 은 그 접두사를 안 받는다 — 가드가
    파싱 결과(canonical str)를 안 쓰고 원문을 그대로 실으면 404가 다시 500으로 돌아간다.
    FakeCursor 의 _looks_like_pg_uuid_literal 이 그 Postgres 쪽 엄격함을 흉내낸다.
  · 배포본이 철회돼도 union 에는 그대로 남는다 — revoked_at 이 있는 배포본과 없는 배포본이
    응답에서 구별이 안 되면 모델에게 거짓 정보를 준다. revoked:bool 필드를 더해 구별한다
    (컷 행은 항상 false). 화이트리스트는 4개 → 5개로 넓어진다 — 셀러/프로젝트/유저/원본해시는
    여전히 금지, revoked 는 모델 자신의 데이터라 이 규칙에 안 걸린다.
"""
import contextlib
import re

import pytest
from fastapi.testclient import TestClient

from app import facemarket
from app.facemarket import UsageCard
from app.main import create_app
from conftest import make_settings

FORBIDDEN = {"sellerId", "userId", "projectId", "r2Key", "imageSha256"}

MODEL_ID = "11111111-1111-1111-1111-111111111111"
OTHER_MODEL_ID = "22222222-2222-2222-2222-222222222222"
OWNER_ID = "user-1"

# 실제 Postgres uuid_in 은 하이픈 정규형(중괄호 허용)만 받는다 — Python uuid.UUID() 가 받아주는
# urn:uuid:… · 언더스코어 없는 32자리 hex 같은 별칭 표기는 여기서 거부돼야 진짜 캐스팅 실패를
# 흉내낸다(그래야 "파싱은 통과하되 원문을 그대로 썼다"는 버그가 이 가짜 DB에서도 재현된다).
_PG_UUID_LITERAL_RE = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)


def _looks_like_pg_uuid_literal(value) -> bool:
    return bool(_PG_UUID_LITERAL_RE.match(value or ""))


def test_usage_card_hides_seller_identity():
    camel = {
        "".join(w if i == 0 else w.capitalize() for i, w in enumerate(n.split("_")))
        for n in UsageCard.model_fields
    }
    assert not (camel & FORBIDDEN)


def test_usage_card_whitelist():
    # revoked 는 리뷰가 승인한 5번째 필드(모델 자신의 데이터, 셀러 신원 아님) —
    # equality 로 남겨 임의로 더 넓어지는 걸 막는다.
    assert set(UsageCard.model_fields) == {
        "kind", "created_at", "image_hash_prefix", "chain_status", "revoked",
    }


class _FakeCursor:
    def __init__(self, store):
        self.store = store
        self._result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        params = params or ()
        self.store.setdefault("sql", []).append(s)
        if s.startswith("select 1 from fm_models where id = %s and user_id = %s"):
            model_id, user_id = params
            if not _looks_like_pg_uuid_literal(model_id):
                # 실제 psycopg/Postgres 가 uuid 컬럼 캐스팅에서 여기서 InvalidTextRepresentation
                # 을 던진다. get_conn 은 쿼리 오류를 안 잡으므로(§db.py) 이 예외는 그대로
                # 일반 500 핸들러까지 올라간다 — 가드가 없으면 이게 그 500이다.
                raise ValueError(f'invalid input syntax for type uuid: "{model_id}"')
            self._result = (
                {"?column?": 1}
                if any(
                    m["id"] == model_id and m["user_id"] == user_id
                    for m in self.store["models"]
                )
                else None
            )
        elif "union all" in s and "fm_output_records" in s:
            model_id = params[0]
            if not _looks_like_pg_uuid_literal(model_id):
                raise ValueError(f'invalid input syntax for type uuid: "{model_id}"')
            rows = sorted(
                (r for r in self.store["usage"] if r["model_id"] == model_id),
                key=lambda r: r["created_at"],
                reverse=True,
            )
            self._result = [
                {
                    "kind": r["kind"],
                    "created_at": r["created_at"],
                    "prefix": r["image_sha256"][:12],
                    "chain_status": r.get("chain_status")
                    if r["kind"] == "publication"
                    else None,
                    "revoked": bool(r.get("revoked_at"))
                    if r["kind"] == "publication"
                    else False,
                }
                for r in rows[:200]
            ]
        else:  # pragma: no cover - 이 라우트가 안 쓰는 쿼리가 오면 바로 드러나야 한다
            raise AssertionError(f"unexpected query in fake cursor: {s}")

    async def fetchone(self):
        return self._result

    async def fetchall(self):
        return self._result or []


class _FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _FakeCursor(self.store)


@pytest.fixture()
def usage_fm(keypair, monkeypatch):
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    store = {
        "models": [{"id": MODEL_ID, "user_id": OWNER_ID}],
        "usage": [],
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield _FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    # raise_server_exceptions=False — 진짜 서버처럼 미처리 예외도 500 응답으로 받아야
    # "가드 없으면 500" 을 테스트가 검증할 수 있다(기본값은 예외를 테스트로 재던진다).
    return TestClient(app, raise_server_exceptions=False), store


def _auth(make_token, sub=OWNER_ID):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def test_malformed_model_id_is_404_not_500(usage_fm, make_token):
    client, store = usage_fm
    r = client.get(
        "/v1/facemarket/models/not-a-uuid/usage", headers=_auth(make_token)
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    # 가드가 앞단에서 컷했다면 DB 쿼리는 한 번도 안 나갔어야 한다.
    assert store.get("sql", []) == []


def test_alias_uuid_form_is_normalized_not_500(usage_fm, make_token):
    # urn:uuid:… 는 uuid.UUID() 가드는 통과하지만, 파싱된 canonical 값을 안 쓰고 원문을
    # 그대로 쿼리에 실으면 Postgres 쪽에서 다시 캐스팅 실패로 500이 된다.
    client, store = usage_fm
    aliased = f"urn:uuid:{MODEL_ID}"
    r = client.get(
        f"/v1/facemarket/models/{aliased}/usage", headers=_auth(make_token)
    )
    assert r.status_code == 200
    assert r.json() == []
    # 쿼리에 실제로 들어간 값이 정규형이어야 한다(별칭 원문이 아니라).
    assert store["sql"], "guard should still let a valid alias reach the query"


def test_owner_sees_own_usage_history(usage_fm, make_token):
    client, store = usage_fm
    store["usage"] = [
        {
            "model_id": MODEL_ID,
            "kind": "cut",
            "created_at": "2026-09-01T00:00:00Z",
            "image_sha256": "a" * 64,
        },
        {
            "model_id": MODEL_ID,
            "kind": "publication",
            "created_at": "2026-09-02T00:00:00Z",
            "image_sha256": "b" * 64,
            "chain_status": "confirmed",
            "revoked_at": None,
        },
        {
            "model_id": MODEL_ID,
            "kind": "publication",
            "created_at": "2026-09-03T00:00:00Z",
            "image_sha256": "c" * 64,
            "chain_status": "confirmed",
            "revoked_at": "2026-09-04T00:00:00Z",
        },
    ]
    r = client.get(
        f"/v1/facemarket/models/{MODEL_ID}/usage", headers=_auth(make_token)
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    # 가장 최근(철회된 배포본)부터 내림차순
    assert body[0]["revoked"] is True
    assert body[0]["chainStatus"] == "confirmed"
    assert body[1]["revoked"] is False
    assert body[2]["kind"] == "cut"
    assert body[2]["revoked"] is False
    assert body[2]["chainStatus"] is None
    for card in body:
        assert set(card.keys()) == {
            "kind", "createdAt", "imageHashPrefix", "chainStatus", "revoked",
        }


def test_not_owned_model_is_404(usage_fm, make_token):
    client, _store = usage_fm
    r = client.get(
        f"/v1/facemarket/models/{OTHER_MODEL_ID}/usage", headers=_auth(make_token)
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
