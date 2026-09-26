"""상세페이지 생성 중 컷 미리보기 — 읽는 순간 권한을 다시 확인한다(2026-09-26, 오너 결정 b).

배경: REAL(FaceMarket 실존 모델) 얼굴 컷은 최종 권한 펜스(성공 종결의 라이선스 재확인) 전까지
출력 주소·서명 주소·원시 키·정리 표식 id 를 이벤트 원장(job_events)에 싣지 않는다(492cbc64).
원장은 영구히 남고, 마감 재확인에서 라이선스가 철회되면 그 출력은 지워져야 하기 때문이다.
그래서 대기 화면은 REAL 컷 자리에 '완성됐어요' 타일만 보였다(#419).

오너가 고른 길(b): 원장에는 여전히 싣지 않는다. 대신 이 라우트가 **요청마다**
  1) 잡이 이 사용자·이 프로젝트의 상세페이지 잡이고 아직 돌고 있는지(pending/running),
  2) 이 잡이 이 블록 자리에 올린, 아직 공개 전인 출력이 있는지(서버 전용 정리 표식),
  3) REAL 잡이면 라이선스가 **지금** 유효한지 — 성공 종결과 같은 검사
     (resolve_model_license + verify_license_local, 네트워크 없음),
를 확인하고, 통과했을 때만 바이트를 API 가 직접 실어 보낸다(Cache-Control: private, no-store).
서명 주소를 만들지 않는다 — 브라우저에 넘길 주소도, 로그에 남을 주소도 없다. 원장·로그에 키를
쓰지 않는다. 비-REAL 잡도 같은 라우트를 쓸 수 있다(3번만 건너뛴다).

잡이 끝나면(done/error/cancelled) 404 다 — 성공이면 완료 병합의 안정 주소가 이기고, 실패면
출력은 지워지는 중이다(삭제 실패로 표식이 남아도 여기서는 내보내지 않는다).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException

from . import facemarket, repo
from .r2 import MIME_EXT

#: 미리보기를 내보낼 수 있는 잡 상태 — 생성 중에만. 끝난 잡은 완료 병합·실패 정리가 맡는다.
_ACTIVE_JOB_STATUSES = frozenset({"pending", "running"})
#: 콘티 블록 id 길이 상한 — 쓰레기 입력은 DB 전에 404.
_MAX_BLOCK_ID_LEN = 200
_MIME_BY_EXT = {ext: mime for mime, ext in MIME_EXT.items()}
_NOT_FOUND = {"code": "not_found", "message": "미리보기를 찾을 수 없어요."}


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=dict(_NOT_FOUND))


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def is_real_job(payload) -> bool:
    """REAL 잡인가 — 보수적으로 판정한다. 라이선스 스냅샷이 있거나, 고른 모델이 실존 모델 id 면
    REAL 로 본다(스냅샷이 빠진 REAL 잡은 아래 검사에서 거절된다)."""
    if not isinstance(payload, dict):
        return False
    return isinstance(payload.get("_facemarket"), dict) or facemarket.is_real_model_id(
        payload.get("modelId"))


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"code": code, "message": message})


async def verify_real_license(app, conn, payload: dict) -> None:
    """REAL 잡의 라이선스를 **지금** 다시 확인한다 — 성공 종결(detail_page_job 의 마감 재확인)과
    같은 두 함수. 철회·만료·정지·등록 변경·자산 불일치면 403(그 사유 코드 그대로)."""
    snapshot = payload.get("_facemarket") if isinstance(payload, dict) else None
    model_id = str((snapshot or {}).get("modelId") or "") if isinstance(snapshot, dict) else ""
    license_id = str((snapshot or {}).get("licenseId") or "") if isinstance(snapshot, dict) else ""
    if not _is_uuid(model_id) or not license_id.strip():
        raise _forbidden("model_unavailable", "사용할 수 없는 모델입니다.")
    license_row = await facemarket.resolve_model_license(conn, model_id, license_id=license_id)
    try:
        facemarket.verify_license_local(
            app,
            license_row,
            model_id=model_id,
            brand_use_category=payload.get("brandUseCategory"),
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        raise _forbidden(
            str(detail.get("code") or "license_unavailable"),
            str(detail.get("message") or "얼굴 라이선스를 확인하지 못했어요."),
        ) from None


async def authorize(app, conn, *, user_id: str, project_id: str, job_id: str,
                    block_id: str) -> str:
    """통과하면 내보낼 R2 키. 아니면 HTTPException(404 없음·남의 것·끝난 잡 / 403 권한 없음).

    DB 는 가벼운 조회 두세 번뿐이다: 잡 1행(id·user), 그 잡의 정리 표식(job_id 인덱스),
    REAL 이면 라이선스 1행."""
    if (
        not _is_uuid(project_id)
        or not _is_uuid(job_id)
        or not block_id
        or len(block_id) > _MAX_BLOCK_ID_LEN
    ):
        raise _not_found()
    job = await repo.get_job(conn, user_id, job_id)
    if (
        job is None
        or str(job.get("project_id")) != str(project_id)
        or job.get("kind") != "detail_page"
        or job.get("status") not in _ACTIVE_JOB_STATUSES
    ):
        raise _not_found()
    key = await repo.get_detail_cut_preview_key(conn, job_id=str(job_id), block_id=block_id)
    # 방어선 하나 더 — 이 사용자·이 프로젝트·이 잡의 AI 출력 키 모양(ai_key)만 내보낸다.
    # 체크포인트(…/ckpt/…)나 다른 경로의 키는 여기서 걸린다.
    prefix = f"users/{user_id}/projects/{project_id}/ai/{job_id}/"
    if not isinstance(key, str) or not key.startswith(prefix) or "/" in key[len(prefix):]:
        raise _not_found()
    payload = job.get("payload") or {}
    if is_real_job(payload):
        await verify_real_license(app, conn, payload)
    return key


def mime_for_key(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return _MIME_BY_EXT.get(ext, "application/octet-stream")


def is_missing_object(exc: BaseException) -> bool:
    """R2 에 객체가 없다(지워지는 중) — 404 로 답한다. 그 밖의 오류는 503."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str((response.get("Error") or {}).get("Code") or "")
    status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404
