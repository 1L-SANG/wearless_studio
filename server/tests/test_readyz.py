"""/readyz — "서비스가 실제로 되나" 를 답하는 엔드포인트.

되돌아가면 2026-09-08 이 반복된다: 커넥션 풀이 완전히 죽어 모든 워커와 요청이
PoolTimeout 으로 실패하는 동안에도 /healthz 는 200 을 계속 줬고, 그래서 ECS 롤링이
정상 완료되고 배포 워크플로가 success 로 끝났다. 장애를 알려준 건 Slack 알림뿐이었다.
"""
import contextlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from app.main import create_app
from conftest import make_settings


@pytest.fixture()
def app(keypair):
    _, public_key = keypair
    application = create_app(make_settings())
    application.state.jwt_key_resolver = lambda token: public_key
    return application


class FakeCursor:
    async def execute(self, sql, params=None):
        return None

    async def fetchone(self):
        return {"?column?": 1}


class HealthyPool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            class Conn:
                def cursor(self):
                    @contextlib.asynccontextmanager
                    async def _c():
                        yield FakeCursor()

                    return _c()

            yield Conn()

        return _cm()


class DeadPool:
    """2026-09-08 의 상태 — 풀이 커넥션을 하나도 못 준다."""

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            raise PoolTimeout("couldn't get a connection after 10.00 sec")
            yield  # pragma: no cover

        return _cm()


def test_readyz_ok_when_db_answers(app):
    app.state.pool = HealthyPool()
    res = TestClient(app).get("/readyz")
    assert res.status_code == 200
    assert res.json()["db"] == "ok"


def test_readyz_is_503_when_the_pool_is_dead(app):
    """이 테스트가 이 파일의 존재 이유다 — 배포를 멈출 신호가 여기서 나와야 한다."""
    app.state.pool = DeadPool()
    res = TestClient(app).get("/readyz")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "db_unavailable"


def test_healthz_stays_up_when_the_pool_is_dead(app):
    """liveness 는 의존성을 보지 않는다 — 여기에 DB 를 넣으면 DB 가 흔들릴 때 모든
    태스크가 동시에 unhealthy 가 되어 일시 장애가 전면 장애로 커진다."""
    app.state.pool = DeadPool()
    assert TestClient(app).get("/healthz").status_code == 200


def test_readyz_tolerates_a_dbless_deployment(app):
    """DB 없이 뜨는 구성(JWT 검증 전용)은 고장이 아니다."""
    app.state.pool = None
    res = TestClient(app).get("/readyz")
    assert res.status_code == 200
    assert res.json()["db"] == "not_configured"


def test_alb_healthcheck_still_points_at_healthz():
    """ALB 를 /readyz 로 옮기면 DB 블립이 전면 장애가 된다 — 의도적으로 healthz 다."""
    manifest = (Path(__file__).resolve().parents[2] / "copilot/api/manifest.yml").read_text()
    assert "path: '/healthz'" in manifest
    assert "path: '/readyz'" not in manifest


def test_deploy_gate_checks_readyz():
    """엔드포인트만 만들고 아무도 안 부르면 이번 사고가 그대로 재현된다."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/deploy-server.yml"
    ).read_text()
    assert "/readyz" in workflow, "배포 워크플로가 readyz 를 확인하지 않는다"
