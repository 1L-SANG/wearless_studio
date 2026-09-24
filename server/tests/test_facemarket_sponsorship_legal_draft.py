"""협찬 동의의 선택성과 공개 법무 문서의 버전·시행일을 검증해요. DB에는 적용하지 않아요.

2026-09-23 오너 결정으로 협찬 개정(약관 v1.2·처리방침 v1.6·FAQ v1.3·E-2 동의)이 시행됐어요.
요청·배송 기능용 델타(02·05 v3)는 아직 초안이에요.
"""
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


def test_sponsorship_consents_are_optional_and_separate_from_required_enrollment():
    registry = legal_versions.SPONSORSHIP_CONSENTS
    assert set(registry) == TYPES
    assert all(item["required_for_enrollment"] is False for item in registry.values())
    assert legal_versions.SPONSORSHIP_CONSENT_VERSION == "2026-09-sponsorship-v2"
    assert "draft" not in legal_versions.SPONSORSHIP_CONSENT_VERSION
    assert all(item["version"] == legal_versions.SPONSORSHIP_CONSENT_VERSION
               for item in registry.values())
    assert enrollment.OPTIONAL_CONSENT_VERSIONS == {
        key: item["version"] for key, item in registry.items()
    }
    assert enrollment.BIOMETRIC_CONSENT_VERSION == "2026-09-v4"
    assert enrollment.OVERSEAS_NOTICE_VERSION == "2026-09-v4"
    assert enrollment.ACCEPTED_BIOMETRIC_CONSENT_VERSIONS == (
        "2026-09-v1", "2026-09-v2", "2026-09-v3", "2026-09-v4",
    )
    assert not set(enrollment.OPTIONAL_CONSENT_VERSIONS.values()).intersection(
        enrollment.ACCEPTED_CONSENT_VERSIONS
    )
    # 협찬을 seller 가입 필수 문서로 추가하면 기존 회원에게 강제 동의가 생겨요.
    assert set(legal_versions.required_versions()) == {"terms", "privacy"}


def test_effective_sponsorship_documents_match_manifest():
    """시행본(약관·처리방침·FAQ·E-2 동의)은 시행일이 있고 초안 표시가 없어요. 서버가 동의 이력에
    기록하는 버전이 공개된 동의문의 버전과 같아야 증빙이 돼요."""
    manifest = {d["slug"]: d for d in json.loads(
        (ROOT / "public/legal/manifest.json").read_text()
    )}
    for slug, version in legal_versions.SPONSORSHIP_DOCUMENT_VERSIONS.items():
        assert manifest[slug]["version"] == version, slug
        assert manifest[slug]["effectiveDate"] == ("2026-09-23" if slug == "privacy-model" else "2026-09-25"), slug
        assert "status" not in manifest[slug], slug
        text = (ROOT / f"public/legal/{slug}.md").read_text()
        assert "법률 검토 전 초안" not in text and "시행일 미확정" not in text and "검토안" not in text, slug
    assert manifest["sponsorship-consent"]["version"] == legal_versions.SPONSORSHIP_CONSENT_VERSION
    assert manifest["biometric-consent"]["version"] == enrollment.BIOMETRIC_CONSENT_VERSION
    assert manifest["overseas-transfer"]["version"] == enrollment.OVERSEAS_NOTICE_VERSION


def test_request_flow_deltas_stay_draft_without_an_effective_date():
    manifest = {d["slug"]: d for d in json.loads(
        (ROOT / "public/legal/manifest.json").read_text()
    )}
    drafts = legal_versions.DRAFT_SPONSORSHIP_DOCUMENT_VERSIONS
    assert set(drafts) == {"license-agreement-sponsorship-draft", "seller-license-terms-sponsorship-draft"}
    for slug, version in drafts.items():
        assert manifest[slug]["version"] == version
        assert manifest[slug]["status"] == "draft"
        assert manifest[slug]["effectiveDate"] is None
    # 초안이 시행본 슬러그를 덮으면 /terms·/privacy·/answers 에서 효력 있는 문서가 사라져요.
    assert not set(drafts) & {"terms-model", "privacy-model", "answers"}


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
    table.write_text(table.read_text().replace("옷 수령 후 7일 이내", "옷 수령 후 4일 이내"))
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    for slug in ("sponsorship-consent", "terms-model", "answers", "seller-license-terms-sponsorship-draft"):
        path = root / f"public/legal/{slug}.md"
        assert path.is_file(), slug
        text = path.read_text()
        assert "옷 수령 후 4일 이내" in text
        assert "{{sponsorship_" not in text
    draft = (root / "public/legal/seller-license-terms-sponsorship-draft.md").read_text()
    assert "법률 검토 전 초안" in draft and "시행일 미확정" in draft
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


def test_consent_source_version_table_tracks_published_sponsorship_revisions():
    manifest = {d["slug"]: d for d in json.loads(
        (ROOT / "public/legal/manifest.json").read_text()
    )}
    source = (ROOT / "documents/legal/04_facemarket_biometric_consent_forms_v1.md").read_text()
    table = source.split("## 부록. 문서 버전 관리", 1)[1]
    rows = {}
    for line in table.splitlines():
        if line.startswith("| "):
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            rows[cells[0]] = cells[1:]
    assert rows["모델 이용약관"] == [manifest["terms-model"]["version"], manifest["terms-model"]["effectiveDate"]]
    assert rows["초상 라이선스 협찬 델타"][0] == manifest["license-agreement-sponsorship-draft"]["version"]
    assert rows["셀러 조건 협찬 델타"][0] == manifest["seller-license-terms-sponsorship-draft"]["version"]
