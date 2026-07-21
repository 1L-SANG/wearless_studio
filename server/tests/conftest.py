import time
import contextlib
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

AUDIENCE = "authenticated"


def auth_headers(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


class FakeConn:
    async def commit(self):
        return None


def patch_route_db(monkeypatch, routes_module):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield FakeConn()

    monkeypatch.setattr(routes_module, "get_conn", fake_conn)


class FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeConn()

        return _cm()


class FakeR2:
    def get_bytes(self, key):
        return b"\x89PNG-bytes"

    def put_bytes(self, key, data, mime):
        return None

    def delete(self, key):
        return None


class FakeGemini:
    pass


def fake_worker_app(settings, *, r2=None, gemini=None):
    state = types.SimpleNamespace(
        settings=settings,
        pool=FakePool(),
        r2=r2 or FakeR2(),
        gemini=gemini or FakeGemini(),
    )
    return types.SimpleNamespace(state=state)


def worker_job(payload=None, *, credits_reserved=1):
    return {
        "id": "j1",
        "user_id": "u1",
        "project_id": "p1",
        "lease_token": "u1:tok",
        "credits_reserved": credits_reserved,
        "payload": payload or {},
    }


@pytest.fixture(scope="session")
def keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


def make_settings(**overrides) -> Settings:
    base = dict(
        app_env="prod",
        supabase_url="https://example.supabase.co",
        jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        jwt_audience=AUDIENCE,
        cors_origins=["http://localhost:5173"],
        database_url=None,
        r2_account_id=None,
        r2_access_key_id=None,
        r2_secret_access_key=None,
        r2_bucket=None,
        r2_endpoint=None,
        r2_public_base=None,
        # 운영 기본은 bestof. 관련 없는 기존 워커 테스트는 외부 vision 판정을 호출하지 않게
        # 테스트 기본만 명시적으로 off로 두고 QC 테스트에서 모드를 개별 활성화한다.
        garment_qc_mode="off",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def client(keypair):
    private_key, public_key = keypair
    app = create_app(make_settings())
    # 테스트에서는 JWKS 네트워크 대신 테스트 공개키로 검증
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app)


@pytest.fixture()
def make_token(keypair):
    private_key, _ = keypair

    def _make(sub="user-1", aud=AUDIENCE, exp_offset=3600, **extra):
        claims = {
            "sub": sub,
            "aud": aud,
            "exp": int(time.time()) + exp_offset,
            **extra,
        }
        return jwt.encode(claims, private_key, algorithm="ES256")

    return _make
