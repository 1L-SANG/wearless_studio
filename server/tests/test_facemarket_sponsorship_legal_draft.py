"""협찬의 선택성, 미적용 초안과 공개 검토본을 검증해요. DB에는 적용하지 않아요."""
import json
import pathlib
import shutil
import subprocess

import pytest

from app import facemarket_enrollment as enrollment, legal_versions

ROOT = pathlib.Path(__file__).resolve().parents[2]
TYPES = {
    "sponsorship_participation",
    "sponsorship_profile_collection",
    "sponsorship_shipping_collection",
    "sponsorship_shipping_disclosure",
}


def test_sponsorship_draft_is_separate_from_required_enrollment():
    registry = getattr(legal_versions, "DRAFT_SPONSORSHIP_CONSENTS", {})
    assert set(registry) == TYPES
    assert all(item["required_for_enrollment"] is False for item in registry.values())
    assert all(item["version"] == legal_versions.DRAFT_SPONSORSHIP_CONSENT_VERSION
               for item in registry.values())
    assert enrollment.DRAFT_OPTIONAL_CONSENT_VERSIONS == {
        key: item["version"] for key, item in registry.items()
    }
    assert enrollment.BIOMETRIC_CONSENT_VERSION == "2026-09-v4"
    assert enrollment.OVERSEAS_NOTICE_VERSION == "2026-09-v4"
    assert enrollment.ACCEPTED_BIOMETRIC_CONSENT_VERSIONS == (
        "2026-09-v1", "2026-09-v2", "2026-09-v3", "2026-09-v4",
    )
    assert not set(enrollment.DRAFT_OPTIONAL_CONSENT_VERSIONS.values()).intersection(
        enrollment.ACCEPTED_CONSENT_VERSIONS
    )
    # 협찬을 seller 가입 필수 문서로 추가하면 기존 회원에게 강제 동의가 생겨요.
    assert set(legal_versions.required_versions()) == {"terms", "privacy"}


def test_draft_versions_match_review_manifest_without_an_effective_date():
    manifest = {d["slug"]: d for d in json.loads(
        (ROOT / "public/legal/manifest.json").read_text()
    )}
    drafts = getattr(legal_versions, "DRAFT_SPONSORSHIP_DOCUMENT_VERSIONS", {})
    assert len(drafts) == 6
    for slug, version in drafts.items():
        assert manifest[slug]["version"] == version
        assert manifest[slug]["status"] == "draft"
        assert manifest[slug]["effectiveDate"] is None
    assert manifest["biometric-consent"]["version"] == enrollment.BIOMETRIC_CONSENT_VERSION
    assert manifest["overseas-transfer"]["version"] == enrollment.OVERSEAS_NOTICE_VERSION


def test_migration_is_non_executable_draft_with_separate_evidence():
    path = ROOT / "supabase/migration_drafts/20260922120000_facemarket_sponsorship_consents.sql.draft"
    assert path.is_file()
    assert not list((ROOT / "supabase/migrations").glob("*sponsorship*consents*"))
    sql = path.read_text()
    for consent_type in TYPES:
        assert f"'{consent_type}'" in sql
    for field in ("user_id", "occurred_at", "doc_version", "ip_address", "screen_id",
                  "request_id", "recipient_id", "notice_snapshot", "document_sha256",
                  "idempotency_key", "reason", "actor_user_id"):
        assert field in sql
    assert "enable row level security" in sql.lower()
    assert "on delete cascade" not in sql.lower()
    assert "'granted', 'withdrawn'" in sql
    assert "sponsorship_profile_collection'" in sql
    # 프로필 컬럼 추가와 기존 동의 백필은 화면 트랙의 소유예요.
    assert "alter table public.fm_models" not in sql.lower()
    assert "insert into" not in sql.lower()


@pytest.fixture
def publisher(tmp_path):
    shutil.copytree(ROOT / "documents/legal", tmp_path / "documents/legal")
    (tmp_path / "tools").mkdir()
    shutil.copy(ROOT / "tools/legal_publish.py", tmp_path / "tools/legal_publish.py")
    (tmp_path / "src/lib").mkdir(parents=True)
    shutil.copy(ROOT / "src/lib/companyInfo.json", tmp_path / "src/lib/companyInfo.json")

    def run():
        return subprocess.run(
            ["python3", str(tmp_path / "tools/legal_publish.py")],
            capture_output=True, text=True, check=False,
        )
    return tmp_path, run


def test_publisher_uses_conditions_and_preserves_draft_warning(publisher):
    root, run = publisher
    table = root / "documents/legal/00_facemarket_legal_notice_map_v1.md"
    table.write_text(table.read_text().replace("옷 수령 후 3일 이내", "옷 수령 후 4일 이내"))
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    for slug in ("sponsorship-consent", "terms-model", "answers", "seller-license-terms-sponsorship-draft"):
        path = root / f"public/legal/{slug}.md"
        assert path.is_file(), slug
        text = path.read_text()
        assert "옷 수령 후 4일 이내" in text
        assert "{{sponsorship_" not in text
        assert "법률 검토 전 초안" in text
        assert "시행일 미확정" in text
    # 검토되지 않은 v2 사용처 확장을 발행기가 자동 합본하지 않아요.
    agreement = (root / "public/legal/license-agreement.md").read_text()
    assert "사용처는 스튜디오·무지 배경" in agreement
    assert "장소·스타일링 배경과 거울 컷" not in agreement


def test_publisher_rejects_an_unresolved_condition(publisher):
    root, run = publisher
    path = root / "documents/legal/01_facemarket_terms_of_service_model_v1.md"
    path.write_text(path.read_text().replace(
        "## 제1조 (목적)", "## 제1조 (목적)\n\n{{sponsorship_missing}}",
    ))
    result = run()
    assert result.returncode != 0
    assert "sponsorship_missing" in result.stdout + result.stderr


def test_shipping_consent_is_not_granted_to_unknown_sellers(publisher):
    root, run = publisher
    run()
    path = root / "public/legal/sponsorship-consent.md"
    assert path.is_file()
    text = path.read_text()
    assert "실제 셀러 상호와 사업자 식별정보" in text
    assert "미표시 상태에서는 동의를 받지 않아요" in text
    assert "E-2d-1" in text and "E-2d-2" in text
    assert "건별 협찬 수락 버튼이 아니에요" in text
