"""매칭의류 시드 — 로컬 이미지 → R2(seed/matching/...) + assets + matching_items.

멱등: checksum/크기 동일 시 재업로드 skip, id·r2_key upsert. 운영자 1회성 스크립트.
실행: cd server && .venv/bin/python -m scripts.seed_matching
전제: server/seed/matching_items.json(생성기 산출), public/assets/matching/ 이미지,
      R2 자격증명·R2_PUBLIC_BASE·DATABASE_URL(server/.env). prod 쓰기 — 사용자 승인 후.
"""
import hashlib
import json
from pathlib import Path

import psycopg

from app.config import load_settings
from app.r2 import R2Client

ROOT = Path(__file__).resolve().parents[2]
SEED_JSON = ROOT / "server/seed/matching_items.json"
PUB = ROOT / "public/assets/matching"
_IMMUTABLE = "public, max-age=31536000, immutable"


def _local(url: str) -> Path:
    # "/assets/matching/{type}/{file}" → public/assets/matching/{type}/{file}
    return PUB / url.replace("/assets/matching/", "")


def _upsert_asset(cur, bucket: str, key: str, mime: str, data: bytes) -> str:
    cur.execute(
        """
        insert into assets (source, visibility, r2_bucket, r2_key, mime_type, byte_size, checksum)
        values ('seed', 'public', %s, %s, %s, %s, %s)
        on conflict (r2_key) do update set
          mime_type = excluded.mime_type, byte_size = excluded.byte_size, checksum = excluded.checksum
        returning id::text
        """,
        (bucket, key, mime, len(data), hashlib.sha256(data).hexdigest()),
    )
    return cur.fetchone()[0]


def main() -> None:
    settings = load_settings()
    assert settings.r2_public_base, "R2_PUBLIC_BASE 필요 (공개 서빙 전제)"
    assert settings.database_url, "DATABASE_URL 필요"
    r2 = R2Client(settings)
    items = json.loads(SEED_JSON.read_text())
    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        for it in items:
            img_data = _local(it["imageUrl"]).read_bytes()
            thumb_data = _local(it["thumbnailUrl"]).read_bytes()
            img_key = f"seed/matching/{it['id']}.png"
            thumb_key = f"seed/matching/thumb/{it['id']}.png"
            for key, data in ((img_key, img_data), (thumb_key, thumb_data)):
                head = r2.head(key)
                if not head or head["size"] != len(data):
                    r2.put_bytes(key, data, "image/png", _IMMUTABLE)
                assert r2.head(key), f"upload failed: {key}"
            img_id = _upsert_asset(cur, settings.r2_bucket, img_key, "image/png", img_data)
            thumb_id = _upsert_asset(cur, settings.r2_bucket, thumb_key, "image/png", thumb_data)
            cur.execute(
                """
                insert into matching_items
                  (id, name, clothing_type, gender, category, color_name, color_group,
                   style_tags, fit, length, color_brightness, image_asset_id, thumbnail_asset_id,
                   is_active, sort_order)
                values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,true,%s)
                on conflict (id) do update set
                  name = excluded.name, color_brightness = excluded.color_brightness,
                  image_asset_id = excluded.image_asset_id,
                  thumbnail_asset_id = excluded.thumbnail_asset_id,
                  is_active = true, sort_order = excluded.sort_order
                """,
                (it["id"], it["name"], it["clothingType"], it["gender"], it["category"],
                 it["colorName"], it["colorGroup"], json.dumps(it["styleTags"]), it["fit"],
                 it["length"], it.get("colorBrightness", 50), img_id, thumb_id, it["sortOrder"]),
            )
        conn.commit()
    print(f"seeded {len(items)} matching_items")


if __name__ == "__main__":
    main()
