"""가상모델 아이덴티티 자산 시드 — 로컬 팩 → R2(seed/models/...) + manifest JSON.

계약: ai_agent_modules §3 AG-06 '가상모델 아이덴티티 레퍼런스 계약' —
  face_front = 원본 베이스컷(생성물 재주입 금지), grid_sedcard = v2 통짜 2x2 그리드,
  body_front = 기존 체형 기준, grid_face_direction = 검증된 GPT 프로필의 원본 픽셀 얼굴
  방향 시트, grid_fullbody = 같은 프로필의 전신 방향 시트,
  나머지 3뷰(시트)는 QC 폴백 전용 보관.
소스: public/models/{gender}/{sid}.webp(앵커) + spike/runs/facepack-{sid}v2-*/(v2 팩).
산출: R2 seed/models/{modelId}/{view}.{ext} + server/app/data/virtual_models.json (파일 기반
  manifest — example_assets.json 패턴, DB 테이블 없음).

멱등: 객체 존재·크기·MIME 동일 시 재업로드 skip. 운영자 1회성 스크립트(macOS sips 사용).
실행: cd server && .venv/bin/python -m scripts.seed_virtual_models
좁은 시드: 위 명령에 --grid-face-direction-only 또는 --grid-fullbody-only
전제: R2 자격증명·R2_PUBLIC_BASE(server/.env), spike/runs/facepack-*v2-* 로컬 존재.
"""
import argparse
import glob
import hashlib
from io import BytesIO
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path
import zlib

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def _load_env(path: Path):
    """server/.env → os.environ (미설정 키만). smoke_* 스크립트와 동일 패턴."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / "server/.env")

from app.config import load_settings  # noqa: E402 (env 로드 후 import)
from app.r2 import R2Client  # noqa: E402
MANIFEST = ROOT / "server/app/data/virtual_models.json"
_IMMUTABLE = "public, max-age=31536000, immutable"
_MAX_EDGE = "1536"  # v2 팩 자산 리샘플 상한 — 아이덴티티 참조엔 충분, 첨부 페이로드 절감
_PACK_MIME = "image/jpeg"  # 기존 v2 팩의 .png 파일명과 달리 실제 바이트는 JPEG
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
_PROVENANCE_TERMS = (
    b"google", b"openai", b"gpt-image", b"gemini", b"c2pa",
    b"content credentials", b"contentcredentials", b"contentauth", b"jumb",
)

# 프론트 모델 ID(src/mock/db.js AI_MODELS) ↔ 스파이크 소스 ID 매핑
# mF~mN(2026-08-17): 여성 9인 2차 배치. 크롭 바이트는 JPEG 라 pack_mime 기본값
# (image/jpeg)을 그대로 쓴다. 앵커는 mD·mE 와 같이 셀렉터 썸네일({sid}.webp)과 분리된
# {sid}-face.webp — 썸네일 restyle 이 R2 의 아이덴티티 정본을 덮지 못하게 하는 장치
# (m3 qc-notes 의 확립된 패턴).
#
# `run` = 큐레이션(시각 QC) 통과 런 디렉터리. 모델마다 재생성 때문에 런이 2~3개씩 있고
# 어느 것이 통과본인지는 디렉터리 이름만으론 알 수 없다. "가장 최신 런"에 의존하면
# 나중에 누가 실험 삼아 한 번 더 돌린 팩이 조용히 R2 를 덮어쓴다(spike/runs 는 gitignore
# 라 저장소 diff 에도 안 남는다) — 통과본을 여기 못박는다.
# 생성에 쓴 IDENTITY 버전은 모델마다 다르다(v3: mF·mL / v4: mG·mH·mJ·mK·mN / v5: mI·mM).
# 문구 원문과 개정 내력은 documents/virtual_model_batch_2026-08-17.md 참조.
MODELS = {
    "mA": {"sid": "w1", "gender": "women", "name": "Mia"},
    "mB": {"sid": "m1", "gender": "men", "name": "Leo"},
    "mC": {"sid": "m2", "gender": "men", "name": "도윤"},
    "mD": {
        "sid": "m3", "gender": "men", "name": "수혁", "pack_mime": "image/png",
        "anchor": "m3-face.webp",
    },
    "mE": {
        "sid": "w2", "gender": "women", "name": "지안", "pack_mime": "image/png",
        "anchor": "w2-face.webp",
    },
    "mF": {"sid": "w3", "gender": "women", "name": "하린",
            "anchor": "w3-face.webp", "run": "facepack-w3v2-2026-08-16T19-27-21"},
    "mG": {"sid": "w4", "gender": "women", "name": "세아",
            "anchor": "w4-face.webp", "run": "facepack-w4v2-2026-08-16T19-50-35"},
    "mH": {"sid": "w5", "gender": "women", "name": "예린",
            "anchor": "w5-face.webp", "run": "facepack-w5v2-2026-08-16T19-50-35"},
    "mI": {"sid": "w6", "gender": "women", "name": "다인",
            "anchor": "w6-face.webp", "run": "facepack-w6v2-2026-08-16T20-06-03"},
    "mJ": {"sid": "w7", "gender": "women", "name": "소윤",
            "anchor": "w7-face.webp", "run": "facepack-w7v2-2026-08-16T19-50-35"},
    "mK": {"sid": "w8", "gender": "women", "name": "유나",
            "anchor": "w8-face.webp", "run": "facepack-w8v2-2026-08-16T19-51-46"},
    "mL": {"sid": "w9", "gender": "women", "name": "채원",
            "anchor": "w9-face.webp", "run": "facepack-w9v2-2026-08-16T19-31-01"},
    "mM": {"sid": "w10", "gender": "women", "name": "나윤",
            "anchor": "w10-face.webp", "run": "facepack-w10v2-2026-08-16T20-06-03"},
    "mN": {"sid": "w11", "gender": "women", "name": "Nora",
            "anchor": "w11-face.webp", "run": "facepack-w11v2-2026-08-16T19-51-46"},
}

# 승인된 로컬 방향 시트가 같은 경로에서 조용히 바뀌어도 새 정본으로 받아들이지 않는다.
_FACE_DIRECTION_SOURCE_SHA256 = {
    "mA": "4d6ee1a185fa78f6670246ec43549bce90478f9b0a2829100e2ded024e03acf8",
    "mB": "99b9980a14814d2422f4abcecbb0029c10d16b6ca2e8d0fdd17df56341a687fc",
    "mC": "3ed8456e4793d9ee625911de5e49463154c0952d78d93fcc592a10a24fbec774",
    "mD": "ab244d4ab484ee80a843312ea6289b4b5a81ce660416c2225e4e85d10939b5f9",
    "mE": "e906d14b996fc32ad63b0e694c110b7d6b4c96b5cdb7da35289f98460c7f6062",
    "mF": "32fe183ff9d4741086750258fac8b694d4256d3cc6434818edf540f8c7c0cc6c",
    "mG": "50ec960ea5f5ccacec02fed668452b9f9fef4267701d30536b4e4b324407202d",
    "mH": "544d6770f14dded28581ffe765cb45a176b5000bb21725765fe2360222325771",
    "mI": "ce2cc9e45c6335b743bb055b936e7ae0a8119d0f4ab9d6e7adcfb6acf9dd983b",
    "mJ": "4cff1c56e1626cbc6faa1fd67f95fe0da8ce982e3462109d58bcd9d208c3797d",
    "mK": "a38f49d77ed074775e223a5f41c7769e4d976a3e1132dea457d35de70d7698f6",
    "mL": "e8e67971c8775fcf9caa8c77ac7cdd7fe51b64273ceff22a2c2a069053781d50",
    "mM": "02068ac88c792768bc566c3a163c528533eddd92089cd1c198f66e26a72a758a",
    "mN": "a6469cbd0b1ac0b6bb3fe653b184bba0219fdb82ad3f9ae6655609569fa0cf56",
}
_FULLBODY_SOURCE_SHA256 = {
    "mA": "0060a50185c77c4ba866cee9d1b21e5efa6780c47e4119795c0a844de18e861f",
    "mB": "44d6a1358a6bd12bfc5d1f6c6d04914a53d1cbe9d9d9e9e81a9d28862c330bc1",
    "mC": "6dd9ee2d87f6948347c4d74b54364a3f8938b42a3ca864ea5d64b4b77d955058",
    "mD": "d4f6f336f5f5d2475c88d692dee49f2cbde377dc1293984557ebf4aa4fe8b12f",
    "mE": "d41a41271cb0f0a13586c508e4c00ada48712fd36fea62cce75bcd50c1f1a45b",
    "mF": "b3875984dade00ca6ed79cb539484eec070f6158eceefd5371556ddda9bdc1d8",
    "mG": "0730d45375e6fe11e6d761dea7a525f7b9900514b39c18d5249c3d4c53104e60",
    "mH": "8fb98c16c2d52fdaa1f64da3bb7c4f54e63a531609fe65706fbda5328c3c7ae2",
    "mI": "20f845c8217050ff97b10fca88c6b3a35723e964734df98ac3a4c0390fced5ea",
    "mJ": "28e96b228fd75a2580d7698b03d33e35fd8124d09df5d7211258c597fa65724f",
    "mK": "6384b0bc90e8a4e99f5449a23b528e10eb295a7a505b926cc49b590d331b860b",
    "mL": "a25ca421aab59d4050f0324c3f5f2a276fa2816076ca20a810c4b24690be72b8",
    "mM": "ae8c6776fd99b8d1010e61861fc2ad5fad47914a901b0fee75d524b0a1cf0c75",
    "mN": "9c47fc0cb235ec023a0314c60a6b37ba37fb010ed37ea006d6460afac6ab32b3",
}
_HISTORICAL_ME_FACE_DIRECTION = {
    "sha256": "987c88e59cc5fcbf20b290494ecc831ef926cd48c2efed3fb90ccbdbed04b1a9",
    "byteLength": 1_739_708,
    "width": 1254,
    "height": 1254,
    "decodedRgbaSha256": "c2a3f5d7a838c7f7db3541423887f554dfd26e38e3d6ce51af3cf5b1fd033f88",
}
# 팩 크롭 파일명 → manifest 뷰 키 (계약의 시트 낱장 4뷰)
PACK_VIEWS = {
    "three-quarter-left.png": "three_quarter",
    "profile-left.png": "profile",
    "body-front.png": "body_front",
    "body-back.png": "body_back",
}


def _pack_dir(sid: str, run: str | None = None) -> Path:
    """큐레이션 통과 런의 pack 디렉터리. `run` 이 지정되면 그것만 쓴다.

    미지정 모델(1차 배치 mA~mE)은 종전대로 최신 런 폴백 — 런이 하나뿐이라 모호하지 않다.
    """
    if run:
        pack = ROOT / "spike/runs" / run / "pack"
        assert pack.is_dir(), f"고정된 런이 없다: {pack}"
        return pack
    runs = sorted(glob.glob(str(ROOT / f"spike/runs/facepack-{sid}v2-*/pack")))
    assert runs, f"팩 없음: spike/runs/facepack-{sid}v2-*/pack"
    return Path(runs[-1])


def _resample(src: Path, dst: Path) -> bytes:
    subprocess.run(
        ["sips", "-Z", _MAX_EDGE, str(src), "--out", str(dst)],
        check=True, capture_output=True,
    )
    return dst.read_bytes()


def _put_if_changed(r2: R2Client, key: str, data: bytes, mime: str) -> bool:
    head = r2.head(key)
    if head and head["size"] == len(data) and head.get("mime") == mime:
        return False
    r2.put_bytes(key, data, mime, _IMMUTABLE)
    assert r2.head(key), f"upload failed: {key}"
    return True


def _decoded_rgba_fingerprint(data: bytes) -> dict:
    """파일 컨테이너와 무관한 픽셀·크기 정본 지문."""
    with Image.open(BytesIO(data)) as opened:
        opened.load()
        rgba = opened.convert("RGBA")
        return {
            "width": opened.width,
            "height": opened.height,
            "decodedRgbaSha256": hashlib.sha256(rgba.tobytes()).hexdigest(),
        }


def _provenance_terms(data: bytes) -> tuple[bytes, ...]:
    lowered = data.lower()
    return tuple(term for term in _PROVENANCE_TERMS if term in lowered)


def _png_chunks(data: bytes) -> list[dict]:
    if not data.startswith(_PNG_SIGNATURE):
        raise RuntimeError("grid_face_direction PNG signature missing")
    chunks = []
    position = len(_PNG_SIGNATURE)
    while position < len(data):
        start = position
        if position + 12 > len(data):
            raise RuntimeError("grid_face_direction truncated PNG chunk")
        length = struct.unpack(">I", data[position:position + 4])[0]
        chunk_type = data[position + 4:position + 8]
        payload_start = position + 8
        payload_end = payload_start + length
        end = payload_end + 4
        if end > len(data):
            raise RuntimeError("grid_face_direction truncated PNG payload")
        payload = data[payload_start:payload_end]
        expected_crc = struct.unpack(">I", data[payload_end:end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            raise RuntimeError("grid_face_direction PNG CRC mismatch")
        chunks.append({
            "type": chunk_type,
            "payload": payload,
            "raw": data[start:end],
            "critical": not bool(chunk_type[0] & 0x20),
        })
        position = end
        if chunk_type == b"IEND":
            break
    if (
        not chunks
        or chunks[0]["type"] != b"IHDR"
        or chunks[-1]["type"] != b"IEND"
        or position != len(data)
    ):
        raise RuntimeError("grid_face_direction invalid PNG structure")
    return chunks


def _png_searchable_metadata(chunk: dict) -> bytes:
    payload = chunk["payload"]
    try:
        if chunk["type"] == b"zTXt":
            _keyword, rest = payload.split(b"\x00", 1)
            if rest and rest[0] == 0:
                return payload + b"\x00" + zlib.decompress(rest[1:])
        if chunk["type"] == b"iTXt":
            _keyword, rest = payload.split(b"\x00", 1)
            compressed, method = rest[0], rest[1]
            _language, remainder = rest[2:].split(b"\x00", 1)
            _translated, text = remainder.split(b"\x00", 1)
            if compressed == 1 and method == 0:
                text = zlib.decompress(text)
            return payload + b"\x00" + text
    except (IndexError, ValueError, zlib.error):
        pass
    return payload


def _sanitize_png_metadata(data: bytes) -> tuple[bytes, int]:
    chunks = _png_chunks(data)
    kept = []
    removed = 0
    for chunk in chunks:
        terms = _provenance_terms(_png_searchable_metadata(chunk))
        remove = chunk["type"] == b"caBX" or bool(terms and not chunk["critical"])
        if terms and chunk["critical"]:
            raise RuntimeError("producer term found in critical PNG chunk")
        if remove:
            removed += 1
        else:
            kept.append(chunk["raw"])
    sanitized = _PNG_SIGNATURE + b"".join(kept)
    # 픽셀 스트림 자체가 바뀌지 않았음을 컨테이너 수준에서도 확인한다.
    before_idat = b"".join(c["payload"] for c in chunks if c["type"] == b"IDAT")
    after_idat = b"".join(
        c["payload"] for c in _png_chunks(sanitized) if c["type"] == b"IDAT"
    )
    if before_idat != after_idat:
        raise RuntimeError("grid_face_direction PNG pixel payload changed")
    return sanitized, removed


def _jpeg_before_sos(data: bytes) -> tuple[list[dict], bytes, bytes]:
    if not data.startswith(_JPEG_SOI):
        raise RuntimeError("grid_face_direction JPEG SOI missing")
    position = 2
    segments = []
    while position < len(data):
        start = position
        if data[position] != 0xFF:
            raise RuntimeError("grid_face_direction invalid JPEG segment")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            raise RuntimeError("grid_face_direction truncated JPEG marker")
        marker = data[position]
        position += 1
        if marker == 0x00 or marker == 0xD9:
            raise RuntimeError("grid_face_direction JPEG ended before SOS")
        if marker in {0x01, *range(0xD0, 0xD8)}:
            end, payload = position, b""
        else:
            if position + 2 > len(data):
                raise RuntimeError("grid_face_direction truncated JPEG length")
            length = struct.unpack(">H", data[position:position + 2])[0]
            end = position + length
            if length < 2 or end > len(data):
                raise RuntimeError("grid_face_direction invalid JPEG length")
            payload = data[position + 2:end]
        raw = data[start:end]
        position = end
        if marker == 0xDA:
            return segments, raw, data[end:]
        segments.append({"marker": marker, "payload": payload, "raw": raw})
    raise RuntimeError("grid_face_direction JPEG SOS missing")


def _sanitize_jpeg_metadata(data: bytes) -> tuple[bytes, int]:
    segments, sos, tail = _jpeg_before_sos(data)
    if not tail.endswith(_JPEG_EOI):
        raise RuntimeError("grid_face_direction JPEG must end at EOI")
    kept = []
    removed = 0
    for segment in segments:
        marker, payload = segment["marker"], segment["payload"]
        terms = _provenance_terms(payload)
        remove = (marker == 0xEB and payload.startswith(b"JP")) or bool(
            terms and (0xE0 <= marker <= 0xEF or marker == 0xFE)
        )
        if terms and not remove:
            raise RuntimeError("producer term found in structural JPEG segment")
        if remove:
            removed += 1
        else:
            kept.append(segment["raw"])
    return _JPEG_SOI + b"".join(kept) + sos + tail, removed


def _sanitize_face_direction(data: bytes) -> tuple[bytes, str, dict]:
    """과거 실험과 같이 리사이즈·재인코딩 없이 식별 메타데이터만 제거한다."""
    before = _decoded_rgba_fingerprint(data)
    if data.startswith(_PNG_SIGNATURE):
        mime = "image/png"
        sanitized, removed = _sanitize_png_metadata(data)
        second_pass, second_removed = _sanitize_png_metadata(sanitized)
    elif data.startswith(_JPEG_SOI):
        mime = "image/jpeg"
        sanitized, removed = _sanitize_jpeg_metadata(data)
        second_pass, second_removed = _sanitize_jpeg_metadata(sanitized)
    else:
        raise RuntimeError("grid_face_direction supports only PNG/JPEG")
    if _decoded_rgba_fingerprint(sanitized) != before:
        raise RuntimeError("grid_face_direction sanitization changed decoded pixels")
    if second_pass != sanitized or second_removed:
        raise RuntimeError("grid_face_direction metadata sanitization is not idempotent")
    return sanitized, mime, {**before, "removedContainerCount": removed}


def _grid_face_direction_records() -> list[dict]:
    """승인된 얼굴 방향 시트를 과거 실험과 같은 무리사이즈 픽셀로 봉인한다."""
    records = []
    for model_id, model in MODELS.items():
        source = _pack_dir(model["sid"], model.get("run")).parent / "grid-sedcard.png"
        original = source.read_bytes()
        source_sha = hashlib.sha256(original).hexdigest()
        if source_sha != _FACE_DIRECTION_SOURCE_SHA256[model_id]:
            raise RuntimeError(f"grid_face_direction approved source drift: {model_id}")
        data, mime, proof = _sanitize_face_direction(original)
        record = {
            "modelId": model_id,
            "source": source,
            "sourceSha256": source_sha,
            "key": f"seed/models/{model_id}/grid_face_direction.png",
            "mime": mime,
            "data": data,
            "byteLength": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            **proof,
        }
        if model_id == "mE":
            observed = {key: record[key] for key in _HISTORICAL_ME_FACE_DIRECTION}
            if observed != _HISTORICAL_ME_FACE_DIRECTION:
                raise RuntimeError("mE historical face-direction outbound drift")
        records.append(record)
    return records


def _grid_fullbody_records() -> list[dict]:
    """승인된 런의 전신 방향 시트 원본과 불변 R2 목적지를 반환한다."""
    records = []
    for model_id, model in MODELS.items():
        source = _pack_dir(model["sid"], model.get("run")).parent / "grid-fullbody.png"
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != _FULLBODY_SOURCE_SHA256[model_id]:
            raise RuntimeError(f"grid_fullbody approved source drift: {model_id}")
        mime = model.get("pack_mime", _PACK_MIME)
        records.append({
            "modelId": model_id,
            "source": source,
            "key": f"seed/models/{model_id}/grid_fullbody.png",
            "mime": mime,
            "data": data,
            "sha256": digest,
        })
    return records


def _seed_immutable_records(r2: R2Client, records: list[dict], label: str) -> list[dict]:
    """기존 객체를 덮지 않고 승인된 불변 자산 묶음을 안전하게 시드한다.

    모든 목적지를 먼저 검사한다. 이미 같은 바이트가 있으면 멱등 skip하고, 같은 키에
    다른 바이트나 MIME이 있으면 어떤 PUT도 하기 전에 중단한다. 업로드 뒤에는 R2에서
    다시 내려받아 SHA-256까지 확인한다.
    """
    for record in records:
        head = r2.head(record["key"])
        if head is None:
            record["status"] = "missing"
            continue
        remote = r2.get_bytes(record["key"])
        if (
            head.get("size") != len(record["data"])
            or head.get("mime") != record["mime"]
            or hashlib.sha256(remote).hexdigest() != record["sha256"]
        ):
            raise RuntimeError(
                f"{label} destination differs; refusing overwrite: {record['modelId']}"
            )
        record["status"] = "verified-existing"

    for record in records:
        if record["status"] == "missing":
            r2.put_bytes(
                record["key"], record["data"], record["mime"], _IMMUTABLE,
            )
            record["status"] = "uploaded"

    for record in records:
        head = r2.head(record["key"])
        remote = r2.get_bytes(record["key"])
        if (
            head is None
            or head.get("size") != len(record["data"])
            or head.get("mime") != record["mime"]
            or hashlib.sha256(remote).hexdigest() != record["sha256"]
        ):
            raise RuntimeError(f"{label} verification failed: {record['modelId']}")
    return records


def _seed_grid_fullbody_only(r2: R2Client) -> list[dict]:
    return _seed_immutable_records(r2, _grid_fullbody_records(), "grid_fullbody")


def _seed_grid_face_direction_only(r2: R2Client) -> list[dict]:
    return _seed_immutable_records(
        r2, _grid_face_direction_records(), "grid_face_direction"
    )


def _print_seed_result(records: list[dict], label: str) -> None:
    for record in records:
        print(
            f"{record['modelId']} {record['status']} {record['mime']} "
            f"{len(record['data'])} {record['sha256']} {record['key']} "
            f"source={record['source']}"
        )
    print(f"verified {label} assets: {len(records)}")


def seed_grid_fullbody_only() -> None:
    """운영 승인용 좁은 진입점 — manifest와 기존 R2 키는 수정하지 않는다."""
    settings = load_settings()
    assert settings.r2_public_base, "R2_PUBLIC_BASE 필요 (공개 서빙 전제)"
    records = _seed_grid_fullbody_only(R2Client(settings))
    _print_seed_result(records, "grid_fullbody")


def seed_grid_face_direction_only() -> None:
    """기존 객체를 보존하며 exact 얼굴 방향 시트 새 키만 시드한다."""
    settings = load_settings()
    assert settings.r2_public_base, "R2_PUBLIC_BASE 필요 (공개 서빙 전제)"
    records = _seed_grid_face_direction_only(R2Client(settings))
    _print_seed_result(records, "grid_face_direction")


def main() -> None:
    settings = load_settings()
    assert settings.r2_public_base, "R2_PUBLIC_BASE 필요 (공개 서빙 전제)"
    r2 = R2Client(settings)
    face_records = {
        record["modelId"]: record for record in _seed_grid_face_direction_only(r2)
    }
    fullbody_records = {
        record["modelId"]: record for record in _seed_grid_fullbody_only(r2)
    }
    manifest: dict = {
        "_meta": {
            "description": (
                "Virtual model identity assets. Default worn-cut contract: face_front "
                "(facial identity only) + body_front (full-body proportions only). "
                "Confirmed GPT contract: grid_face_direction (lossless original-pixel face "
                "directions) + grid_fullbody (full-body directions) as one atomic pair."
            ),
            "source": "spike facepack v2 + public/models 원본 앵커",
        },
        "models": {},
    }
    uploaded = sum(
        record["status"] == "uploaded"
        for record in (*face_records.values(), *fullbody_records.values())
    )
    skipped = len(face_records) + len(fullbody_records) - uploaded
    with tempfile.TemporaryDirectory() as tmp:
        for model_id, m in MODELS.items():
            views: dict = {}
            pack_mime = m.get("pack_mime", _PACK_MIME)
            # face_front = 원본 베이스컷 그대로 (리샘플·재인코딩 없음 — 앵커 보존)
            anchor_name = m.get("anchor", f"{m['sid']}.webp")
            anchor = ROOT / "public/models" / m["gender"] / anchor_name
            key = f"seed/models/{model_id}/face_front.webp"
            fresh = _put_if_changed(r2, key, anchor.read_bytes(), "image/webp")
            uploaded, skipped = uploaded + fresh, skipped + (not fresh)
            views["face_front"] = {"key": key, "url": r2.public_url(key), "mime": "image/webp"}
            pack = _pack_dir(m["sid"], m.get("run"))
            # 세드카드 = v2 팩 루트의 2x2 통짜 그리드 리샘플(max 1536px)
            dst = Path(tmp) / f"{model_id}-grid_sedcard.png"
            data = _resample(pack.parent / "grid-sedcard.png", dst)
            key = f"seed/models/{model_id}/grid_sedcard.png"
            fresh = _put_if_changed(r2, key, data, pack_mime)
            uploaded, skipped = uploaded + fresh, skipped + (not fresh)
            views["grid_sedcard"] = {
                "key": key, "url": r2.public_url(key), "mime": pack_mime,
                "byteLength": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            }
            # 확인된 GPT 실험의 얼굴 방향 시트는 리사이즈하지 않았다. 기존 grid_sedcard는
            # 1536px 재인코딩 자산이므로 별도 exact 키를 써서 두 계약을 섞지 않는다.
            face = face_records[model_id]
            views["grid_face_direction"] = {
                "key": face["key"], "url": r2.public_url(face["key"]),
                "mime": face["mime"], "byteLength": len(face["data"]),
                "sha256": face["sha256"], "sourceSha256": face["sourceSha256"],
                "width": face["width"], "height": face["height"],
                "decodedRgbaSha256": face["decodedRgbaSha256"],
                "sanitization": "lossless_metadata_strip_no_resize",
            }
            # 전신 방향 시트는 확인된 GPT 실험에서 독립 입력 역할이었다. front/back 크롭으로
            # 대체하거나 재합성하면 당시 입력 계약과 달라지므로 검수 통과 원본 바이트를 그대로
            # 보존한다. 운영 업로드는 별도 승인된 seed 실행에서만 일어난다.
            fullbody = fullbody_records[model_id]
            views["grid_fullbody"] = {
                "key": fullbody["key"], "url": r2.public_url(fullbody["key"]),
                "mime": fullbody["mime"], "byteLength": len(fullbody["data"]),
                "sha256": fullbody["sha256"],
            }
            # 시트 4뷰 = v2 팩 크롭 리샘플(max 1536px) 후 업로드
            for fname, view in PACK_VIEWS.items():
                dst = Path(tmp) / f"{model_id}-{view}.png"
                data = _resample(pack / fname, dst)
                key = f"seed/models/{model_id}/{view}.png"
                fresh = _put_if_changed(r2, key, data, pack_mime)
                uploaded, skipped = uploaded + fresh, skipped + (not fresh)
                views[view] = {"key": key, "url": r2.public_url(key), "mime": pack_mime}
            manifest["models"][model_id] = {
                "gender": m["gender"], "name": m["name"],
                "thumb": f"/models/{m['gender']}/{m['sid']}.webp",  # 프론트 public 경로(기존)
                "views": views,
            }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"seeded {len(MODELS)} models — uploaded {uploaded}, skipped {skipped}")
    print(f"manifest: {MANIFEST}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--grid-fullbody-only",
        action="store_true",
        help="upload and verify only the 14 approved grid_fullbody assets",
    )
    group.add_argument(
        "--grid-face-direction-only",
        action="store_true",
        help="upload and verify only 14 lossless historical face-direction assets",
    )
    args = parser.parse_args()
    if args.grid_fullbody_only:
        seed_grid_fullbody_only()
    elif args.grid_face_direction_only:
        seed_grid_face_direction_only()
    else:
        main()
