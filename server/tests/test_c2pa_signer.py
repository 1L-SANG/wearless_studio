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
    """실제 서명 → 읽기. c2pa 미설치·인증서 미발급이면 스킵.

    바이트가 변했다는 것만으로는 안 된다 — 서명은 성공했는데 라이선스 페이로드가
    빠지거나 뭉개진 매니페스트도 "c2pa"/"jumb" 마커는 그대로 남는다. Reader 로
    다시 읽어 커스텀 assertion 의 실제 필드 값까지 확인한다.
    """
    import io

    c2pa = pytest.importorskip("c2pa")
    import subprocess
    import sys
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

    kwargs = base_kwargs()
    signer = c2pa_signer.C2paSigner(cert, key, app_version="test")
    manifest = c2pa_signer.build_manifest(**kwargs)
    signed = signer.sign(data, "image/png", manifest)

    assert signed != data
    assert len(signed) > len(data)

    with c2pa.Reader("image/png", io.BytesIO(signed)) as reader:
        # 자체발급 리프라 발급자 미확인은 알려진·기대된 한계다(설계 §6.5) — 실패로
        # 취급하지 않는다. 대신 그것만이 유일한 불만인지 확인해서, 진짜 에러가 나면
        # (라이선스 데이터 손상 등) 이 assert 가 잡아내게 한다.
        assert reader.get_validation_state() == "Valid"
        results = reader.get_validation_results()
        failure_codes = {f["code"] for f in results["activeManifest"]["failure"]}
        assert failure_codes == {"signingCredential.untrusted"}

        manifest_json = json.loads(reader.json())
        active = manifest_json["manifests"][manifest_json["active_manifest"]]
        custom = next(
            a["data"] for a in active["assertions"]
            if a["label"] == "kr.wearless.facemarket"
        )
        assert custom["modelId"] == kwargs["model_id"]
        assert custom["licenseId"] == kwargs["license_id"]
        assert custom["verifyUrl"] == kwargs["verify_url"]
        assert custom["forbiddenUse"] == kwargs["forbidden_use"]

        # 서명 파일의 "produced by" 는 claim_generator(레거시 문자열)가 아니라
        # claim_generator_info(리스트)에서만 읽힌다 — c2pa-python 0.37.8 실측
        # (claim_generator 는 조용히 버려지고 top-level 로는 None 으로 읽힌다).
        # 이 assert 를 빼면 우리 이름이 안 실린 채로도 조용히 green 이 나온다.
        generator_names = {g["name"] for g in active["claim_generator_info"]}
        assert "wearless-facemarket" in generator_names
