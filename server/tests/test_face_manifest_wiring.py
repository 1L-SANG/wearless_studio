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
    variables = manifest["variables"]
    assert variables["FACE_IDENTITY_ENABLED"] == "false", name
    url = variables["FACE_IDENTITY_BACKEND_URL"]
    # 파드 id 기반 프록시 주소여야 한다 — TCP 포트는 재시작마다 바뀌지만 이 주소는 그대로다
    # (2026-09-10 실측: 22 번 매핑이 17500 → 17667 로 바뀌었다).
    assert url.startswith("https://") and ".proxy.runpod.net/render" in url, name


def test_only_api_drives_the_pod():
    """기동/종료 reconciler 는 한 곳만 — 두 서비스가 같은 파드를 밀고 당기면 안 된다."""
    assert API["variables"]["FACE_AUTOSCALE"] == "off"
    assert API["variables"]["FACE_RUNPOD_POD_ID"]
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
