"""SAM2 서비스 HTTP 계약.

여기서 진짜 SAM2 추론은 한 번도 돌리지 않는다 — 모델 로드 6초 + 뷰당 90초 이상이라
유닛 스위트에서 반복할 물건이 아니다. 대신 세그멘터를 가짜로 주입해서 계약만 잠근다:

  * 인증이 없거나 틀리면 이미지 근처에도 못 간다
  * Front 와 Back 은 독립이다 — 한쪽 실패가 다른 쪽 성공을 버리지 않는다
  * 모델은 프로세스당 한 번만 로드된다(뷰마다 재로드 금지)
  * 임의 URL/경로는 버킷에 닿기 전에 거절된다

실제 마스크 품질은 test_sam_service_segmentation.py 가 순수 로직으로 검증한다.
"""
import base64
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sam_service import model as model_registry
from sam_service.api import create_app
from sam_service.config import SamSettings
from sam_service.segmentation import Cutout, SegmentationUnavailable
from sam_service.storage import SourceRejected, SourceUnavailable, validate_key

TOKEN = "test-internal-token"
KEY_FRONT = "users/11111111-1111-1111-1111-111111111111/projects/22222222-2222-2222-2222-222222222222/uploads/front.jpg"
KEY_BACK = KEY_FRONT.replace("front.jpg", "back.jpg")


def _settings(**kw) -> SamSettings:
    base = {"internal_token": TOKEN, "r2_account_id": "acct", "r2_access_key_id": "k",
            "r2_secret_access_key": "s", "r2_bucket": "b", "r2_endpoint": None,
            "model_id": ""}
    return SamSettings(**{**base, **kw})


class FakeSource:
    """R2 stand-in. Records reads and writes so cache behaviour is observable."""

    def __init__(self, behaviour: dict, existing: dict | None = None):
        self.behaviour = behaviour
        self.existing = dict(existing or {})      # key -> {size, checksum}
        self.fetched: list[str] = []
        self.written: list[str] = []

    def fetch(self, key: str):
        self.fetched.append(key)
        outcome = self.behaviour.get(key, b"bytes")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, "image/jpeg"

    def head(self, key: str):
        return self.existing.get(key)

    def put(self, key: str, data: bytes, mime: str = "image/png"):
        self.written.append(key)
        self.existing[key] = {"size": len(data), "checksum": "etag"}


class FakeSegmenter:
    """Counts constructions so 'loaded once' is observable, not assumed."""

    instances = 0

    def __init__(self, *_a, **_kw):
        type(self).instances += 1
        self.calls: list[str] = []

    def cutout(self, data: bytes, *, view: str) -> Cutout:
        self.calls.append(view)
        if data == b"bad-image":
            raise SegmentationUnavailable("source image failed to decode")
        png = _png_bytes()
        return Cutout(view=view, png=png, width=4, height=4,
                      source_sha256=f"hash-{view.lower()}",
                      model_version="fake@v1", area_frac=0.42)


def _png_bytes() -> bytes:
    rgba = np.zeros((4, 4, 4), np.uint8)
    rgba[1:3, 1:3] = (200, 150, 40, 255)          # opaque garment
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    model_registry.reset_for_tests()
    FakeSegmenter.instances = 0
    monkeypatch.setattr(model_registry, "Sam2Segmenter", FakeSegmenter)
    yield
    model_registry.reset_for_tests()


def _client(behaviour=None, settings=None, existing=None) -> tuple[TestClient, FakeSource]:
    src = FakeSource(behaviour or {}, existing)
    app = create_app(source_factory=lambda _s: src)
    app.dependency_overrides.update({})
    from sam_service.api import get_settings
    app.dependency_overrides[get_settings] = lambda: settings or _settings()
    return TestClient(app), src


def _post(client, views, token=TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/segment-garment",
                       json={"views": {v: {"key": k} for v, k in views.items()}},
                       headers=headers)


# ── health ───────────────────────────────────────────────────────────────────

def test_health_is_unauthenticated_and_reports_model_state():
    client, _ = _client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["modelLoaded"] is False       # 로드는 첫 요청까지 미룬다


# ── 인증 ─────────────────────────────────────────────────────────────────────

def test_valid_token_is_accepted():
    client, _ = _client()
    assert _post(client, {"Front": KEY_FRONT}).status_code == 200


def test_missing_token_is_rejected_before_any_work():
    client, src = _client()
    r = _post(client, {"Front": KEY_FRONT}, token=None)
    assert r.status_code == 401
    assert src.fetched == []                      # 소스도 안 읽는다


def test_invalid_token_is_rejected():
    client, src = _client()
    r = _post(client, {"Front": KEY_FRONT}, token="wrong-token")
    assert r.status_code == 403
    assert src.fetched == []


def test_unconfigured_secret_fails_closed_rather_than_serving_openly():
    client, _ = _client(settings=_settings(internal_token=None))
    r = _post(client, {"Front": KEY_FRONT}, token=None)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "auth_not_configured"


# ── 정상 경로 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("view,key", [("Front", KEY_FRONT), ("Back", KEY_BACK)])
def test_a_single_view_segments_independently(view, key):
    client, src = _client()
    body = _post(client, {view: key}).json()
    assert body["status"] == "ready"
    assert set(body["views"]) == {view}
    assert body["views"][view]["status"] == "ready"
    assert body["views"][view]["cutoutKey"], "cutout must come back by reference"
    assert "cutoutPngBase64" not in body["views"][view], "base64 transport must be gone"
    assert src.written == [body["views"][view]["cutoutKey"]]


def test_front_and_back_together_report_each_view():
    client, _ = _client()
    body = _post(client, {"Front": KEY_FRONT, "Back": KEY_BACK}).json()
    assert body["status"] == "ready"
    assert [v["status"] for v in body["views"].values()] == ["ready", "ready"]
    assert body["views"]["Front"]["cacheKey"] != body["views"]["Back"]["cacheKey"]
    assert body["views"]["Front"]["cutoutKey"] != body["views"]["Back"]["cutoutKey"]


def test_no_views_is_a_422():
    client, _ = _client()
    r = client.post("/segment-garment", json={"views": {}},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 422


# ── 부분 성공 ────────────────────────────────────────────────────────────────

def test_back_failure_does_not_discard_a_good_front():
    client, _ = _client({KEY_BACK: SourceUnavailable("gone")})
    body = _post(client, {"Front": KEY_FRONT, "Back": KEY_BACK}).json()
    assert body["status"] == "partial"
    assert body["views"]["Front"]["status"] == "ready"
    assert body["views"]["Back"]["status"] == "failed"
    assert body["views"]["Back"]["code"] == "source_unavailable"


def test_front_failure_does_not_discard_a_good_back():
    client, _ = _client({KEY_FRONT: SourceUnavailable("gone")})
    body = _post(client, {"Front": KEY_FRONT, "Back": KEY_BACK}).json()
    assert body["status"] == "partial"
    assert body["views"]["Back"]["status"] == "ready"


def test_both_views_failing_is_failed_not_an_exception():
    client, _ = _client({KEY_FRONT: SourceUnavailable("x"), KEY_BACK: SourceUnavailable("y")})
    r = _post(client, {"Front": KEY_FRONT, "Back": KEY_BACK})
    assert r.status_code == 200                   # 전송은 성공, 내용이 실패다
    assert r.json()["status"] == "failed"


def test_malformed_image_is_a_controlled_view_failure():
    client, _ = _client({KEY_FRONT: b"bad-image"})
    body = _post(client, {"Front": KEY_FRONT}).json()
    assert body["status"] == "failed"
    assert body["views"]["Front"]["code"] == "segmentation_failed"


def test_unsupported_source_type_is_rejected_as_a_view_failure():
    client, _ = _client({KEY_FRONT: SourceRejected("unsupported source type: image/gif")})
    body = _post(client, {"Front": KEY_FRONT}).json()
    assert body["views"]["Front"]["code"] == "source_rejected"


def test_model_unavailable_is_reported_per_view_not_a_500(monkeypatch):
    def boom(*_a, **_kw):
        raise SegmentationUnavailable("torch is not installed")
    monkeypatch.setattr(model_registry, "Sam2Segmenter", boom)
    client, _ = _client()
    r = _post(client, {"Front": KEY_FRONT})
    assert r.status_code == 200
    assert r.json()["views"]["Front"]["code"] == "model_unavailable"


# ── 모델 수명주기 ────────────────────────────────────────────────────────────

def test_model_is_lazy_and_loaded_once_across_views_and_requests():
    client, _ = _client()
    assert FakeSegmenter.instances == 0            # 요청 전에는 로드하지 않는다
    _post(client, {"Front": KEY_FRONT, "Back": KEY_BACK})
    assert FakeSegmenter.instances == 1            # 두 뷰가 같은 모델을 쓴다
    _post(client, {"Front": KEY_FRONT})
    assert FakeSegmenter.instances == 1            # 요청마다 재로드하지 않는다
    assert model_registry.is_loaded()


def test_a_failed_load_is_remembered_instead_of_retried_every_request(monkeypatch):
    calls = {"n": 0}

    def boom(*_a, **_kw):
        calls["n"] += 1
        raise SegmentationUnavailable("weights missing")
    monkeypatch.setattr(model_registry, "Sam2Segmenter", boom)
    client, _ = _client()
    for _ in range(3):
        _post(client, {"Front": KEY_FRONT})
    assert calls["n"] == 1
    assert model_registry.load_failure() == "weights missing"


# ── 컷아웃 내용 ──────────────────────────────────────────────────────────────

def test_the_cutout_is_persisted_to_r2_not_returned_inline():
    """4MB PNG 를 JSON 으로 실어 나르던 임시 경로는 제거됐다 — 이제 키만 돌려준다."""
    client, src = _client()
    view = _post(client, {"Front": KEY_FRONT}).json()["views"]["Front"]
    assert "cutoutPngBase64" not in view
    key = view["cutoutKey"]
    assert key in src.existing and src.written == [key]
    assert view["checksum"] and view["algorithmVersion"] and view["bytes"] > 0
    assert view["cached"] is False


def test_an_existing_cutout_is_reused_without_running_inference():
    """같은 소스·뷰·모델·알고리즘이면 이미 만든 객체가 있다 — 재추론은 25초 낭비다."""
    from sam_service.segmentation import cutout_key, source_fingerprint
    key = cutout_key(source_fingerprint(b"bytes"), "Front")
    client, src = _client(existing={key: {"size": 123, "checksum": "abc"}})
    view = _post(client, {"Front": KEY_FRONT}).json()["views"]["Front"]
    assert view["status"] == "ready" and view["cached"] is True
    assert view["cutoutKey"] == key and view["checksum"] == "abc"
    assert src.written == [], "cache hit must not rewrite the object"
    assert FakeSegmenter.instances == 0, "cache hit must not load or run the model"


def test_a_retry_after_success_does_not_create_a_duplicate_object():
    client, src = _client()
    first = _post(client, {"Front": KEY_FRONT}).json()["views"]["Front"]
    second = _post(client, {"Front": KEY_FRONT}).json()["views"]["Front"]
    assert first["cutoutKey"] == second["cutoutKey"]
    assert len(src.written) == 1 and second["cached"] is True


def test_cutout_keys_separate_source_view_model_and_algorithm():
    from sam_service.segmentation import ALGORITHM_VERSION, MODEL_VERSION, cutout_key
    base = cutout_key("srchash", "Front")
    assert base != cutout_key("other", "Front")
    assert base != cutout_key("srchash", "Back")
    assert base != cutout_key("srchash", "Front", model_version="m2")
    assert base != cutout_key("srchash", "Front", algorithm_version="sam2-grid8-v99")
    assert ALGORITHM_VERSION in base and MODEL_VERSION.replace("/", "_") in base


# ── 입력 안전성 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "https://evil.example/x.jpg",
    "http://169.254.169.254/latest/meta-data/",
    "/etc/passwd",
    "../../secrets/key",
    "s3://bucket/obj",
    "random/prefix/file.jpg",
    "",
])
def test_arbitrary_urls_and_paths_never_reach_the_bucket(bad):
    with pytest.raises(SourceRejected):
        validate_key(bad)


@pytest.mark.parametrize("good", [
    KEY_FRONT,
    "users/11111111-1111-1111-1111-111111111111/projects/22222222-2222-2222-2222-222222222222/ai/job/cut.png",
    "seed/mannequin/base_women.png",
])
def test_project_owned_keys_are_accepted(good):
    assert validate_key(good) == good


def test_an_over_long_key_is_rejected():
    with pytest.raises(SourceRejected):
        validate_key("users/" + "a" * 600)
