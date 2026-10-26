"""Connection-scoped Cafe24 token rotation and version-conditional invalidation."""

from datetime import datetime, timedelta, timezone

import httpx

from .. import repo
from .cafe24_client import (
    AppRemoved, Cafe24Client, ConnectionChanged, MissingPermission, ReconnectRequired, classify_error,
)


def _check_connection(row, user_id, connection_id):
    if (not row or row["id"] != connection_id or row["user_id"] != user_id
            or row["mall"] != "cafe24" or row["status"] != "connected"):
        raise ConnectionChanged()
    if not row["refresh_expires_at"] or row["refresh_expires_at"] <= datetime.now(timezone.utc):
        raise ReconnectRequired()


async def ensure_cafe24_access_token(app, *, user_id: str, connection_id: str,
                                     rejected_token_expires_at: datetime | None = None) -> tuple[str, datetime]:
    settings = app.state.settings
    async with app.state.pool.connection() as conn:
        row = await repo.get_cafe24_connection_by_id(conn, user_id=user_id, connection_id=connection_id)
        _check_connection(row, user_id, connection_id)
        if (rejected_token_expires_at is None and row["token_expires_at"]
                and row["token_expires_at"] - datetime.now(timezone.utc) > timedelta(minutes=5)):
            row = await repo.get_cafe24_tokens(conn, user_id=user_id, connection_id=connection_id,
                                              kek=settings.mall_keys_kek)
            _check_connection(row, user_id, connection_id)
            if (row["access_token"] and row["token_expires_at"]
                    and row["token_expires_at"] - datetime.now(timezone.utc) > timedelta(minutes=5)):
                return row["access_token"], row["token_expires_at"]

        async with conn.cursor() as cur:
            await cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"mall:{user_id}:cafe24",))
        row = await repo.get_cafe24_connection_by_id(conn, user_id=user_id, connection_id=connection_id)
        _check_connection(row, user_id, connection_id)
        tokens = await repo.get_cafe24_tokens(conn, user_id=user_id, connection_id=connection_id,
                                             kek=settings.mall_keys_kek)
        if not tokens or not tokens["access_token"] or not tokens["refresh_token"]:
            raise ReconnectRequired()
        if ((rejected_token_expires_at is not None and row["token_expires_at"] != rejected_token_expires_at)
                or (rejected_token_expires_at is None and row["token_expires_at"]
                    and row["token_expires_at"] - datetime.now(timezone.utc) > timedelta(minutes=5))):
            return tokens["access_token"], row["token_expires_at"]

        client = Cafe24Client(row["shop_id"], client_id=settings.cafe24_client_id,
            client_secret=settings.cafe24_client_secret, api_version=settings.cafe24_api_version)
        try:
            rotated = await client.refresh(tokens["refresh_token"])
        except ReconnectRequired:
            await mark_cafe24_reconnect_required(conn, connection_id=connection_id,
                                                 rejected_token_expires_at=row["token_expires_at"])
            await conn.commit()
            raise ReconnectRequired() from None
        received_at = datetime.now(timezone.utc)
        version = received_at + timedelta(hours=2, seconds=-60)
        updated = await repo.update_cafe24_tokens(conn, user_id=user_id, connection_id=connection_id,
            access_token=rotated["access_token"], refresh_token=rotated["refresh_token"], token_expires_at=version,
            refresh_expires_at=received_at + timedelta(days=14, hours=-1), kek=settings.mall_keys_kek)
        if not updated:
            raise ConnectionChanged()
        await conn.commit()
        return rotated["access_token"], version


async def mark_cafe24_reconnect_required(conn, *, connection_id: str,
                                         rejected_token_expires_at: datetime) -> bool:
    async with conn.cursor() as cur:
        await cur.execute("select user_id::text from mall_connections where id = %s and mall = 'cafe24'",
                          (connection_id,))
        row = await cur.fetchone()
        if not row:
            return False
        await cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                          (f"mall:{row['user_id']}:cafe24",))
        await cur.execute("""update mall_connections set refresh_expires_at = now(),
            access_key_enc = null, secret_key_enc = null, updated_at = now()
            where id = %s and status = 'connected' and mall = 'cafe24' and token_expires_at = %s""",
            (connection_id, rejected_token_expires_at))
        return cur.rowcount == 1


async def _mark(app, connection_id, version):
    async with app.state.pool.connection() as conn:
        changed = await mark_cafe24_reconnect_required(conn, connection_id=connection_id,
                                                      rejected_token_expires_at=version)
        await conn.commit()
        return changed


async def call_with_token(app, *, user_id, connection_id, fn):
    token, version = await ensure_cafe24_access_token(app, user_id=user_id, connection_id=connection_id)
    for attempt in range(3):
        try:
            return await fn(token)
        except AppRemoved:
            await _mark(app, connection_id, version)
            raise AppRemoved() from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                if classify_error(exc) == "app_removed":
                    await _mark(app, connection_id, version)
                    raise AppRemoved() from None
                raise MissingPermission() from None
            if exc.response.status_code != 401:
                raise
        if attempt == 0:
            token, version = await ensure_cafe24_access_token(app, user_id=user_id, connection_id=connection_id,
                                                             rejected_token_expires_at=version)
        else:
            marked = await _mark(app, connection_id, version)
            if marked or attempt == 2:
                raise ReconnectRequired() from None
            token, version = await ensure_cafe24_access_token(app, user_id=user_id, connection_id=connection_id)
