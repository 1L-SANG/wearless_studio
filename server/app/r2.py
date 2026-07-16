"""R2 (Cloudflare, S3 호환) 자산 저장 — backend_integration_plan §3.

업로드: 백엔드가 presigned PUT URL 발급(인증·소유권 확인 후) → 브라우저 직접 PUT →
        complete 에서 HEAD 로 실제 업로드 검증. (서버가 바이트 프록시 안 함 §3)
서빙: R2_PUBLIC_BASE(커스텀 도메인) 있으면 공개 URL 직접, 없으면 short-lived signed GET.
키 규칙(§3): users/{userId}/projects/{projectId}/uploads/{assetId}.{ext}

boto3는 동기 클라이언트 — 네트워크 호출(head_object/put_bytes)은 라우트에서
asyncio.to_thread 로 감싸 이벤트 루프를 막지 않는다(§5). presigned URL 생성은
서명만 하는 로컬 연산이라 블로킹이 아니다.
"""

import base64
import hashlib

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


def face_key(model_id: str, license_id: str, ext: str) -> str:
    """FaceMarket 얼굴 라이선스 이미지의 비공개 R2 키. 서버에서만 유도(클라 신뢰 금지).
    게이트 라우트만 스트림 → 공개 URL 미노출. license_id(uuid) = 추측 불가."""
    return f"facemarket/models/{model_id}/licenses/{license_id}/face.{ext}"


def sha256_sri(data: bytes) -> str:
    """SRI 무결성 digest 'sha256-<base64>'. fm_licenses.face_image_digest 포맷."""
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode()


class R2Client:
    """앱 1개당 1개. app.state.r2 에 둔다. settings.r2_* 미설정이면 생성하지 않는다."""

    def __init__(
        self,
        settings: Settings,
        bucket: str | None = None,
        public_base: str | None = "",
    ):
        # bucket/public_base 오버라이드: FaceMarket 얼굴 = 전용 비공개 버킷 + 공개도메인 차단.
        # public_base 기본 "" = 센티널(미지정 → settings 값 사용). None 명시 = 공개도메인 강제 차단.
        self._bucket = bucket or settings.r2_bucket
        self._public_base = settings.r2_public_base if public_base == "" else public_base
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

    def put_bytes(self, key: str, data: bytes, mime: str, cache: str | None = None) -> None:
        """AI 생성 이미지 등 서버사이드 저장 (Gemini/OpenAI 응답 → R2).
        seed/public 불변 자산은 cache='public, max-age=31536000, immutable' 권장."""
        extra = {"CacheControl": cache} if cache else {}
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=mime, **extra)

    def get_bytes(self, key: str) -> bytes:
        """R2 객체 바이트 로드 (베이스 마네킹·상품사진 → Gemini 입력). 동기 → to_thread로 감쌀 것."""
        return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        """객체 삭제 (lease 상실로 버려진 생성물 best-effort 정리). 동기 → to_thread."""
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def list_prefix(self, prefix: str) -> list[str]:
        """prefix 하위 객체 키 전량(1000건 초과 시 자동 페이지네이션). 동기 → to_thread.

        용도: 파기 캐스케이드의 고아 객체 스캔 — DB가 참조하지 않는(= put 후 finalize 전
        크래시로 남은) 객체는 키를 알 수 없어 list 없이는 회수 불가(api-spec §3.5 파기 완전성).
        반환 키는 호출자가 삭제 대상으로만 쓰고 로그·감사로그엔 카운트만 남긴다(§1.4).
        """
        keys: list[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return keys

    def public_url(self, key: str) -> str:
        """서빙 URL. 커스텀 도메인 있으면 공개 URL, 없으면 1h signed GET."""
        if self._public_base:
            return f"{self._public_base}/{key}"
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=3600,
        )
