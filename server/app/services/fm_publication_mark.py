"""배포본 추적 표식 — 워터마크 삽입 + 지문 계산을 한 번의 디코드로 끝낸다(2026-09-26).

sign 라우트가 C2PA 서명 **직전에** asyncio.to_thread 로 부른다. 여기서 나온 바이트가 서명·
signed_sha256·앵커의 대상이 된다(= 워터마크가 든 바이트를 전부 덮는다).

🔴 실패 정책: 어떤 실패도 예외로 올리지 않는다. 워터마크를 못 박으면 원본 바이트를 그대로
   돌려주고 wm_status='failed' — 셀러의 다운로드를 막지 않는다(설계 §6.2 와 같은 원칙).
   지문은 워터마크와 독립이다 — 워터마크가 실패해도 계산할 수 있으면 계산한다.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from dataclasses import dataclass, field

from PIL import Image

from . import fm_fingerprint, fm_watermark

logger = logging.getLogger("facemarket.publication_mark")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ZIP_MAX_ENTRIES = 300
_ZIP_MAX_UNCOMPRESSED = 600 * 1024 * 1024   # 선언 크기 합 상한 — 압축 폭탄 방어
_PNG_COMPRESS_LEVEL = 6


@dataclass
class MarkResult:
    data: bytes                        # 내려보낼 바이트(성공이면 워터마크본, 아니면 원본)
    wm_status: str                     # 'embedded' | 'failed' | 'skipped'
    fingerprints: list[dict] = field(default_factory=list)
    embed_ms: int = 0
    error: str | None = None


def _encode_png(img: Image.Image, src_info: dict) -> bytes:
    buf = io.BytesIO()
    kw = {"compress_level": _PNG_COMPRESS_LEVEL}
    if src_info.get("icc_profile"):
        kw["icc_profile"] = src_info["icc_profile"]
    if src_info.get("dpi"):
        kw["dpi"] = src_info["dpi"]
    img.save(buf, "PNG", **kw)
    return buf.getvalue()


def _mark_one(raw: bytes, code: int | None) -> tuple[bytes | None, Image.Image]:
    """PNG 1장 → (워터마크 PNG 바이트 | None(코드 없음), 지문용 정규화 이미지). 실패는 raise."""
    img = fm_fingerprint.open_image(raw)
    if code is None:
        return None, fm_fingerprint.normalize(img)
    marked = fm_watermark.embed_image(img, code)
    out = _encode_png(marked, img.info)
    return out, fm_fingerprint.normalize(marked)


def _fingerprints(normalized: list[Image.Image]) -> list[dict]:
    try:
        return fm_fingerprint.publication_fingerprints(normalized) if normalized else []
    except Exception:
        logger.warning("publication fingerprint failed", exc_info=True)
        return []


def _mark_png(data: bytes, code: int | None) -> MarkResult:
    t0 = time.monotonic()
    try:
        out, norm = _mark_one(data, code)
    except Exception as e:
        logger.warning("publication watermark failed (png)", exc_info=True)
        try:
            norm = fm_fingerprint.normalize(fm_fingerprint.open_image(data))
        except Exception:
            norm = None
        return MarkResult(data, "failed", _fingerprints([norm] if norm else []),
                          error=type(e).__name__)
    elapsed = int((time.monotonic() - t0) * 1000)
    if out is None:   # 코드 발급 실패 — 지문만 남긴다
        return MarkResult(data, "failed", _fingerprints([norm]), elapsed, "no_code")
    return MarkResult(out, "embedded", _fingerprints([norm]), elapsed)


def _mark_zip(data: bytes, code: int | None) -> MarkResult:
    """ZIP 속 PNG 마다 워터마크. 하나라도 실패하면 **원본 ZIP 전체**를 돌려준다(부분 표식 금지 —
    어느 블록은 되고 어느 블록은 안 된 파일은 추적 결과를 헷갈리게 한다)."""
    t0 = time.monotonic()
    try:
        zin = zipfile.ZipFile(io.BytesIO(data))
        infos = zin.infolist()
        if len(infos) > _ZIP_MAX_ENTRIES or sum(i.file_size for i in infos) > _ZIP_MAX_UNCOMPRESSED:
            raise ValueError("zip too large")
    except Exception as e:
        logger.warning("publication watermark failed (zip open)", exc_info=True)
        return MarkResult(data, "failed", [], error=type(e).__name__)

    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    normalized: list[Image.Image] = []
    failed: str | None = None if code is not None else "no_code"
    pngs = 0
    for info in infos:
        raw = zin.read(info)
        if info.is_dir() or raw[:8] != _PNG_MAGIC:
            entries.append((info, raw))
            continue
        pngs += 1
        if failed is None:
            try:
                out, norm = _mark_one(raw, code)
                entries.append((info, out))
                normalized.append(norm)
                continue
            except Exception as e:
                logger.warning("publication watermark failed (zip entry)", exc_info=True)
                failed = type(e).__name__
        entries.append((info, raw))
        try:
            normalized.append(fm_fingerprint.normalize(fm_fingerprint.open_image(raw)))
        except Exception:
            pass
    if pngs == 0:
        return MarkResult(data, "skipped", [])
    if failed is not None:
        # 이미 표식된 블록의 정규화본이 섞여 있어도 지문 거리는 같다(워터마크는 pHash 를 안 움직인다).
        return MarkResult(data, "failed", _fingerprints(normalized), error=failed)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info, raw in entries:
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zi.comment = info.comment
            zout.writestr(zi, raw)
    elapsed = int((time.monotonic() - t0) * 1000)
    return MarkResult(buf.getvalue(), "embedded", _fingerprints(normalized), elapsed)


def mark_publication(data: bytes, kind: str, code: int | None) -> MarkResult:
    """배포본 1건 표식. 절대 raise 하지 않는다."""
    try:
        if kind == "zip":
            return _mark_zip(data, code)
        return _mark_png(data, code)
    except Exception as e:   # 방어선 — 위 함수들이 이미 잡지만 셀러 다운로드는 무조건 지킨다
        logger.exception("publication mark crashed")
        return MarkResult(data, "failed", [], error=type(e).__name__)
