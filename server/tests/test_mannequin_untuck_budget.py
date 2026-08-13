"""untuck 전용 예산 분리 계약 (2026-08-12).

프로덕션 실측이 계기다: attempt 5 를 소진한 잡 2건이 연속으로 `budget_exhausted` 로 untuck
을 스킵하고 tuck 인 채 출고됐다. 예산을 5 로 올려 둔 것이 원인을 가리고 있었다. 새 계약은:

    attempt 1: 최초 생성 → QC
    필요 시 attempt 2: 마지막 일반 retry → QC     ← 일반 예산은 여기서 끝(2 콜 고정)
    일반 retry 종료
    ↓
    untuck 필요 여부 판단 (플래그 + top/outer + 매칭 하의)
    ↓
    untuck 전용 post-pass ≤ 1 콜 (일반 예산과 무관한 전용 슬롯)
    ↓
    최종 저장

    최악 총합 = 일반 2 + untuck 1 = 3 이미지 콜

여기 테스트는 `_run_candidate` 를 끝까지 돌려 그 콜 수를 **가짜 Gemini 의 실측**으로
증명한다. untuck 호출은 프롬프트의 untuck 전용 문구("unbroken visible line")로 식별한다 —
호출자 쪽 분류가 아니라 실제로 나간 프롬프트가 근거다.
"""

import asyncio
import types

from app.services.qc import QcResult
from app.workers import mannequin_job
from conftest import make_settings

#: untuck 프롬프트(prompts/mannequin_untuck_v1.txt)에만 있는 문구 — 생성 프롬프트와 구분자.
_UNTUCK_MARKER = "unbroken visible line"

_PASS = QcResult("pass", [], {})
#: 캔버스 사유는 shadow 모드와 무관하게 재생성을 강제한다 — retry 경로를 만드는 손잡이.
_REROLL = QcResult("retry", ["transparent_canvas"], {"transparentPixelCount": 9})


class _Gemini:
    """생성 콜과 untuck 콜을 프롬프트로 구분해 따로 센다."""

    def __init__(self, outputs, *, untuck_raises=False):
        self.outputs = list(outputs)
        self.generation_calls = []
        self.untuck_calls = []
        self.untuck_raises = untuck_raises

    async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
        if _UNTUCK_MARKER in prompt:
            self.untuck_calls.append(prompt)
            if self.untuck_raises:
                raise RuntimeError("untuck model unavailable")
        else:
            self.generation_calls.append(prompt)
        return types.SimpleNamespace(image=self.outputs.pop(0), mime="image/png")


class _R2:
    def __init__(self):
        self.puts = []

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, data, mime))


async def _no_series(**kwargs):
    return None


def _run(monkeypatch, *, outputs, verdicts, with_match=True, untuck_raises=False):
    """_run_candidate 전체 구동. 일반 예산은 항상 2 로 고정한다(계약값)."""
    emits = []
    verdicts = list(verdicts)
    monkeypatch.setattr(
        mannequin_job.qc, "evaluate_mannequin_qc", lambda _data: verdicts.pop(0))
    # 편집·untuck 산출물은 가짜 바이트라 디코드가 안 된다 — 무결성 게이트는 통과로 고정.
    monkeypatch.setattr(
        mannequin_job.qc, "evaluate_canvas_alpha_qc", lambda _data: QcResult("pass"))
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", _no_series)

    async def fake_emit(pool, job_id, event_type, payload):
        emits.append(dict(payload))

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)

    gemini = _Gemini(outputs, untuck_raises=untuck_raises)
    r2 = _R2()
    settings = make_settings(
        r2_bucket="bucket",
        image_qc="off",
        mannequin_axis_qc="off",
        mannequin_max_attempts=2,          # 계약값 고정 — outputs 길이에 따라 늘리지 않는다
        mannequin_bust_pass="off",
        mannequin_fabric_pass="off",
        mannequin_untuck_pass="on",
    )
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=object(), r2=r2, gemini=gemini))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "payload": {}}

    result = asyncio.run(mannequin_job._run_candidate(
        app=app,
        job=job,
        candidate="A",
        base_fit="regular",
        base_gender="women",
        base_img=types.SimpleNamespace(mime="image/png", data=b"base"),
        prod_imgs=[],
        match_img=(mannequin_job.InlineImage("image/png", b"bottom") if with_match else None),
        product_count=0,
        template="${baseGender} ${clothingType} ${imageManifest}",
        product={},
        analysis={},
        clothing_type="top",
    ))
    return result, gemini, r2, emits


def _untuck_events(emits):
    return [e for e in emits if e.get("status") == "untuck_pass"]


def test_attempt1_pass_with_untuck_costs_exactly_two_calls(monkeypatch):
    """attempt 1 PASS + untuck 필요 → 생성 1 + untuck 1 = 총 2 콜."""
    result, gemini, r2, emits = _run(
        monkeypatch, outputs=[b"gen-1", b"untucked"], verdicts=[_PASS])

    assert result is not None
    assert len(gemini.generation_calls) == 1
    assert len(gemini.untuck_calls) == 1
    assert r2.puts[0][1] == b"untucked", "출고본은 untuck 결과여야 한다"
    (ev,) = _untuck_events(emits)
    assert ev["generation_attempts"] == 1
    assert ev["untuck_attempted"] is True and ev["untuck_calls"] == 1
    assert ev["untuck_outcome"] == "applied"


def test_attempt2_pass_still_leaves_untuck_its_dedicated_slot(monkeypatch):
    """attempt 1 RETRY → attempt 2 PASS + untuck → 총 3 콜. 예산 소진이어도 untuck 은 돈다.

    구 구조에서 정확히 이 경로가 budget_exhausted 였다(일반 예산 2 를 생성이 다 씀).
    """
    result, gemini, r2, emits = _run(
        monkeypatch, outputs=[b"gen-1", b"gen-2", b"untucked"],
        verdicts=[_REROLL, _PASS])

    assert result is not None
    assert len(gemini.generation_calls) == 2, "일반 생성은 정확히 2회"
    assert len(gemini.untuck_calls) == 1, "일반 예산 소진과 무관하게 전용 슬롯 1회"
    assert r2.puts[0][1] == b"untucked"
    (ev,) = _untuck_events(emits)
    assert ev["generation_attempts"] == 2
    assert ev["untuck_outcome"] == "applied"
    assert not any("budget_exhausted" in str(e.values()) for e in emits), \
        "untuck 이 예산 사유로 스킵되는 경로는 더 이상 없다"


def test_untuck_not_needed_costs_zero_extra_calls(monkeypatch):
    """매칭 하의가 없으면 untuck 은 판단만 하고 호출하지 않는다 — 추가 콜 0."""
    result, gemini, r2, emits = _run(
        monkeypatch, outputs=[b"gen-1"], verdicts=[_PASS], with_match=False)

    assert result is not None
    assert len(gemini.generation_calls) == 1
    assert len(gemini.untuck_calls) == 0
    assert r2.puts[0][1] == b"gen-1"
    (ev,) = _untuck_events(emits)
    assert ev["untuck_attempted"] is False and ev["untuck_calls"] == 0
    assert ev["untuck_outcome"] == "not_needed"


def test_untuck_failure_keeps_last_generation_and_never_retries(monkeypatch):
    """untuck 실패 → 마지막 정상 생성본 그대로 저장. 생성 재시도도, untuck 재시도도 없다."""
    result, gemini, r2, emits = _run(
        monkeypatch, outputs=[b"gen-1"], verdicts=[_PASS], untuck_raises=True)

    assert result is not None
    assert len(gemini.generation_calls) == 1, "untuck 실패가 생성 재시도를 만들면 안 된다"
    assert len(gemini.untuck_calls) == 1, "untuck 재시도 루프도 없다 — 정확히 1회 시도"
    assert r2.puts[0][1] == b"gen-1", "실패 시 마지막 정상 생성본이 출고된다"
    (ev,) = _untuck_events(emits)
    assert ev["untuck_outcome"] == "failed" and ev["untuck_calls"] == 1


def test_no_third_generation_attempt_path_exists(monkeypatch):
    """모든 attempt 가 거절돼도 일반 생성은 2회에서 끝난다 — 여분 출력이 있어도 안 쓴다."""
    result, gemini, _r2, _emits = _run(
        monkeypatch, outputs=[b"g1", b"g2", b"g3", b"g4"],
        verdicts=[_REROLL, _REROLL, _REROLL, _REROLL])

    assert result is None, "캔버스 거절만 있으면 구제 없이 드롭된다(기존 alpha 계약)"
    assert len(gemini.generation_calls) == 2, "3번째 attempt 경로는 존재하지 않는다"
    assert len(gemini.untuck_calls) == 0, "저장할 컷이 없으면 untuck 도 없다"


def test_shared_budget_untuck_cannot_come_back(monkeypatch):
    """구 결합 지점 자체가 사라졌는지 — 이름·시그니처 레벨의 회귀 가드."""
    import inspect

    assert not hasattr(mannequin_job, "_apply_untuck_pass"), \
        "공유 예산 시절의 함수가 되살아났다"
    params = inspect.signature(mannequin_job._apply_untuck_postpass).parameters
    assert "calls_spent" not in params, "untuck 이 일반 예산을 다시 알게 되면 안 된다"
    src = inspect.getsource(mannequin_job._apply_edits)
    # 주석의 언급은 허용, **호출**은 금지 — await 가 붙는 순간 공유 예산 결합이 되살아난다.
    assert "await _apply_untuck" not in src, \
        "untuck 이 편집 체인(공유 예산)으로 돌아오면 안 된다"
