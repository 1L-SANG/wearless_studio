"""Deterministic editor export rendering.

This module is intentionally provider-free. It converts an editor snapshot into local
PNG/ZIP bytes only; workers own storage and database side effects.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import textwrap
import uuid
import zipfile
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

RENDERER_VERSION = "export-render-v1"
_ASSET_FILE_RE = re.compile(r"/v1/assets/([0-9a-fA-F-]{36})/file(?:$|[?#])")
ASSET_FILE_RE = _ASSET_FILE_RE
_FIXED_ZIP_DATE = (2026, 1, 1, 0, 0, 0)
_MAX_BLOCKS = 200


class ExportRenderError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RenderedExport:
    files: list[dict]
    manifest: dict


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_snapshot_hash(snapshot: dict, expected: str) -> str:
    actual = sha256_hex(canonical_bytes(snapshot))
    if expected != actual:
        raise ExportRenderError("snapshot_hash_mismatch", "내보내기 화면 상태가 최신이 아니에요.")
    return actual


def referenced_asset_ids(snapshot: dict) -> list[str]:
    """저장된 editor snapshot에서 앱 asset URL만 추출하고 UUID를 정규화한다."""
    found: set[str] = set()
    for block in _blocks(snapshot):
        for value in _walk_values(block):
            if not isinstance(value, str):
                continue
            match = _ASSET_FILE_RE.search(value)
            if not match:
                continue
            try:
                found.add(str(uuid.UUID(match.group(1))))
            except ValueError as exc:
                raise ExportRenderError(
                    "invalid_export_asset", "내보내기 이미지 식별자가 올바르지 않아요."
                ) from exc
    return sorted(found)


def render(
    *,
    snapshot: dict,
    body: dict,
    options: dict,
    asset_bytes: Callable[[str], bytes | None] | None = None,
) -> RenderedExport:
    fmt = (options or {}).get("format") or "long_png"
    if fmt not in {"long_png", "zip"}:
        raise ExportRenderError("invalid_export_format", "내보내기 형식이 올바르지 않아요.")
    png = _render_png(snapshot=snapshot, body=body or {}, options=options or {},
                      asset_bytes=asset_bytes)
    png_sha = sha256_hex(png)
    files = [{
        "role": "long_png",
        "filename": "wearless-export.png",
        "mime": "image/png",
        "bytes": png,
        "sha256": png_sha,
    }]
    manifest = {
        "rendererVersion": RENDERER_VERSION,
        "format": fmt,
        "snapshotSha256": sha256_hex(canonical_bytes(snapshot)),
        "bodySha256": sha256_hex(canonical_bytes(body or {})),
        "optionsSha256": sha256_hex(canonical_bytes(options or {})),
        "files": [{"role": f["role"], "filename": f["filename"], "sha256": f["sha256"]}
                  for f in files],
        "providerCalls": 0,
    }
    if fmt == "zip":
        entries = {
            "wearless-export.png": png,
            "snapshot.json": _pretty_json(snapshot),
            "body.json": _pretty_json(body or {}),
            "provenance.json": _pretty_json(manifest),
        }
        for index, block in enumerate(_blocks(snapshot), start=1):
            block_snapshot = {"editorBlocks": [{**block, "x": 0, "y": 0}]}
            entries[f"sections/{index:03d}.png"] = _render_png(
                snapshot=block_snapshot, body={}, options=options or {}, asset_bytes=asset_bytes)
        zip_bytes = _zip_bytes(entries)
        files.append({
            "role": "zip",
            "filename": "wearless-export.zip",
            "mime": "application/zip",
            "bytes": zip_bytes,
            "sha256": sha256_hex(zip_bytes),
        })
        manifest["files"].append({
            "role": "zip",
            "filename": "wearless-export.zip",
            "sha256": files[-1]["sha256"],
        })
    return RenderedExport(files=files, manifest=manifest)


def _pretty_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n"


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, _FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, entries[name])
    return out.getvalue()


def _blocks(snapshot: dict) -> list[dict]:
    blocks = snapshot.get("editorBlocks")
    if blocks is None:
        blocks = snapshot.get("blocks")
    if not isinstance(blocks, list):
        raise ExportRenderError("invalid_snapshot", "내보내기할 에디터 블록을 찾을 수 없어요.")
    normalized = [b for b in blocks if isinstance(b, dict)]
    if len(normalized) > _MAX_BLOCKS:
        raise ExportRenderError("export_too_large", "한 번에 내보낼 수 있는 블록 수를 초과했어요.")
    return normalized


def _walk_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def _render_png(*, snapshot: dict, body: dict, options: dict,
                asset_bytes: Callable[[str], bytes | None] | None) -> bytes:
    blocks = _blocks(snapshot)
    width = _bounded_int(options.get("width"), default=1200, lo=480, hi=2400)
    scale = width / 1200
    max_bottom = max([_num(b.get("y")) + _num(b.get("h"), 260) for b in blocks] + [800])
    height = min(max(int(max_bottom * scale) + 96, int(width * 1.35)), 12000)
    canvas = Image.new("RGB", (width, height), _color(options.get("background") or "#ffffff"))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    title = str((body or {}).get("title") or snapshot.get("title") or "Wearless export")
    draw.text((32, 24), title[:120], fill=(20, 24, 31), font=font)
    for block in sorted(blocks, key=lambda b: (_num(b.get("y")), _num(b.get("x")))):
        x = int(_num(block.get("x"), 48) * scale)
        y = int(_num(block.get("y"), 72) * scale) + 48
        w = int(_num(block.get("w"), 520) * scale)
        h = int(_num(block.get("h"), 320) * scale)
        x2, y2 = min(x + max(w, 40), width - 24), min(y + max(h, 40), height - 24)
        draw.rectangle(
            (x, y, x2, y2),
            fill=_color(block.get("bg") or block.get("background") or "#ffffff"),
        )
        elements = block.get("elements")
        if not isinstance(elements, list):
            elements = [block]
        for element in sorted([e for e in elements if isinstance(e, dict)],
                              key=lambda e: _num(e.get("z"))):
            _render_element(canvas, draw, font, block, element, (x, y, x2, y2), scale, asset_bytes)
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=False)
    return out.getvalue()


def _render_element(canvas: Image.Image, draw, font, block: dict, element: dict, block_box,
                    scale: float, asset_bytes) -> None:
    bx1, by1, bx2, by2 = block_box
    is_block = element is block
    ex = bx1 if is_block else bx1 + int(_num(element.get("x")) * scale)
    ey = by1 if is_block else by1 + int(_num(element.get("y")) * scale)
    ew = (bx2 - bx1) if is_block else int(_num(element.get("w"), 240) * scale)
    eh = (by2 - by1) if is_block else int(_num(element.get("h"), 160) * scale)
    box = (
        max(bx1, ex),
        max(by1, ey),
        min(bx2, ex + max(1, ew)),
        min(by2, ey + max(1, eh)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    kind = str(element.get("type") or "").lower()
    src = element.get("src") or element.get("imageUrl")
    if kind in {"image", "photo"} or src:
        if not _paste_asset(canvas, src, box, asset_bytes):
            draw.rounded_rectangle(box, radius=4, fill=(245, 246, 248),
                                   outline=(206, 211, 219), width=1)
        return
    if kind in {"rect", "shape", "line"}:
        fill = _color(element.get("fill") or element.get("color") or "#f5f6f8")
        outline = _color(element.get("stroke") or "#cfd4dc")
        draw.rounded_rectangle(
            box,
            radius=int(_num(element.get("radius"), 4) * scale),
            fill=fill,
            outline=outline,
            width=1,
        )
        return
    label = _label(element)
    if label:
        _draw_wrapped(draw, label, (box[0] + 8, box[1] + 8), max(1, box[2] - box[0] - 16), font)


def _paste_asset(canvas: Image.Image, src, box, asset_bytes) -> bool:
    if not src:
        return False
    m = _ASSET_FILE_RE.search(str(src or ""))
    if not m:
        raise ExportRenderError(
            "unsupported_export_image_source",
            "내보내기에 지원되지 않는 이미지 주소가 포함되어 있어요.",
        )
    if asset_bytes is None:
        raise ExportRenderError("export_asset_unavailable", "내보내기 이미지를 불러올 수 없어요.")
    try:
        asset_id = str(uuid.UUID(m.group(1)))
    except ValueError as exc:
        raise ExportRenderError("invalid_export_asset", "내보내기 이미지 식별자가 올바르지 않아요.") from exc
    raw = asset_bytes(asset_id)
    if not raw:
        raise ExportRenderError("export_asset_unavailable", "내보내기 이미지를 불러올 수 없어요.")
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ExportRenderError("invalid_export_asset", "내보내기 이미지 파일이 손상됐어요.") from exc
    x1, y1, x2, y2 = box
    img.thumbnail((max(1, x2 - x1), max(1, y2 - y1)))
    px = x1 + max(0, (x2 - x1 - img.width) // 2)
    py = y1 + max(0, (y2 - y1 - img.height) // 2)
    canvas.paste(img, (px, py))
    return True


def _label(block: dict) -> str:
    for key in ("text", "copy", "title", "caption", "contentRole", "cutType"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _draw_wrapped(draw, text: str, xy, width: int, font) -> None:
    chars = max(12, width // 7)
    for i, line in enumerate(textwrap.wrap(text, width=chars)[:8]):
        draw.text((xy[0], xy[1] + i * 14), line, fill=(20, 24, 31), font=font)


def _bounded_int(value, *, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return min(max(n, lo), hi)


def _num(value, default=0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _color(value: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(value or ""))
    if not m:
        return (255, 255, 255)
    h = m.group(1)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
