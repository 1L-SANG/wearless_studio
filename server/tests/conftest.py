import os
import tempfile
import time
import contextlib
import types
import uuid

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


@pytest.fixture(autouse=True)
def stub_enrollment_photo_check(request, monkeypatch):
    """등록 사진 업로드 검사(YuNet)를 기본 '통과'로 둔다.

    진짜 판정에는 onnx 가중치가 필요한데 그건 gitignore 라 저장소·CI 에 없고(Dockerfile 이
    빌드 때 받는다), 테스트가 올리는 사진은 대부분 `b"image"` 같은 가짜 바이트다. 검사 자체는
    tests/test_facemarket_photo_check.py 가 숫자 픽스처로 본다.
    라우트 배선(400 photo_framing · 503)을 보는 테스트는 real_photo_check 마커로 이걸 끈다.
    """
    if request.node.get_closest_marker("real_photo_check"):
        return
    from app import facemarket_enrollment

    monkeypatch.setattr(facemarket_enrollment, "check_enrollment_photo",
                        lambda data, slot, **kw: (None, {"stub": True}))


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
        # 관리자 기기 게이트도 같은 이유로 테스트 기본 off — 기존 admin 라우트 테스트는 FakeConn
        # 큐에 role 행 하나만 넣고 도는데, shadow 도 기기 조회를 실행한다. 기기 테스트만 켠다.
        admin_device_gate="off",
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


# ── Task5: 간편인증(simple_auth) 등록 경로 공유 픽스처 ──────────────────────────────────
# Task6·7 도 이 픽스처를 재사용한다(컨트롤러 룰링). test_facemarket_biometric_enrollment.py
# 의 EnrollmentStore/FakeCursor/FakePool 조립을 그대로 재사용해 세 벌로 드리프트하지
# 않게 한다. 그 파일은 mid 경로의 회귀 방어망이라 손대지 않는다 — 필요한 확장(신분증
# 촬영 경로가 id_capture_pending 에서 시작하는 것을 그 파일의 FakeCursor 가 표현하지
# 못하는 문제)은 이 픽스처 안에서 monkeypatch 로 감싸 처리하고, 그 외 SQL 은 전부
# 원래 구현에 위임한다.
#
# 이름을 `enrollment_client_factory`로 둔다(fix round 2) — `enrollment_client`는
# test_facemarket_biometric_enrollment.py 가 이미 모듈 스코프 픽스처로 오래 쓰고 있다
# (거기선 바로 TestClient 를 반환, 팩토리가 아니다). 모듈 스코프 픽스처가 conftest 것을
# 가려서 오늘은 안 깨지지만, 나중에 그 로컬 픽스처를 "이제 공유 걸로 대체됐겠지" 하고
# 지우면 수십 곳의 `enrollment_client.post(...)` 호출이 조용히 함수 객체로 재바인딩된다.
# 이름을 아예 다르게 둬서 그 함정 자체를 없앤다.
_ID_CAPTURE_ACTIVE_STATUSES = {
    "id_capture_pending", "identity_pending", "photos_pending", "review_pending",
    "liveness_pending", "processing", "asset_building", "license_pending", "vc_pending",
}


@pytest.fixture()
def enrollment_client_factory(keypair, monkeypatch, make_token):
    """생체등록 라우트 통합 테스트용 팩토리 픽스처.

    ``enrollment_client_factory(**settings_overrides) -> (TestClient, EnrollmentStore, Settings)``.
    반환된 client 는 기본 Authorization 헤더(sub="user-1")를 이미 갖고 있어 개별 테스트가
    매번 auth 헤더를 넘길 필요가 없다.
    """

    def _make(**settings_overrides):
        # 지연 임포트: test_facemarket_biometric_enrollment.py 가 모듈 최상단에서
        # `from conftest import make_settings` 를 한다. 이 임포트를 conftest 모듈
        # 최상단에 두면, conftest 가 자기 자신을 다 로드하기 전에 그 파일을 당겨오면서
        # 순환 임포트로 죽는다. 픽스처가 실제로 호출되는 시점(수집 완료 후)까지 미룬다.
        import test_facemarket_biometric_enrollment as biometric_tests

        EnrollmentStore = biometric_tests.EnrollmentStore
        FakeCursor = biometric_tests.FakeCursor
        FakePool = biometric_tests.FakePool
        FakeRekognition = biometric_tests.FakeRekognition
        FakeSts = biometric_tests.FakeSts
        from app import facemarket_enrollment as facemarket_enrollment_module

        private_key, public_key = keypair
        overrides = dict(
            app_env="dev",
            facemarket_enabled=True,
            fm_biometric_enrollment_enabled=True,
            fm_oacx_contract_mode="dev-mock-v1",
            fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/test",
            fm_liveness_confidence_threshold=90.0,
            fm_id_live_threshold=0.45,
            fm_retouched_live_threshold=0.40,
            fm_match_policy_version="dev-gold-v1",
            fm_ci_pepper="pep",
            fm_face_qc_enabled=True,
            opendid_holder_url="http://holder.test",
        )
        overrides.update(settings_overrides)
        settings = make_settings(**overrides)

        store = EnrollmentStore()
        pool = FakePool(store)
        fake_rekognition = FakeRekognition()
        fake_sts = FakeSts()
        fake_r2 = FakeR2()

        monkeypatch.setattr(
            facemarket_enrollment_module,
            "build_biometric_aws_clients",
            lambda _settings: (fake_rekognition, fake_sts),
        )

        @contextlib.asynccontextmanager
        async def fake_get_conn(_request):
            async with pool.connection() as conn:
                yield conn

        monkeypatch.setattr(
            facemarket_enrollment_module, "get_conn", fake_get_conn, raising=False
        )

        # create_enrollment 는 simple_auth 일 때 identity_method/status 를 INSERT 문
        # 텍스트에 리터럴로 박는다(바인드 파라미터 개수를 mid 분기와 똑같이 유지하기
        # 위해서 — test_facemarket_biometric_enrollment.py 의 FakeCursor 는 그 INSERT 를
        # 고정 개수로 언패킹하고 status 를 'identity_pending' 으로 하드코딩해서, 개수가
        # 갈라지면 회귀 스위트 전체가 ValueError 로 죽는다). 여기서만 그 리터럴을 인식해
        # 올바른 상태로 행을 만들고, 그 외 SQL 은 원래 구현에 위임한다.
        # #285 가 동의 버전 두 컬럼(terms/overseas)을 더해 파라미터는 8개다.
        _original_execute = FakeCursor.execute

        async def _patched_execute(self, sql, params=None):
            query = " ".join(sql.split()).lower()
            if (
                query.startswith("insert into fm_biometric_enrollments")
                and "'simple_auth'" in query
                and "'id_capture_pending'" in query
            ):
                (
                    user_id, model_id, device_digest, consent_version, expires_at,
                    application_id, terms_consent_version, overseas_consent_version,
                ) = params
                existing = next(
                    (
                        row
                        for row in self.store.enrollments
                        if row["user_id"] == user_id
                        and row["status"] in _ID_CAPTURE_ACTIVE_STATUSES
                    ),
                    None,
                )
                if existing:
                    self.result = None
                else:
                    row = {
                        "id": str(uuid.uuid4()),
                        "user_id": user_id,
                        "model_id": model_id,
                        "device_digest": device_digest,
                        "consent_version": consent_version,
                        "status": "id_capture_pending",
                        "identity_method": "simple_auth",
                        "review_status": None,
                        "decision": None,
                        "reason": None,
                        "provider_versions": {},
                        "cooldown_until": None,
                        "expires_at": expires_at,
                        "completed_at": None,
                        "raw_deletion_evidence": {},
                        "identity_ci_hash": None,
                        "identity_name_masked": None,
                        "identity_birth_year": None,
                        "identity_tx_digest": None,
                        "identity_contract_version": None,
                        "application_id": application_id,
                        "terms_consent_version": terms_consent_version,
                        "overseas_consent_version": overseas_consent_version,
                        "photo_revision": 0,
                    }
                    self.store.enrollments.append(row)
                    self.result = {"id": row["id"]}
                self.many = []
                return
            await _original_execute(self, sql, params)

        monkeypatch.setattr(FakeCursor, "execute", _patched_execute)

        # _enrollment_db_view 는 EnrollmentView 로 나가는 필드를 만드는 단일 지점이다.
        # 원본은 identity_method/review_status 를 모른다(이 두 컬럼이 생기기 전에 쓰였다) —
        # 감싸서 원시 row 에서 그대로 흘려보낸다. mid 로 만들어진 행(원본 INSERT 분기)은
        # 이 두 키가 아예 없으므로 DB 기본값과 같은 폴백("mid"/None)을 쓴다.
        _original_enrollment_db_view = biometric_tests._enrollment_db_view

        def _patched_enrollment_db_view(row, *, model_gender=None):
            view = _original_enrollment_db_view(row, model_gender=model_gender)
            view["identity_method"] = row.get("identity_method") or "mid"
            view["review_status"] = row.get("review_status")
            return view

        monkeypatch.setattr(
            biometric_tests, "_enrollment_db_view", _patched_enrollment_db_view
        )

        app = create_app(settings)
        app.state.jwt_key_resolver = lambda _token: public_key
        app.state.r2_face = fake_r2
        app.state.pool = pool

        token = make_token()
        client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
        return client, store, settings

    return _make


# ── Task8: 관리자 육안 심사(admin review) 픽스처 ─────────────────────────────────────
# 컨트롤러 룰링: admin_client 는 T8 소관, 여기(conftest.py)에 둔다. enrollment_client_factory
# 와 별개의 lean 한 페이크 DB(AdminStore)를 쓴다 — 정의는
# test_facemarket_admin_review.py 에 있고 여기선 지연 임포트만 한다(순환 임포트 회피,
# enrollment_client_factory 와 같은 패턴).


@pytest.fixture()
def admin_client(keypair, monkeypatch, make_token):
    """관리자 심사 API(Task8) 통합 테스트용 팩토리 픽스처.

    ``admin_client(*, is_admin: bool, **settings_overrides) -> (TestClient, AdminStore)``.
    반환된 client 는 기본 Authorization 헤더를 이미 갖고 있다(sub="admin-1" — 심사 대상
    등록의 소유자는 별개 유저 "enrollee-1" 이 기본값이라 관리자/등록자 신원이 우연히
    섞이지 않는다).
    """

    def _make(*, is_admin: bool, **settings_overrides):
        import test_facemarket_admin_review as admin_tests

        AdminStore = admin_tests.AdminStore
        AdminFakePool = admin_tests.AdminFakePool
        AdminFakeR2 = admin_tests.AdminFakeR2

        from app import facemarket_admin_review as admin_review_module

        private_key, public_key = keypair
        overrides = dict(
            app_env="dev",
            facemarket_enabled=True,
            fm_biometric_enrollment_enabled=True,
            fm_match_policy_version="dev-gold-v1",
            fm_ci_pepper="pep",
            opendid_holder_url="http://holder.test",
            # 리뷰 대상은 항상 fm_liveness_enabled=False 조합이다(Task7) — validate_
            # biometric_settings 가 켜져 있으면 브라우저 role ARN 등 라이브니스 전용
            # 설정을 추가로 요구하므로, 이 API 와 무관한 그 요구를 여기서 끈다.
            fm_liveness_enabled=False,
            fm_face_qc_enabled=True,
            fm_retouched_live_threshold=0.15,
            fm_oacx_contract_mode="dev-mock-v1",
        )
        overrides.update(settings_overrides)
        settings = make_settings(**overrides)

        store = AdminStore()
        admin_user_id = "admin-1"
        if is_admin:
            store.admin_user_ids.add(admin_user_id)
        pool = AdminFakePool(store)

        @contextlib.asynccontextmanager
        async def fake_get_conn(_request):
            async with pool.connection() as conn:
                yield conn

        monkeypatch.setattr(admin_review_module, "get_conn", fake_get_conn, raising=False)

        app = create_app(settings)
        app.state.jwt_key_resolver = lambda _token: public_key
        app.state.r2_face = AdminFakeR2(store)
        app.state.pool = pool

        token = make_token(sub=admin_user_id)
        client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
        return client, store

    return _make


def assert_query_binds(sql, params):
    """가짜 커서가 실제 드라이버처럼 쿼리를 검사하게 한다.

    가짜 커서는 SQL 을 문자열로만 보관해서 psycopg 가 잡아내는 오류를 통째로 못 본다.
    - 리터럴 ``%`` (예: ``like 'a/%'``) 는 params 를 넘긴 순간 psycopg 가 자리표시자로 읽고 죽는다.
      params 가 빈 튜플이어도 ``None`` 이 아니면 검사가 돈다.
    - 자리표시자 개수와 params 개수가 어긋나도 죽는다.
    둘 다 DB 왕복 전에 터져서, 진짜 커서를 안 쓰는 테스트는 통과하고 운영만 500 이 난다.
    """
    from psycopg._queries import PostgresQuery
    from psycopg.adapt import Transformer

    PostgresQuery(Transformer()).convert(sql, params)
