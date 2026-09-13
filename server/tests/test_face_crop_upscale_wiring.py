"""얼굴 크롭 확대 배달 경로 — 서버가 묻고, 파드가 답하고, 없으면 조용히 Lanczos.

확대기는 **마감 개선**이지 필수 경로가 아니다. 옛 파드(=/upscale 없음)·가중치 없음·네트워크 실패
어느 쪽이든 컷은 그대로 나와야 한다. 그 계약을 여기서 고정한다.
"""

import base64
import pathlib
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import face_esrgan
import face_render_service as svc
from app.agents import face_identity as fi
from app.agents import face_recipe

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOKEN = "s3cret-internal-0123456789abcdefghij"


def _png(size=(540, 540), color=(180, 140, 120)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(svc, "TOKEN", TOKEN)
    return TestClient(svc.app)


# ── 파드 쪽 ─────────────────────────────────────────────────────────────────
def test_upscale_needs_the_token(client):
    res = client.post("/upscale", json={"image_png": base64.b64encode(_png()).decode(), "scale": 2})
    assert res.status_code == 401


def test_upscale_without_weights_says_503_so_the_caller_falls_back(client, monkeypatch):
    monkeypatch.setattr(face_esrgan, "get", lambda device="cuda": None)
    monkeypatch.setattr(face_esrgan, "status",
                        lambda: {"available": False, "reason": "weights_missing", "weights_sha12": None})
    res = client.post("/upscale", headers={"Authorization": f"Bearer {TOKEN}"},
                      json={"image_png": base64.b64encode(_png()).decode(), "scale": 2})
    assert res.status_code == 503
    assert "weights_missing" in res.json()["detail"]


def test_upscale_returns_the_bigger_png(client, monkeypatch):
    monkeypatch.setattr(face_esrgan, "get",
                        lambda device="cuda": lambda im, k: im.resize((im.width * k, im.height * k), Image.NEAREST))
    res = client.post("/upscale", headers={"Authorization": f"Bearer {TOKEN}"},
                      json={"image_png": base64.b64encode(_png()).decode(), "scale": 2})
    assert res.status_code == 200
    with Image.open(BytesIO(base64.b64decode(res.json()["image_png"]))) as im:
        assert im.size == (1080, 1080)


def test_upscale_refuses_a_whole_photo(client, monkeypatch):
    """호출자는 얼굴 크롭만 보낸다 — 사진 전체가 오면 거절한다(옷 픽셀을 우리가 다시 그리지 않는다)."""
    monkeypatch.setattr(face_esrgan, "get", lambda device="cuda": lambda im, k: im)
    res = client.post("/upscale", headers={"Authorization": f"Bearer {TOKEN}"},
                      json={"image_png": base64.b64encode(_png(size=(1024, 1536))).decode(), "scale": 2})
    assert res.status_code == 400 and "too large" in res.json()["detail"]


def test_healthz_reports_the_upscaler_and_the_recipe(client):
    body = client.get("/healthz").json()
    assert set(body["esrgan"]) == {"available", "reason", "weights_sha12"}
    assert len(body["recipe"]) == 12
    # 확대기가 없는 파드는 다른 레시피다 — 같은 해시로 뭉뚱그리지 않는다
    off = face_recipe.recipe_id(face_recipe.recipe_fields(
        model_id=svc.MODEL_ID, upscale_scope=face_recipe.UPSCALE_SCOPE_OFF))
    on = face_recipe.recipe_id(face_recipe.recipe_fields(
        model_id=svc.MODEL_ID, upscale_scope=face_recipe.UPSCALE_SCOPE_FACE_CROP))
    assert body["recipe"] in (off, on) and off != on


def test_a_broken_recipe_does_not_take_the_pod_down(client, monkeypatch):
    """레시피는 관측용이다 — 못 만들면 비워 두고 렌더는 계속한다."""
    import builtins
    real_import = builtins.__import__

    def no_face_recipe(name, *a, **kw):
        if name.endswith("face_recipe") or name == "app.agents":
            raise ImportError("no cv2")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_face_recipe)
    body = client.get("/healthz").json()
    assert body["ok"] is True and body["recipe"] is None


# ── 서버 쪽(HttpFaceBackend) ────────────────────────────────────────────────
class _Res:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _backend(monkeypatch, responder):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, **kw: responder(url, kw))
    return fi.HttpFaceBackend("https://pod.example/render", lora="k", token="t")


def test_upscale_sends_the_crop_and_returns_the_result(monkeypatch):
    seen = {}

    def responder(url, kw):
        seen["url"] = url
        seen["scale"] = kw["json"]["scale"]
        seen["auth"] = kw["headers"]["Authorization"]
        return _Res(200, {"image_png": base64.b64encode(_png(size=(1080, 1080))).decode()})

    out = _backend(monkeypatch, responder).upscale(Image.new("RGB", (540, 540)), 2)
    assert out.size == (1080, 1080)
    assert seen["url"] == "https://pod.example/upscale" and seen["scale"] == 2
    assert seen["auth"] == "Bearer t"


@pytest.mark.parametrize("status", [404, 405, 501, 503])
def test_an_old_pod_is_asked_only_once(monkeypatch, status):
    """404/503 이면 그 파드는 못 하는 것이다 — 컷마다 다시 묻지 않는다."""
    calls = []
    backend = _backend(monkeypatch, lambda url, kw: calls.append(url) or _Res(status))
    assert backend.upscale(Image.new("RGB", (540, 540)), 2) is None
    assert backend.upscale(Image.new("RGB", (540, 540)), 2) is None
    assert len(calls) == 1


def test_a_network_error_is_absorbed(monkeypatch):
    def boom(url, kw):
        raise OSError("connection reset")

    backend = _backend(monkeypatch, boom)
    assert backend.upscale(Image.new("RGB", (540, 540)), 2) is None
    # 일시적 오류는 지원 여부와 무관하다 — 다음 컷에서 다시 시도한다
    assert backend._upscale_supported is True


def test_the_face_pass_still_produces_a_cut_when_the_pod_cannot_upscale(monkeypatch):
    """확대 실패가 컷을 막지 않는다 — 폴백이 계약이다."""
    class Backend:
        def render(self, control, prompt, seed):
            return control.copy()

        def upscale(self, image, scale):
            raise RuntimeError("no weights")

    from unittest import mock
    plan = fi.plan_from_box(1024, 1536, (360.0, 300.0, 180.0, 240.0), yaw_proxy=0.05, eye_dist=60.0,
                            expression="neutral")
    buf = BytesIO()
    Image.new("RGB", (1024, 1536), (120, 120, 120)).save(buf, "PNG")
    with mock.patch.object(fi, "prepare_image",
                           lambda image, model_dir=None: (image.convert("RGB"), plan,
                                                          {"skipped_reason": None, "pose_risk": False})):
        res = fi.run_face_pass(buf.getvalue(), Backend(), seeds=(42,))
    assert res.meta["crop_upscale"]["method"] == "lanczos"
    assert res.meta["crop_upscale"]["error"] == "RuntimeError"
    assert res.meta["attempts"] == 1          # 렌더까지 갔다


# ── 설정·배달 ───────────────────────────────────────────────────────────────
def test_setting_defaults_on_with_an_escape_hatch(monkeypatch):
    from conftest import make_settings
    assert make_settings(gemini_api_key="x", r2_bucket="b").face_crop_upscale is True
    text = (ROOT / "server/app/config.py").read_text(encoding="utf-8")
    assert 'os.getenv("FACE_CROP_UPSCALE", "true").lower() != "false"' in text


def test_bootstrap_installs_the_weights_and_checks_the_hash():
    text = (ROOT / "server/deploy/face_render/bootstrap.sh").read_text(encoding="utf-8")
    assert face_esrgan.WEIGHTS_SHA256 in text          # 코드와 부트스트랩이 같은 값을 본다
    assert face_esrgan.WEIGHTS_URL in text
    # 받은 걸 검증하고, 틀리면 버린다
    assert 'sha256sum "$ESRGAN_PATH.part"' in text and 'rm -f "$ESRGAN_PATH.part"' in text
    # ★ 실패해도 exit 하지 않는다 — 확대기가 없다고 서비스가 안 뜨면 안 된다
    esr = text.split("얼굴 크롭 확대기 가중치")[1]
    assert "exit 78" not in esr


def test_start_script_points_at_the_bootstrapped_weights():
    text = (ROOT / "server/deploy/face_render/start.sh").read_text(encoding="utf-8")
    assert 'FACE_RENDER_ESRGAN_WEIGHTS="${FACE_RENDER_ESRGAN_WEIGHTS:-$ROOT/weights/RealESRGAN_x4plus.pth}"' in text
    assert "weights}" in text          # mkdir 에 weights 가 들어 있다


def test_the_bundle_carries_the_upscaler_and_the_recipe():
    text = (ROOT / "server/scripts/face_render_bundle.sh").read_text(encoding="utf-8")
    for name in ("face_esrgan.py", "face_recipe.py", "face_identity.py"):
        assert name in text


def test_our_rrdbnet_matches_the_official_checkpoint():
    """아키텍처가 실물 가중치와 맞는가 — torch 없이 .pth 안의 키 이름만 읽어 대조한다.

    basicsr 를 안 쓰고 RRDBNet 을 직접 들고 있으므로(numpy 되돌림·torchvision 패치를 피하려고)
    키가 하나만 어긋나도 파드에서 load_state_dict 가 터지고 그 뒤로는 조용히 Lanczos 로 떨어진다.
    가중치 파일이 있을 때만 돈다(FACE_ESRGAN_WEIGHTS 로 가리킨다).
    """
    import os
    import re
    import zipfile

    path = os.path.expanduser(os.getenv("FACE_ESRGAN_WEIGHTS", "")) or face_esrgan.weights_path()
    if not os.path.exists(path):
        pytest.skip(f"esrgan weights not present ({path})")
    with zipfile.ZipFile(path) as z:
        blob = z.read([n for n in z.namelist() if n.endswith("data.pkl")][0])
    found = {k.decode() for k in re.findall(rb"[A-Za-z_][A-Za-z0-9_.]*\.(?:weight|bias)", blob)}
    assert found == face_esrgan.expected_state_dict_keys()


def test_the_cut_metadata_records_the_recipe():
    text = (ROOT / "server/app/workers/detail_page_job.py").read_text(encoding="utf-8")
    assert '"face_recipe": face_pass_outcome["face_recipe"]' in text


def test_apply_face_pass_stamps_the_recipe_only_on_applied_cuts():
    text = (ROOT / "server/app/agents/face_identity.py").read_text(encoding="utf-8")
    body = text.split("async def apply_face_pass(")[1]
    applied = body.split('_record("applied")')[1].split("else:")[0]
    assert "_record_recipe(result.meta)" in applied
