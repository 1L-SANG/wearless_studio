"""R2 (Cloudflare, S3 호환) 자산 저장 — backend_integration_plan §3.

업로드: 백엔드가 presigned PUT URL 발급(인증·소유권 확인 후) → 브라우저 직접 PUT →
        complete 에서 HEAD 로 실제 업로드 검증. (서버가 바이트 프록시 안 함 §3)
서빙: R2_PUBLIC_BASE(커스텀 도메인) 있으면 공개 URL 직접, 없으면 short-lived signed GET.
키 규칙(§3): users/{userId}/projects/{projectId}/uploads/{assetId}.{ext}

boto3는 동기 클라이언트 — 네트워크 호출(head_object/put_bytes)은 라우트에서
asyncio.to_thread 로 감싸 이벤트 루프를 막지 않는다(§5). presigned URL 생성은
서명만 하는 로컬 연산이라 블로킹이 아니다.
"""

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import Settings

# 업로드 허용 이미지 MIME → 확장자. 화이트리스트(임의 타입 업로드 차단).
MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
}


def ext_for_mime(mime: str | None) -> str | None:
    return MIME_EXT.get((mime or "").lower())


def upload_key(user_id: str, project_id: str, asset_id: str, ext: str) -> str:
    """업로드 자산의 R2 키 (§3). user_id/project_id 경로 = prefix 단위 삭제·소유 경계."""
    return f"users/{user_id}/projects/{project_id}/uploads/{asset_id}.{ext}"


def ai_key(user_id: str, project_id: str, job_id: str, asset_id: str, ext: str) -> str:
    """AI 생성 자산 키 (§3). 서버사이드 저장(put_bytes)용."""
    return f"users/{user_id}/projects/{project_id}/ai/{job_id}/{asset_id}.{ext}"


class R2Client:
    """앱 1개당 1개. app.state.r2 에 둔다. settings.r2_* 미설정이면 생성하지 않는다."""

    def __init__(self, settings: Settings):
        self._bucket = settings.r2_bucket
        self._public_base = settings.r2_public_base
        endpoint = settings.r2_endpoint or (
            f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
        )
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def presigned_put(self, key: str, mime: str, expires: int = 300) -> str:
        """브라우저가 직접 PUT 할 short-lived URL. ContentType 고정(업로드 시 동일 헤더 필요)."""
        return self._s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": mime},
            ExpiresIn=expires,
        )

    def head(self, key: str) -> dict | None:
        """객체 메타({size, mime}) 또는 None(미존재). complete 검증용."""
        try:
            r = self._s3.head_object(Bucket=self._bucket, Key=key)
        except ClientError:
            return None
        return {"size": r["ContentLength"], "mime": r.get("ContentType")}

    def put_bytes(self, key: str, data: bytes, mime: str) -> None:
        """AI 생성 이미지 등 서버사이드 저장 (Gemini/OpenAI 응답 → R2)."""
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=mime)

    def get_bytes(self, key: str) -> bytes:
        """R2 객체 바이트 로드 (베이스 마네킹·상품사진 → Gemini 입력). 동기 → to_thread로 감쌀 것."""
        return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        """객체 삭제 (lease 상실로 버려진 생성물 best-effort 정리). 동기 → to_thread."""
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def public_url(self, key: str) -> str:
        """서빙 URL. 커스텀 도메인 있으면 공개 URL, 없으면 1h signed GET."""
        if self._public_base:
            return f"{self._public_base}/{key}"
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=3600,
        )
