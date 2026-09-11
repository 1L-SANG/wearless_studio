"""운영 매니페스트의 얼굴 패스 배선 — **전부 꺼진 채**로 들어가 있는가.

이 파일이 지키는 사고 두 가지:
  · 플래그가 실수로 켜진 채 머지되는 것(얼굴 패스는 법적·요금 영향이 있다).
  · 시크릿 줄만 들어가고 SSM 값이 없어 ECS 기동이 실패하는 것 — 주석으로 절차를 남긴다.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
API = yaml.safe_load((ROOT / "copilot/api/manifest.yml").read_text(encoding="utf-8"))
WORKER = yaml.safe_load((ROOT / "copilot/detail-worker/manifest.yml").read_text(encoding="utf-8"))
SSM_PREFIX = "/copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/"


@pytest.mark.parametrize("manifest,name", [(API, "api"), (WORKER, "detail-worker")])
def test_face_identity_ships_disabled(manifest, name):
    assert manifest["variables"]["FACE_IDENTITY_ENABLED"] == "false", name


@pytest.mark.parametrize("manifest,name", [(API, "api"), (WORKER, "detail-worker")])
def test_pod_address_is_not_pinned_in_the_manifest(manifest, name):
    """★ 파드 id·렌더 URL 의 정본은 DB(fm_face_render_pod)다.

    파드는 재고 때문에 바뀐다(2026-09-10·11 실측: start "not enough free GPUs",
    create "no instances"). 매니페스트에 박아 두면 갈아탄 뒤 죽은 주소를 계속 찌르고,
    그걸 고치려면 매번 재배포해야 한다.
    """
    variables = manifest["variables"]
    assert "FACE_IDENTITY_BACKEND_URL" not in variables, name
    assert "FACE_RUNPOD_POD_ID" not in variables, name
    text = (ROOT / f"copilot/{name}/manifest.yml").read_text(encoding="utf-8")
    assert "fm_face_render_pod" in text, name      # 어디가 정본인지 주석으로 남아 있어야 한다


def test_only_api_drives_the_pod():
    """기동/종료 reconciler 는 한 곳만 — 두 서비스가 같은 파드를 밀고 당기면 안 된다."""
    assert API["variables"]["FACE_AUTOSCALE"] == "off"
    assert "FACE_AUTOSCALE" not in WORKER["variables"]
    assert "RUNPOD_API_KEY" not in WORKER.get("secrets", {})


def test_idle_and_grace_match_the_measured_cold_start():
    variables = API["variables"]
    assert int(variables["FACE_AUTOSCALE_IDLE_MINUTES"]) == 10      # detail-worker 와 같은 근거
    # 콜드스타트 중앙값 × 2, 최소 8. 실측 8.5분(적재 472초 + 부팅) → 17.
    assert int(variables["FACE_AUTOSCALE_START_GRACE_MINUTES"]) >= 8


@pytest.mark.parametrize("manifest,name,keys", [
    (API, "api", ("FACE_IDENTITY_BACKEND_TOKEN", "RUNPOD_API_KEY")),
    (WORKER, "detail-worker", ("FACE_IDENTITY_BACKEND_TOKEN",)),
])
def test_secrets_point_at_ssm_paths(manifest, name, keys):
    secrets = manifest["secrets"]
    for key in keys:
        assert secrets[key] == f"{SSM_PREFIX}{key}", (name, key)


def test_manifest_warns_that_ssm_values_are_required_first():
    text = (ROOT / "copilot/api/manifest.yml").read_text(encoding="utf-8")
    assert "copilot secret init" in text
