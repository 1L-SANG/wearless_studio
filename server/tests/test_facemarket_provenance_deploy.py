"""배포 매니페스트가 필요한 env 를 실제로 참조하는지 — 문서만 맞고 배포가 틀리는 걸 막는다.

2026-08-29 에 CI 의 SUPABASE_DB_URL 이 앱 DB 와 다른 옛 DB 를 가리켜 마이그레이션이
prod 에 안 붙었다. 그런 종류의 어긋남을 텍스트 수준에서라도 잡는다 — 런북이 설명하는
env 가 실제 copilot 매니페스트에도 배선돼 있는지까지 확인해서, 문서와 배포 설정이
따로 놀아도 아무도 못 알아채는 사고를 막는다.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/runbooks/facemarket-provenance-deploy.md"
API_MANIFEST = ROOT / "copilot/api/manifest.yml"
WORKER_MANIFEST = ROOT / "copilot/detail-worker/manifest.yml"

REQUIRED_ENV = [
    "FM_PROVENANCE_ENABLED",
    "FM_C2PA_CERT_PEM",
    "FM_C2PA_KEY_PEM",
    "FM_PROVENANCE_ADDRESS",
    "FM_CHAIN_RPC_URL",
    "FM_SETTLEMENT_ADDRESS",
    "FM_CHAIN_PRIVATE_KEY",
    "PUBLIC_WEB_ORIGIN",
]

# SSM SecureString 으로 두 서비스 매니페스트 모두에 배선돼야 하는 값. FM_CHAIN_RPC_URL·
# FM_CHAIN_PRIVATE_KEY 는 이미 배선돼 있었다(2026-07-20, 커밋 bd70d267) — 이 테스트는 그게
# 계속 참이라는 것과, 새로 추가하는 셋(C2PA 인증서 두 장 + 앵커 컨트랙트 주소)도 같은
# 자리에 들어갔다는 것을 함께 잠근다.
REQUIRED_SECRETS = [
    "FM_C2PA_CERT_PEM",
    "FM_C2PA_KEY_PEM",
    "FM_PROVENANCE_ADDRESS",
    "FM_CHAIN_RPC_URL",
    "FM_CHAIN_PRIVATE_KEY",
]

# 평문 variables: 로 배선돼야 하는 값 — 공개 컨트랙트 주소·플래그·검증 링크 출처.
REQUIRED_VARS = [
    "FM_PROVENANCE_ENABLED",
    "PUBLIC_WEB_ORIGIN",
    "FM_SETTLEMENT_ADDRESS",
]


def _manifest_doc(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_runbook_exists():
    assert RUNBOOK.exists()


def test_runbook_lists_every_required_env():
    body = RUNBOOK.read_text(encoding="utf-8")
    missing = [k for k in REQUIRED_ENV if k not in body]
    assert not missing, f"런북에 빠진 env: {missing}"


def test_runbook_documents_each_env_not_just_names_it():
    """이름만 나열하고 끝나면 안 된다 — 문서만 맞고 배포가 틀리는 사고는 '어디 있는지는
    아는데 왜인지는 모른다'에서 시작한다. 명령/설정 예시 한 곳 + 설명 한 곳, 최소 2회
    등장을 '문서화됐다'의 하한선으로 잡는다."""
    body = RUNBOOK.read_text(encoding="utf-8")
    thin = [k for k in REQUIRED_ENV if body.count(k) < 2]
    assert not thin, f"런북에 이름만 있고 설명이 부실한 env: {thin}"


def test_runbook_warns_about_r2_cors():
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "CORS" in body, "브라우저 presigned PUT 은 R2 버킷 CORS 가 없으면 실패한다"


def test_runbook_flags_chain_silent_no_op_risk():
    """체인 3종 미배선이 조용한 no-op 이 된다는 사실이 실제 함수명과 함께 눈에 띄어야 한다."""
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "from_settings" in body
    assert "no-op" in body


def test_runbook_warns_about_self_signed_untrusted_verifier_result():
    """자체서명 인증서라 검증기가 untrusted 를 보고하는 것은 정상 — 모르면 배포가
    실패했다고 오판한다."""
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "untrusted" in body


def test_runbook_warns_about_root_key_handling():
    """gen_c2pa_cert.py 는 리프와 함께 루트키(c2pa_root_key.pem)도 만든다. 그 처리(오프라인
    보관 후 폐기 가능)를 적지 않으면 운영자가 개인키 두 장을 아무 데나 남겨둔다."""
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "root" in body.lower()
    assert "0600" in body


def test_runbook_documents_migration_db_target_mismatch_risk():
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "ftjxwxuactfjopbokbni" in body
    assert "2026-08-29" in body


def test_runbook_documents_rollback_keeps_existing_records():
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "FM_PROVENANCE_ENABLED=false" in body or 'FM_PROVENANCE_ENABLED: "false"' in body


def test_runbook_names_the_deploy_tool_and_target():
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "copilot-aws" in body
    assert "us-east-1" in body
    assert "use1" in body


def test_runbook_forbids_local_deploy():
    """2026-08-26 사고(로컬 copilot 배포가 .env 평문·4.7GB 실험 디렉터리를 이미지에 실었다)를
    이름으로 박아, '왜 CI 로만' 인지가 규칙과 함께 남게 한다."""
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "2026-08-26" in body
    assert "CI" in body


def test_runbook_orders_public_web_origin_verification_before_enabling():
    """verifyUrl 은 이미 배포된 파일 안에 박혀 회수 불가 — '먼저 확인'이 순서상 먼저
    적혀 있어야 한다(FM_PROVENANCE_ENABLED 를 켜는 지점보다 앞)."""
    body = RUNBOOK.read_text(encoding="utf-8")
    origin_idx = body.index("PUBLIC_WEB_ORIGIN")
    enable_idx = body.index("FM_PROVENANCE_ENABLED=true")
    assert origin_idx < enable_idx


@pytest.mark.parametrize("manifest", [API_MANIFEST, WORKER_MANIFEST], ids=["api", "detail-worker"])
def test_manifest_references_every_required_secret(manifest):
    secrets = _manifest_doc(manifest).get("secrets") or {}
    missing = [k for k in REQUIRED_SECRETS if k not in secrets]
    assert not missing, f"{manifest}: secrets 블록에 빠짐: {missing}"


@pytest.mark.parametrize("manifest", [API_MANIFEST, WORKER_MANIFEST], ids=["api", "detail-worker"])
def test_manifest_declares_every_required_variable(manifest):
    variables = _manifest_doc(manifest).get("variables") or {}
    missing = [k for k in REQUIRED_VARS if k not in variables]
    assert not missing, f"{manifest}: variables 블록에 빠짐: {missing}"


@pytest.mark.parametrize("manifest", [API_MANIFEST, WORKER_MANIFEST], ids=["api", "detail-worker"])
def test_provenance_disabled_by_default_in_manifest(manifest):
    """FM_PROVENANCE_ENABLED 는 배선 검증 전까지 반드시 false 로 배포된다 — 런북 순서(§6)를
    건너뛰고 커밋만으로 켜지는 사고를 막는다."""
    variables = _manifest_doc(manifest).get("variables") or {}
    assert variables.get("FM_PROVENANCE_ENABLED") == "false"


def test_both_manifests_use_the_same_secret_path_template():
    """api 와 detail-worker 가 같은 이미지·같은 설정 로딩 경로를 쓰므로, 새 시크릿의
    SSM 경로 표현이 두 매니페스트에서 갈리면 한쪽만 기동 시 값이 비게 된다."""
    api_secrets = _manifest_doc(API_MANIFEST).get("secrets") or {}
    worker_secrets = _manifest_doc(WORKER_MANIFEST).get("secrets") or {}
    for name in REQUIRED_SECRETS:
        assert api_secrets[name] == worker_secrets[name], name
