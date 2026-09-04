"""배포 매니페스트가 필요한 env 를 실제로 참조하는지 — 문서만 맞고 배포가 틀리는 걸 막는다.

2026-08-29 에 CI 의 SUPABASE_DB_URL 이 앱 DB 와 다른 옛 DB 를 가리켜 마이그레이션이
prod 에 안 붙었다. 그런 종류의 어긋남을 텍스트 수준에서라도 잡는다 — 런북이 설명하는
env 가 실제 copilot 매니페스트에도 배선돼 있는지까지 확인해서, 문서와 배포 설정이
따로 놀아도 아무도 못 알아채는 사고를 막는다.

🔴 Fix round 1(2026-09-04 리뷰): 여기 REQUIRED_ENV 는 더 이상 사람이 손으로 적은
목록이 아니다 — `server/app/config.py` 의 FaceMarket Chain·출처증명 필드 선언 블록에서
**직접 유도**한다. 최초 버전은 사람이 적은 8개 목록이었고, 같은 브랜치의 동시 작업(태스크
4 재검토, 커밋 e307179c, 이 태스크 커밋보다 2분 23초 뒤)이 `FM_PROVENANCE_TOKEN_SECRET`
을 config.py 에 추가했는데 이 파일은 그걸 몰랐다 — 19/19 통과한 채로 정확히 그 필드
하나를 놓쳤다. 코드에서 직접 유도하면 다음에 필드가 또 추가돼도 이 테스트가 즉시
빨간불이 된다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/runbooks/facemarket-provenance-deploy.md"
API_MANIFEST = ROOT / "copilot/api/manifest.yml"
WORKER_MANIFEST = ROOT / "copilot/detail-worker/manifest.yml"
CONFIG_PY = ROOT / "server/app/config.py"

# config.py 의 Settings 필드 선언에서 이 두 주석 사이(경계 포함/제외)가 "FaceMarket Chain
# + 출처증명" 배포 설정 블록이다. 다음 섹션 마커가 시작되기 전까지를 긁는다.
_BLOCK_START = "# ---- FaceMarket Chain (선택과제2, OmniOne Chain Free-Gas BESU) — record-only 정산 ----"
_BLOCK_END = "# ---- OpenDID 홀더(선택과제1) — 커스터디얼 홀더 MSA(로컬 :8100). ----"

# 위 블록 안에 있지만 "반드시 문서화·매니페스트 배선"을 강제하지 않는 필드 — 침묵 누락이
# 아니라 이유를 남긴 명시적 예외다. 지금은 하나뿐이다.
EXCLUDED_FIELDS = {
    # FaceMarketChain.from_settings 가 요구하는 필수 3종은 rpc_url·settlement_address·
    # private_key 뿐이다(server/app/facemarket_chain.py:129-134) — chain_id 는 없으면
    # eth_chainId 로 자동 조회한다(같은 파일 122-123행). 이미 두 매니페스트에 평문
    # "201210"으로 고정 배선돼 있고 런북에도 이미 2회 이상 등장해 실질적으로는 문서화돼
    # 있지만, "없으면 기능이 죽는다" 부류가 아니라서 강제 목록에서는 뺀다.
    "fm_chain_id": "선택값 — from_settings 필수 3종 아님, 없으면 eth_chainId 자동 조회",
}


def _config_src() -> str:
    return CONFIG_PY.read_text(encoding="utf-8")


def _declared_fields() -> list[str]:
    """config.py 필드 선언 블록에서 필드 이름을 코드 그대로 뽑는다(손으로 옮겨 적지 않음)."""
    src = _config_src()
    start = src.index(_BLOCK_START)
    end = src.index(_BLOCK_END, start)
    block = src[start:end]
    names = re.findall(r"^\s+(\w+):\s", block, re.MULTILINE)
    assert names, "필드 블록에서 이름을 하나도 못 뽑았다 — 마커 문자열이 바뀌었는지 확인"
    return names


def _env_name_for_field(field: str) -> str:
    """load_settings() 안에서 그 필드가 실제로 읽는 os.getenv("...") 이름을 찾는다."""
    src = _config_src()
    loader = src[src.index("def load_settings"):]
    m = re.search(
        rf"\b{re.escape(field)}\s*=.*?os\.getenv\(\s*\"([A-Z0-9_]+)\"",
        loader,
        re.DOTALL,
    )
    assert m, f"load_settings 에서 {field} 의 os.getenv(...) 를 못 찾았다 — 추출 정규식을 갱신하라"
    return m.group(1)


def _required_env_from_config() -> list[str]:
    fields = [f for f in _declared_fields() if f not in EXCLUDED_FIELDS]
    return [_env_name_for_field(f) for f in fields]


#: config.py 에서 직접 유도한, "배포 시 문서화·배선을 빠뜨리면 안 되는" env 목록.
REQUIRED_ENV = _required_env_from_config()

# 코드는 "이게 SSM SecureString 인지 평문 variables 인지"를 알려주지 않는다 — 그건 배포
# 관례상의 사람 판단이다. 그래서 이 매핑만은 사람이 정하고, 판단 근거는 런북(§2/§3/부록)에
# 남긴다. REQUIRED_ENV 에 있는 모든 항목이 여기 있어야 한다는 것은 아래 테스트가 잠근다.
SECTION = {
    "FM_CHAIN_RPC_URL": "secrets",
    "FM_SETTLEMENT_ADDRESS": "variables",
    "FM_CHAIN_PRIVATE_KEY": "secrets",
    "FM_PROVENANCE_ENABLED": "variables",
    "FM_C2PA_CERT_PEM": "secrets",
    "FM_C2PA_KEY_PEM": "secrets",
    # 공개 정보(체인 익스플로러에서 누구나 보는 컨트랙트 주소)지만 재배포 시 매니페스트
    # 재커밋 없이 SSM 값만 갱신할 수 있도록 secrets: 로 둔다 — FM_SETTLEMENT_ADDRESS 와
    # 같은 성격의 값이 다르게 배선된 이유. 런북 §2 + 두 매니페스트의 FM_SETTLEMENT_ADDRESS
    # 줄 주석에 같은 설명을 남겼다(리뷰 finding 3).
    "FM_PROVENANCE_ADDRESS": "secrets",
    "FM_PROVENANCE_TOKEN_SECRET": "secrets",
    "PUBLIC_WEB_ORIGIN": "variables",
}

REQUIRED_SECRETS = [e for e in REQUIRED_ENV if SECTION.get(e) == "secrets"]
REQUIRED_VARS = [e for e in REQUIRED_ENV if SECTION.get(e) == "variables"]


def _manifest_doc(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_config_extraction_is_sane():
    """추출 메커니즘 자체가 조용히 깨지지 않았는지 하한선. 필드가 늘면 이 숫자를 올리는
    게 자연스러운 실패이므로, 정확한 이름 나열 대신 개수 하한만 둔다."""
    assert len(REQUIRED_ENV) >= 9
    assert set(REQUIRED_ENV) == set(SECTION), (
        "REQUIRED_ENV(코드에서 유도)와 SECTION(사람이 분류)가 어긋난다 — 새 필드가 추가됐는데 "
        "SECTION 매핑을 안 했거나, SECTION 에 있는데 코드에서 지워진 값이 남아 있다."
    )


def test_runbook_exists():
    assert RUNBOOK.exists()


def test_runbook_lists_every_required_env():
    body = RUNBOOK.read_text(encoding="utf-8")
    missing = [k for k in REQUIRED_ENV if k not in body]
    assert not missing, f"런북에 빠진 env: {missing}"


def test_runbook_documents_each_env_not_just_names_it():
    """이름만 나열하고 끝나면 안 된다 — 명령/설정 예시 한 곳 + 설명 한 곳, 최소 2회 등장을
    '문서화됐다'의 하한선으로 잡는다."""
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
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "untrusted" in body


def test_runbook_warns_about_root_key_handling():
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
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "2026-08-26" in body
    assert "CI" in body


def test_runbook_orders_public_web_origin_verification_before_enabling():
    body = RUNBOOK.read_text(encoding="utf-8")
    origin_idx = body.index("PUBLIC_WEB_ORIGIN")
    enable_idx = body.index("FM_PROVENANCE_ENABLED=true")
    assert origin_idx < enable_idx


def test_runbook_explains_token_secret_is_distinct_from_ci_pepper():
    """FM_PROVENANCE_TOKEN_SECRET 을 fm_ci_pepper 로 '단순화'하고 싶어지는 다음 사람을
    막는다 — 코드가 이미 이걸 명시적으로 거부한다(폐쇄 실패 503), 이유를 런북에도 남긴다."""
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "FM_CI_PEPPER" in body
    assert "503" in body


def test_runbook_documents_facemarket_enabled_dependency():
    """FM_PROVENANCE_ENABLED 는 FACEMARKET_ENABLED 블록 안에서만 라우트·서명기를 켠다
    (server/app/main.py:413-439) — 이 의존을 모르면 두 번째 조용한 no-op 경로가 생긴다."""
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "FACEMARKET_ENABLED" in body


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
    variables = _manifest_doc(manifest).get("variables") or {}
    assert variables.get("FM_PROVENANCE_ENABLED") == "false"


def test_both_manifests_use_the_same_secret_path_template():
    """api 와 detail-worker 가 같은 이미지·같은 설정 로딩 경로를 쓰므로, 새 시크릿의
    SSM 경로 표현이 두 매니페스트에서 갈리면 한쪽만 기동 시 값이 비게 된다."""
    api_secrets = _manifest_doc(API_MANIFEST).get("secrets") or {}
    worker_secrets = _manifest_doc(WORKER_MANIFEST).get("secrets") or {}
    for name in REQUIRED_SECRETS:
        assert api_secrets[name] == worker_secrets[name], name
