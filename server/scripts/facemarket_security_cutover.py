"""Read-only FaceMarket initial cutover inventory CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from types import SimpleNamespace

from psycopg_pool import AsyncConnectionPool

from app.config import load_settings
from app.facemarket_cutover import CutoverBlocked, build_initial_cutover_manifest
from app.r2 import R2Client
from app.services.biometric_purge import PurgeIncomplete


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only FaceMarket cutover inventory")
    parser.add_argument("--dry-run", action="store_true", help="print PII-free inventory summary")
    return parser


async def _run_dry_run() -> dict:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("database_url_required")
    pool = AsyncConnectionPool(settings.database_url, open=False)
    await pool.open()
    try:
        app = SimpleNamespace(
            state=SimpleNamespace(
                pool=pool,
                r2=R2Client(settings),
                r2_face=R2Client(settings, bucket=settings.r2_face_bucket or settings.r2_bucket),
            )
        )
        return (await build_initial_cutover_manifest(app)).public_summary()
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> None:
    _parser().parse_args(argv)
    try:
        print(json.dumps(asyncio.run(_run_dry_run()), ensure_ascii=False, sort_keys=True))
    except (CutoverBlocked, PurgeIncomplete) as exc:
        print(exc.code, file=sys.stderr)
        raise SystemExit(2) from None
    except RuntimeError as exc:
        code = str(exc)
        if code not in {"database_url_required"}:
            code = "internal_error"
        print(code, file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print("internal_error", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
