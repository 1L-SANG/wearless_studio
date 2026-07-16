"""Holder 장애로 놓친 활성 FaceLicense VC 발급을 재시도한다.

``fm_licenses.status='active' and vc_id is null``인 행만 대상으로 기존 발급 함수를
재사용한다. 개인화 철회 뒤에도 FaceMarket 라이선스는 별도 서비스 관계로 보존한다.

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
from app.facemarket import _issue_face_vc  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 VC 발급을 수행한다. 생략하면 대상만 확인한다.",
    )
    args = parser.parse_args()

    settings = load_settings()
    if not settings.database_url:
        sys.exit("DATABASE_URL이 필요합니다.")
    if not settings.opendid_holder_url:
        sys.exit("OPENDID_HOLDER_URL이 필요합니다.")

    pool = create_pool(settings.database_url)
    await pool.open()
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, pool=pool))

    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select id::text as id,
                           model_id::text as model_id,
                           allowed_use,
                           forbidden_use,
                           unit_price,
                           license_valid_until,
                           face_image_digest
                      from fm_licenses
                     where status = 'active' and vc_id is null
                     order by created_at
                    """
                )
                pending = await cur.fetchall()

        print(f"mode={'APPLY' if args.apply else 'DRY_RUN'} pending={len(pending)}")
        if not args.apply:
            return

        failed = 0
        for index, row in enumerate(pending, start=1):
            await _issue_face_vc(
                app,
                license_id=row["id"],
                model_id=row["model_id"],
                allowed=row["allowed_use"],
                forbidden=row["forbidden_use"],
                unit_price=row["unit_price"],
                valid_until=row["license_valid_until"],
                digest=row["face_image_digest"],
            )
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select vc_id from fm_licenses where id = %s", (row["id"],)
                    )
                    issued = bool((await cur.fetchone() or {}).get("vc_id"))
            print(f"[{index}] {'issued' if issued else 'failed'}")
            failed += int(not issued)

        if failed:
            sys.exit(f"VC 재발급 실패: {failed}/{len(pending)}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
