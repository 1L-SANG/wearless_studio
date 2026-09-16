"""학습 결과 → fm_model_loras 한 행. R2 업로드 + upsert, **멱등**.

정본은 scripts/seed_model_lora.py 였다. 학습이 자동으로 돌기 시작하면서 같은 일을 두 곳이 하게
돼(손으로 한 번, 잡이 한 번) 여기로 뺐다 — 스크립트는 이제 이 모듈을 부르는 껍데기다.

행은 **status='ready', enabled=false** 로 넣는다. 켜는 건 등록자가 테스트컷을 확인·승인하는
트랜잭션 하나뿐이다(facemarket_admin_models._enable_ready_lora). 학습이 끝났다고 얼굴이
바로 팔리면, 본인이 자기 얼굴을 한 번도 못 본 채로 상품이 나간다.

머리·얼굴형 값은 **등록자의 현재 모습이 아니라 이 LoRA 가 학습한 모습**이다
(마이그레이션 20260910100000 주석). 학습셋이 그때 그 사진이라 나중에 머리를 잘라도 이 값은 안 바뀐다.
"""
from __future__ import annotations

import hashlib
import logging

log = logging.getLogger("wearless.model_lora")

MIME = "application/octet-stream"


def lora_key(model_id: str, version: int, filename: str) -> str:
    """R2 키는 (model_id, version, 파일명)이 정한다 — 같은 내용이면 같은 자리라 멱등이다."""
    return f"facemarket/models/{model_id}/loras/v{version}_{filename}"


def upload_weights(r2_face, key: str, data: bytes) -> tuple[str, bool]:
    """(sha256, 올렸는가). 같은 크기의 객체가 이미 있으면 다시 안 올린다."""
    digest = hashlib.sha256(data).hexdigest()
    head = r2_face.head(key)
    if head is not None and int(head.get("size") or 0) == len(data):
        return digest, False
    r2_face.put_bytes(key, data, MIME)
    return digest, True


async def upsert_row(conn, *, model_id: str, version: int, key: str, sha256: str,
                     trigger_token: str, base_model: str, trained_steps: int | None,
                     source_enrollment_id: str | None, physique: dict | None,
                     metrics: dict | None, enable: bool) -> str | None:
    """fm_model_loras upsert. unique(model_id, version) 기준이라 두 번 돌려도 행이 안 는다.

    enable=True 는 **이미 verified 인 모델의 재학습 교체**에만 쓴다. 새 학습은 False 다 —
    partial unique index(model_id) where enabled 가 모델당 하나만 허용하므로, 켤 때는
    다른 버전을 **먼저** 꺼야 insert 가 통과한다.
    """
    from psycopg.types.json import Json

    physique = physique or {}
    async with conn.cursor() as cur:
        if enable:
            await cur.execute(
                "update fm_model_loras set enabled = false "
                "where model_id = %s and version <> %s and enabled",
                (model_id, version))
        await cur.execute(
            """insert into fm_model_loras
                 (model_id, version, status, enabled, base_model, lora_r2_key, lora_sha256, bucket,
                  trigger_token, hair_length, hair_color, hair_texture, face_shape, jaw_line,
                  trained_steps, source_enrollment_id, metrics)
               values (%s, %s, 'ready', %s, %s, %s, %s, 'face', %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (model_id, version) do update set
                 status = 'ready', enabled = excluded.enabled, base_model = excluded.base_model,
                 lora_r2_key = excluded.lora_r2_key, lora_sha256 = excluded.lora_sha256,
                 trigger_token = excluded.trigger_token,
                 hair_length = excluded.hair_length, hair_color = excluded.hair_color,
                 hair_texture = excluded.hair_texture, face_shape = excluded.face_shape,
                 jaw_line = excluded.jaw_line, trained_steps = excluded.trained_steps,
                 source_enrollment_id = excluded.source_enrollment_id, metrics = excluded.metrics
               returning id::text as id""",
            (model_id, version, enable, base_model, key, sha256, trigger_token,
             physique.get("hair_length"), physique.get("hair_color"), physique.get("hair_texture"),
             physique.get("face_shape"), physique.get("jaw_line"),
             trained_steps, source_enrollment_id,
             Json(metrics) if metrics is not None else None))
        row = await cur.fetchone()
    return (row or {}).get("id")


async def next_version(conn, model_id: str) -> int:
    """이 모델의 다음 버전 번호. 재학습이 이전 행을 덮지 않게 한다."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select coalesce(max(version), 0) + 1 as next from fm_model_loras where model_id = %s",
            (model_id,))
        row = await cur.fetchone()
    return int((row or {}).get("next") or 1)


async def register(conn, r2_face, *, model_id: str, weights: bytes, filename: str,
                   trigger_token: str, base_model: str, trained_steps: int | None = None,
                   source_enrollment_id: str | None = None, physique: dict | None = None,
                   metrics: dict | None = None, version: int | None = None,
                   enable: bool = False, build_test_cuts: bool = True) -> dict:
    """가중치 업로드 + 행 등록. 반환은 **키와 숫자만** — 바이트도 URL 도 안 싣는다.

    ready 행이 붙는 순간이 곧 테스트컷 12장을 만들 시점이다(대표 결정 2026-09-16) — 그래서
    같은 트랜잭션에서 생성 큐에 한 건 넣는다. 손으로 seed 하든 학습이 자동으로 끝나든 자리는
    여기 하나다. build_test_cuts=False 는 백필·복구 스크립트용 탈출구다.
    """
    resolved = version if version is not None else await next_version(conn, model_id)
    key = lora_key(model_id, resolved, filename)
    sha256, uploaded = upload_weights(r2_face, key, weights)
    row_id = await upsert_row(
        conn, model_id=model_id, version=resolved, key=key, sha256=sha256,
        trigger_token=trigger_token, base_model=base_model, trained_steps=trained_steps,
        source_enrollment_id=source_enrollment_id, physique=physique, metrics=metrics,
        enable=enable)
    build_id = None
    if build_test_cuts:
        from . import test_cut_build

        build_id = await test_cut_build.enqueue(conn, model_id, lora_id=row_id)
    log.info("model_lora registered model=%s v%d bytes=%d sha12=%s uploaded=%s enabled=%s build=%s",
             model_id, resolved, len(weights), sha256[:12], uploaded, enable, build_id)
    return {"id": row_id, "version": resolved, "key": key, "sha256": sha256,
            "bytes": len(weights), "uploaded": uploaded, "enabled": enable,
            "test_cut_build_id": build_id}
