"""Holder 장애로 남은 pending FaceLicense VC 발급과 활성화를 재시도한다.

현재 소유 모델의 ``vc_pending`` enrollment에 결속된 pending/null-VC 행만 대상으로
동일한 멱등 발급 함수와 원자적 활성화 finalizer를 재사용한다.

실행:
    cd server
    set -a; source .env.local; set +a
    .venv/bin/python -m scripts.retry_pending_face_vcs
    .venv/bin/python -m scripts.retry_pending_face_vcs --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from app.config import load_settings  # noqa: E402
from app.db import create_pool  # noqa: E402
from app.facemarket import finalize_issued_face_vc, issue_face_vc  # noqa: E402


async def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 VC 발급을 수행한다. 생략하면 대상만 확인한다.",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    if not settings.database_url:
        sys.exit("DATABASE_URL이 필요합니다.")
    if not settings.opendid_holder_url or not settings.opendid_holder_url.strip():
        sys.exit("OPENDID_HOLDER_URL이 필요합니다.")
    if (
        not settings.opendid_holder_hmac_secret
        or not settings.opendid_holder_hmac_secret.strip()
    ):
        sys.exit("OPENDID_HOLDER_HMAC_SECRET이 필요합니다.")

    pool = create_pool(settings.database_url)
    await pool.open()
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, pool=pool))

    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select l.id::text as id,
                           l.model_id::text as model_id,
                           m.user_id::text as user_id,
                           e.id::text as enrollment_id,
                           l.allowed_use,
                           l.forbidden_use,
                           l.unit_price,
                           l.license_valid_until,
                           l.face_image_digest
                      from fm_licenses l
                      join fm_models m on m.id = l.model_id
                      join fm_biometric_enrollments e on e.id = l.enrollment_id
                     where l.status = 'pending'
                       and l.vc_id is null
                       and m.user_id is not null
                       and e.status = 'vc_pending'
                       and e.user_id = m.user_id
                       and e.model_id = m.id
                     order by l.created_at
                    """
                )
                pending = await cur.fetchall()

        if not args.apply:
            print(f"mode=DRY_RUN pending={len(pending)} issued=0 failed=0")
            return

        failed = 0
        issued_count = 0
        for row in pending:
            try:
                issued = await issue_face_vc(
                    app,
                    license_id=row["id"],
                    model_id=row["model_id"],
                    allowed=row["allowed_use"],
                    forbidden=row["forbidden_use"],
                    unit_price=row["unit_price"],
                    valid_until=row["license_valid_until"],
                    digest=row["face_image_digest"],
                )
                await finalize_issued_face_vc(
                    pool.connection,
                    user_id=row["user_id"],
                    license_id=row["id"],
                    model_id=row["model_id"],
                    enrollment_id=row["enrollment_id"],
                    issued=issued,
                )
                issued_count += 1
            except Exception:
                failed += 1

        print(
            f"mode=APPLY pending={len(pending)} "
            f"issued={issued_count} failed={failed}"
        )

        if failed:
            sys.exit(f"VC 재발급 실패: {failed}/{len(pending)}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
