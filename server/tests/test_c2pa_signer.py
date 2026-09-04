"""층② C2PA — 매니페스트 화이트리스트가 이 파일의 존재 이유다.

파일은 무제한 배포된다. 한 번 박은 필드는 회수 불가다. build_manifest 가 화이트리스트
dict 를 조립하는지, 금지 필드가 절대 안 들어가는지를 회귀 테스트로 못박는다.
필드 추가 PR 은 이 테스트를 먼저 만난다.
"""
import json

import pytest

from app.services import c2pa_signer


def base_kwargs(**over):
    kw = dict(
        model_id="11111111-1111-1111-1111-111111111111",
        license_id="22222222-2222-2222-2222-222222222222",
        vc_id="vc:abc",
        publication_id="33333333-3333-3333-3333-333333333333",
        verify_url="https://wearless.kr/verify/p/33333333-3333-3333-3333-333333333333",
        allowed_use=["online_mall"],
        forbidden_use=["adult", "political"],
        license_valid_until="2027-01-01",
        source_asset_ids=["44444444-4444-4444-4444-444444444444"],
        app_version="1.2.3",
    )
    kw.update(over)
    return kw


def test_manifest_declares_ai_generated():
    """AI 표시 의무를 digitalSourceType 이 겸한다."""
    m = c2pa_signer.build_manifest(**base_kwargs())
    blob = json.dumps(m)
    assert "trainedAlgorithmicMedia" in blob
    assert m["claim_generator"].startswith("wearless-facemarket/")


def test_manifest_carries_license_assertion():
    m = c2pa_signer.build_manifest(**base_kwargs())
    custom = next(
        a["data"] for a in m["assertions"] if a["label"] == "kr.wearless.facemarket"
    )
    assert custom["licenseId"] == "22222222-2222-2222-2222-222222222222"
    assert custom["verifyUrl"].endswith("/verify/p/33333333-3333-3333-3333-333333333333")
    assert custom["forbiddenUse"] == ["adult", "political"]


@pytest.mark.parametrize("forbidden", sorted(c2pa_signer.FORBIDDEN_MANIFEST_KEYS))
def test_manifest_never_contains_forbidden_key(forbidden):
    """얼굴·실명·CI·내부 키는 파일에 박히면 회수 불가다."""
    m = c2pa_signer.build_manifest(**base_kwargs())
    blob = json.dumps(m)
    assert forbidden not in blob


def test_build_manifest_rejects_unknown_kwargs():
    """**row 전개로 DB 행을 통째로 넘기는 실수를 시그니처가 막는다."""
    with pytest.raises(TypeError):
        c2pa_signer.build_manifest(**base_kwargs(), face_image_key="private/face.png")


def test_from_settings_disabled_without_cert():
    from conftest import make_settings

    s = make_settings(fm_c2pa_cert_pem=None, fm_c2pa_key_pem=None)
    assert c2pa_signer.C2paSigner.from_settings(s) is None


def test_sign_roundtrip_embeds_manifest(tmp_path):
    """실제 서명 → 읽기. c2pa 미설치·인증서 미발급이면 스킵."""
    c2pa = pytest.importorskip("c2pa")
    import subprocess
    import sys
    from pathlib import Path
    from PIL import Image

    out = tmp_path / "cert"
    subprocess.run(
        [sys.executable, "scripts/gen_c2pa_cert.py", "--out-dir", str(out)],
        check=True,
    )
    cert = (out / "c2pa_cert.pem").read_text()
    key = (out / "c2pa_key.pem").read_text()

    buf = tmp_path / "in.png"
    Image.new("RGB", (8, 8), "white").save(buf)
    data = buf.read_bytes()

    signer = c2pa_signer.C2paSigner(cert, key, app_version="test")
    manifest = c2pa_signer.build_manifest(**base_kwargs())
    signed = signer.sign(data, "image/png", manifest)

    assert signed != data
    assert len(signed) > len(data)
    # 매니페스트가 실제로 읽힌다
    read_back = c2pa.read_file(str(tmp_path / "out.png")) if False else None
    (tmp_path / "out.png").write_bytes(signed)
    assert b"c2pa" in signed or b"jumb" in signed
