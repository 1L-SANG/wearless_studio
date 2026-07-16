"""잘못 저장된 FaceMarket 얼굴 객체를 전용 비공개 R2 버킷으로 이동한다.

기본 실행은 읽기 전용 dry-run이다. 실제 변경은 ``--apply``가 있어야 수행한다.
개인화 철회가 완료된 프로필의 객체는 복원하지 않고 양쪽 버킷에서 삭제한다.

실행:
    cd server
    set -a; source .env.local; set +a
    .venv/bin/python -m scripts.migrate_face_objects_to_private_bucket
    .venv/bin/python -m scripts.migrate_face_objects_to_private_bucket --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.r2 import R2Client, sha256_sri  # noqa: E402


def _mime_for_key(key: str) -> str:
    suffix = Path(key).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def _verified_bytes(client: R2Client, key: str, expected_digest: str) -> bytes:
    data = client.get_bytes(key)
    actual = sha256_sri(data)
    if actual != expected_digest:
        raise RuntimeError("저장 객체와 DB digest가 일치하지 않아 중단합니다.")
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="검증 후 실제 이동/삭제를 수행한다. 생략하면 dry-run.",
    )
    args = parser.parse_args()

    settings = load_settings()
    if not settings.database_url:
        sys.exit("DATABASE_URL이 필요합니다.")
    if not all(
        (
            settings.r2_account_id,
            settings.r2_access_key_id,
            settings.r2_secret_access_key,
            settings.r2_bucket,
            settings.r2_face_bucket,
        )
    ):
        sys.exit("R2 기본 버킷과 R2_FACE_BUCKET 설정이 모두 필요합니다.")
    if settings.r2_bucket == settings.r2_face_bucket:
        sys.exit("R2_BUCKET과 R2_FACE_BUCKET은 서로 달라야 합니다.")

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            select l.id::text as id,
                   l.face_image_key,
                   l.face_image_digest,
                   l.status as license_status,
                   l.profile_id is not null as profile_linked,
                   coalesce(p.status, 'none') as profile_status
              from fm_licenses l
              left join personalization_profiles p on p.id = l.profile_id
             where l.face_image_key is not null
             order by l.created_at
            """
        ).fetchall()

    rows_by_key: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        rows_by_key[row["face_image_key"]].append(row)

    for key_rows in rows_by_key.values():
        purge_flags = {
            bool(row["profile_linked"] and row["profile_status"] == "purged")
            for row in key_rows
        }
        if len(purge_flags) > 1:
            sys.exit("같은 객체를 유지/파기 라이선스가 함께 참조해 중단합니다.")

    main_r2 = R2Client(settings)
    face_r2 = R2Client(settings, bucket=settings.r2_face_bucket, public_base=None)
    counts: dict[str, int] = defaultdict(int)

    print(f"mode={'APPLY' if args.apply else 'DRY_RUN'} objects={len(rows_by_key)}")
    for index, (key, key_rows) in enumerate(rows_by_key.items(), start=1):
        expected_digest = key_rows[0]["face_image_digest"]
        if any(row["face_image_digest"] != expected_digest for row in key_rows):
            raise RuntimeError("같은 객체 키에 서로 다른 DB digest가 있어 중단합니다.")

        should_purge = bool(
            key_rows[0]["profile_linked"]
            and key_rows[0]["profile_status"] == "purged"
        )
        main_meta = main_r2.head(key)
        face_meta = face_r2.head(key)

        if should_purge:
            if args.apply:
                if main_meta:
                    main_r2.delete(key)
                if face_meta:
                    face_r2.delete(key)
            action = "purged_residue_deleted" if (main_meta or face_meta) else "purged_already_absent"
        elif face_meta:
            _verified_bytes(face_r2, key, expected_digest)
            if main_meta:
                _verified_bytes(main_r2, key, expected_digest)
                if args.apply:
                    main_r2.delete(key)
                action = "verified_main_duplicate_removed"
            else:
                action = "already_private"
        elif main_meta:
            data = _verified_bytes(main_r2, key, expected_digest)
            if args.apply:
                mime = main_meta.get("mime") or _mime_for_key(key)
                face_r2.put_bytes(key, data, mime)
                _verified_bytes(face_r2, key, expected_digest)
                main_r2.delete(key)
            action = "migrated_to_private_bucket"
        else:
            action = "missing_from_both_buckets"

        counts[action] += 1
        statuses = ",".join(sorted({row["license_status"] for row in key_rows}))
        print(f"[{index}] refs={len(key_rows)} licenses={statuses} action={action}")

    print("summary " + " ".join(f"{name}={count}" for name, count in sorted(counts.items())))


if __name__ == "__main__":
    main()
