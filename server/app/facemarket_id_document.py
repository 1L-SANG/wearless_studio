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
import logging

from .agents.face_qc import QcFailed, load_face_qc  # signature: load_face_qc(settings, *, required=False)

logger = logging.getLogger(__name__)

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
    """box 주변을 FACE_CROP_MARGIN 만큼 넓혀 잘라 JPEG 로 재인코딩.

    검출 박스가 프레임 가장자리 밖이면(클램프 후 폭·높이가 0 이하) 잘라낼 영역이 없다.
    numpy 슬라이스 자체는 조용히 빈 배열을 주지만 cv2.imencode 는 빈 이미지에 ok=False 가
    아니라 cv2.error 를 던진다(리뷰 finding) — 그 예외가 IdDocumentError/QcFailed 어느 쪽도
    아니라서 라우트의 재촬영 처리를 건너뛰고 그대로 500 으로 샌다. 클램프 직후 명시적으로
    막고, cv2.error 자체도 한 번 더 감싼다.
    """
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
    if x1 <= x0 or y1 <= y0:
        raise IdDocumentError("id_document_unreadable")
    try:
        ok, buf = cv2.imencode(".jpg", image[y0:y1, x0:x1])
    except cv2.error:
        raise IdDocumentError("id_document_unreadable")
    if not ok:
        raise IdDocumentError("id_document_unreadable")
    return bytearray(buf.tobytes())


def crop_id_face(image_bytes: bytes, *, settings) -> bytearray:
    """마스킹 신분증에서 얼굴 영역만 잘라 SFace 앵커로 쓸 바이트를 만든다."""
    qc = load_face_qc(settings, required=True)
    try:
        box = qc.detect_largest_face(image_bytes)
    except QcFailed as exc:
        # decode_failed 는 (스푸핑된 content-type 등으로) 이미지 자체가 깨진 것 —
        # QC 인프라 장애(qc_unavailable)와 달리 사용자 재촬영으로 해결되는 4xx 다.
        # 여기서 IdDocumentError 로 바꿔 두면 라우트는 항상 재촬영 안내로 응답하고,
        # 진짜 qc_unavailable(설정·가중치 문제)만 503 으로 남는다(리뷰 finding).
        if exc.reason == "decode_failed":
            raise IdDocumentError("id_document_unreadable") from exc
        raise
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
                # 객체 삭제 실패는 7일 배치 스윕이 잡는다. 상태는 진실대로 남긴다 —
                # 다만 조용히 삼키면(리뷰 finding) 호출부(admin approve/reject)가 파기
                # 실패를 볼 방법이 없다. 키·바이트는 절대 남기지 않는다(원시 PII 미저장).
                logger.warning(
                    "id_document_r2_delete_failed enrollment=%s", enrollment_id, exc_info=True
                )
        await cur.execute(
            "update fm_biometric_enrollments "
            "set id_document_r2_key = null, id_document_purged_at = now() "
            "where id = %s",
            (enrollment_id,),
        )


def sweep_stale_id_documents(r2client, *, older_than_seconds: int = 7 * 86400) -> int:
    """승인·거절·취소·만료 경로가 전부 실패해도 7일이 상한이 되게 하는 마지막 방어선.

    DB 를 보지 않고 **R2 prefix 를 직접 훑는다** — Task4 의 버전화(uuid4 접미사)로 실패한
    시도마다 자기 객체를 남기고, DB 행이 지워졌거나(purge_id_document 가 delete 실패를
    삼키고도 컬럼은 null 로 남기는 경우, Task8 리뷰에서 받아들인 트레이드오프) 애초에 커밋되지
    않은 고아 객체는 DB 조인으로는 찾을 수 없다. 그래서 이 스윕만이 그 객체들의 유일한 회수
    경로다 — DB 구동 스윕으로 바꾸면 이 함수의 존재 이유가 없어진다.

    사진(quarantine/)은 건드리지 않는다 — 자산 빌드 전 단계에서 정상적으로
    오래 남아 있을 수 있고, 그건 _drain_photo_cleanup 의 책임이다. prefix 만으로는
    quarantine/ 과 iddoc/ 을 가르지 못하므로("facemarket/enrollments/" 아래 둘 다 있다)
    반드시 키 안에 "/iddoc/" 이 있는지 명시적으로 걸러야 한다 — 이 필터가 빠지면 사진까지
    지워지는, 이 스윕에서 가능한 최악의 회귀다.
    """
    removed = 0
    for key in r2client.list_prefix_aged(
        "facemarket/enrollments/", older_than_seconds=older_than_seconds
    ):
        if "/iddoc/" not in key:
            continue
        try:
            r2client.delete(key)
            removed += 1
        except Exception:
            # 키만 남기고 트레이스백은 남긴다(원시 PII 미저장 규율은 바이트·내용에 대한
            # 것이지 예외 스택에 대한 것이 아니다) — 권한 만료·네트워크 장애 등 프로덕션
            # 삭제 실패를 진단하려면 exc_info 가 필요하다(review fix round1).
            logger.warning("id_document_sweep_delete_failed key=%s", key, exc_info=True)
    return removed
