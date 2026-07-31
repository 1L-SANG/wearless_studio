"""마네킹 베이스 asset 행 시드 — R2 에 이미 있는 객체를 DB 에 등록한다 (멱등).

왜 필요한가(2026-08-01 실측): DB 를 새로 만들면 스키마 마이그레이션과 매칭의류 시드는
돌지만 **마네킹 베이스 asset 행**이 빠진다. 그러면 `mannequin_job` 이
`base_mannequin_missing` 으로 죽고 셀러에게 "마네킹 베이스가 설정되지 않았어요" 가 뜬다.
(원래 이 행은 일회용 `seed_phase4.py` 가 넣었는데, 그 스크립트는 spike/base 로컬 원본과
`.env` 쓰기를 전제해서 DB 교체 시 재실행하기 어렵다.)

이 스크립트는 **R2 객체를 진실의 원천**으로 삼는다 — 업로드는 하지 않고, 이미 올라간
`seed/mannequin/base-{gender}-2K.png` 를 읽어 `assets` 행만 맞춘다.

멱등: `assets.r2_key` UNIQUE 기준. 이미 있으면 건드리지 않고 현재 id 를 보고한다.
신규 삽입 시 id 는 `MANNEQUIN_BASE_{WOMEN,MEN}_ASSET_ID` 환경변수 값을 그대로 쓴다 —
설정과 DB 가 어긋나지 않게(그래야 배포 환경변수를 바꿀 필요가 없다).

실행:
    cd server && .venv/bin/python -m scripts.seed_mannequin_base          # 확인만(기본)
    cd server && .venv/bin/python -m scripts.seed_mannequin_base --apply  # 실제 쓰기

DB 는 `server/.env` 의 DATABASE_URL 을 따른다(= 현재 prod). 다른 DB 를 대상으로 하려면
DATABASE_URL 을 앞에 붙여 실행할 것.
"""
import argparse
import hashlib
import io
import os

import psycopg

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

GENDERS = ("women", "men")


def _key(gender: str) -> str:
    return f"seed/mannequin/base-{gender}-2K.png"


def _env_id(gender: str) -> str | None:
    return os.getenv(f"MANNEQUIN_BASE_{gender.upper()}_ASSET_ID") or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 INSERT 한다(기본은 확인만)")
    args = ap.parse_args()

    s = load_settings()
    r2 = R2Client(s)
    bucket = s.r2_bucket

    rows = []
    for gender in GENDERS:
        key = _key(gender)
        want_id = _env_id(gender)
        try:
            data = r2.get_bytes(key)
        except Exception as e:  # R2 에 없으면 이 스크립트로는 복구 불가 — 원본 업로드가 먼저다
            print(f"  ❌ {gender}: R2 객체 없음 {key} ({str(e)[:60]})")
            return 1
        width = height = None
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            width, height = im.width, im.height
        except Exception:
            pass  # 크기는 부가정보 — 없어도 서빙에는 지장 없다
        rows.append({
            "gender": gender, "key": key, "want_id": want_id, "data": data,
            "checksum": hashlib.sha256(data).hexdigest(),
            "width": width, "height": height,
        })

    with psycopg.connect(s.database_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    "select id::text, deleted_at is not null from assets where r2_key = %s",
                    (r["key"],))
                found = cur.fetchone()
                if found:
                    r["state"] = "exists"
                    r["db_id"] = found[0]
                    r["deleted"] = found[1]
                else:
                    r["state"] = "missing"
                    r["db_id"] = None

        print(f"DB: {s.database_url.split('@')[-1].split('/')[0]}  bucket: {bucket}")
        for r in rows:
            tag = "이미 있음" if r["state"] == "exists" else "없음 → 삽입 대상"
            print(f"  {r['gender']:6} {tag:14} key={r['key']}")
            print(f"         bytes={len(r['data']):,} {r['width']}x{r['height']} "
                  f"env_id={(r['want_id'] or '(미설정)')[:8]} db_id={(r['db_id'] or '-')[:8]}")
            if r["state"] == "exists" and r["want_id"] and r["db_id"] != r["want_id"]:
                print("         ⚠️ env id 와 DB id 가 다르다 — 환경변수를 DB id 로 맞출 것")
            if r["state"] == "exists" and r.get("deleted"):
                print("         ⚠️ deleted_at 이 설정돼 있다 — 조회에서 제외된다")

        todo = [r for r in rows if r["state"] == "missing"]
        if not todo:
            print("\n삽입할 것 없음(멱등).")
            return 0
        if not args.apply:
            print(f"\n확인 모드 — {len(todo)}건 삽입 예정. 실제로 쓰려면 --apply")
            return 0

        with conn.cursor() as cur:
            for r in todo:
                if not r["want_id"]:
                    print(f"  ❌ {r['gender']}: MANNEQUIN_BASE_{r['gender'].upper()}_ASSET_ID 미설정 — "
                          "id 를 정할 수 없다")
                    return 1
                cur.execute(
                    "insert into assets (id, user_id, project_id, source, visibility, r2_bucket, "
                    "r2_key, mime_type, byte_size, width, height, checksum) "
                    "values (%s, null, null, 'seed', 'public', %s, %s, 'image/png', %s, %s, %s, %s) "
                    "on conflict (r2_key) do nothing",
                    (r["want_id"], bucket, r["key"], len(r["data"]),
                     r["width"], r["height"], r["checksum"]))
                print(f"  ✅ {r['gender']}: asset {r['want_id']} 삽입")
        conn.commit()

    print("\n완료. 마네킹 생성을 다시 시도해 볼 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
