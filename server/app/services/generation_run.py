"""Generation Run 기록 — provider 호출 1건을 재현 가능한 행으로 남긴다 (Phase 1).

이 모듈은 **관측기**다. 생성 결과에 개입하지 않고, 자신의 실패를 호출자에게 전파하지
않는다(job_event emit 과 같은 규율 — `workers/_common.emit_job_event`). 기록이 실패해도
셀러의 컷은 나가야 한다.

왜 이벤트로 충분하지 않은가: job_events 는 스텝 단위라 "이 컷 한 장"을 만든 생성 1회 +
편집 3패스를 하나로 묶을 수 없고, 보존 기간·조회 경로가 컷과 다르다. 재현·비용·실패율을
세려면 provider 호출이 1급 행이어야 한다.

프롬프트 전문은 DB 에 넣지 않는다 — R2 object + sha256(기존 이벤트 규율과 동일). 설정
스냅샷은 allowlist 로만 뜬다: 시크릿·URL·토큰이 스냅샷에 섞이면 DB 덤프 하나가 유출이 된다.
"""

import hashlib
import logging
import uuid

from .. import repo
from ..r2 import genrun_prompt_key

log = logging.getLogger("wearless.generation_run")

# 판정·생성 결과에 실제로 영향을 주는 설정만. 여기 없는 값은 스냅샷에 담기지 않는다.
# 새 항목을 추가할 때는 "이 값이 유출돼도 안전한가"를 먼저 답할 것 — 테스트가 이름 기반으로
# 시크릿류(key/secret/token/password/url/dsn)를 차단하지만, 이름이 안전해 보이는 시크릿까지
# 막아주지는 않는다.
SETTINGS_ALLOWLIST: tuple[str, ...] = (
    "mannequin_tier",
    "mannequin_adjust_tier",
    "mannequin_image_size",
    "mannequin_aspect_ratio",
    "mannequin_max_attempts",
    "mannequin_axis_qc",
    "mannequin_qc_enabled",
    "mannequin_hybrid_composite",
    "image_qc",
    "garment_qc_mode",
    "qc_score_auto_pass",
    "qc_score_review",
    "credit_cost_version",
)

_FORBIDDEN_SUBSTRINGS = ("key", "secret", "token", "password", "credential", "url", "dsn")


def settings_snapshot(s) -> dict:
    """allowlist 설정 스냅샷 (순수). 스칼라만 담는다 — 중첩 객체는 시크릿을 숨길 수 있다."""
    out: dict = {}
    for name in SETTINGS_ALLOWLIST:
        if any(bad in name.lower() for bad in _FORBIDDEN_SUBSTRINGS):
            continue  # allowlist 자체의 오염 방지 — 이름만으로 걸리는 것은 담지 않는다
        v = getattr(s, name, None)
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[name] = v
    return out


def input_asset_snapshot(refs) -> list[dict]:
    """[{assetId, slot, sha256}] (순수). 바이트·URL 은 담지 않는다.

    checksum 은 **실제로 호출에 들어간 바이트**에서 뜬다 — asset row 의 기록값이 아니라.
    전처리(리사이즈·트랜스코드)가 끼면 둘은 갈라지고, 재현에 필요한 쪽은 나간 바이트다.
    """
    out = []
    for r in refs or ():
        img = getattr(r, "image", None)
        # ProductReference.image 는 InlineImage(.data) 다 — 바이트로 넘어오는 경로도 있어
        # 둘 다 받는다. 어느 쪽도 아니면 checksum 은 null(기록은 남기되 거짓말은 안 한다).
        data = img if isinstance(img, bytes) else getattr(img, "data", None)
        out.append({
            "assetId": getattr(r, "asset_id", None),
            "slot": getattr(r, "slot", None),
            "sha256": hashlib.sha256(data).hexdigest() if isinstance(data, bytes) else None,
        })
    return out


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


class RunLogger:
    """provider 호출 1건 = 행 1개. `enabled=False` 면 모든 메서드가 no-op(행 0)."""

    def __init__(self, *, pool, r2, job_id: str, project_id: str, user_id: str,
                 enabled: bool = False):
        self.pool = pool
        self.r2 = r2
        self.job_id = job_id
        self.project_id = project_id
        self.user_id = user_id
        self.enabled = bool(enabled)
        # 이미지 바이트 sha → run_id. 편집이 회귀로 되돌려지면 채택본은 이전 run 의 것이므로,
        # "마지막 run"이 아니라 **바이트로 역참조**해야 output 연결이 어긋나지 않는다.
        self._by_image: dict[str, str] = {}

    async def begin(self, *, kind: str, prompt: str, model: str | None = None,
                    candidate: str | None = None, attempt: int | None = None,
                    image_size: str | None = None, aspect_ratio: str | None = None,
                    prompt_version: str | None = None, input_assets=None,
                    fit_profile: dict | None = None, settings=None) -> str | None:
        """호출 **직전** 기록. 프로세스가 응답 대기 중 죽어도 시도 흔적이 남는다."""
        if not self.enabled:
            return None
        run_id = str(uuid.uuid4())
        r2_key = None
        try:
            if self.r2 is not None and prompt:
                r2_key = genrun_prompt_key(self.user_id, self.project_id, self.job_id, run_id)
                self.r2.put_bytes(r2_key, prompt.encode("utf-8"), "text/plain; charset=utf-8")
        except Exception as e:  # 프롬프트 보관 실패 — sha256 은 그대로 남긴다
            log.warning("genrun prompt upload failed (job %s): %r", self.job_id, e)
            r2_key = None
        try:
            async with self.pool.connection() as conn:
                await repo.insert_generation_run(
                    conn, run_id=run_id, job_id=self.job_id, project_id=self.project_id,
                    user_id=self.user_id, kind=kind, candidate=candidate, attempt=attempt,
                    model=model, image_size=image_size, aspect_ratio=aspect_ratio,
                    prompt_version=prompt_version, prompt_sha256=prompt_sha256(prompt),
                    prompt_r2_key=r2_key,
                    input_assets=input_asset_snapshot(input_assets) if input_assets else None,
                    fit_profile_snapshot=fit_profile,
                    settings_snapshot=settings_snapshot(settings) if settings is not None else None)
                await conn.commit()
        except Exception as e:
            log.warning("genrun insert failed (job %s): %r", self.job_id, e)
            return None
        return run_id

    async def finish(self, run_id: str | None, *, image: bytes | None = None,
                     usage: dict | None = None, latency_ms: int | None = None,
                     error: BaseException | None = None) -> None:
        """응답 후 갱신. `image` 를 주면 그 바이트가 이 run 의 산출물로 등록된다."""
        if not self.enabled or not run_id:
            return
        if isinstance(image, bytes):
            self._by_image[hashlib.sha256(image).hexdigest()] = run_id
        try:
            async with self.pool.connection() as conn:
                await repo.update_generation_run(
                    conn, run_id=run_id,
                    status="failed" if error is not None else "succeeded",
                    usage=usage, latency_ms=latency_ms,
                    provider_error=(f"{type(error).__name__}: {error}"[:200]
                                    if error is not None else None))
                await conn.commit()
        except Exception as e:
            log.warning("genrun update failed (job %s): %r", self.job_id, e)

    def run_id_for_image(self, image: bytes | None) -> str | None:
        """이 바이트를 만든 run. 미기록(플래그 off·기록 실패·베이스 이미지)이면 None."""
        if not self.enabled or not isinstance(image, bytes):
            return None
        return self._by_image.get(hashlib.sha256(image).hexdigest())
