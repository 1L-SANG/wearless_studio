"""R2 순수 로직 단위테스트 — MIME 화이트리스트·키 규칙 (계약 §3).

네트워크(presigned/head/put)는 자격증명 필요 → 로컬 통합 검증으로 따로 확인.
"""

from app.r2 import ai_key, ext_for_mime, upload_key


def test_ext_for_mime_whitelist():
    assert ext_for_mime("image/png") == "png"
    assert ext_for_mime("image/jpeg") == "jpg"
    assert ext_for_mime("IMAGE/WEBP") == "webp"  # 대소문자 무관
    # 허용 외 타입은 None → 업로드 거부 근거
    assert ext_for_mime("application/pdf") is None
    assert ext_for_mime("") is None
    assert ext_for_mime(None) is None


def test_upload_key_rule():
    key = upload_key("user-1", "proj-9", "asset-7", "png")
    assert key == "users/user-1/projects/proj-9/uploads/asset-7.png"


def test_ai_key_rule():
    key = ai_key("user-1", "proj-9", "job-3", "asset-7", "png")
    assert key == "users/user-1/projects/proj-9/ai/job-3/asset-7.png"
