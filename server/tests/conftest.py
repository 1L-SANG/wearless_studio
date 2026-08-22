import os
import tempfile
import time
import contextlib
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.agents.face_qc import default_model_dir
from app.config import Settings
from app.main import create_app

AUDIENCE = "authenticated"

_FACE_QC_TEST_WEIGHTS_DIR = None


def _face_qc_test_weights_dir() -> str:
    """SFace/YuNet 자리표시(placeholder) 파일을 담은 임시 디렉터리.

    validate_biometric_settings 가 startup 에 weight 파일 '존재'만 확인하고(내용을 로드하지
    않음), 실제 QC 호출은 각 테스트가 load_face_qc/FaceQc 를 monkeypatch 하므로 내용은 비어
    있어도 된다. real weights(server/app/data/face_models/*.onnx) 는 gitignore 되어 이
    저장소·테스트 환경에는 없다.
    """
    global _FACE_QC_TEST_WEIGHTS_DIR
    if _FACE_QC_TEST_WEIGHTS_DIR is None:
        model_dir = default_model_dir()
        names = ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx")
        if all(os.path.exists(os.path.join(model_dir, name)) for name in names):
            # 실제 weights 가 이미 존재하면(로컬 빌드 등) 그대로 재사용한다.
            _FACE_QC_TEST_WEIGHTS_DIR = model_dir
        else:
            d = tempfile.mkdtemp(prefix="fm_face_qc_test_weights_")
            for name in names:
                open(os.path.join(d, name), "wb").close()
            _FACE_QC_TEST_WEIGHTS_DIR = d
    return _FACE_QC_TEST_WEIGHTS_DIR


def auth_headers(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


class FakeConn:
    async def commit(self):
        return None

    async def rollback(self):
        # 실제 psycopg 커넥션에는 있고 여기만 없어서, 라우트의 예외 정리 경로가
        # AttributeError 로 다시 터졌다(2026-08-12). 가짜 커넥션도 같은 표면을 가져야 한다.
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

    def put_bytes(self, key, data, mime, cache=None):
        return None

    def delete(self, key):
        return None

    def public_url(self, key):
        # 실제 R2.public_url 미러 — cut_done 이벤트의 previewUrl 근거(editor_wait_dev_spec §2-1)
        return f"https://r2.test/{key}"

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/{key}"


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
        detail_cut_stagger_ms=0,  # 테스트는 제출 간격 없이(실시간 sleep 방지) — 운영 기본은 3000
        detail_cut_retry_delay_seconds=0,  # 컷 재시도 대기도 테스트에서는 0 — 운영 기본은 2초
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
    if overrides.get("fm_biometric_enrollment_enabled"):
        # validate_biometric_settings 는 이제 fm_ci_pepper 와 SFace/YuNet weight 파일 존재를
        # startup 에 요구한다(2026-08-23). 대부분의 기존 테스트는 그 자체를 검증 대상으로
        # 삼지 않으므로 여기서 안전한 기본값을 깔아 준다 — 호출자가 명시적으로 override 하면
        # (None 포함) 그 값이 우선한다.
        if "fm_ci_pepper" not in overrides:
            base["fm_ci_pepper"] = "test-pepper"
        if "fm_face_qc_dir" not in overrides:
            base["fm_face_qc_dir"] = _face_qc_test_weights_dir()
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
