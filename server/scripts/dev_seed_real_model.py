"""개발 DB 전용 — 셀러 화면에서 고를 수 있는 실존(REAL) 모델 한 명을 심는다.

얼굴 패스를 사람 손으로 끝까지 돌려보려면 셀러가 실제로 **고를 수 있는** 모델이 있어야 한다.
개발 DB 에는 fm_models 가 0행이고, 등록(OACX·라이브니스·QC)은 개발에서 완주할 수 없다.
그래서 등록이 끝난 뒤의 상태를 그대로 만들어 준다 — 카탈로그 노출 조건
(app/facemarket.py `_CURRENT_CARD_ELIGIBILITY`)과 컷 생성 게이트
(`verify_license_local` · `identity_source.resolve_real_model_assets`)를 **전부** 만족하는 한 벌.

안전장치(이 스크립트는 운영에서 절대 돌면 안 된다):
  · APP_ENV 이 dev 가 아니면 종료 1
  · DATABASE_URL 호스트가 개발 허용목록(ap-southeast-1 pooler / localhost)이 아니면 종료 1
  · 운영 표식(us-east-1 · ftjxwx)이 보이면 즉시 종료 1
  · 기본은 **미리보기**다. 실제 쓰기는 --apply 를 줘야 한다.

R2 는 개발과 운영이 같은 얼굴 버킷을 쓰므로 모든 키를 `dev-seed/` 접두어 아래에만 올리고,
올린 키 목록을 파일로 남긴다(나중에 지울 때 그 파일이 목록이다).

실행:
    cd server && .venv/bin/python -m scripts.dev_seed_real_model            # 미리보기
    cd server && .venv/bin/python -m scripts.dev_seed_real_model --apply    # 실제 쓰기
"""
import argparse
import hashlib
import io
import json
import os
import pathlib
import sys
import uuid
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402
from app.facemarket import BRAND_USE_CATEGORIES  # noqa: E402
from app.facemarket_enrollment import BIOMETRIC_CONSENT_VERSION  # noqa: E402
from app.r2 import R2Client, sha256_sri  # noqa: E402

#: 개발 DB 로 인정하는 호스트. 허용목록이 없으면 "운영 표식이 없는 낯선 호스트"가 통과한다.
_DEV_HOST_OK = ("ap-southeast-1.pooler.supabase.com", "localhost", "127.0.0.1", "db")
#: 보이면 즉시 중단하는 운영 표식.
_PROD_MARKERS = ("us-east-1", "ftjxwx")

#: 모든 R2 키의 접두어. 개발·운영이 얼굴 버킷을 공유하므로 한 접두어로 모아 둔다.
KEY_PREFIX = "dev-seed"
DISPLAY_NAME = "[DEV] v6 테스트"
#: 등록 증거 버전 — 자산/등록이 서로 같은 값을 가리키기만 하면 된다(게이트가 상호 비교만 한다).
POLICY_VERSION = "dev-seed-v1"

DATASET = pathlib.Path.home() / "Downloads/lora_runs/v6_dataset"
#: (각도, 후보 glob) — 개발 시드라 정확한 측면 촬영본이 없으면 깊은 3/4 를 쓴다.
PHOTO_SOURCES = (
    ("front", ("refset/*정면_무표정.png", "train/*정면_무표정.png")),
    ("angle45", ("train/*__3_4_오른쪽.png", "train/*3_4_오른쪽*.png")),
    ("side", ("train/*3_4_왼쪽*깊게*.png", "train/*3_4_왼쪽*.png")),
)
#: 증거 그리드(2×2)에 쓸 얼굴들.
GRID_SOURCES = (
    "refset/*정면_무표정.png",
    "refset/*정면_미소.png",
    "refset/*시선_왼쪽.png",
    "refset/*시선_오른쪽.png",
)
BODY_SOURCE = "train/*전신_3_4*.png"

MIME = "image/png"
MAX_SIDE = 1024


def _fail(message: str) -> None:
    print(f"중단: {message}", file=sys.stderr)
    raise SystemExit(1)


def guard_dev(database_url: str) -> str:
    """개발 환경이 아니면 한 줄도 쓰지 않는다. 호스트만 돌려준다(자격증명은 절대 출력 안 함)."""
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    if app_env != "dev":
        _fail(f"APP_ENV={app_env or '(없음)'} — 이 스크립트는 dev 에서만 돈다")
    host = (urlsplit(database_url).hostname or "").lower()
    if not host:
        _fail("DATABASE_URL 에서 호스트를 읽지 못했다")
    if any(marker in host for marker in _PROD_MARKERS):
        _fail("DB 호스트에 운영 표식이 있다")
    if not any(host == ok or host.endswith(ok) for ok in _DEV_HOST_OK):
        _fail(f"개발 허용목록 밖 DB 호스트({host[:24]}…)")
    return host


def _pick(patterns: tuple[str, ...]) -> pathlib.Path:
    for pattern in patterns:
        hits = sorted(DATASET.glob(pattern))
        if hits:
            return hits[0]
    _fail(f"원본 사진을 찾지 못했다: {patterns[0]} (DATASET={DATASET})")


def _load_rgb(path: pathlib.Path):
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).convert("RGB")


def _png(image, max_side: int = MAX_SIDE) -> bytes:
    """EXIF 없는 PNG 바이트. 원본이 3000px 대라 그대로 올리면 업로드·추론이 다 느리다."""
    scale = min(1.0, max_side / max(image.size))
    if scale < 1.0:
        from PIL import Image

        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _grid_png() -> bytes:
    """증거 시드카드 — 얼굴 4장을 2×2 로 붙인 1024px 정사각."""
    from PIL import Image

    cell = MAX_SIDE // 2
    canvas = Image.new("RGB", (MAX_SIDE, MAX_SIDE), (255, 255, 255))
    for index, pattern in enumerate(GRID_SOURCES):
        source = _load_rgb(_pick((pattern,)))
        side = min(source.size)
        left = (source.width - side) // 2
        # 얼굴이 위쪽에 있는 인물 사진이라 정사각 크롭은 위에서 딴다.
        crop = source.crop((left, 0, left + side, side)).resize((cell, cell), Image.LANCZOS)
        canvas.paste(crop, ((index % 2) * cell, (index // 2) * cell))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def build_objects() -> dict[str, dict]:
    """올릴 바이트를 전부 먼저 만든다 — DB 를 건드리기 전에 원본 부재로 실패하게."""
    objects: dict[str, dict] = {}
    for angle, patterns in PHOTO_SOURCES:
        source = _pick(patterns)
        objects[f"photo:{angle}"] = {"data": _png(_load_rgb(source)), "source": source.name}
    objects["asset:face_front"] = {
        "data": objects["photo:front"]["data"],
        "source": objects["photo:front"]["source"],
    }
    objects["asset:grid_sedcard"] = {"data": _grid_png(), "source": "2×2 grid"}
    body_hits = sorted(DATASET.glob(BODY_SOURCE))
    if body_hits:
        objects["asset:body_front"] = {
            "data": _png(_load_rgb(body_hits[0])),
            "source": body_hits[0].name,
        }
    # 카탈로그 카드 커버 — 얼굴이 보여야 셀러가 고른다. 정면 원본의 위쪽 정사각 크롭.
    front = _load_rgb(_pick(PHOTO_SOURCES[0][1]))
    side = min(front.size)
    left = (front.width - side) // 2
    objects["cover"] = {
        "data": _png(front.crop((left, 0, left + side, side)), 768),
        "source": "정면 정사각 크롭",
    }
    return objects


def _keys(model_id: str, enrollment_id: str, objects: dict[str, dict]) -> dict[str, str]:
    base = f"{KEY_PREFIX}/facemarket/models/{model_id}"
    keys = {
        "cover": f"{base}/cover.png",
    }
    for name in objects:
        if name.startswith("photo:"):
            keys[name] = f"{base}/enrollments/{enrollment_id}/originals/{name[6:]}.png"
        elif name.startswith("asset:"):
            keys[name] = f"{base}/enrollments/{enrollment_id}/assets/{name[6:]}.png"
    return keys


def seed(conn, r2_face, *, objects, keys, model_id, enrollment_id, license_id, vc_id):
    """등록이 끝난 뒤의 상태를 그대로 만든다. 순서는 FK 순환 때문에 고정이다."""
    digests = {}
    with conn.cursor() as cur:
        cur.execute(
            "insert into fm_models (id, display_name, status, assets_status, gender, "
            "height_bucket, body_type) values (%s, %s, 'pending', 'none', 'male', "
            "'m_175_180', 'regular')",
            (model_id, DISPLAY_NAME),
        )
        cur.execute(
            "insert into fm_biometric_enrollments (id, model_id, device_digest, consent_version, "
            "status, decision, match_policy_version, vc_id, completed_at, provider_versions, "
            "raw_deletion_evidence, height_bucket, body_type, expires_at) "
            "values (%s, %s, %s, %s, 'passed', 'passed', %s, %s, now(), %s, %s, "
            "'m_175_180', 'regular', now() + interval '365 days')",
            (enrollment_id, model_id, f"dev-seed-{enrollment_id}", BIOMETRIC_CONSENT_VERSION,
             POLICY_VERSION, vc_id, Json({"seed": "dev_seed_real_model"}),
             Json({"seed": "dev_seed_real_model"})),
        )
        for angle, _ in PHOTO_SOURCES:
            data = objects[f"photo:{angle}"]["data"]
            key = keys[f"photo:{angle}"]
            digest = sha256_sri(data)
            digests[angle] = digest
            r2_face.put_bytes(key, data, MIME)
            cur.execute(
                "insert into fm_biometric_enrollment_photos (enrollment_id, angle, r2_key, "
                "image_digest, mime_type, byte_size, qc_status, storage_state, approved_at) "
                "values (%s, %s, %s, %s, %s, %s, 'passed', 'approved', now())",
                (enrollment_id, angle, key, digest, MIME, len(data)),
            )
        # 자산 무결성 해시는 승인된 사진 다이제스트의 함수 — 게이트가 이 값을 다시 계산해 비교한다.
        source_hash = hashlib.sha256(
            "|".join(digests[a] for a in ("front", "angle45", "side")).encode()
        ).hexdigest()
        for name, key in keys.items():
            if not name.startswith("asset:"):
                continue
            data = objects[name]["data"]
            r2_face.put_bytes(key, data, MIME)
            cur.execute(
                "insert into fm_model_assets (model_id, view, r2_key, mime, bucket, "
                "source_enrollment_id, evidence_version) values (%s, %s, %s, %s, 'face', %s, %s)",
                (model_id, name[6:], key, MIME, enrollment_id, POLICY_VERSION),
            )
        r2_face.put_bytes(keys["cover"], objects["cover"]["data"], MIME)
        cur.execute(
            "update fm_models set status = 'verified', assets_status = 'ready', "
            "current_enrollment_id = %s, assets_source_hash = %s, cover_image_url = %s, "
            "confirmed_at = now(), confirm_consent_version = %s, updated_at = now() "
            "where id = %s",
            (enrollment_id, source_hash, keys["cover"], BIOMETRIC_CONSENT_VERSION, model_id),
        )
        face_key = keys["photo:front"]
        cur.execute(
            "insert into fm_licenses (id, model_id, enrollment_id, face_image_uri, face_image_key, "
            "face_image_digest, allowed_use, forbidden_use, license_valid_until, status, vc_id, "
            "opt_location_cuts, opt_lookbook_person_replace, opt_consent_version, opt_consented_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, '{}', now() + interval '365 days', 'active', %s, "
            "true, true, %s, now())",
            (license_id, model_id, enrollment_id, f"r2://{face_key}", face_key,
             digests["front"], list(BRAND_USE_CATEGORIES), vc_id, BIOMETRIC_CONSENT_VERSION),
        )
    return source_hash


def verify(conn, model_id: str) -> dict:
    """심은 직후 카탈로그 노출 조건을 **운영과 같은 SQL** 로 되묻는다."""
    from app.facemarket import _CURRENT_CARD_ELIGIBILITY, _CURRENT_CARD_JOINS

    with conn.cursor() as cur:
        cur.execute(
            f"select m.id::text as id, l.id::text as license_id from fm_models m "
            f"{_CURRENT_CARD_JOINS} where {_CURRENT_CARD_ELIGIBILITY} and m.id = %s limit 1",
            (BIOMETRIC_CONSENT_VERSION, model_id),
        )
        return cur.fetchone() or {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제로 쓴다(기본은 미리보기)")
    parser.add_argument("--keys-out", default="tmp/dev_seed_real_model_keys.txt",
                        help="올린 R2 키 목록을 남길 파일")
    args = parser.parse_args()

    settings = load_settings()
    database_url = os.environ.get("DATABASE_URL") or ""
    host = guard_dev(database_url)
    print(f"→ 개발 확인: APP_ENV=dev, host={host}")

    objects = build_objects()
    total = sum(len(o["data"]) for o in objects.values())
    for name, obj in objects.items():
        print(f"  {name:24s} {len(obj['data']) / 1024:7.0f} KB  ← {obj['source']}")
    print(f"  합계 {total / 1024 / 1024:.1f} MB")

    model_id, enrollment_id, license_id = (str(uuid.uuid4()) for _ in range(3))
    vc_id = f"urn:dev-seed:{uuid.uuid4()}"
    keys = _keys(model_id, enrollment_id, objects)

    if not args.apply:
        print("\n미리보기다(--apply 없음). 쓸 내용:")
        print(f"  fm_models 1 · fm_biometric_enrollments 1 · photos {len(PHOTO_SOURCES)} · "
              f"fm_model_assets {sum(1 for k in keys if k.startswith('asset:'))} · fm_licenses 1")
        print(f"  R2 접두어: {KEY_PREFIX}/facemarket/models/<model_id>/")
        return 0

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("select id::text as id from fm_models where display_name = %s limit 1",
                        (DISPLAY_NAME,))
            existing = cur.fetchone()
        if existing:
            print(f"이미 있다 — model_id={existing['id']} (지우고 다시 심으려면 그 행을 먼저 삭제)")
            return 0
        r2_face = R2Client(settings, bucket=settings.r2_face_bucket, public_base=None)
        seed(conn, r2_face, objects=objects, keys=keys, model_id=model_id,
             enrollment_id=enrollment_id, license_id=license_id, vc_id=vc_id)
        conn.commit()
        eligible = verify(conn, model_id)

    out = pathlib.Path(args.keys_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"bucket": settings.r2_face_bucket, "model_id": model_id,
                    "enrollment_id": enrollment_id, "license_id": license_id,
                    "keys": sorted(keys.values())}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")

    print(f"\n심었다: model_id={model_id}")
    print(f"  license_id={license_id}  enrollment_id={enrollment_id}")
    print(f"  카탈로그 노출 조건: {'통과' if eligible else '불통과 — 확인 필요'}")
    print(f"  R2 키 목록: {out}")
    return 0 if eligible else 1


if __name__ == "__main__":
    raise SystemExit(main())
