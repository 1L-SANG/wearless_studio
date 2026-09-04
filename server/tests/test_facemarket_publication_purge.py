"""철회 = R2 사본 삭제 + revoked_at 표시. **행은 지우지 않는다.**

지우면 그 파일이 '우리가 모르는 파일'이 되어 무단 사용과 정당한 과거 사용을 구별할 수
없게 된다. 파기의 목적(생체정보 제거)은 R2 사본 삭제로 달성된다. image_sha256 은
생체정보가 아니라 파일 지문이다(설계 §9).
"""
import inspect

from app.services import biometric_purge


def test_purge_collects_publication_signed_copies():
    src = inspect.getsource(biometric_purge)
    assert "fm_publication_records" in src, "서명본 사본이 purge 대상에 없다"
    assert "r2_key" in src


def test_purge_never_deletes_publication_rows():
    src = inspect.getsource(biometric_purge)
    assert "delete from fm_publication_records" not in src
    assert "delete from fm_output_records" not in src
    assert "revoked_at" in src
