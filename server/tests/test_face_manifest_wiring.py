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
def test_face_identity_is_on(manifest, name):
    """2026-09-11 켬. 켜는 것만으로는 동작이 안 바뀐다 — 얼굴 패스는 fm_model_loras 의 켜진 행이
    있는 모델에서만 걸린다(test_fm_model_loras / test_lora_profile_wiring 이 그걸 고정한다)."""
    assert manifest["variables"]["FACE_IDENTITY_ENABLED"] == "true", name


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
    """기동/종료 reconciler 는 한 곳만 — 두 서비스가 같은 파드를 밀고 당기면 안 된다.

    값(on/off)은 운영 판단이라 여기서 고정하지 않는다. 2026-09-11 현재는 off —
    수동 파드로 운영 테스트 중이고, 자동 생성 경로를 켜는 건 검증 뒤다.

    ★ 2026-09-21: 이 시험은 "워커에 RUNPOD_API_KEY 가 없을 것"으로도 걸려 있었다. 그때는
      그 키의 용도가 파드 생성·삭제뿐이라 맞는 대리 조건이었다. 서버리스 각도 교체가
      들어오면서 갈렸다 — ServerlessBackend 는 같은 키로 `/v2/<endpoint>/run`·`/status`
      만 부르고 파드 API 는 안 건드린다. 그런데 각도 교체는 **워커에서** 돈다.
      대리 조건을 그대로 두는 바람에 키가 워커에 안 갔고, spec_from 이 조용히 None 을
      돌려줘 뒷모습 컷이 원본 머리로 나갔다(첫 운영 QA).

      그래서 진짜 불변식만 남긴다: **reconciler 를 켜는 플래그가 워커에 없을 것.**
      키 유무가 아니라 그 플래그가 파드를 미는 권한을 정한다(둘 다 기본값 off).
    """
    assert API["variables"]["FACE_AUTOSCALE"] in {"on", "off"}
    assert "FACE_AUTOSCALE" not in WORKER["variables"]
    assert "ANGLE_AUTOSCALE" not in WORKER["variables"]


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


# ── 각도 교체(옆·뒷모습) 배선 ─────────────────────────────────────────────
#
# 2026-09-21 첫 운영 QA 에서 뒷모습 컷이 **실패가 아니라 원본 머리 그대로** 나갔다.
# 원인은 매니페스트였다: FACE_ANGLE_* env 는 api·detail-worker 양쪽에 넣었는데
# RUNPOD_API_KEY 시크릿은 api 에만 있었다. 각도 교체는 **워커에서** 돈다.
#
# 그러면 face_angle_swap.spec_from 이 조용히 None 을 돌려준다 —
# 엔드포인트 id 는 있는데 키가 없어 서버리스로 못 가고, 파드도 없으니 주소가 없다.
# None 이면 워커는 angle_swap 키를 안 넣고, 컷은 아무 표시 없이 원본으로 나간다.
# **조용한 실패라서 로그로도 안 잡힌다** — 그래서 여기서 막는다.


@pytest.mark.parametrize("manifest,name", [(API, "api"), (WORKER, "detail-worker")])
def test_the_angle_swap_endpoint_is_wired_on_both(manifest, name):
    variables = manifest["variables"]
    assert variables.get("FACE_ANGLE_SWAP_ENABLED") == "true", name
    assert str(variables.get("FACE_ANGLE_ENDPOINT_ID") or "").strip(), name


def test_the_worker_carries_the_runpod_key_because_the_swap_runs_there():
    """★ env 만 맞춰 두면 안 된다 — 키가 없으면 spec_from 이 조용히 None 이다."""
    assert WORKER["secrets"].get("RUNPOD_API_KEY") == SSM_PREFIX + "RUNPOD_API_KEY"


def test_the_endpoint_id_matches_between_the_two_services():
    """서로 다른 엔드포인트를 보면 api 의 상태 표시와 워커의 실제 호출이 갈린다."""
    assert (API["variables"]["FACE_ANGLE_ENDPOINT_ID"]
            == WORKER["variables"]["FACE_ANGLE_ENDPOINT_ID"])
