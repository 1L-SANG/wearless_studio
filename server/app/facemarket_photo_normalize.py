"""등록 사진의 **원본**과 **정규화본** — 받은 바이트는 그대로 두고, 읽을 것은 따로 만든다.

왜 원본을 그대로 받나: 등록 사진은 곧 LoRA 학습셋이다. 프런트가 긴 변 4000px·JPEG 0.85 로
다시 인코딩해 올리면(셀러 상품 사진용 규칙 toUploadableImage) 48MP 원본이 12MP·손실본이 되어
학습에 들어간다. v7 은 48MP 원본을 무손실 PNG 로 바꿔 학습했다.

왜 정규화본이 따로 필요한가:
  · 아이폰은 HEIC 로 준다. Pillow 는 플러그인 없이 못 읽고, **cv2 는 아예 못 읽는다**
    (얼굴 QC·검출은 cv2 다). 학습 스크립트도 PNG 를 읽는다.
  · JPEG 의 EXIF orientation 을 PIL 은 적용 안 하고 cv2 는 적용한다 — 그대로 두면 검출과
    크롭이 서로 다른 그림을 본다(2026-08 '칼 사태'의 원인).
그래서 서버가 **한 번** EXIF 를 픽셀에 적용한 무손실 PNG 를 만들어 함께 저장하고, 그 뒤의
모든 읽기(QC·관리자 열람·학습 내보내기)는 정규화본을 쓴다.

정규화본의 검증 규칙은 scripts/fm_export_training_set.normalize_png 와 **같다**:
저장 결과를 PIL 과 cv2 로 각각 읽어 크기가 같아야 하고, EXIF orientation 이 남으면 안 된다.
(입력이 HEIC 면 cv2 로는 입력 자체를 못 읽으므로, 대조는 언제나 **산출물 PNG** 로 한다.)

HEIC 디코더는 pi-heif(LGPLv3, libheif+libde265) 다 — pillow-heif 는 x265 인코더를 묶어
휠 전체가 GPLv2 라 쓰지 않는다. 우리는 디코드만 하고 라이브러리를 고치지 않는다.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

#: 정규화본 MIME. 무손실이라 학습이 이 사본의 화질을 그대로 본다.
NORMALIZED_MIME = "image/png"

#: 정규화본 긴 변 기본 상한(설정 FM_NORMALIZED_MAX_EDGE 가 덮어쓴다). 원본은 그대로 보관한다 —
#: 줄이는 것은 읽기용 사본뿐이다.
#: 4096 인 이유: 학습은 얼굴폭×3 크롭을 1024 로 줄여 쓴다. 4096 에서도 얼굴폭이 ≥342px 면
#: 크롭이 1024 를 넘어 업스케일이 안 일어난다(등록 스펙은 얼굴폭 ≈ 가로의 1/4 이라 4096 에서
#: 1024px 로, 여유가 3배다). 그 위는 인코드 시간과 관리자 열람 부담만 늘린다.
DEFAULT_MAX_EDGE = 4096

#: ISO-BMFF ftyp 브랜드. iOS·AirDrop 은 File.type 을 비워 보내기도 해서 **매직바이트**로 본다.
#: 프런트 src/lib/imageTranscode.js 의 HEIC_BRANDS 와 같은 집합이다.
HEIC_BRANDS = frozenset({
    b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs",
    b"mif1", b"msf1", b"heif",
})

_MIME_BY_BRAND = "image/heic"
_HEIF_BRANDS = frozenset({b"mif1", b"msf1", b"heif"})

_heif_registered = False


def _register_heif() -> None:
    """pi-heif 를 Pillow 에 한 번만 등록한다. 없으면 HEIC 만 못 읽고 나머지는 그대로 돈다."""
    global _heif_registered
    if _heif_registered:
        return
    try:
        import pi_heif

        pi_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 — 배포 이미지에 휠이 없을 수도 있다
        logger.warning("pi-heif unavailable — HEIC uploads will be refused", exc_info=True)
    _heif_registered = True


def sniff_image_mime(data: bytes, declared: str | None = None) -> str:
    """바이트로 판정한 MIME. 브라우저가 준 값이 비었거나 일반값이면 여기서 정한다.

    아이폰 사진은 확장자가 .HEIC/.heif/.hif 로 제각각이고 content-type 이 빈 문자열이거나
    application/octet-stream 으로 오는 경우가 흔하다. 확장자를 믿지 않고 ftyp 브랜드를 본다.
    """
    head = data[:16]
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12].lower()
        if brand in HEIC_BRANDS:
            return "image/heif" if brand in _HEIF_BRANDS else _MIME_BY_BRAND
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return (declared or "").lower()


class NormalizeFailed(RuntimeError):
    """정규화본을 만들지 못했다 — 업로드를 받으면 안 된다(읽을 수 없는 사진이 학습셋에 남는다)."""


def _fit(size: tuple[int, int], max_edge: int) -> tuple[int, int]:
    """긴 변을 max_edge 로 맞춘 크기. 이미 작으면 그대로(**절대 키우지 않는다**)."""
    width, height = size
    longest = max(width, height)
    if max_edge <= 0 or longest <= max_edge:
        return size
    scale = max_edge / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def normalize_png(data: bytes, max_edge: int = DEFAULT_MAX_EDGE) -> tuple[bytes, tuple[int, int]]:
    """원본 바이트 → (EXIF 를 픽셀에 적용한 무손실 PNG 바이트, (가로, 세로)).

    긴 변이 max_edge 를 넘으면 LANCZOS 로 줄인다 — **원본은 호출부가 따로 그대로 저장한다.**
    무손실은 "이 사본 안에서 재압축 손실이 없다" 는 뜻이고, 축소는 그와 별개의 의도된 결정이다.

    **호출부는 이 함수를 반드시 asyncio.to_thread 로 돌린다.** 48MP 한 장이 여기서 수 초를 쓴다 —
    이벤트 루프에서 돌리면 그 동안 /healthz 까지 멈춘다(2026-08-26 ALB 장애).
    """
    _register_heif()
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            target = _fit(image.size, max_edge)
            if target != image.size:
                image = image.resize(target, Image.LANCZOS)
            size = image.size
            buffer = io.BytesIO()
            # compress_level=3 — 무손실은 레벨과 무관하다(픽셀은 같다). 실측(48MP 8064×6048,
            # 이 맥): 레벨 6 = 49.1MB/2.20s, 레벨 3 = 51.0MB/1.18s, 레벨 1 = 75.4MB/0.82s.
            # API 태스크가 0.5 vCPU 라 1초가 2MB 보다 비싸다.
            image.save(buffer, "PNG", compress_level=3)
    except Exception as exc:  # noqa: BLE001
        raise NormalizeFailed(f"decode failed: {type(exc).__name__}") from exc
    png = buffer.getvalue()

    # 산출물로 대조한다 — 입력이 HEIC 면 cv2 는 입력 자체를 못 읽는다. 그리고 다운스트림이
    # 실제로 읽는 것도 이 PNG 다.
    with Image.open(io.BytesIO(png)) as check:
        pil_size = check.size
        if check.getexif().get(274) not in (None, 1):
            raise NormalizeFailed("EXIF orientation survived the rewrite")
    decoded = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    cv_size = (decoded.shape[1], decoded.shape[0]) if decoded is not None else None
    if cv_size != pil_size or pil_size != size:
        raise NormalizeFailed(f"size disagreement: PIL {pil_size} vs cv2 {cv_size}")
    return png, pil_size
