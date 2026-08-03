"""Generation Run 기록 — provider 호출 1건을 재현 가능한 행으로 남긴다 (Phase 1).

이 모듈은 **관측기**다. 생성 결과에 개입하지 않고, 자신의 실패를 호출자에게 전파하지
않는다(job_event emit 과 같은 규율 — `workers/_common.emit_job_event`). 기록이 실패해도
셀러의 컷은 나가야 한다.

왜 이벤트로 충분하지 않은가: job_events 는 스텝 단위라 "이 컷 한 장"을 만든 생성 1회 +
편집 3패스를 하나로 묶을 수 없고, 보존 기간·조회 경로가 컷과 다르다. 재현·비용·실패율을
세려면 provider 호출이 1급 행이어야 한다.

기록에 담지 않는 것: 프롬프트 전문(R2 object + sha256 만), 이미지 바이트, URL/presigned
URL, API key, provider 응답 원문. provider 실패는 **예외 타입 + allowlist 코드**로만 남긴다
— GeminiError 메시지에는 요청 URL 과 응답 본문 500자가 들어간다(gemini_image.py).
"""

import asyncio
import hashlib
import logging
import re
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
    "enable_product_truth",
    "mannequin_structured_qc",
    "image_qc",
    "garment_qc_mode",
    "qc_score_auto_pass",
    "qc_score_review",
    "credit_cost_version",
)

_FORBIDDEN_SUBSTRINGS = ("key", "secret", "token", "password", "credential", "url", "dsn")

# provider 입력의 역할 — 스냅샷만 보고 "어떤 이미지가 몇 번째로 나갔는가"를 복원한다.
INPUT_ROLES = (
    "base_mannequin",      # 고정 베이스 마네킹
    "parent_cut",          # 조정 편집의 원본 컷(이전 채택본)
    "product_reference",   # Front/Back/Detail/Fit — slot 이 붙는다
    "matching_garment",    # 매칭 하의/상의
    "style_reference",     # 스타일 참조(look-only)
    "edit_source",         # axis/bust/untuck 편집 대상 = 직전 provider 산출물
)

# provider 실패를 DB 에 남길 때 쓰는 코드. 여기 없는 값은 전부 "unknown" 이다 —
# 원문을 코드처럼 흘려보내는 경로를 만들지 않는다.
PROVIDER_ERROR_CODES = (
    "api_key_missing",
    "request_failed",
    "no_image_in_response",
    "safety_blocked",
    "timeout",
    "http_400", "http_401", "http_403", "http_404", "http_408",
    "http_429", "http_500", "http_502", "http_503", "http_504",
    "http_other",
    "unknown",
)

_HTTP_STATUS_RE = re.compile(r"\b([45]\d{2})\b")


def sanitize_provider_error(exc: BaseException | None) -> str | None:
    """provider 예외 → "<ExcType>:<code>" (원문 미포함).

    GeminiError 메시지에는 요청 URL·응답 본문이 들어간다. 저장은 **타입 + allowlist 코드**
    까지만 한다. 코드는 메시지에서 뽑되, 뽑은 숫자를 고정 토큰으로 매핑해서만 쓴다 —
    메시지의 어떤 부분도 그대로 나가지 않는다.
    """
    if exc is None:
        return None
    etype = type(exc).__name__
    msg = str(exc)
    code = "unknown"
    if "GEMINI_API_KEY" in msg or "api key" in msg.lower():
        code = "api_key_missing"
    elif "응답에 이미지 없음" in msg or "no image" in msg.lower():
        code = "no_image_in_response"
    elif "safety" in msg.lower() or "blocked" in msg.lower():
        code = "safety_blocked"
    elif "timeout" in msg.lower() or "timed out" in msg.lower():
        code = "timeout"
    else:
        m = _HTTP_STATUS_RE.search(msg)
        if m:
            candidate = f"http_{m.group(1)}"
            code = candidate if candidate in PROVIDER_ERROR_CODES else "http_other"
        elif "요청 실패" in msg or isinstance(exc, (ConnectionError, OSError)):
            code = "request_failed"
    if code not in PROVIDER_ERROR_CODES:  # 방어 — allowlist 밖은 존재할 수 없다
        code = "unknown"
    return f"{etype}:{code}"


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


def image_sha256(image) -> str | None:
    """InlineImage(.data) · 원시 bytes 어느 쪽이든 실제 나간 바이트의 sha256."""
    data = image if isinstance(image, bytes) else getattr(image, "data", None)
    return hashlib.sha256(data).hexdigest() if isinstance(data, bytes) else None


def input_snapshot(entries) -> list[dict]:
    """provider 호출에 들어간 이미지 **전체**를 호출 순서대로 (순수).

    entries: [(role, image, asset_id, slot)] 또는 [(role, image, asset_id, slot, output_id)].
    `images=[...]` 리스트와 **같은 소스에서** 만들어야 순서가 갈라지지 않는다 — 두 벌로 두면
    프롬프트의 "image 1 = 현재 컷" 계약과 스냅샷이 조용히 어긋난다.

    checksum 은 asset row 의 기록값이 아니라 **실제로 호출에 들어간 바이트**에서 뜬다.
    전처리(리사이즈·트랜스코드)가 끼면 둘은 갈라지고, 재현에 필요한 쪽은 나간 바이트다.
    """
    out = []
    for pos, e in enumerate(entries or ()):
        role, image, asset_id, slot = e[0], e[1], e[2], e[3]
        output_id = e[4] if len(e) > 4 else None
        out.append({
            "role": role if role in INPUT_ROLES else "unknown",
            "assetId": asset_id,
            "outputId": output_id,
            "slot": slot,
            "sha256": image_sha256(image),
            "position": pos,
        })
    return out


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


class RunLogger:
    """provider 호출 1건 = 행 1개. `enabled=False` 면 모든 메서드가 no-op(행 0).

    산출물 역참조는 **(candidate, 이미지 sha)** 로 키를 잡는다. sha 단독이면 후보 A/B 가
    같은 바이트를 내는 경우(재시도·결정적 모델·테스트 fake) B 의 컷이 A 의 run 에 붙는다.
    """

    def __init__(self, *, pool, r2, job_id: str, project_id: str, user_id: str,
                 enabled: bool = False, truth_package_id: str | None = None):
        self.pool = pool
        self.r2 = r2
        self.job_id = job_id
        self.project_id = project_id
        self.user_id = user_id
        self.enabled = bool(enabled)
        self.truth_package_id = truth_package_id
        self._by_image: dict[tuple[str | None, str], str] = {}
        self._last_run: dict[str | None, str] = {}      # candidate → 마지막 provider run
        self._last_sha: dict[str | None, str] = {}      # candidate → 그 run 의 산출 바이트 sha

    # ── 조회 ────────────────────────────────────────────────────────────────
    def run_id_for_image(self, image, candidate: str | None = None) -> str | None:
        """이 바이트를 **그대로** 만든 run. 후처리가 끼면 None(= ancestor 를 써야 한다)."""
        if not self.enabled:
            return None
        sha = image_sha256(image)
        return self._by_image.get((candidate, sha)) if sha else None

    def last_provider_run(self, candidate: str | None = None) -> str | None:
        """이 후보에서 마지막으로 성공한 provider 호출 — deterministic 후처리의 조상."""
        return self._last_run.get(candidate) if self.enabled else None

    def last_provider_sha(self, candidate: str | None = None) -> str | None:
        return self._last_sha.get(candidate) if self.enabled else None

    def has_recorded_success(self, candidate: str | None = None) -> bool:
        """이 후보에 **성공한 provider run 이 하나라도 기록됐는가**.

        "계보를 모른다"와 "기록기가 아예 없다"를 구분하는 신호다. 전자는 행을 남겨야 한다
        (사람이 보고 조사할 수 있게), 후자는 남길 것 자체가 없다 — 플래그 off 이거나 DB
        기록이 통째로 실패한 상태다.
        """
        if not self.enabled:
            return False
        return candidate in self._last_run

    # ── 기록 ────────────────────────────────────────────────────────────────
    async def begin(self, *, kind: str, prompt: str, model: str | None = None,
                    candidate: str | None = None, attempt: int | None = None,
                    image_size: str | None = None, aspect_ratio: str | None = None,
                    prompt_version: str | None = None, inputs=None,
                    input_image=None, explicit_parent_generation_run_id: str | None = None,
                    fit_profile: dict | None = None, settings=None) -> str | None:
        """호출 **직전** 기록. 프로세스가 응답 대기 중 죽어도 시도 흔적이 남는다.

        순서가 중요하다: **DB 행을 먼저** 만들고 그 다음 R2 에 프롬프트를 올린다. 반대로 하면
        insert 실패(migration 미적용·DB 장애) 때마다 R2 에 고아 프롬프트가 쌓인다.
        """
        if not self.enabled:
            return None
        run_id = str(uuid.uuid4())
        in_sha = image_sha256(input_image) if input_image is not None else None
        # 부모 결정: **명시 부모가 정본**이다. 이전 job 의 컷을 편집하는 조정 경로는 이 job
        # 안에 그 이미지를 만든 호출이 없으므로 역참조로는 절대 찾을 수 없다.
        # 명시 부모가 없을 때만 이 job 안의 (candidate, 입력 sha) 역참조로 떨어진다.
        # 둘 다 없으면 null — 부모를 **추정하지 않는다**(flag-off 시기 컷이 부모인 정상 경우).
        parent = explicit_parent_generation_run_id
        if parent is None and in_sha:
            parent = self._by_image.get((candidate, in_sha))
        try:
            async with self.pool.connection() as conn:
                await repo.insert_generation_run(
                    conn, run_id=run_id, job_id=self.job_id, project_id=self.project_id,
                    user_id=self.user_id, kind=kind, candidate=candidate, attempt=attempt,
                    model=model, image_size=image_size, aspect_ratio=aspect_ratio,
                    prompt_version=prompt_version, prompt_sha256=prompt_sha256(prompt),
                    prompt_r2_key=None,
                    input_assets=input_snapshot(inputs) if inputs else None,
                    input_image_sha256=in_sha,
                    parent_generation_run_id=parent,
                    truth_package_id=self.truth_package_id,
                    fit_profile_snapshot=fit_profile,
                    settings_snapshot=settings_snapshot(settings) if settings is not None else None)
                await conn.commit()
        except Exception as e:
            log.warning("genrun insert failed (job=%s project=%s error=%s)",
                        self.job_id, self.project_id, type(e).__name__)
            return None
        await self._store_prompt(run_id, prompt)
        return run_id

    async def _store_prompt(self, run_id: str, prompt: str) -> None:
        """프롬프트 전문을 R2 에 두고 키만 행에 채운다. 실패해도 sha256 은 이미 행에 있다."""
        if self.r2 is None or not prompt:
            return
        key = genrun_prompt_key(self.user_id, self.project_id, self.job_id, run_id)
        try:
            # boto3 는 동기 blocking 이다 — event loop 에서 직접 부르면 그 시간만큼 워커의
            # 다른 코루틴(진행률 tick·이벤트 emit)이 통째로 멈춘다.
            await asyncio.to_thread(
                self.r2.put_bytes, key, prompt.encode("utf-8"), "text/plain; charset=utf-8")
        except Exception as e:
            log.warning("genrun prompt upload failed (job=%s run=%s error=%s)",
                        self.job_id, run_id, type(e).__name__)
            return
        try:
            async with self.pool.connection() as conn:
                await repo.update_generation_run_prompt_key(conn, run_id=run_id, key=key)
                await conn.commit()
        except Exception as e:
            # 키 갱신 실패 = R2 에 객체는 있는데 행이 모른다. 지워서 고아를 남기지 않는다.
            log.warning("genrun prompt key update failed (job=%s run=%s error=%s)",
                        self.job_id, run_id, type(e).__name__)
            try:
                await asyncio.to_thread(self.r2.delete, key)
            except Exception as de:
                # 삭제까지 실패하면 고아가 남는다 — 사실을 남기되 키 원문은 로그에 없다.
                log.warning("genrun orphan prompt delete failed (job=%s run=%s error=%s)",
                            self.job_id, run_id, type(de).__name__)

    async def finish(self, run_id: str | None, *, image=None, candidate: str | None = None,
                     usage: dict | None = None, latency_ms: int | None = None,
                     error: BaseException | None = None) -> None:
        """응답 후 갱신. `image` 를 주면 그 바이트가 이 run 의 산출물로 등록된다."""
        if not self.enabled or not run_id:
            return
        sha = image_sha256(image)
        if sha:
            self._by_image[(candidate, sha)] = run_id
            self._last_run[candidate] = run_id
            self._last_sha[candidate] = sha
        try:
            async with self.pool.connection() as conn:
                await repo.update_generation_run(
                    conn, run_id=run_id,
                    status="failed" if error is not None else "succeeded",
                    usage=usage, latency_ms=latency_ms,
                    provider_error=sanitize_provider_error(error))
                await conn.commit()
        except Exception as e:
            log.warning("genrun update failed (job=%s run=%s error=%s)",
                        self.job_id, run_id, type(e).__name__)

    # ── 산출물 계보 ─────────────────────────────────────────────────────────
    def output_lineage(self, image, candidate: str | None = None,
                       carrier_run_id: str | None = None) -> dict:
        """최종 채택 바이트 → generation_outputs 에 넣을 계보 (순수 조회).

        `generation_run_id` 의 의미는 **"최종 결과의 마지막 provider 조상"**이다 —
        "최종 바이트와 동일한 응답"이 아니다. hybrid composite 같은 deterministic 후처리가
        바이트를 바꿔도 행이 사라지면 안 되기 때문이다. 둘의 구분은 `post_processed` 가 한다:
        False 면 그 run 의 응답 바이트와 최종 바이트가 **정확히 같다**.

        조상은 **추정하지 않는다**. 후처리로 바이트가 달라진 경우, 호출자가 후처리 **직전**에
        캡처한 `carrier_run_id` 만 쓴다. "마지막으로 성공한 run" 같은 추정은 틀린 조상을
        기록한다 — 편집이 회귀로 폐기됐거나(G→E→rollback G) 후보 여러 개 중 앞선 것이
        선택된 경우(G1,G2→G1), 마지막 성공 run 은 **폐기된 쪽**이다. carrier 가 없으면
        null 로 남겨 사람이 볼 수 있게 한다(잘못된 계보보다 빈 계보가 낫다).
        """
        sha = image_sha256(image)
        exact = self._by_image.get((candidate, sha)) if sha else None
        if exact:
            return {"generation_run_id": exact, "output_sha256": sha, "post_processed": False}
        return {
            "generation_run_id": carrier_run_id,
            "output_sha256": sha,
            "post_processed": True,
        }
