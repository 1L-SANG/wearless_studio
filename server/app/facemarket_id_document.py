"""간편인증 경로의 신분증 촬영본 — 수용·얼굴 크롭·파기.

경로 S(간편인증)에는 OACX 신분증 초상(dlphotoimage)이 없다. 그래서 사용자가
실물 신분증을 찍어 올리고, 그 안의 얼굴이 SFace 앵커 자리를 대신한다.

보관 범위는 **마스킹 전체본**이다(주민번호 뒷자리를 사용자가 가린 상태).
얼굴만 자르지 않는 이유는 관리자가 발급일·이름·사진을 함께 봐야 위조를 판별할
수 있어서다. 마스킹은 클라이언트가 캔버스에 픽셀을 덮어써서 수행하며 서버는
이행 여부를 검증할 수 없다 — 관리자 심사 카드의 확인 항목이 그 자리를 메운다.

파기: 승인·거절 즉시 삭제하고 id_document_purged_at 을 남긴다. 취소·만료
경로와 7일 배치 스윕이 안전망이다.

주의: 이 모듈은 facemarket_enrollment 를 import 하지 않는다(모듈 레벨 순환
금지 — 업로드 라우트는 facemarket_enrollment.py 에 있고 거기서 이 모듈을
import 한다. 반대 방향은 금지).
"""

import asyncio

from .agents.face_qc import load_face_qc  # signature: load_face_qc(settings, *, required=False)

ALLOWED_ID_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_ID_BYTES = 12 * 1024 * 1024
ID_DOCUMENT_TYPES = {"rrc", "dl", "passport", "arc"}
# 얼굴 주변 여백. 신분증 사진은 작아서 타이트하게 자르면 SFace 특징이 뭉개진다.
FACE_CROP_MARGIN = 0.35


class IdDocumentError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _crop_jpeg(data: bytes, box: tuple[int, int, int, int]) -> bytearray:
    """box 주변을 FACE_CROP_MARGIN 만큼 넓혀 잘라 JPEG 로 재인코딩."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise IdDocumentError("id_document_unreadable")
    height, width = image.shape[:2]
    x, y, w, h = box
    mx, my = int(w * FACE_CROP_MARGIN), int(h * FACE_CROP_MARGIN)
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(width, x + w + mx), min(height, y + h + my)
    ok, buf = cv2.imencode(".jpg", image[y0:y1, x0:x1])
    if not ok:
        raise IdDocumentError("id_document_unreadable")
    return bytearray(buf.tobytes())


def crop_id_face(image_bytes: bytes, *, settings) -> bytearray:
    """마스킹 신분증에서 얼굴 영역만 잘라 SFace 앵커로 쓸 바이트를 만든다."""
    qc = load_face_qc(settings, required=True)
    box = qc.detect_largest_face(image_bytes)
    if box is None:
        raise IdDocumentError("id_face_not_detected")
    return _crop_jpeg(image_bytes, box)


async def purge_id_document(r2client, conn, enrollment_id: str) -> None:
    """R2 객체를 지우고 id_document_purged_at 을 남긴다. 두 번 불러도 안전하다."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id_document_r2_key from fm_biometric_enrollments where id = %s",
            (enrollment_id,),
        )
        row = await cur.fetchone()
        key = row["id_document_r2_key"] if row else None
        if key:
            try:
                await asyncio.to_thread(r2client.delete, key)
            except Exception:
                # 객체 삭제 실패는 7일 배치 스윕이 잡는다. 상태는 진실대로 남긴다.
                pass
        await cur.execute(
            "update fm_biometric_enrollments "
            "set id_document_r2_key = null, id_document_purged_at = now() "
            "where id = %s",
            (enrollment_id,),
        )
