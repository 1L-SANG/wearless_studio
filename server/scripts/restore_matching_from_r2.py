"""매칭의류 카탈로그 DB 행 복구 — **R2 객체는 그대로 두고 행만 되살린다**.

2026-07-31 상황: prod 에서 `matching_items` 0행 · `assets` 의 seed/matching 0행이라 매칭의류가
화면에 안 떴다. 그런데 R2 에는 60개 이미지 + 60개 썸네일이 **전부 살아 있었다** — DB 행만
지워진 것이다. `seed_matching.py` 는 로컬 이미지를 읽어 업로드하는 전제라, 이미지가 로컬에
없는 지금은 못 쓴다(그래서 이 스크립트가 따로 있다).

여기서 하는 일: 시드 JSON 의 메타데이터 + R2 의 실제 객체 크기로 `assets` 행을 만들고
`matching_items` 를 upsert 한다. **업로드·삭제 없음.** 멱등(on conflict do update).

checksum 은 비운다 — 로컬 원본이 없어 계산할 수 없다. 무결성 대신 정직함을 택한 것이고,
나중에 원본이 생기면 `seed_matching.py` 가 upsert 하면서 채운다.

실행:
    cd server && .venv/bin/python -m scripts.restore_matching_from_r2 [--dry-run]
전제: server/.env(DATABASE_URL·R2). **prod 쓰기 — 사용자 승인 후.**
"""
import argparse
import json
import pathlib

import psycopg

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

SEED_JSON = pathlib.Path(__file__).resolve().parents[1] / "seed/matching_items.json"


def _upsert_asset(cur, bucket: str, key: str, size: int | None) -> str:
    cur.execute(
        """
        insert into assets (source, visibility, r2_bucket, r2_key, mime_type, byte_size)
        values ('seed', 'public', %s, %s, 'image/png', %s)
        on conflict (r2_key) do update set byte_size = excluded.byte_size
        returning id::text
        """,
        (bucket, key, size),
    )
    return cur.fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="R2 대조만 하고 DB 는 안 건드린다")
    args = ap.parse_args()

    s = load_settings()
    assert s.database_url, "DATABASE_URL 필요"
    r2 = R2Client(s)
    items = json.loads(SEED_JSON.read_text())

    # 먼저 R2 에 전부 있는지 확인한다 — 없는 걸 행으로 만들면 화면에 깨진 이미지가 뜬다.
    plan, missing = [], []
    for it in items:
        img_key = f"seed/matching/{it['id']}.png"
        thumb_key = f"seed/matching/thumb/{it['id']}.png"
        heads = {k: r2.head(k) for k in (img_key, thumb_key)}
        absent = [k for k, h in heads.items() if not h]
        if absent:
            missing.append((it["id"], absent))
            continue
        plan.append((it, img_key, thumb_key, heads))
    print(f"[restore] 시드 {len(items)} · R2 확인 완료 {len(plan)} · 누락 {len(missing)}")
    for mid, keys in missing[:5]:
        print(f"  누락: {mid} → {keys}")
    if args.dry_run:
        print("[restore] dry-run — DB 미변경")
        return 0
    if not plan:
        print("[restore] 복구할 것이 없다")
        return 1

    with psycopg.connect(s.database_url) as conn, conn.cursor() as cur:
        for it, img_key, thumb_key, heads in plan:
            img_id = _upsert_asset(cur, s.r2_bucket, img_key, heads[img_key].get("size"))
            thumb_id = _upsert_asset(cur, s.r2_bucket, thumb_key, heads[thumb_key].get("size"))
            cur.execute(
                """
                insert into matching_items
                  (id, name, clothing_type, gender, category, color_name, color_group,
                   style_tags, fit, length, color_brightness, image_asset_id, thumbnail_asset_id,
                   is_active, sort_order)
                values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,true,%s)
                on conflict (id) do update set
                  name = excluded.name, clothing_type = excluded.clothing_type,
                  gender = excluded.gender, category = excluded.category,
                  color_name = excluded.color_name, color_group = excluded.color_group,
                  style_tags = excluded.style_tags, fit = excluded.fit, length = excluded.length,
                  color_brightness = excluded.color_brightness,
                  image_asset_id = excluded.image_asset_id,
                  thumbnail_asset_id = excluded.thumbnail_asset_id,
                  is_active = true, sort_order = excluded.sort_order
                """,
                (it["id"], it["name"], it["clothingType"], it["gender"], it["category"],
                 it["colorName"], it["colorGroup"], json.dumps(it["styleTags"]), it["fit"],
                 it["length"], it.get("colorBrightness", 50), img_id, thumb_id, it["sortOrder"]),
            )
        conn.commit()
        cur.execute("select count(*) from matching_items where is_active")
        print(f"[restore] 완료 — 활성 matching_items {cur.fetchone()[0]}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
