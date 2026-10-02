"""로그인 사용자의 분석 호출 한도와 업로드 실제 크기 검증 (2026-10-02 보안 점검 Medium 2건).

공개 분석(/v1/public/analyze)은 비로그인만 IP 한도를 받고 로그인 사용자는 한도가 없었다 —
무료 가입 한 번이면 과금 LLM 을 끝없이 부를 수 있었다. 프로젝트 분석(/analyze)도 같았다.
업로드는 presigned PUT 이 크기를 강제하지 않아, 발급 때 신고한 size 만 25MB 이하면 됐다.
"""

import contextlib

import app.public_routes as public_routes
import app.routes as routes
from app.public_routes import PublicAnalysisRateLimiter
from app.routes import MAX_UPLOAD_BYTES
from conftest import auth_headers, patch_route_db

JPEG = b"\xff\xd8\xffjpeg-bytes"


def _analysis_result():
    return {"result_data": {"clothingType": "top", "suggestedName": "니트"}}


class RecordingLimiter:
    def __init__(self, allow=True):
        self.keys = []
        self._allow = allow

    def allow(self, key):
        self.keys.append(key)
        return self._allow


def _post_public(client, headers=None):
    return client.post(
        "/v1/public/analyze",
        headers=headers or {},
        files=[("images", ("front.jpg", JPEG, "image/jpeg"))],
    )


def test_default_user_limiter_is_generous_but_bounded(client):
    limiter = client.app.state.user_analysis_limiter
    assert isinstance(limiter, PublicAnalysisRateLimiter)
    # 30일 실측 최대가 한 IP 시간당 4회 — 정상 셀러가 닿지 않을 만큼 넉넉하되 무한은 아니다.
    assert limiter.hourly_limit == 20
    assert limiter.daily_limit == 60


def test_public_analyze_limits_logged_in_user_by_user_id(client, make_token, monkeypatch):
    async def fake_analyze(settings, source_images, **kwargs):
        return _analysis_result()

    user_limiter = RecordingLimiter()
    ip_limiter = RecordingLimiter()
    client.app.state.user_analysis_limiter = user_limiter
    client.app.state.public_analysis_limiter = ip_limiter
    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)

    response = _post_public(client, {"Authorization": f"Bearer {make_token(sub='seller-9')}"})

    assert response.status_code == 200, response.text
    assert user_limiter.keys == ["user:seller-9"]
    assert ip_limiter.keys == []


def test_public_analyze_logged_in_user_over_limit_gets_429_before_llm(
    client, make_token, monkeypatch
):
    async def must_not_run(*args, **kwargs):
        raise AssertionError("LLM must not be called once the user is over the limit")

    client.app.state.user_analysis_limiter = RecordingLimiter(allow=False)
    monkeypatch.setattr(public_routes, "analyze_image_bytes", must_not_run)

    response = _post_public(client, {"Authorization": f"Bearer {make_token()}"})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"


def test_user_limiter_failure_is_fail_open(client, make_token, monkeypatch):
    class Broken:
        def allow(self, key):
            raise RuntimeError("boom")

    async def fake_analyze(settings, source_images, **kwargs):
        return _analysis_result()

    client.app.state.user_analysis_limiter = Broken()
    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)

    response = _post_public(client, {"Authorization": f"Bearer {make_token()}"})

    assert response.status_code == 200


def test_project_analyze_over_limit_gets_429_without_creating_job(
    client, make_token, monkeypatch
):
    async def fake_get_project(conn, uid, pid):
        return {"id": pid}

    async def must_not_create(conn, **kw):
        raise AssertionError("no job once the user is over the limit")

    limiter = RecordingLimiter(allow=False)
    client.app.state.user_analysis_limiter = limiter
    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "create_job", must_not_create)
    patch_route_db(monkeypatch, routes)

    res = client.post("/v1/projects/p1/analyze", headers=auth_headers(make_token))

    assert res.status_code == 429
    assert limiter.keys == ["user:user-1"]


def test_project_analyze_shares_the_same_user_budget_as_public_analyze(
    client, make_token, monkeypatch
):
    """두 분석 경로가 같은 한도를 나눠 쓴다 — 경로를 바꿔 가며 한도를 두 배로 쓰지 못한다."""
    async def fake_get_project(conn, uid, pid):
        return {"id": pid}

    async def fake_create_job(conn, **kw):
        return {"id": f"job-{kw['kind']}"}, True

    async def fake_analyze(settings, source_images, **kwargs):
        return _analysis_result()

    client.app.state.user_analysis_limiter = PublicAnalysisRateLimiter(
        hourly_limit=2, daily_limit=10
    )
    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    patch_route_db(monkeypatch, routes)
    headers = auth_headers(make_token)

    assert client.post("/v1/projects/p1/analyze", headers=headers).status_code == 202
    assert _post_public(client, headers).status_code == 200
    assert client.post("/v1/projects/p1/analyze", headers=headers).status_code == 429


class _R2:
    def __init__(self, size):
        self.size = size
        self.deletes = []

    def head(self, key):
        return {"mime": "image/jpeg", "size": self.size}

    def delete(self, key):
        self.deletes.append(key)

    def public_url(self, key):
        return f"https://r2.test/{key}"


def _complete(client, make_token, monkeypatch, size):
    r2 = _R2(size)
    client.app.state.r2 = r2
    created = []

    class _Conn:
        async def commit(self):
            pass

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()

    async def create_asset(conn, **kwargs):
        created.append(kwargs)
        return {"id": kwargs["asset_id"], "mime_type": kwargs["mime"], "byte_size": kwargs["size"]}

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes.repo, "create_asset", create_asset)
    res = client.post(
        "/v1/assets/a1/complete",
        headers=auth_headers(make_token),
        json={"mime": "image/jpeg", "filename": "f.jpg", "projectId": None,
              "purpose": "draft_slot"},
    )
    return res, r2, created


def test_complete_upload_rejects_object_larger_than_declared_limit(
    client, make_token, monkeypatch
):
    res, r2, created = _complete(client, make_token, monkeypatch, MAX_UPLOAD_BYTES + 1)

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "file_too_large"
    assert created == []
    # 한도를 넘긴 객체는 R2 에 남기지 않는다 — 참조 없는 큰 고아가 저장 비용만 먹는다.
    assert len(r2.deletes) == 1


def test_complete_upload_accepts_exactly_25mb(client, make_token, monkeypatch):
    res, r2, created = _complete(client, make_token, monkeypatch, MAX_UPLOAD_BYTES)

    assert res.status_code == 200, res.text
    assert r2.deletes == []
    assert created[0]["size"] == MAX_UPLOAD_BYTES
