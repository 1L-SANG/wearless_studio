import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

AUDIENCE = "authenticated"


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
