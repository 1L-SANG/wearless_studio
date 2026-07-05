"""D4 입력측 업로드 QC — evaluate_input_qc 단위 + config flag 테스트."""

import io

from PIL import Image

from app.config import load_settings
from app.services.input_qc import MIN_SIDE, evaluate_input_qc, input_qc_message


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 130, 140)).save(buf, format="PNG")
    return buf.getvalue()


def test_normal_image_passes():
    r = evaluate_input_qc(_png(800, 1000))
    assert r.verdict == "pass"
    assert r.reasons == []
    assert r.metrics == {"width": 800, "height": 1000}


def test_corrupt_bytes_decode_failed():
    r = evaluate_input_qc(b"this is not an image")
    assert r.verdict == "reject"
    assert "decode_failed" in r.reasons


def test_tiny_image_too_small():
    r = evaluate_input_qc(_png(200, 300))
    assert r.verdict == "reject"
    assert "too_small" in r.reasons


def test_boundary_min_side_passes():
    # 정확히 MIN_SIDE = 통과(관대 임계, 오탐 회피)
    r = evaluate_input_qc(_png(MIN_SIDE, MIN_SIDE + 50))
    assert r.verdict == "pass"


def test_message_is_korean_and_helpful():
    r = evaluate_input_qc(_png(100, 100))
    msg = input_qc_message(r)
    assert msg
    assert str(MIN_SIDE) in msg  # 최소 크기를 셀러에게 안내


def test_truncated_jpeg_rejected():
    # §A: 헤더는 온전하나 스캔(픽셀) 스트림이 잘린 JPEG — .size는 통과하지만 .load()가 잡는다.
    noise = Image.effect_noise((800, 1000), 80).convert("RGB")
    buf = io.BytesIO()
    noise.save(buf, format="JPEG", quality=90)
    truncated = buf.getvalue()[: len(buf.getvalue()) // 2]
    r = evaluate_input_qc(truncated)
    assert r.verdict == "reject"
    assert "decode_failed" in r.reasons


def test_evaluate_never_raises_on_garbage():
    # fail-open 전제: evaluate는 어떤 입력에도 예외를 던지지 않는다(항상 결과 반환).
    for bad in [b"", b"\x00\x01\x02", b"GIF89a-broken", bytes(range(60))]:
        r = evaluate_input_qc(bad)
        assert r.verdict == "reject"
        assert "decode_failed" in r.reasons


def test_config_flag_fallback_and_valid(monkeypatch):
    monkeypatch.setenv("INPUT_QC", "garbage")
    assert load_settings().input_qc == "off"  # 허용값 밖 → off 폴백
    monkeypatch.setenv("INPUT_QC", "enforce")
    assert load_settings().input_qc == "enforce"
