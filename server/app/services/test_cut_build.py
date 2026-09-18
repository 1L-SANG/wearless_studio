"""테스트컷 12장 자동 생성 — **꺼진 LoRA 로** 얼굴만 다시 그린다.

지금까지 테스트컷은 사람이 맥에서 만들어 관리자 화면에 올렸다. 그 일을 서버가 한다.

  기준 원본 4장(클로즈업 2 + 전신 2)  ×  보정 3종(prod · texture · soft50)  =  12장

기준 원본은 **고정 자산**이다(설정 FM_TEST_CUT_SOURCE_*). 사람이 바뀌어도 그대로여야 서로
비교가 된다. 컷 생성(gpt-image)은 하지 않는다 — 같은 원본에 얼굴만 세 번 다시 그린다.
그래서 생성 비용 0, 얼굴 렌더 12회다.

★ 이 경로는 **꺼진 LoRA**(status='ready', enabled=false)로 그린다. 학습이 막 끝나 아직 아무도
  못 본 얼굴을 보여 주는 게 이 화면의 목적이라, 셀러 경로(identity_source.resolve_enabled_lora)
  로는 애초에 찾을 수 없는 행이다. 반대로 **셀러 경로가 이 모듈을 부르면 안 된다** — 부르면
  등록자가 확인도 안 한 얼굴이 팔린다. 그래서
    · 이 모듈은 services/ 에 두고 agents/·workers/ 의 컷 생성 경로가 임포트하지 않는다,
    · test_test_cut_build.py 가 그 금지를 테스트로 잠근다.

파드가 없으면 **실패가 아니라 대기**다(FM_TEST_CUT_POD_WAIT_SECONDS, 기본 20분). 상한을 넘으면
실패로 남기고 관리자가 본다. 12장 중 일부만 되면 **된 것만 저장하고 'partial'** 로 남긴다 —
관리자가 다시 생성을 누를 수 있다.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from ..agents import face_identity
from ..r2 import ext_for_mime, model_test_cut_key

log = logging.getLogger("wearless.test_cut_build")

#: 기준 원본 종류별 장수. (보정, 종류) 조합마다 2장이라는 상한과 같은 숫자다.
SOURCES_PER_KIND = 2
#: 한 번에 만드는 장수 = 보정 3종 × (클로즈업 2 + 전신 2).
EXPECTED_CUTS = len(face_identity.SKIN_FINISH_CODES) * SOURCES_PER_KIND * 2
#: 저장 형식. 원본 컷이 무엇이든 얼굴 패스 결과는 PNG 로 돌아온다.
FALLBACK_MIME = "image/png"


class SourcesMissing(RuntimeError):
    """기준 원본 컷이 설정되지 않았거나 R2 에서 읽히지 않는다 — 시작도 하지 않는다."""



def source_keys(settings) -> dict[str, tuple[str, ...]]:
    """설정 → {kind: (r2 키, ...)}. 쉼표로 나누고 빈 칸은 버린다.

    키를 **여기서만** 읽는다 — 관리자가 R2 에서 원본을 갈아 끼우면 다음 생성부터 새 원본을 쓴다.
    """
    def parse(raw) -> tuple[str, ...]:
        return tuple(part.strip() for part in str(raw or "").split(",") if part.strip())

    return {
        "closeup": parse(getattr(settings, "fm_test_cut_source_closeup", "")),
        "fullbody": parse(getattr(settings, "fm_test_cut_source_fullbody", "")),
    }


def assert_sources(settings) -> dict[str, tuple[str, ...]]:
    """종류마다 정확히 2장인지. 아니면 시작하지 않는다 — 반쪽 묶음을 만들면 전송이 막힌다."""
    keys = source_keys(settings)
    for kind, values in keys.items():
        if len(values) != SOURCES_PER_KIND:
            raise SourcesMissing(
                f"{kind} 기준 원본이 {len(values)}장 — {SOURCES_PER_KIND}장이어야 한다 "
                f"(FM_TEST_CUT_SOURCE_{kind.upper()})")
    return keys


async def ready_lora_spec(conn, model_id: str) -> face_identity.FaceIdentitySpec | None:
    """이 모델의 **ready** LoRA 한 행 → spec. 켜져 있든 아니든 상관없다.

    identity_source.resolve_enabled_lora 와 일부러 다른 함수다. 저쪽은 `enabled` 를 걸어서
    셀러가 살 수 있는 얼굴만 찾고, 이쪽은 **아직 아무도 승인하지 않은** 얼굴을 찾는다.
    model_id 를 받아 그 모델의 행으로만 만든다 — 남의 행이 섞일 자리가 없다.

    피부 보정은 여기서 정하지 않는다(spec.skin_finish=None). 12장은 보정마다 따로 그리고,
    그 값은 render_variant 가 렌더 인자로 직접 넘긴다.
    """
    try:
        uuid.UUID(str(model_id))
    except (TypeError, ValueError):
        return None
    async with conn.cursor() as cur:
        await cur.execute(
            """select id::text as id, lora_r2_key, lora_sha256, bucket, trigger_token
                 from fm_model_loras
                where model_id = %s and status = 'ready'
                order by enabled desc, version desc
                limit 1""",
            (model_id,))
        row = await cur.fetchone()
    if not row or row.get("bucket") != "face" or not str(row.get("lora_r2_key") or "").strip():
        return None
    from ..agents import identity_source

    row = dict(row)
    row["face_backend_url"] = await identity_source._active_face_backend_url(conn, model_id)
    return face_identity.face_identity_from_lora_row(row)


def plan(keys: dict[str, tuple[str, ...]]) -> list[dict]:
    """만들 12장의 목록. 순서는 보정 → 종류 → 원본이라 sort 번호가 묶음대로 붙는다."""
    out: list[dict] = []
    for code in face_identity.SKIN_FINISH_CODES:
        for kind in ("closeup", "fullbody"):
            for index, key in enumerate(keys.get(kind, ())):
                out.append({"skin_finish": code, "kind": kind, "source_key": key,
                            "source_index": index, "sort": len(out)})
    return out


async def render_variant(settings, spec, image: bytes, mime: str, skin_finish: str,
                         *, render_url: str, model_dir=None) -> tuple[bytes, str] | None:
    """한 장. 얼굴 패스가 채택하면 (bytes, mime), 아니면 None.

    apply_face_pass 를 부르지 않는다 — 저쪽은 셀러 계약이라 실패를 예외로 올리고 파드 장애
    알림까지 울린다. 여기서는 12장 중 한 장이 안 된 것뿐이라 **None 이 정상적인 답**이다.
    """
    from dataclasses import replace

    live = replace(spec, backend_url=render_url) if render_url else spec
    backend = face_identity.resolve_backend(settings, live)
    if backend is None:
        return None
    seam_mode = str(getattr(settings, "face_seam_repair", "off") or "off").lower()
    seam_enabled = seam_mode in {"shadow", "on"}
    seam_kwargs = {"capture_seam_context": True, "capture_tone_context": getattr(settings, "face_tone_fix", "off") == "on"} if seam_enabled else {}
    result = await asyncio.to_thread(
        face_identity.run_face_pass, image, backend,
        token=live.token,
        model_dir=model_dir,
        mime=mime,
        references=live.references,
        crop_upscale=bool(getattr(settings, "face_crop_upscale", True)),
        crop_pad=bool(getattr(settings, "face_crop_pad", True)),
        mask_lock=bool(getattr(settings, "face_mask_lock", True)),
        skin_finish=skin_finish,
        skin_negative=str(getattr(settings, "face_skin_negative_prompt", "") or ""),
        **seam_kwargs,
    )
    if not result.applied:
        log.info("test_cut_build: %s 한 장이 채택되지 않았다 — %s",
                 skin_finish, result.meta.get("reason") or "gate_failed")
        return None
    try:
        if seam_enabled:
            from ..agents import face_seam_repair

            result = await face_seam_repair.repair_after_face_pass(settings, result)
    except Exception:  # noqa: BLE001 - test-cut generation keeps the face-pass contract.
        pass
    return result.image, result.mime


async def load_sources(r2_face, keys: dict[str, tuple[str, ...]]) -> dict[str, bytes]:
    """기준 원본 바이트. 한 장이라도 못 읽으면 시작하지 않는다 — 묶음이 반쪽이 된다."""
    out: dict[str, bytes] = {}
    for values in keys.values():
        for key in values:
            if key in out:
                continue
            try:
                out[key] = await asyncio.to_thread(r2_face.get_bytes, key)
            except Exception as exc:  # noqa: BLE001 — 키를 로그에 싣지 않는다
                raise SourcesMissing(
                    f"기준 원본을 읽지 못했다({type(exc).__name__}) — R2 키를 확인하라") from exc
    return out


async def store_cut(conn, r2_face, *, model_id: str, item: dict, data: bytes, mime: str) -> str:
    """R2 에 올리고 행 하나. 행이 안 들어가면 올린 객체를 지운다(고아 객체를 남기지 않는다)."""
    cut_id = str(uuid.uuid4())
    key = model_test_cut_key(model_id, cut_id, ext_for_mime(mime))
    await asyncio.to_thread(r2_face.put_bytes, key, data, mime)
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """insert into fm_model_test_cuts
                     (id, model_id, r2_key, mime, kind, sort, skin_finish_code)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (cut_id, model_id, key, mime, item["kind"], item["sort"], item["skin_finish"]))
        await conn.commit()
    except Exception:
        # 되돌리기·지우기가 또 실패해도 원래 예외를 삼키지 않는다 — 호출자가 그 한 장을 건너뛴다.
        with contextlib.suppress(Exception):
            await conn.rollback()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(r2_face.delete, key)
        raise
    return cut_id


async def clear_cuts(conn, r2_face, model_id: str) -> int:
    """다시 만들기 전에 **승인되지 않은** 컷을 비운다. 승인된 원본은 건드리지 않는다.

    sort 가 (model_id, sort) 유니크라 남겨 두면 다음 생성이 충돌한다.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "delete from fm_model_test_cuts where model_id = %s "
            "and coalesce(approved, false) = false returning r2_key",
            (model_id,))
        rows = await cur.fetchall()
    await conn.commit()
    for row in rows:
        try:
            await asyncio.to_thread(r2_face.delete, row["r2_key"])
        except Exception:  # noqa: BLE001 — 지우기 실패가 생성을 막으면 안 된다
            log.warning("test_cut_build: 옛 컷 객체 정리 실패", exc_info=True)
    return len(rows)


async def enqueue(conn, model_id: str, *, lora_id: str | None = None) -> str | None:
    """생성 한 건을 큐에 넣는다. 같은 모델이 이미 큐·진행 중이면 **아무것도 하지 않는다**.

    부분 유니크 인덱스(fm_test_cut_builds_one_queued_per_model)가 그 판정을 한다 — 여기서
    세어 보고 정하면 두 프로세스가 같은 순간에 "자리 있음" 을 볼 수 있다.
    테이블이 아직 없는 환경(마이그 미적용)에서도 죽지 않는다 — 학습·승인이 그것 때문에 멈추면 안 된다.
    """
    from psycopg.errors import UniqueViolation

    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """insert into fm_test_cut_builds (model_id, lora_id, requested)
                   values (%s, %s, %s) returning id::text as id""",
                (model_id, lora_id, EXPECTED_CUTS))
            row = await cur.fetchone()
        return (row or {}).get("id")
    except UniqueViolation:
        with contextlib.suppress(Exception):
            await conn.rollback()
        log.info("test_cut_build: model=%s 는 이미 큐에 있다 — 넘어간다", model_id)
        return None
    except Exception as exc:  # noqa: BLE001 — 테이블 부재·권한 등
        with contextlib.suppress(Exception):
            await conn.rollback()
        log.warning("test_cut_build enqueue 실패 (%s) — 건너뛴다", type(exc).__name__)
        return None
