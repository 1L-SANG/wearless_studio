"""상세 컷 체크포인트 — 이미 값을 치른 베이스컷·완성 컷을 다음 시도가 이어 쓴다(2026-09-23).

오너 원칙(2026-09-23): "생성 비용을 최대한 아껴야 한다. 이미 만든 베이스컷이 있으면 그대로 쓴다."
그날 운영 로그: ECS 배포가 상세 잡을 죽여(worker_shutdown, exit 137) 끝난 컷 $1.53·$0.53 어치를
버렸고, 셀러의 "다시 시도"는 전부 처음부터 다시 그렸다. 얼굴 패스·각도 교체가 실패해도 그 앞의
gpt-image 베이스가 어디에도 남지 않았다(감사 1·2·9번).

두 단계를 남긴다:
  base   provider 가 그린 원본(후처리 전). 후처리(얼굴 패스·각도 교체)만 다시 돌리면 된다.
  final  QC·보정까지 끝난 채택본. 새 잡은 provider·QC 를 한 번도 부르지 않고 그대로 싣는다.

어디에·얼마나 두나:
  · R2 키 = users/{user}/projects/{project}/ai/{job}/ckpt/{digest}/{stage}.bin — 원 잡의 ai/ 접두사
    아래다. 그래서 동의 철회 파기(biometric_purge, ai/{job}/ 접두사 스윕)가 실존 모델 컷의
    체크포인트까지 같이 지운다. 캐시는 항상 private, no-store.
  · 객체 하나에 헤더(JSON)와 이미지 바이트를 같이 담는다 — 메타와 그림이 따로 놀 수 없다.
  · TTL 24시간 = 정리 표식(ai_output_cleanup_intents)의 not_before. 리클레이머가 그 뒤에 지운다.
    실패·중단된 잡의 체크포인트는 바로 지우지 않는다(다음 시도가 이어 쓴다).
  · 바로 지우는 경우: 잡 성공(이미 셀러에게 나갔다) · 라이선스 철회 · 완성 컷이 생겨 베이스가
    필요 없어짐 · 그림 탓 실패로 베이스를 버림. 리클레이머는 한 시간에 200건만 집으므로
    성공 경로까지 거기 맡기면 밀린다 — 거기엔 실패한 잡의 남은 것만 간다.

언제 이어 쓰나(보수적으로 — 애매하면 오늘처럼 새로 그린다):
  · 같은 사용자·같은 프로젝트의 **실패한(error)** 상세 잡이 남긴 것만(repo.list_detail_cut_checkpoints).
    성공한 잡의 컷은 이미 나갔다 — 그걸 다시 집으면 같은 그림을 두 번 판다.
  · digest 가 같아야 한다: provider 요청 지문(cut_generator.base_fingerprint — 정규화 스펙·모델·
    프롬프트·참고 이미지 바이트) + 콘티 블록 id + 신원(REAL 모델·라이선스·등록·LoRA / VIRTUAL /
    NONE) + user·project + (final 만) 후처리·QC 설정. 헤더의 user·project·digest·단계·바이트 해시가 하나라도 안 맞거나
    R2 읽기가 실패하면 쓰지 않는다.
  · 크레딧은 그대로다 — 이어 받은 컷도 셀러에게 나가는 컷 1장이다(provider 호출만 빠진다).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import repo
from ..agents import cut_generator, face_angle_swap, face_identity
from ..r2 import PRIVATE_NO_STORE

log = logging.getLogger("wearless.cut_checkpoints")

#: 실패·중단된 잡의 체크포인트를 남겨 두는 시간(오너 2026-09-23: 24시간).
TTL_SECONDS = 24 * 3600
#: 헤더 형식 버전. 읽는 쪽이 모르는 버전이면 쓰지 않는다.
FORMAT_VERSION = 1
#: 완성 컷 지문 규칙 버전 — 후처리·QC 의 뜻이 바뀌어 옛 완성 컷을 실으면 안 될 때 올린다.
FINAL_RECIPE_VERSION = "cut-final-v1"
STAGE_BASE = "base"
STAGE_FINAL = "final"
_STAGES = (STAGE_BASE, STAGE_FINAL)
_MAGIC = b"WLCKPT1\n"
_MAX_HEADER_BYTES = 1 << 20
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
#: 완성 컷을 바꾸는 설정 — 배포로 이 값이 바뀌면 옛 완성 컷을 싣지 않는다(베이스는 그대로 쓴다).
_FINAL_RECIPE_SETTINGS = (
    "face_identity_enabled",
    "face_angle_swap_enabled",
    "face_angle_seed",
    "face_crop_upscale",
    "face_crop_pad",
    "face_mask_lock",
    "face_skin_negative_prompt",
    "garment_qc_mode",
    "cut_output_qc_mode",
    "bg_scene_qc_attempts",
    "real_horizon_neck_repair_enabled",
)
#: 이 사유로 후처리가 실패했으면 **베이스는 멀쩡하다**(인프라 탓) — 다음 시도가 후처리만 다시 한다.
#: 그 밖의 사유(게이트 탈락·옆얼굴 건너뜀·톤 불일치·사진 방향 불일치 등)는 그림 탓이라 같은
#: 베이스로 다시 돌려도 같은 답이다 — 베이스를 버려 다음 시도가 새로 그리게 한다.
_FACE_INFRA_REASONS = frozenset({"pod_not_ready", "backend_error"})
_ANGLE_INFRA_REASONS = frozenset({"backend_error"})


def checkpoint_key(user_id, project_id, job_id, digest: str, stage: str) -> str:
    return f"users/{user_id}/projects/{project_id}/ai/{job_id}/ckpt/{digest}/{stage}.bin"


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def key_pattern(user_id, project_id) -> str:
    """이 프로젝트의 체크포인트 키 LIKE 패턴(escape '\\')."""
    return _like_escape(f"users/{user_id}/projects/{project_id}/ai/") + "%/ckpt/%"


def parse_key(key, *, user_id, project_id, job_id) -> tuple[str, str] | None:
    """체크포인트 키 → (digest, stage). 이 프로젝트·그 잡의 키 모양이 아니면 None."""
    if not isinstance(key, str) or not job_id:
        return None
    prefix = f"users/{user_id}/projects/{project_id}/ai/{job_id}/ckpt/"
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    digest, _, name = rest.partition("/")
    stage = name[:-len(".bin")] if name.endswith(".bin") else ""
    if not _DIGEST_RE.match(digest) or stage not in _STAGES:
        return None
    return digest, stage


def pack(header: dict, payload: bytes) -> bytes:
    raw = json.dumps(header, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return _MAGIC + len(raw).to_bytes(4, "big") + raw + payload


def unpack(blob) -> tuple[dict, bytes] | None:
    """pack 의 역. 모양이 하나라도 어긋나면 None(=쓰지 않는다)."""
    if not isinstance(blob, (bytes, bytearray)) or not blob.startswith(_MAGIC):
        return None
    start = len(_MAGIC)
    if len(blob) < start + 4:
        return None
    size = int.from_bytes(blob[start:start + 4], "big")
    if size <= 0 or size > _MAX_HEADER_BYTES or len(blob) < start + 4 + size:
        return None
    try:
        header = json.loads(bytes(blob[start + 4:start + 4 + size]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(header, dict):
        return None
    return header, bytes(blob[start + 4 + size:])


def _digest(material: dict) -> str:
    raw = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def job_identity(source: str | None, *, selected_model_id=None, license_row=None,
                 lora_spec=None, angle_photos=None) -> str:
    """잡 단위 신원 — REAL 이면 모델·라이선스·등록·LoRA·각도 사진까지, 아니면 소스와 선택 id.

    이게 digest 에 들어가므로 REAL 과 VIRTUAL, 다른 라이선스, 다시 등록한 얼굴 사이에서는 절대
    이어 쓰지 않는다. 사진 바이트는 해시만 남긴다(헤더·로그에 원본이 들어가지 않는다)."""
    material: dict = {"source": str(source or "NONE"), "model": str(selected_model_id or "")}
    if source == "REAL":
        row = license_row if isinstance(license_row, dict) else {}
        material.update({
            "license": str(row.get("id") or ""),
            "licensedModel": str(row.get("model_id") or ""),
            "enrollment": str(row.get("current_enrollment_id") or ""),
            "evidence": str(row.get("match_policy_version") or ""),
            "lora": getattr(lora_spec, "lora_path", None),
            "loraSha": getattr(lora_spec, "sha256", None),
            "skinFinish": getattr(lora_spec, "skin_finish", None),
            "angle": {
                str(slot): hashlib.sha256(data).hexdigest()
                for slot, data in sorted((angle_photos or {}).items())
                if isinstance(data, (bytes, bytearray)) and data
            },
        })
    return _digest(material)


def _final_recipe(settings) -> dict:
    return {
        "v": FINAL_RECIPE_VERSION,
        "angle": face_angle_swap.PROMPT_VERSION,
        "settings": {name: getattr(settings, name, None) for name in _FINAL_RECIPE_SETTINGS},
    }


def base_survives(exc: BaseException) -> bool:
    """후처리 실패 뒤에도 베이스를 남길까 — 인프라 탓이면 True, 그림 탓·모르는 사유면 False."""
    if isinstance(exc, face_identity.FacePassUnavailable):
        return str(exc) in _FACE_INFRA_REASONS
    if isinstance(exc, face_angle_swap.AngleSwapUnavailable):
        return exc.reason in _ANGLE_INFRA_REASONS
    return False


def _valid_final_meta(meta) -> bool:
    if not isinstance(meta, dict):
        return False
    return (
        (meta.get("garmentQc") is None or isinstance(meta.get("garmentQc"), dict))
        and (meta.get("cutQc") is None or isinstance(meta.get("cutQc"), dict))
        and isinstance(meta.get("warnings"), list)
        and all(isinstance(w, dict) for w in meta["warnings"])
        and (meta.get("neckRepair") is None or isinstance(meta.get("neckRepair"), dict))
        and isinstance(meta.get("facePass"), dict)
    )


@dataclass
class Loaded:
    """R2 에서 읽어 검증을 통과한 체크포인트 1개."""

    image: bytes
    mime: str
    meta: dict
    key: str


class PersistedBase(cut_generator.BaseCheckpoint):
    """generate() 가 받는 베이스 자리 — 새 베이스를 받으면 R2 에 남긴다."""

    def __init__(self, cut: "CutCheckpoint", image: tuple[bytes, str] | None = None,
                 key: str | None = None):
        super().__init__(image)
        self._cut = cut
        self.key = key

    async def keep(self, image: bytes, mime: str) -> None:
        self.image = (image, mime)
        self.key = await self._cut.store.save(
            self._cut.base_digest, STAGE_BASE, image, mime, {}, block_id=self._cut.block_id)

    async def discard(self) -> None:
        """이 베이스는 다시 쓰지 않는다 — 메모리에서 빼고 R2 에서도 지운다."""
        self.image = None
        if self.key:
            await self._cut.store.discard([self.key])
        self.key = None


class CutCheckpoint:
    """컷 하나의 체크포인트 — 워커 _one_impl 이 쓴다."""

    def __init__(self, store: "CutCheckpointStore", base_digest: str, final_digest: str,
                 block_id):
        self.store = store
        self.base_digest = base_digest
        self.final_digest = final_digest
        self.block_id = block_id
        self.final: Loaded | None = None
        self.base = PersistedBase(self)
        #: 이 컷의 베이스를 앞선 잡에서 이어 받았는가(로그·이벤트용).
        self.base_reused = False

    async def after_failure(self, exc: BaseException) -> None:
        """generate() 가 실패했다. 베이스가 있었다면(=후처리에서 실패) 남길지 버릴지 정한다."""
        if self.base.image is None:
            return        # 베이스 전에 실패(provider 오류) — 남길 것도 버릴 것도 없다
        if base_survives(exc):
            log.info("cut checkpoint base kept after post-processing failure %r job %s block %s",
                     exc, self.store.job_id, self.block_id)
            return
        log.info("cut checkpoint base dropped after %r job %s block %s",
                 exc, self.store.job_id, self.block_id)
        await self.base.discard()

    async def save_final(self, image: bytes, mime: str, meta: dict) -> bool:
        key = await self.store.save(self.final_digest, STAGE_FINAL, image, mime, meta,
                                    block_id=self.block_id)
        if key is None:
            return False
        if self.base.key:
            # 완성 컷이 남았으면 베이스는 더 쓸 일이 없다 — 지금 지운다.
            await self.store.discard([self.base.key])
            self.base.key = None
        return True


class CutCheckpointStore:
    """잡 하나의 체크포인트 창구. open() 이 None 이면 이 잡은 오늘처럼 이어하기 없이 돈다."""

    def __init__(self, app, job: dict, *, identity: str, index: dict, auto_named: bool = False):
        self._pool = app.state.pool
        self._r2 = app.state.r2
        self.job_id = str(job["id"])
        self.user_id = str(job["user_id"])
        self.project_id = str(job["project_id"])
        self.identity = identity
        #: 상품명을 이 잡이 지었는가(셀러가 비워 둠). 그 이름은 LLM 이 매번 다르게 짓고 성공해야
        #: 저장된다 — 지문에 넣으면 "다시 시도"마다 달라져 이어 쓰기가 한 번도 안 맞는다.
        self.auto_named = auto_named
        self._index: dict[tuple[str, str], list[dict]] = index
        #: 이 잡이 만든 것 / 앞선 잡에서 이어 받은 것 — 성공·라이선스 철회면 지운다(discard_all).
        self.created: list[dict] = []
        self.reused: list[dict] = []

    @classmethod
    async def open(cls, app, job: dict, *, identity: str,
                   auto_named: bool = False) -> "CutCheckpointStore | None":
        settings = app.state.settings
        if not getattr(settings, "detail_cut_checkpoint_enabled", True):
            return None
        r2 = getattr(app.state, "r2", None)
        if r2 is None or not all(
            callable(getattr(r2, name, None)) for name in ("get_bytes", "put_bytes", "delete")
        ):
            return None
        user_id, project_id = str(job["user_id"]), str(job["project_id"])
        try:
            async with app.state.pool.connection() as conn:
                rows = await repo.list_detail_cut_checkpoints(
                    conn, user_id=user_id, project_id=project_id,
                    key_pattern=key_pattern(user_id, project_id))
        except Exception as e:  # noqa: BLE001 — 조회 실패는 이어하기만 끈다(생성은 오늘처럼)
            log.warning("cut checkpoints unavailable job %s: %r", job["id"], e)
            return None
        if rows is None:
            return None
        index: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            parsed = parse_key(row.get("r2_key"), user_id=user_id, project_id=project_id,
                               job_id=row.get("job_id"))
            if parsed is not None:
                index.setdefault(parsed, []).append(row)
        if index:
            log.info("cut checkpoints found for job %s: %d", job["id"], sum(map(len, index.values())))
        return cls(app, job, identity=identity, index=index, auto_named=auto_named)

    async def for_cut(self, block: dict, settings, product: dict, images, generate_kwargs: dict,
                      *, real_identity_attached: bool) -> CutCheckpoint | None:
        """컷 하나의 digest 를 정하고, 남아 있는 완성 컷·베이스를 찾아 둔다."""
        # 잡이 지은 상품명은 지문에서 뺀다(auto_named 주석). 셀러가 붙인 이름은 그대로 들어간다.
        fingerprint_product = {**product, "name": ""} if self.auto_named else product
        try:
            fingerprint = cut_generator.base_fingerprint(
                settings, block, fingerprint_product, images, **generate_kwargs)
        except Exception as e:  # noqa: BLE001 — 계약 위반 등은 generate() 가 기존대로 처리한다
            log.info("cut checkpoint skipped (no fingerprint) job %s block %s: %r",
                     self.job_id, block.get("id"), e)
            return None
        base_digest = _digest({
            "fingerprint": fingerprint,
            # 블록도 넣는다 — 같은 설정의 블록을 일부러 따로 그리는 경로(공간 세트 컷은 복제 접기에서
            # 빠진다)에서 한 블록의 그림이 다른 블록 자리로 새지 않게. 콘티 블록 id 는 재시도에도 같다.
            "blockId": str(block.get("id") or ""),
            "identity": self.identity,
            "realIdentityAttached": bool(real_identity_attached),
            "userId": self.user_id,
            "projectId": self.project_id,
        })
        final_digest = _digest({"base": base_digest, "recipe": _final_recipe(settings)})
        cut = CutCheckpoint(self, base_digest, final_digest, block.get("id"))
        cut.final = await self._load(final_digest, STAGE_FINAL)
        if cut.final is None:
            base = await self._load(base_digest, STAGE_BASE)
            if base is not None:
                cut.base = PersistedBase(cut, image=(base.image, base.mime), key=base.key)
                cut.base_reused = True
        return cut

    async def _load(self, digest: str, stage: str) -> Loaded | None:
        for row in self._index.get((digest, stage), ()):
            key = row["r2_key"]
            try:
                blob = await asyncio.to_thread(self._r2.get_bytes, key)
            except Exception as e:  # noqa: BLE001 — 못 읽으면 쓰지 않는다
                log.warning("cut checkpoint unreadable (%s) job %s: %s",
                            stage, self.job_id, type(e).__name__)
                continue
            loaded = self._validate(unpack(blob), digest, stage, key)
            if loaded is None:
                log.warning("cut checkpoint rejected (%s) job %s key %s", stage, self.job_id, key)
                continue
            try:
                async with self._pool.connection() as conn:
                    await repo.set_ai_checkpoint_expiry(
                        conn, [key], ttl_seconds=TTL_SECONDS, extend_only=True)
                    await conn.commit()
            except Exception as e:  # noqa: BLE001 — 연장 실패는 재사용을 막지 않는다
                log.warning("cut checkpoint ttl extend failed job %s: %r", self.job_id, e)
            self.reused.append({"key": key, "cleanup_intent_id": row.get("id")})
            return loaded
        return None

    def _validate(self, parsed, digest: str, stage: str, key: str) -> Loaded | None:
        if parsed is None:
            return None
        header, payload = parsed
        meta = header.get("meta")
        if (
            header.get("v") != FORMAT_VERSION
            or header.get("stage") != stage
            or header.get("digest") != digest
            or str(header.get("userId")) != self.user_id
            or str(header.get("projectId")) != self.project_id
            or header.get("mime") not in _IMAGE_MIMES
            or not payload
            or header.get("sha256") != hashlib.sha256(payload).hexdigest()
            or not isinstance(meta, dict)
        ):
            return None
        if stage == STAGE_FINAL and not _valid_final_meta(meta):
            return None
        return Loaded(payload, header["mime"], meta, key)

    async def save(self, digest: str, stage: str, image: bytes, mime: str, meta: dict,
                   *, block_id=None) -> str | None:
        """체크포인트를 남긴다. 실패해도 예외 없이 None — 저장 실패가 컷을 막지 않는다."""
        if mime not in _IMAGE_MIMES or not image:
            return None
        key = checkpoint_key(self.user_id, self.project_id, self.job_id, digest, stage)
        header = {
            "v": FORMAT_VERSION,
            "stage": stage,
            "digest": digest,
            "userId": self.user_id,
            "projectId": self.project_id,
            "jobId": self.job_id,
            "blockId": block_id,
            "mime": mime,
            "sha256": hashlib.sha256(image).hexdigest(),
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "meta": meta or {},
        }
        try:
            blob = pack(header, image)
            # 표식 먼저, 객체는 그 다음(create_ai_output_cleanup_intent 와 같은 순서) — 올린 뒤
            # 죽어도 리클레이머가 TTL 뒤에 지운다. 표식이 없으면 올리지 않는다.
            async with self._pool.connection() as conn:
                intent_id = await repo.create_ai_checkpoint_intent(
                    conn, job_id=self.job_id, r2_key=key, ttl_seconds=TTL_SECONDS)
                await conn.commit()
            if not intent_id:
                return None
            await asyncio.to_thread(
                self._r2.put_bytes, key, blob, "application/octet-stream", PRIVATE_NO_STORE)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("cut checkpoint save failed (%s) job %s block %s: %r",
                        stage, self.job_id, block_id, e)
            return None
        self.created.append({"key": key, "cleanup_intent_id": intent_id})
        log.info("cut checkpoint saved: %s job %s block %s", stage, self.job_id, block_id)
        return key

    async def _expire(self, keys: list[str]) -> None:
        """지금 만료 — 조회에서 빠지고, 원 잡이 끝나면 다음 리클레이머 주기에 지워진다(삭제 실패 대비)."""
        keys = [key for key in keys if key]
        if not keys:
            return
        try:
            async with self._pool.connection() as conn:
                await repo.set_ai_checkpoint_expiry(conn, keys, ttl_seconds=0)
                await conn.commit()
        except Exception as e:  # noqa: BLE001 — 만료 실패는 TTL 이 대신 지운다
            log.warning("cut checkpoint expire failed job %s: %r", self.job_id, e)

    async def _delete_one(self, key: str) -> None:
        """R2 에서 지우고, 없어진 걸 확인한 뒤에만 정리 표식을 지운다(워커의 산출물 삭제와 같은 순서).
        못 지우면 표식을 만료시켜 리클레이머에게 넘긴다. 예외를 올리지 않는다."""
        try:
            await asyncio.to_thread(self._r2.delete, key)
            head = getattr(self._r2, "head", None)
            if head is not None and await asyncio.to_thread(head, key) is not None:
                raise RuntimeError("R2 object remained after delete")
        except Exception as e:  # noqa: BLE001
            log.warning("cut checkpoint delete deferred job %s: %s", self.job_id, type(e).__name__)
            await self._expire([key])
            return
        intent_ids = [c.get("cleanup_intent_id") for c in [*self.created, *self.reused]
                      if c["key"] == key and c.get("cleanup_intent_id")]
        # 지운 것은 목록에서 뺀다 — 성공 정리(discard_all)가 같은 키를 두 번 지우지 않게.
        self.created = [c for c in self.created if c["key"] != key]
        self.reused = [c for c in self.reused if c["key"] != key]
        try:
            async with self._pool.connection() as conn:
                for intent_id in intent_ids:
                    await repo.clear_ai_output_cleanup_intent(conn, intent_id)
                await conn.commit()
        except Exception as e:  # noqa: BLE001 — 표식이 남으면 리클레이머가 빈 키를 치운다
            log.warning("cut checkpoint intent clear failed job %s: %r", self.job_id, e)

    async def discard(self, keys: list[str]) -> None:
        """이 체크포인트들은 다시 쓰지 않는다 — 지금 지운다(동시 4개까지, 커넥션 풀을 막지 않게)."""
        keys = list(dict.fromkeys(key for key in keys if key))
        if not keys:
            return
        gate = asyncio.Semaphore(4)

        async def one(key: str) -> None:
            async with gate:
                await self._delete_one(key)

        await asyncio.gather(*(one(key) for key in keys))

    async def discard_all(self) -> None:
        """이 잡이 만든 것과 이어 받은 것을 전부 지운다 — 잡 성공(이미 셀러에게 나갔다)이나
        라이선스 철회(그 얼굴로 만든 것은 남기지 않는다) 때."""
        await self.discard([c["key"] for c in [*self.created, *self.reused]])
