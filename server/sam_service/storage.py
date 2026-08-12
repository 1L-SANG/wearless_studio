"""Source loading for the SAM2 service: trusted R2 object keys only.

Why keys and not URLs or base64:

  * a URL parameter is an SSRF hole — the service would fetch whatever it is told to, and
    "just validate the host" is the kind of check that rots;
  * base64 in the request body means shipping a 20MB photograph through JSON twice
    (encode + parse) for no benefit, since both sides already share one R2 bucket;
  * `assets.r2_key` is already the identifier the whole codebase uses for a stored image.

So the caller names an object and this service reads it with its own credentials. The key must
look like a key this project writes; anything else is refused before a single byte is fetched.
"""

from __future__ import annotations

import re

#: Object key layouts this project actually produces (see `app/r2.py`). A caller may only name
#: an object under one of these prefixes — the service is not a general-purpose bucket reader.
_KEY_PATTERNS = (
    re.compile(r"^users/[0-9a-fA-F-]{36}/projects/[0-9a-fA-F-]{36}/uploads/[\w.-]+$"),
    re.compile(r"^users/[0-9a-fA-F-]{36}/projects/[0-9a-fA-F-]{36}/ai/[\w./-]+$"),
    re.compile(r"^seed/[\w./-]+$"),
)

SUPPORTED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


class SourceRejected(ValueError):
    """The request named something this service will not read. Maps to 4xx."""


class SourceUnavailable(RuntimeError):
    """The object could not be fetched or was too large. Maps to 502/413."""


def validate_key(key: str) -> str:
    """Return the key, or raise `SourceRejected`.

    Rejects traversal, absolute paths and anything URL-shaped before pattern matching, so a
    value like `https://evil/x` or `../../secrets` never reaches the bucket.
    """
    if not isinstance(key, str) or not key.strip():
        raise SourceRejected("object key is required")
    k = key.strip()
    if len(k) > 512:
        raise SourceRejected("object key is too long")
    if "://" in k or k.startswith("/") or ".." in k or "\\" in k:
        raise SourceRejected("object key must be a plain R2 key, not a URL or path")
    if not any(p.match(k) for p in _KEY_PATTERNS):
        raise SourceRejected("object key is outside the prefixes this service may read")
    return k


class R2Source:
    """Reads source images. Its own boto3 client — no import from `app`."""

    def __init__(self, settings) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = settings.r2_bucket
        self._max_bytes = settings.max_source_bytes
        endpoint = settings.r2_endpoint or (
            f"https://{settings.r2_account_id}.r2.cloudflarestorage.com")
        self._s3 = boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=30,
                          retries={"max_attempts": 3, "mode": "standard"}),
        )

    def head(self, key: str) -> dict | None:
        """{size, checksum} for an existing object, or None. Used for cache hits.

        A cutout whose deterministic key already resolves has already been produced by this
        exact model + algorithm + source, so re-running inference would burn ~25s to write
        identical bytes. JobDispatcher retries make that a real path, not a theoretical one.
        """
        try:
            r = self._s3.head_object(Bucket=self._bucket, Key=key)
        except Exception:                            # noqa: BLE001 - absent or unreadable
            return None
        return {"size": int(r.get("ContentLength") or 0),
                "checksum": (r.get("ETag") or "").strip('"') or None}

    def put(self, key: str, data: bytes, mime: str = "image/png") -> None:
        """Write a derived object. The SAM service owns R2 writes; it never touches the DB."""
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=mime)

    def fetch(self, key: str) -> tuple[bytes, str | None]:
        """(bytes, mime). Size is checked from the HEAD before the body is pulled."""
        key = validate_key(key)
        try:
            head = self._s3.head_object(Bucket=self._bucket, Key=key)
        except Exception as e:                       # noqa: BLE001
            raise SourceUnavailable(f"source object not readable: {key}") from e
        size = int(head.get("ContentLength") or 0)
        if size > self._max_bytes:
            raise SourceUnavailable(
                f"source object is {size} bytes, over the {self._max_bytes} limit")
        mime = (head.get("ContentType") or "").split(";")[0].strip().lower() or None
        if mime and mime not in SUPPORTED_MIME:
            raise SourceRejected(f"unsupported source type: {mime}")
        try:
            body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except Exception as e:                       # noqa: BLE001
            raise SourceUnavailable(f"source object fetch failed: {key}") from e
        if len(body) > self._max_bytes:
            raise SourceUnavailable("source object exceeded the size limit while reading")
        return body, mime
