"""D축 시리즈 일관성 QC 워커 배선 — fail-open 규율과 점수 합류.

핵심 계약: 판정은 **관측이지 게이트가 아니다**. 기존 컷 조회 실패·R2 미스·모델 오류·첫 컷
어느 경우에도 생성이 멈추면 안 된다(_apply_axis_qc·_apply_bust_pass 와 같은 규율).
"""
import asyncio
import contextlib
import types

import pytest

from app.agents import mannequin_series_qc
from app.workers import mannequin_job
from conftest import make_settings
from tests.conftest import make_image_budget_gate


class _Conn:
    pass


class _FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()
        return _cm()


class _R2:
    def __init__(self, error=False):
        self.error, self.gets = error, []

    def get_bytes(self, key):
        self.gets.append(key)
        if self.error:
            raise RuntimeError("R2 down")
        return b"ref-bytes"


def _apply(monkeypatch, *, cuts=None, judge=None, r2=None):
    emits = []

    async def fake_emit(pool, job_id, event_type, payload):
        emits.append((event_type, dict(payload)))

    async def fake_list(conn, project_id, *, limit=3):
        if isinstance(cuts, Exception):
            raise cuts
        return (cuts or [])[:limit]  # SQL LIMIT 재연

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    monkeypatch.setattr(mannequin_job.repo, "list_series_reference_cuts", fake_list)
    if judge is not None:
        async def fake_judge(settings, generated, references, **kw):
            if isinstance(judge, Exception):
                raise judge
            return judge
        monkeypatch.setattr(mannequin_series_qc, "judge", fake_judge)

    r2 = r2 or _R2()
    app = types.SimpleNamespace(state=types.SimpleNamespace(r2=r2))
    out = asyncio.run(mannequin_job._apply_series_qc(
        app=app, pool=_FakePool(), s=make_settings(), job_id="j1",
        project_id="p1", candidate="A", attempt=1,
        res=types.SimpleNamespace(mime="image/png", image=b"gen")))
    return out, emits, r2


def _statuses(emits):
    return [p.get("status") for t, p in emits if t == "step"]


# ── 스킵 / fail-open ─────────────────────────────────────────────────────────

def test_first_cut_skips_without_event(monkeypatch):
    """첫 컷은 비교 대상이 없다 — 판정 없음(None)이지 0점이 아니고, 잡음 이벤트도 안 남긴다."""
    out, emits, r2 = _apply(monkeypatch, cuts=[])
    assert out is None
    assert _statuses(emits) == []
    assert r2.gets == []  # 기존 컷이 없으면 R2 를 건드리지도 않는다


def test_db_error_fails_open(monkeypatch):
    out, emits, _ = _apply(monkeypatch, cuts=RuntimeError("db down"))
    assert out is None
    assert "series_qc_failed" in _statuses(emits)


def test_r2_error_fails_open(monkeypatch):
    out, emits, _ = _apply(
        monkeypatch, cuts=[{"candidate": "A", "version": 1, "r2_key": "a.png"}],
        r2=_R2(error=True))
    assert out is None
    assert "series_qc_failed" in _statuses(emits)


def test_judge_error_fails_open(monkeypatch):
    out, emits, _ = _apply(
        monkeypatch, cuts=[{"candidate": "A", "version": 1, "r2_key": "a.png"}],
        judge=RuntimeError("vision down"))
    assert out is None
    assert "series_qc_failed" in _statuses(emits)


# ── 정상 판정 ────────────────────────────────────────────────────────────────

def test_success_emits_score_and_reference_count(monkeypatch):
    out, emits, r2 = _apply(
        monkeypatch,
        cuts=[{"candidate": "A", "version": 2, "r2_key": "b.jpg"}],
        judge={"consistency": 72, "inconsistencies": ["배경이 더 밝음"]})
    assert out == {"consistency": 72, "inconsistencies": ["배경이 더 밝음"]}
    step = [p for t, p in emits if t == "step" and p.get("status") == "series_qc"][0]
    assert step["seriesQc"]["consistency"] == 72
    assert step["referenceCount"] == 1
    assert r2.gets == ["b.jpg"]


def test_reference_cap_passed_to_sql(monkeypatch):
    """cap 은 SQL LIMIT 로 내려간다 — 파이썬에서 자르면 DB 전송 비용이 안 줄어든다."""
    seen = {}

    async def fake_list(conn, project_id, *, limit=3):
        seen["limit"] = limit
        return [{"candidate": "A", "version": 9, "r2_key": "a.jpg"}]

    monkeypatch.setattr(mannequin_job.repo, "list_series_reference_cuts", fake_list)

    async def fake_emit(pool, job_id, event_type, payload):
        pass

    async def fake_judge(settings, generated, references, **kw):
        return {"consistency": 90, "inconsistencies": []}

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    monkeypatch.setattr(mannequin_series_qc, "judge", fake_judge)
    app = types.SimpleNamespace(state=types.SimpleNamespace(r2=_R2()))
    asyncio.run(mannequin_job._apply_series_qc(
        app=app, pool=_FakePool(), s=make_settings(), job_id="j1",
        project_id="p1", candidate="A", attempt=1,
        res=types.SimpleNamespace(mime="image/png", image=b"gen")))
    assert seen["limit"] == mannequin_series_qc.MAX_REFERENCE_CUTS


# ── 점수 합류 (score_outcome 이 D축을 본다) ───────────────────────────────────

def test_series_score_participates_in_outcome():
    """일관성 붕괴가 outcome 에 반영돼야 D축이 실제로 판정에 쓰인다."""
    s = make_settings(qc_score_auto_pass=90, qc_score_review=75)
    healthy = {"product_fidelity": 95, "physical_naturalness": 95,
               "image_quality": 95, "series_consistency": 95, "critical_errors": []}
    assert mannequin_job.score_outcome(s, healthy) == "auto_pass"
    broken = {**healthy, "series_consistency": 40}
    assert mannequin_job.score_outcome(s, broken) == "regenerate"


# ── 최선본 구제 (codex MEDIUM 3) ──────────────────────────────────────────────

def _p2(worst, critical=()):
    return {"verdict": "retry", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": worst, "physical_naturalness": 99,
            "image_quality": 99, "series_consistency": None,
            "critical_errors": list(critical)}


def test_better_candidate_prefers_higher_worst_axis():
    """1차 70점 / 2차 20점이면 20점을 구제하면 안 된다 — 재시도가 손해가 된다."""
    s = make_settings()
    assert mannequin_job._is_better_candidate(s, _p2(70), None) is True
    assert mannequin_job._is_better_candidate(s, _p2(20), _p2(70)) is False
    assert mannequin_job._is_better_candidate(s, _p2(85), _p2(70)) is True


def test_better_candidate_prefers_no_critical_error_over_score():
    """치명 오류는 출고 불가라, 점수가 낮아도 결함 없는 쪽이 낫다."""
    s = make_settings()
    assert mannequin_job._is_better_candidate(
        s, _p2(40), _p2(95, critical=["logo altered"])) is True
    assert mannequin_job._is_better_candidate(
        s, _p2(95, critical=["logo altered"]), _p2(40)) is False


def test_better_candidate_keeps_old_when_new_has_no_signal():
    s = make_settings()
    assert mannequin_job._is_better_candidate(s, {"verdict": "retry"}, _p2(50)) is False


# ── D축 재생성 분기가 R2 를 오염시키지 않는가 ────────────────────────────────

def _run_loop(monkeypatch, *, series_scores, max_attempts=2):
    """_run_candidate 를 실 경로로 돌리되 series QC 만 대본대로 응답시킨다."""
    import test_mannequin_axis_qc as harness

    seq = list(series_scores)

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return seq.pop(0) if seq else None

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    return harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=max_attempts, verdicts=[],
        image_qc="enforce",
        p2={"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 95, "physical_naturalness": 95,
            "image_quality": 95, "series_consistency": None, "critical_errors": []})


def test_low_series_score_actually_rerolls_and_stores_once(monkeypatch):
    """D축 regenerate 는 관측이 아니라 실제 재생성으로 이어진다 (codex HIGH 1).

    그리고 재시도해도 R2 에는 최종 채택본 **1개만** 남아야 한다 — 분기가 저장 뒤에 있으면
    버려진 이미지가 버킷에 쌓인다.
    """
    result, g, r2, emits = _run_loop(
        monkeypatch,
        series_scores=[{"consistency": 30, "inconsistencies": ["배경이 훨씬 어두움"]},
                       {"consistency": 96, "inconsistencies": []}])
    assert len(g.calls) == 2, "1회차 D축 30점 → 재생성했어야 한다"
    assert len(r2.puts) == 1, f"R2 저장이 {len(r2.puts)}건 — 고아 객체가 남았다"
    assert result["qc_scores"]["series_consistency"] == 96
    assert result["qc_scores"]["outcome"] == "auto_pass"
    rejects = [p for t, p in emits if t == "step" and p.get("status") == "final_qc_reject"]
    assert rejects and rejects[0]["seriesConsistency"] == 30
    assert rejects[0]["outcome"] == "regenerate"


def test_series_reject_feedback_reaches_regeneration_prompt(monkeypatch):
    """재생성이 같은 프롬프트면 같은 결과가 나온다 — 불일치 사유가 주입돼야 한다."""
    _result, g, _r2, _emits = _run_loop(
        monkeypatch,
        series_scores=[{"consistency": 20, "inconsistencies": ["배경이 훨씬 어두움"]},
                       {"consistency": 95, "inconsistencies": []}])
    assert "CONSISTENCY" in g.calls[1]["prompt"]
    assert "배경이 훨씬 어두움" in g.calls[1]["prompt"]


def test_series_reject_on_last_attempt_ships_instead_of_dropping(monkeypatch):
    """예산이 없으면 D축이 낮아도 출고한다 — 셀러를 빈손으로 보내지 않는다."""
    result, g, r2, _emits = _run_loop(
        monkeypatch, max_attempts=1,
        series_scores=[{"consistency": 25, "inconsistencies": ["배경 톤 불일치"]}])
    assert result is not None and len(g.calls) == 1 and len(r2.puts) == 1
    assert result["qc_scores"]["outcome"] == "regenerate"  # 판정은 감추지 않는다


def test_shadow_never_rerolls_even_on_worst_scores(monkeypatch):
    """**배포되는 설정(IMAGE_QC=shadow)의 안전 계약.**

    manifest 가 싣는 값이 shadow 다. 여기서 재생성이 발화하면 관측만 켠 배포가 조용히
    생성 비용을 늘리고, 최악의 경우 MANNEQUIN_QC_ENABLED 사고처럼 파이프라인을 흔든다.
    점수가 바닥이고 치명 오류가 있어도 생성은 1회, 저장은 1건이어야 한다.
    """
    import test_mannequin_axis_qc as harness

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 5, "inconsistencies": ["완전히 다른 스튜디오"]}

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    result, g, r2, emits = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=2, verdicts=[], image_qc="shadow",
        p2={"verdict": "retry", "mismatches": ["색 다름"], "correctionPrompt": "fix",
            "product_fidelity": 10, "physical_naturalness": 10, "image_quality": 10,
            "series_consistency": None, "critical_errors": ["garment color changed"]})
    assert len(g.calls) == 1, "shadow 인데 재생성이 발화했다"
    assert len(r2.puts) == 1 and result is not None
    assert not [p for _t, p in emits if p.get("status") == "final_qc_reject"]
    # 판정은 기록하되 출고는 막지 않는다 — 관측의 정의.
    assert result["qc_scores"]["outcome"] == "regenerate"
    assert result["qc_scores"]["salvaged"] is False


def test_axis_edit_consumes_budget_so_no_extra_generation(monkeypatch):
    """axis 편집이 이번 attempt 를 썼으면 D축 reject 여도 재생성하지 않는다.

    codex 지적: 편집으로 예산을 쓰고 또 생성하면 "생성 + 편집 <= max_attempts" 불변식이
    깨져 한 attempt 번호에 생성성 호출이 두 번 들어간다.
    """
    import test_mannequin_axis_qc as harness

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 10, "inconsistencies": ["완전 다름"]}

    async def fake_axis(**kw):
        return kw["res"], True  # 편집 발생 = 예산 소비

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    result, g, r2, _emits = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=2, verdicts=[], image_qc="enforce",
        p2={"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
            "series_consistency": None, "critical_errors": []})
    assert len(g.calls) == 1, "편집이 예산을 썼는데 추가 생성이 일어났다"
    assert result is not None and len(r2.puts) == 1
    assert result["qc_scores"]["salvaged"] is True  # 재생성 못 하니 구제 출고


def test_final_reject_feedback_includes_critical_errors(monkeypatch):
    """치명 오류가 있으면 재생성 프롬프트가 그걸 먼저 말해야 한다."""
    import test_mannequin_axis_qc as harness

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 99, "inconsistencies": []}

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    _r, g, _r2, _e = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=2, verdicts=[], image_qc="enforce",
        p2={"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 99, "physical_naturalness": 99, "image_quality": 99,
            "series_consistency": None, "critical_errors": ["logo altered"]})
    assert len(g.calls) == 2, "치명 오류는 점수와 무관하게 재생성이어야 한다"
    assert "CRITICAL" in g.calls[1]["prompt"] and "logo altered" in g.calls[1]["prompt"]


# ── codex 5차 지적 ────────────────────────────────────────────────────────────

def test_retry_feedback_never_empty_when_only_scores_are_low():
    """점수만 낮고 텍스트 사유가 없어도 지시가 나가야 한다 (codex MEDIUM).

    verdict=pass · critical_errors=[] · correctionPrompt=None 인데 축만 낮은 경우가 실제로
    있다. 그때 빈 피드백이면 다음 attempt 가 같은 프롬프트로 돌아 같은 결과를 낸다.
    """
    fb = mannequin_job._build_retry_feedback(
        {"product_fidelity": 40, "physical_naturalness": 90, "image_quality": 90,
         "series_consistency": None, "critical_errors": []},
        None, {"verdict": "pass", "correctionPrompt": None})
    assert fb, "피드백이 비었다 — 재생성이 같은 결과를 반복한다"
    assert "product_fidelity" in fb          # 가장 낮은 축을 집는다
    assert "color, pattern, print, logo" in fb


def test_retry_feedback_prefers_concrete_reasons_over_axis_fallback():
    fb = mannequin_job._build_retry_feedback(
        {"product_fidelity": 40, "critical_errors": ["logo altered"]},
        {"consistency": 30, "inconsistencies": ["배경 어두움"]},
        {"correctionPrompt": "keep the neckline"})
    assert "CRITICAL: logo altered" in fb
    assert "배경 어두움" in fb
    assert "keep the neckline" in fb
    assert "IMPROVE" not in fb  # 구체 사유가 있으면 폴백은 안 붙인다


def test_final_salvage_never_uses_unedited_pre_gate_candidate(monkeypatch):
    """사전 게이트 후보는 편집·재판정·D축을 안 거쳤다 — 최종 구제에 쓰면 안 된다 (codex HIGH).

    1회차: 사전 게이트에서 거절(낮은 p2) → pre_reject 에 원본 보관
    2회차: 사전 게이트 통과 후 D축에서 거절 → 예산 소진 → 구제
    구제본은 2회차의 편집 완료 이미지여야 하고, 1회차 원본이면 안 된다.
    """
    import test_mannequin_axis_qc as harness

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 10, "inconsistencies": ["다름"]}

    p2_seq = [
        # 1회차: 사전 게이트가 거절할 만큼 낮게
        {"verdict": "retry", "mismatches": [], "correctionPrompt": None,
         "product_fidelity": 20, "physical_naturalness": 20, "image_quality": 20,
         "series_consistency": None, "critical_errors": []},
        # 2회차: 사전 게이트는 통과, D축이 거절
        {"verdict": "pass", "mismatches": [], "correctionPrompt": None,
         "product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
         "series_consistency": None, "critical_errors": []},
        {"verdict": "pass", "mismatches": [], "correctionPrompt": None,
         "product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
         "series_consistency": None, "critical_errors": []},
    ]
    seq = list(p2_seq)

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    result, _g, r2, _emits = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=2, verdicts=[], image_qc="enforce")
    assert result is not None and len(r2.puts) == 1
    # 구제본의 점수는 2회차(사전게이트 통과분)여야 한다 — 1회차의 20점대가 아니라.
    q = result["qc_scores"]
    assert q["product_fidelity"] == 95, f"편집 안 거친 1회차 원본이 출고됐다: {q}"
    assert q["salvaged"] is True


def test_salvage_picks_best_across_both_pools(monkeypatch):
    """구제는 두 풀(pre/final)을 통틀어 최선을 고른다 (codex MEDIUM).

    1회차: 사전 게이트 통과(70점 — 임계 65 위) → D축 거절 → final_reject 에 검증본 보관
    2회차: 사전 게이트에서 20점으로 거절 → 예산 소진
    이때 사전 게이트 풀만 보면 20점이 나간다. 70점 검증본이 있으면 그걸 써야 한다.
    """
    import test_mannequin_axis_qc as harness

    p2_seq = [
        # 사전 게이트(review 임계 65)를 통과해야 최종 단계까지 가 final_reject 에 담긴다.
        {"verdict": "pass", "mismatches": [], "correctionPrompt": None,
         "product_fidelity": 70, "physical_naturalness": 70, "image_quality": 70,
         "series_consistency": None, "critical_errors": []},
        {"verdict": "retry", "mismatches": [], "correctionPrompt": None,
         "product_fidelity": 20, "physical_naturalness": 20, "image_quality": 20,
         "series_consistency": None, "critical_errors": []},
    ]
    seq = list(p2_seq)

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 10, "inconsistencies": ["다름"]}  # 1회차를 최종에서 거절

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    result, _g, r2, _e = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=2, verdicts=[], image_qc="enforce")
    assert result is not None and len(r2.puts) == 1
    assert result["qc_scores"]["product_fidelity"] == 70, \
        f"20점 사전게이트 후보가 70점 검증본을 제치고 나갔다: {result['qc_scores']}"


def test_scores_rescored_when_edit_changed_the_image(monkeypatch):
    """편집으로 이미지가 바뀌면 A~C 를 **최종본 기준으로 재판정**한다 (codex HIGH 2).

    안 하면 저장 점수가 편집 전 이미지의 것이고, 검수자는 실제 출고본과 다른 이미지의
    숫자를 보고 판단하게 된다. 관측에서 이 재판정이 하락을 잡아냈다(85 → 30 사례).
    """
    import test_mannequin_axis_qc as harness

    seq = [
        {"verdict": "pass", "mismatches": [], "correctionPrompt": None,
         "product_fidelity": 90, "physical_naturalness": 90, "image_quality": 90,
         "series_consistency": None, "critical_errors": []},          # 편집 전
        {"verdict": "pass", "mismatches": [], "correctionPrompt": None,
         "product_fidelity": 85, "physical_naturalness": 85, "image_quality": 85,
         "series_consistency": None, "critical_errors": []},          # 편집 후(등급 내 하락)
    ]
    calls = {"n": 0}

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        calls["n"] += 1
        return seq[min(calls["n"] - 1, len(seq) - 1)]

    async def fake_axis(**kw):
        # 이미지를 실제로 바꾼다 → 해시가 달라져 재판정 조건이 성립
        return types.SimpleNamespace(mime=kw["res"].mime, image=b"edited-bytes"), False

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return None

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    result, _g, _r2, emits = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=1, verdicts=[], image_qc="shadow")
    assert calls["n"] == 2, "편집 후 재판정이 일어나지 않았다"
    assert result["qc_scores"]["product_fidelity"] == 85, "편집 전 점수가 저장됐다"
    assert "image_qc_rescored" in [p.get("status") for _t, p in emits]


def _p2(fid=90, nat=90, qual=90, critical=()):
    return {"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": fid, "physical_naturalness": nat, "image_quality": qual,
            "series_consistency": None, "critical_errors": list(critical)}


def test_edit_regressed_only_fires_on_grade_drop_or_new_critical():
    """편집은 A~C 가 안 재는 축(핏·볼륨)을 고치러 돈다 — 작은 하락까지 되돌리면 교정이 죽는다."""
    from app.workers.mannequin_job import edit_regressed
    s = types.SimpleNamespace(qc_score_auto_pass=80, qc_score_review=65, image_qc="enforce")

    assert edit_regressed(s, _p2(90), _p2(85)) is False, "등급 안에서의 하락은 노이즈"
    assert edit_regressed(s, _p2(90), _p2(70)) is True, "auto_pass → needs_review"
    assert edit_regressed(s, _p2(90), _p2(30)) is True, "실측된 85→30 손상"
    assert edit_regressed(s, _p2(90), _p2(90, critical=["logo altered"])) is True
    assert edit_regressed(s, _p2(90, critical=["x"]), _p2(90, critical=["x"])) is False
    assert edit_regressed(s, _p2(70), _p2(90)) is False, "개선을 되돌리면 안 된다"
    # 신호 부재는 비교 불가 → 기존 동작(편집 유지) 유지
    assert edit_regressed(s, None, _p2(30)) is False
    assert edit_regressed(s, {"critical_errors": []}, _p2(30)) is False


def test_edit_regressed_absorbs_judge_noise_within_margin():
    """등급이 내려가도 하락폭이 마진 이내면 편집을 살린다 — 2패스가 노이즈에 상시 롤백되던 버그.

    2026-07-31 prod 실측(job 3c6dd251): 가슴 2패스가 `applied` 로 이미지를 바꿨는데 재채점이
    85/83/80 → 77/78/76 으로 나와 auto_pass→needs_review 로 갈렸고, 편집이 통째로 롤백되어
    (`edit_reverted reason=all_edits`) 가슴 볼륨이 한 번도 출고되지 않았다. 최저점 하락은 4점
    뿐인데 임계 80 이 판정기 최빈값이라 등급만 보면 매번 이 경계에서 깨진다.
    """
    from app.workers.mannequin_job import edit_regressed
    s = types.SimpleNamespace(qc_score_auto_pass=80, qc_score_review=65, image_qc="enforce",
                              qc_edit_regression_margin=10)

    # 실측 케이스: 최저 80 → 76 (4점) — 등급은 내려가지만 노이즈 범위라 편집 유지
    pre, post = _p2(fid=83, nat=80, qual=85), _p2(fid=78, nat=76, qual=77)
    assert edit_regressed(s, pre, post) is False, "4점 하락은 판정 노이즈 — 2패스를 살려야 한다"

    # 경계: 마진과 정확히 같은 하락은 유지, 한 점 더 떨어지면 되돌린다
    assert edit_regressed(s, _p2(80), _p2(70)) is False, "하락 10 = 마진 → 유지"
    assert edit_regressed(s, _p2(80), _p2(69)) is True, "하락 11 > 마진 → 롤백"

    # 진짜 손상은 마진과 무관하게 걸러진다
    assert edit_regressed(s, _p2(90), _p2(30)) is True, "실측 85→30 손상"
    assert edit_regressed(s, _p2(90), _p2(88, critical=["breast-like bulges"])) is True, \
        "신규 치명오류는 하락폭과 무관"

    # 마진 미설정(구 동작)에서는 등급 하락만으로 롤백 — 설정이 없는 호출자 보호
    old = types.SimpleNamespace(qc_score_auto_pass=80, qc_score_review=65, image_qc="enforce")
    assert edit_regressed(old, pre, post) is True


def test_regressive_edit_is_reverted_to_pre_edit_image(monkeypatch):
    """편집이 등급을 떨어뜨리면 이미지·점수 **둘 다** 편집 전으로 돌아가야 한다.

    재판정만으로는 하락을 기록할 뿐 망친 편집본이 그대로 출고된다(2026-07-31 관측:
    가슴 2패스가 치마를 왜곡해 product_fidelity 85 → 30).
    """
    import test_mannequin_axis_qc as harness

    seq, calls = [_p2(90), _p2(30)], {"n": 0}

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        calls["n"] += 1
        return seq[min(calls["n"] - 1, len(seq) - 1)]

    async def fake_axis(**kw):
        return types.SimpleNamespace(mime=kw["res"].mime, image=b"edited-worse"), False

    captured = {}

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        captured["image"] = res.image     # D축은 되돌린 이미지를 봐야 한다
        return None

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    result, _g, r2, emits = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=1, verdicts=[], image_qc="shadow")

    assert result["qc_scores"]["product_fidelity"] == 90, "편집본 점수가 저장됐다"
    assert captured["image"] != b"edited-worse", "D축이 되돌리기 전 이미지를 봤다"
    assert all(b"edited-worse" != d for _k, d, _m in r2.puts), "망친 편집본이 R2 로 나갔다"
    assert "edit_reverted" in [p.get("status") for _t, p in emits]


def test_salvaged_candidate_still_gets_edit_and_series_qc(monkeypatch):
    """구제본도 편집·D축을 받고 그 결과가 저장된다 — salvaged 는 '더 재생성 안 함'만 뜻한다.

    사전 게이트에서 구제하면 본 경로로 계속 흐른다. 그때 편집·재판정·D축을 건너뛰면
    검증 안 된 이미지가 나가고, 반대로 이중 구제가 일어나면 상태가 꼬인다.
    """
    import test_mannequin_axis_qc as harness

    seen = {"series": 0, "axis": 0}

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        seen["series"] += 1
        return {"consistency": 88, "inconsistencies": ["여백 다름"]}

    async def fake_axis(**kw):
        seen["axis"] += 1
        return kw["res"], False

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    result, g, r2, emits = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=1, verdicts=[], image_qc="enforce",
        p2={"verdict": "retry", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 30, "physical_naturalness": 90, "image_quality": 90,
            "series_consistency": None, "critical_errors": []})
    assert result is not None and len(r2.puts) == 1
    assert seen["axis"] == 1 and seen["series"] == 1, "구제본이 편집·D축을 건너뛰었다"
    q = result["qc_scores"]
    assert q["series_consistency"] == 88, "구제본의 D축 결과가 저장되지 않았다"
    assert q["salvaged"] is True
    # 이중 구제 방지: qc_salvaged 이벤트는 1회만
    assert len([p for _t, p in emits if p.get("status") == "qc_salvaged"]) == 1
    assert len(g.calls) == 1  # 구제이므로 재생성 없음


def test_pre_gate_feedback_never_empty_when_only_scores_low(monkeypatch):
    """사전 게이트도 빈 피드백을 내면 안 된다 (codex MEDIUM — 최종 게이트만 고쳤던 것).

    점수만 낮고 critical_errors·correctionPrompt 가 없으면 재시도가 같은 프롬프트로 돈다.
    """
    import test_mannequin_axis_qc as harness

    _r, g, _r2, _e = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=2, verdicts=[], image_qc="enforce",
        p2={"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 20, "physical_naturalness": 90, "image_quality": 90,
            "series_consistency": None, "critical_errors": []})
    assert len(g.calls) == 2
    assert "IMPROVE" in g.calls[1]["prompt"], "사전 게이트 재시도가 지시 없이 돌았다"
    assert "product_fidelity" in g.calls[1]["prompt"]


@pytest.mark.parametrize("max_attempts", [1, 2, 3, 4, 5])
def test_budget_invariant_holds_for_every_max_attempts(monkeypatch, max_attempts):
    """어떤 max_attempts 에서도 생성+편집 총합이 상한을 넘지 않는다.

    워커의 `budget_left` 와 `_apply_axis_qc` 내부 예산은 이제 같은 잔량(calls_spent)을 본다.
    한쪽만 고치면 다시 어긋나므로 값마다 확인한다.
    """
    import test_mannequin_axis_qc as harness

    edits = {"n": 0}

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 10, "inconsistencies": ["다름"]}   # 항상 재생성 압력

    async def fake_axis(**kw):
        # 실제 _apply_axis_qc 의 내부 예산 가드를 그대로 재현한다. 통째로 fake 하면 가드를
        # 우회해 "코드가 막는 것"까지 초과로 세게 된다(내 fake 가 만든 거짓 실패).
        if kw["calls_spent"] >= kw["s"].mannequin_max_attempts:
            return kw["res"], False
        edits["n"] += 1
        return kw["res"], True

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    _r, g, r2, _e = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=max_attempts,
        verdicts=[], image_qc="enforce",
        p2={"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
            "series_consistency": None, "critical_errors": []})
    total = len(g.calls) + edits["n"]
    assert total <= max_attempts, (
        f"max_attempts={max_attempts} 인데 이미지 모델 {total}회"
        f"(생성 {len(g.calls)} + 편집 {edits['n']})")
    assert len(r2.puts) == 1, "저장은 최종 1건이어야 한다"


def test_total_image_model_calls_never_exceed_budget(monkeypatch):
    """예산은 **생성 + 편집 총합**이다 (codex MEDIUM).

    직전 버전은 편집 비용을 현재 attempt 에만 더해 이전 attempt 들의 편집이 안 세어졌고,
    max_attempts=3 에서 이미지 모델이 4회 호출됐다. 그때 내 테스트는 편집을 fake 처리하고
    **생성 횟수만 세어** 결함을 승인했다. 이제 편집도 같은 카운터로 센다.
    """
    import test_mannequin_axis_qc as harness

    calls = {"n": 0}

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 10, "inconsistencies": ["다름"]}

    async def fake_axis(**kw):
        if kw["calls_spent"] >= kw["s"].mannequin_max_attempts:
            return kw["res"], False
        calls["n"] += 1          # 편집 = 이미지 모델 호출 1회
        return kw["res"], True

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    # axis QC 를 enforce 로 둔다 — 편집이 실제로 발생할 수 있는 조건이어야 예산 계산이 검증된다.
    _r, g, r2, _e = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=3, verdicts=[], image_qc="enforce",
        p2={"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
            "series_consistency": None, "critical_errors": []})
    total = len(g.calls) + calls["n"]
    assert total <= 3, f"이미지 모델 호출 {total}회(생성 {len(g.calls)} + 편집 {calls['n']}) — 예산 3 초과"
    assert len(r2.puts) == 1


def _p2c(fid, critical=()):
    return {"verdict": "retry" if critical else "pass", "mismatches": [],
            "correctionPrompt": None, "product_fidelity": fid,
            "physical_naturalness": 95, "image_quality": 95,
            "series_consistency": None, "critical_errors": list(critical)}


def test_bust_pass_counts_against_the_same_budget(monkeypatch):
    """bust 2패스도 같은 통에서 나간다 — 안 세면 계약이 이 설정에서만 조용히 깨진다.

    `mannequin_bust_pass=on` 이면 매 채택본마다 이미지 모델을 한 번 더 부른다. 예산 카운터에
    빠져 있으면 max_attempts=3 에서 6회까지 나갈 수 있다(codex 2026-07-31 7차 HIGH).
    여기서는 fake gemini 가 생성·편집을 **한 카운터로** 세므로 누락되면 바로 초과로 잡힌다.
    """
    import test_mannequin_axis_qc as harness

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 10, "inconsistencies": ["다름"]}   # 항상 재생성 압력

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    _r, g, r2, _e = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=3, verdicts=[], image_qc="enforce",
        mannequin_bust_pass="on",
        p2={"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
            "series_consistency": None, "critical_errors": []})
    assert len(g.calls) <= 3, f"이미지 모델 {len(g.calls)}회 — 예산 3 초과(bust 미계상)"
    assert len(r2.puts) == 1


def test_pre_gate_reject_respects_budget(monkeypatch):
    """사전 게이트 거절 경로도 잔량을 봐야 한다.

    직전 버전은 예산 검사가 최종 게이트에만 있어, 사전 게이트로 빠진 attempt 가 예산을
    안 보고 다음 생성을 돌렸다.
    max=3 에서 1회차가 생성+편집으로 2콜을 쓰고 최종 거절되면, 2회차 생성(3콜)에서 예산이
    끝난다. 사전 게이트가 `attempt` 만 보면(2 < 3) 예산이 없는데도 재시도로 판단해 **구제를
    안 하고** 후보를 통째로 잃는다 — 손에 든 final_reject 가 있는데도 빈손으로 끝난다.
    """
    import test_mannequin_axis_qc as harness

    # 1회차 통과(편집 발생) → 재판정 → D축 10 으로 최종 거절 / 2회차 치명 오류로 사전 거절.
    seq = [_p2c(95), _p2c(95), _p2c(20, critical=["logo altered"])]
    n = {"i": 0}
    edits = {"n": 0}

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        n["i"] += 1
        return seq[min(n["i"] - 1, len(seq) - 1)]

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 10, "inconsistencies": ["다름"]}

    async def fake_axis(**kw):
        if kw["calls_spent"] >= kw["s"].mannequin_max_attempts:
            return kw["res"], False       # 실제 가드 재현
        edits["n"] += 1
        return types.SimpleNamespace(mime=kw["res"].mime, image=b"edited-%d" % edits["n"]), True

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    result, g, r2, _e = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=3, verdicts=[], image_qc="enforce")
    total = len(g.calls) + edits["n"]
    assert total <= 3, (
        f"이미지 모델 {total}회(생성 {len(g.calls)} + 편집 {edits['n']}) — 예산 3 초과")
    assert result is not None, "예산이 끝났는데 구제하지 않아 후보를 통째로 잃었다"
    assert result["qc_scores"]["salvaged"] is True
    # 구제는 **예산이 끝나는 그 지점**에서 일어나야 한다. 루프 밖으로 떨어져 나가서
    # 뒤늦게 건지는 것과는 다르다 — 사전 게이트가 잔량을 안 보면 reason 이 달라진다.
    salv = [p for _t, p in _e if p.get("status") == "qc_salvaged"]
    assert salv and salv[-1]["reason"] == "budget_exhausted", salv
    assert len(r2.puts) == 1


def test_last_attempt_generation_not_blocked_by_unused_edit_reservation(monkeypatch):
    """쓰지도 않을 편집분을 예약해 안전한 재생성을 막으면 안 된다(과소 사용).

    예약형에서는 max=4·attempt=3·편집 0 일 때 next_cost=2 라 4회차 생성이 막혔다.
    실소비만 세면 잔량이 남는 동안 계속 돈다.

    상한은 이제 max_attempts 가 아니라 **job 당 이미지 예산**이다: 생성은 BASE 1 +
    FULL_REGENERATION 1 = 2회까지고, 편집을 한 번도 안 썼다고 해서 3회차 생성이 생기지는
    않는다. 이 테스트가 지키는 성질(안 쓸 편집분을 미리 예약해 생성을 막지 않는다)은
    그대로다 — 예약 없이 남은 생성 슬롯을 끝까지 쓴다.
    """
    import test_mannequin_axis_qc as harness

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 10, "inconsistencies": ["다름"]}

    async def fake_axis(**kw):
        return kw["res"], False          # 편집은 한 번도 안 일어난다

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    _r, g, _r2, _e = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=4, verdicts=[], image_qc="enforce",
        p2={"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
            "series_consistency": None, "critical_errors": []})
    assert len(g.calls) == 2, f"생성 {len(g.calls)}회 — 남은 생성 슬롯을 안 썼다"


def test_comparator_uses_series_when_both_candidates_have_it():
    """둘 다 D축을 가졌으면 비교에 포함해야 한다.

    빼버리면 A~C 95/D10(일관성 붕괴)이 A~C 90/D60 을 이겨서, 세트에서 튀는 컷이 구제된다.
    한쪽만 가진 경우(사전 후보 vs 최종 후보)에는 여전히 빼야 공정하다.
    """
    s = make_settings(qc_score_auto_pass=80, qc_score_review=65)
    broken = {"product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
              "series_consistency": 10, "critical_errors": []}
    balanced = {"product_fidelity": 90, "physical_naturalness": 90, "image_quality": 90,
                "series_consistency": 60, "critical_errors": []}
    assert mannequin_job._is_better_candidate(s, broken, balanced) is False
    assert mannequin_job._is_better_candidate(s, balanced, broken) is True
    # 한쪽에 D축이 없으면 D 를 빼고 본다 — D 보유가 그 자체로 불리해지면 안 된다
    no_series = {**broken, "series_consistency": None}
    assert mannequin_job._is_better_candidate(s, no_series, balanced) is True


def test_new_critical_error_reverts_even_without_pre_scores():
    """편집 전 판정에 숫자가 없어도 신규 치명오류면 되돌린다.

    점수 유무 검사를 앞에 두면, 미채점 모델·판정 부분실패로 편집 전 점수가 비어 있을 때
    편집이 로고를 망가뜨려도 그냥 출고된다(codex 2026-07-31 8차 HIGH). 치명오류는 점수와
    무관하게 그 자체로 출고 불가다.
    """
    from app.workers.mannequin_job import edit_regressed
    s = make_settings(qc_score_auto_pass=80, qc_score_review=65, image_qc="enforce")
    no_scores = {"verdict": "pass", "mismatches": [], "critical_errors": []}
    assert edit_regressed(s, no_scores, _p2c(90, critical=["logo altered"])) is True
    # 치명오류가 안 생겼으면 점수 없는 편집 전과는 여전히 비교 불가 → 유지
    assert edit_regressed(s, no_scores, _p2c(10)) is False


def test_rollback_keeps_axis_fix_when_only_bust_score_regressed(monkeypatch):
    """두 편집이 다 돌았고 bust 만 망쳤으면 axis 교정은 살린다.

    한 덩어리로 되돌리면 핏을 제대로 고친 axis 결과까지 같이 버린다(codex 8차 MEDIUM).
    중간본 재판정 1콜은 **회귀가 실제로 났고 두 편집이 다 돈** 경우에만 나간다.
    """
    import test_mannequin_axis_qc as harness

    # 1: 생성 직후(양호) → 2: 두 편집 후(치명오류) → 3: axis 까지만(양호)
    seq = [_p2c(90), _p2c(30), _p2c(88)]
    n = {"i": 0}

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        n["i"] += 1
        return seq[min(n["i"] - 1, len(seq) - 1)]

    async def fake_axis(**kw):
        return types.SimpleNamespace(mime=kw["res"].mime, image=b"axis-fixed"), True

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return None

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    result, _g, r2, emits = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=3, verdicts=[],
        image_qc="shadow", mannequin_bust_pass="on")

    saved = r2.puts[-1][1]
    assert saved == b"axis-fixed", f"axis 교정까지 버렸다: {saved!r}"
    assert result["qc_scores"]["product_fidelity"] == 88
    reverted = [p for _t, p in emits if p.get("status") == "edit_reverted"]
    assert reverted and reverted[-1]["reason"] == "bust_only"


def test_new_identity_critical_after_edits_cannot_be_rescued_by_a_second_judge(monkeypatch):
    """새 치명 오류가 확인된 편집 체인은 확률적인 중간본 재판정으로 구조하면 안 된다.

    실 QA 재현: 최종 재판정이 ``garment color changed`` 를 잡았지만, untuck 중간본을 같은
    Vision에 다시 물었더니 pass가 나와 그 갈색 손상본이 저장됐다. critical은 점수가 아니라
    출고 금지 계약이므로 편집 전 마지막 통과본으로 즉시 돌아가야 한다.
    """
    import test_mannequin_axis_qc as harness

    seq = [
        _p2c(90),
        _p2c(55, critical=["garment color changed"]),
        _p2c(95),  # 이 판정까지 호출되면 확률적 구조 경로가 다시 열린 것
    ]
    n = {"i": 0}

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        n["i"] += 1
        return seq[min(n["i"] - 1, len(seq) - 1)]

    async def fake_axis(**kw):
        return types.SimpleNamespace(mime=kw["res"].mime, image=b"untuck-or-axis-drift"), True

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    result, _g, r2, emits = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=3, verdicts=[],
        image_qc="shadow", mannequin_bust_pass="on")

    assert r2.puts[-1][1] == harness._PNG_1PX
    assert result["qc_scores"]["product_fidelity"] == 90
    assert n["i"] == 2, "치명 오류 뒤 같은 편집 중간본을 다시 심사하면 안 된다"
    reverted = [p for _t, p in emits if p.get("status") == "edit_reverted"]
    assert reverted[-1]["reason"] == "critical_identity_regression"


def test_rollback_goes_all_the_way_when_axis_is_also_at_fault(monkeypatch):
    """중간본도 회귀면 편집 전까지 되돌린다 — axis 가 손상원인 경우."""
    import test_mannequin_axis_qc as harness

    seq = [_p2c(90), _p2c(30, critical=["broken"]), _p2c(30, critical=["broken"])]
    n = {"i": 0}

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        n["i"] += 1
        return seq[min(n["i"] - 1, len(seq) - 1)]

    async def fake_axis(**kw):
        return types.SimpleNamespace(mime=kw["res"].mime, image=b"axis-broke-it"), True

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return None

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    _r, _g, r2, emits = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=3, verdicts=[],
        image_qc="shadow", mannequin_bust_pass="on")

    saved = r2.puts[-1][1]
    assert saved == harness._PNG_1PX, f"편집 전 원본이 아니다: {saved!r}"
    assert _r["qc_scores"]["product_fidelity"] == 90, "편집 전 점수가 아니다"
    ev = [p for _t, p in emits if p.get("status") == "edit_reverted"][-1]
    assert ev["reason"] == "critical_identity_regression"
    assert (ev["from"], ev["to"]) == ("regenerate", "auto_pass")


def test_failed_bust_does_not_fake_a_second_checkpoint(monkeypatch):
    """bust 가 fail-open 하면 이미지는 안 바뀐다 — "둘 다 편집됨" 분기를 타면 안 된다.

    bust 는 거부·오류 때 원본을 그대로 돌려주면서 예산은 썼다고 보고한다. 소비 기준으로
    분기하면 최종본과 중간본이 **같은 이미지**인데도 중간본을 한 번 더 재판정하고, 확률적으로
    통과하면 손상본을 `bust_only` 로 그대로 출고한다(codex 9차 HIGH — 재현됨).
    """
    import test_mannequin_axis_qc as harness

    # axis 가 망친다 → 재판정 회귀 → bust 는 실패(이미지 불변). 세 번째 판정이 소비되면
    # (=중간본 분기를 탔다면) 그 값이 통과라 손상본이 출고된다.
    seq = [_p2c(90), _p2c(20, critical=["garment shape broken"]), _p2c(95)]
    n = {"i": 0}

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        n["i"] += 1
        return seq[min(n["i"] - 1, len(seq) - 1)]

    async def fake_axis(**kw):
        return types.SimpleNamespace(mime=kw["res"].mime, image=b"axis-broke-it"), True

    async def fake_bust(**kw):
        return kw["res"], True          # fail-open: 원본 그대로, 예산은 소비

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return None

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    monkeypatch.setattr(mannequin_job, "_apply_bust_pass", fake_bust)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    _r, _g, r2, emits = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=1, verdicts=[],
        image_qc="shadow", mannequin_bust_pass="on")

    assert r2.puts[-1][1] != b"axis-broke-it", "손상본이 가짜 중간본 분기로 출고됐다"
    assert n["i"] == 2, f"판정이 {n['i']}회 — 같은 이미지를 다시 판정했다"
    assert ([p for _t, p in emits if p.get("status") == "edit_reverted"][-1]["reason"]
            == "critical_identity_regression")


def test_generation_failure_still_salvages_accumulated_candidate(monkeypatch):
    """마지막 생성이 죽어도 손에 든 final_reject 는 출고한다.

    이전 attempt 에서 편집·D축까지 통과했다가 최종 게이트에만 걸린 후보가 있는데, 마지막
    생성 호출이 GeminiError 면 그냥 루프가 끝나 셀러가 빈손이 됐다(codex 9차 HIGH).
    """
    import test_mannequin_axis_qc as harness
    from app.agents.gemini_image import GeminiError

    class _G:
        def __init__(self):
            self.calls = []

        async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
            self.calls.append({"prompt": prompt})
            if len(self.calls) >= 2:
                raise GeminiError("생성 실패")
            return types.SimpleNamespace(mime="image/png", image=b"first-cut")

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return {"consistency": 10, "inconsistencies": ["다름"]}   # 1회차를 최종 거절시킨다

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    result, g, r2, emits = harness._run(
        monkeypatch, mode="off", guard=True, max_attempts=3, verdicts=[], image_qc="enforce",
        gemini=_G(),
        p2={"verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
            "series_consistency": None, "critical_errors": []})

    assert result is not None, "구제 가능한 후보를 들고도 빈손으로 끝났다"
    assert result["qc_scores"]["salvaged"] is True
    assert result["qc_scores"]["series_consistency"] == 10, "구제본의 D축 스냅샷이 유실됐다"
    assert len(r2.puts) == 1 and r2.puts[0][1] == b"first-cut"
    salv = [p for _t, p in emits if p.get("status") == "qc_salvaged"]
    assert salv and salv[-1]["reason"] == "loop_exhausted"


def test_pre_gate_only_candidate_is_processed_then_salvaged(monkeypatch):
    """사전 게이트 후보만 남고 후속 생성이 전부 죽어도 빈손으로 끝나지 않는다.

    "마지막 거절본이라도 빈손보다 낫다"는 기존 계약(test_mannequin_axis_qc)과 "검증 안 된
    원본을 그대로 내보내지 않는다"(codex 4차 HIGH)를 **둘 다** 지켜야 한다 — 즉 편집·D축을
    태운 뒤 구제한다(codex 10차 HIGH).
    """
    import test_mannequin_axis_qc as harness
    from app.agents.gemini_image import GeminiError

    class _G:
        def __init__(self):
            self.calls = []

        async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
            self.calls.append({"prompt": prompt})
            if len(self.calls) == 1:
                return types.SimpleNamespace(mime="image/png", image=b"only-cut")
            raise GeminiError("생성 실패")

    seen = {"series": 0, "axis": 0}

    async def fake_axis(**kw):
        seen["axis"] += 1
        return kw["res"], False

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        seen["series"] += 1
        return {"consistency": 55, "inconsistencies": ["배경 밝음"]}

    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    result, _g, r2, emits = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=3, verdicts=[], image_qc="enforce",
        gemini=_G(),
        p2=_p2c(20, critical=["logo altered"]))          # 매 attempt 사전 게이트 거절

    assert result is not None, "사전 게이트 후보를 들고도 빈손으로 끝났다"
    assert result["qc_scores"]["salvaged"] is True
    assert seen["series"] == 1, "구제본이 D축 판정을 못 받았다"
    assert seen["axis"] == 1, "구제본이 편집 단계를 건너뛰었다(사전 게이트 경로는 축 QC 미실행)"
    assert result["qc_scores"]["series_consistency"] == 55
    assert len(r2.puts) == 1 and r2.puts[0][1] == b"only-cut"
    assert [p for _t, p in emits if p.get("status") == "qc_salvaged"][-1]["reason"] == "loop_exhausted"


def test_real_axis_qc_respects_shared_budget(monkeypatch):
    """`_apply_axis_qc` 를 **실제로** 돌려 예산 가드를 확인한다.

    다른 예산 테스트들은 이 함수를 mock 하고 가드를 테스트 안에 복사한다 — 운영 가드를
    `attempt` 기준으로 되돌려도 통과한다(codex 8차 LOW). 여기서는 진짜를 부른다.
    """
    import test_mannequin_axis_qc as harness

    calls = {"n": 0}

    class _G:
        async def generate_content_image(self, *a, **kw):
            calls["n"] += 1
            return types.SimpleNamespace(mime="image/png", image=b"edited")

    async def run(calls_spent):
        calls["n"] = 0
        res = types.SimpleNamespace(mime="image/png", image=b"orig")
        out, spent = await mannequin_job._apply_axis_qc(
            budget=make_image_budget_gate(),
            pool=_FakePool(), gemini=_G(), s=make_settings(
                mannequin_axis_qc="enforce", mannequin_max_attempts=3),
            job_id="j1", candidate="A", attempt=1, model="m", res=res,
            prod_imgs=[types.SimpleNamespace(mime="image/png", data=b"p")], match_img=None,
            fit_profile=harness.PROFILE, profile_hash="h", calls_spent=calls_spent)
        return spent, calls["n"]

    async def fake_verdict(settings, prods, gen_img, fit_profile, match_image=None):
        return harness._verdict(fit_ok=False)          # 편집이 필요한 상태

    async def fake_emit(pool, job_id, event_type, payload):
        pass

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    monkeypatch.setattr(mannequin_job.mannequin_fit_qc, "verdict", fake_verdict)
    monkeypatch.setattr(mannequin_job, "_MANNEQUIN_AXIS_QC_ENFORCEMENT_READY", True)

    assert asyncio.run(run(1)) == (True, 1), "잔량이 있는데 편집을 안 했다"
    assert asyncio.run(run(3)) == (False, 0), "예산이 없는데 편집 호출이 나갔다"


def test_final_salvage_is_not_reprocessed(monkeypatch):
    """최종 단계 구제본은 편집·D축을 **다시 돌리지 않는다**.

    이미 편집·재판정·D축까지 끝난 출고 준비본이다. 본 경로를 다시 태우면 bust 가 두 번
    적용되고 D축 스냅샷이 새 판정으로 덮어써진다(codex 2026-07-31 7차 MEDIUM).
    """
    import test_mannequin_axis_qc as harness

    # 1회차: 사전 게이트 통과 → D축 10 으로 최종 거절되어 final_reject 에 적재
    # 2회차: 치명 오류로 사전 게이트 거절 + 예산 소진 → final_reject 구제
    # 1회차 통과(편집으로 이미지 변경 → 재판정) → D축 10 으로 최종 거절 → final_reject 적재
    # 2회차 치명오류로 사전 게이트 거절 + 예산 소진 → final_reject 구제
    seq = [_p2c(95), _p2c(95), _p2c(20, critical=["logo altered"])]
    calls = {"p2": 0, "series": 0, "axis": 0}

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        calls["p2"] += 1
        return seq[min(calls["p2"] - 1, len(seq) - 1)]

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        calls["series"] += 1
        return {"consistency": 10, "inconsistencies": ["다름"]}

    async def fake_axis(**kw):
        calls["axis"] += 1
        return kw["res"], False

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_axis)
    result, _g, r2, emits = harness._run(
        monkeypatch, mode="enforce", guard=True, max_attempts=3, verdicts=[],
        image_qc="enforce", mannequin_bust_pass="on")

    assert result is not None and result["qc_scores"]["salvaged"] is True
    assert calls["series"] == 1, f"D축이 {calls['series']}회 — 구제본을 다시 판정했다"
    assert calls["axis"] == 1, f"축 편집이 {calls['axis']}회 — 구제본을 다시 편집했다"
    bust = [p for _t, p in emits if p.get("status") == "bust_pass"]
    assert len(bust) == 1, f"bust 가 {len(bust)}회 — 구제본에 2패스가 다시 적용됐다"
    assert result["qc_scores"]["series_consistency"] == 10, "D축 스냅샷이 유실됐다"
    assert len(r2.puts) == 1
    assert "qc_salvaged" in [p.get("status") for _t, p in emits]


def test_loop_terminates_when_every_attempt_rejects(monkeypatch):
    """전 attempt 가 D축 거절이어도 루프는 max_attempts 에서 끝난다 — 무한 루프 방지.

    `continue` 경로가 예산 조건을 잘못 읽으면 생성 콜이 무한히 늘 수 있다. 상한을 못박는다.

    상한은 job 당 이미지 예산이 준다 — 생성 슬롯은 BASE 1 + FULL_REGENERATION 1 뿐이라
    max_attempts 를 3 으로 둬도 2회에서 멈춘다.
    """
    result, g, r2, emits = _run_loop(
        monkeypatch, max_attempts=3,
        series_scores=[{"consistency": 10, "inconsistencies": ["다름"]}] * 3)
    assert len(g.calls) == 2, f"생성 콜 {len(g.calls)}회 — 이미지 예산(생성 2슬롯)을 넘었다"
    assert len(r2.puts) == 1                      # 마지막 1건만 저장
    assert result is not None                      # 구제 출고
    assert result["qc_scores"]["salvaged"] is True
    rejects = [p for _t, p in emits if p.get("status") == "final_qc_reject"]
    # 두 생성 모두 거절되고, 구제는 루프가 끝난 뒤 마지막 후보로 이뤄진다. 예산 이전에는
    # 3회차가 루프 안에서 구제돼 거절이 2건이었다 — 건수는 같고 이유가 달라졌다.
    assert len(rejects) == 2


def test_feedback_reaches_every_subsequent_attempt(monkeypatch):
    """거절 사유가 다음 attempt 프롬프트에 실린다 — 안 실리면 재생성이 무의미해진다.

    이미지 예산이 생성을 2회로 묶으므로 "다음"은 한 번뿐이다. 사유가 그 한 번에
    실리는지가 이 테스트의 전부이고, 3회차가 사라진 것은 예산의 결과지 배선의 회귀가
    아니다.
    """
    _r, g, _r2, _e = _run_loop(
        monkeypatch, max_attempts=3,
        series_scores=[{"consistency": 10, "inconsistencies": ["배경 어두움"]},
                       {"consistency": 12, "inconsistencies": ["여백 다름"]},
                       {"consistency": 99, "inconsistencies": []}])
    assert len(g.calls) == 2, "생성 슬롯은 2개다"
    assert "배경 어두움" in g.calls[1]["prompt"]


# 고아 객체 계약은 test_low_series_score_actually_rerolls_and_stores_once 의
# `len(r2.puts) == 1` 이 행동으로 잠근다(소스 문자열 순서를 보는 테스트는 구현 복사라 제거).


def test_worker_passes_declared_fit_to_image_qc(monkeypatch):
    """워커가 선언 핏을 QC 로 넘겨야 한다 — 안 넘기면 조정된 핏이 전부 치명오류가 된다."""
    import test_mannequin_axis_qc as harness

    seen = []

    async def fake_p2(s, prods, gen, *, scored=False, fit_profile=None):
        seen.append(fit_profile)
        return _p2c(90)

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res):
        return None

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    harness._run(monkeypatch, mode="off", guard=True, max_attempts=1, verdicts=[],
                 image_qc="shadow")
    assert seen and seen[0] is harness.PROFILE, seen


def test_effective_image_size_upgrades_only_pattern_products():
    """미세 패턴만 해상도를 올린다 — 2K 에서는 한 주기당 픽셀이 모자라 두 색 줄이 뭉개진다.

    2026-08-01 실측: 줄 주기 8.9px @2K → 주기를 이루는 요소(색 선·흰 간격)당 2px 남짓.
    프롬프트로는 못 넘는 축이라 해상도로 푼다. 무지는 승급하지 않는다(비용만 증가).
    """
    from app.workers.mannequin_job import effective_image_size

    s = types.SimpleNamespace(mannequin_image_size="2K", mannequin_pattern_image_size="4K")
    striped = ({"name": "스트라이프 셔츠"}, {})
    plain = ({"name": "무지 티셔츠"}, {})
    assert effective_image_size(s, *striped) == "4K"
    assert effective_image_size(s, *plain) == "2K"

    # 킬 스위치 — off 면 승급 없이 기본값을 쓴다
    off = types.SimpleNamespace(mannequin_image_size="2K", mannequin_pattern_image_size="OFF")
    assert effective_image_size(off, *striped) == "2K"

    # 설정 자체가 없는 호출자(구 설정 객체)도 죽지 않는다
    legacy = types.SimpleNamespace(mannequin_image_size="1K")
    assert effective_image_size(legacy, *striped) == "1K"

    # 승인 Product Truth 가 stale 텍스트보다 우선한다.
    solid_truth = {"status": "approved", "patternSpec": {"type": "SOLID", "finePattern": False}}
    assert effective_image_size(s, {"name": "스트라이프 셔츠"}, {}, solid_truth) == "2K"
    stripe_truth = {"status": "approved", "patternSpec": {"type": "STRIPE", "finePattern": True}}
    assert effective_image_size(s, {"name": "무지 티셔츠"}, {}, stripe_truth) == "4K"
    stripe_truth_not_fine = {
        "status": "approved",
        "patternSpec": {"type": "STRIPE", "finePattern": False},
    }
    assert effective_image_size(s, {"name": "무지 티셔츠"}, {}, stripe_truth_not_fine) == "4K"
    check_truth = {
        "status": "approved",
        "patternSpec": {"type": "CHECK", "finePattern": True},
    }
    assert effective_image_size(s, {"name": "체크 셔츠"}, {}, check_truth) == "2K"


def test_tier_for_job_splits_adjust_from_initial_generation():
    """조정만 다른 모델로 보낼 수 있어야 한다 — 둘은 같은 워커를 타서 env 하나로는 못 가른다.

    동기(2026-07-31): 조정 흐름에서만 Flash 를 시험하고 싶다는 요구. 초기 생성까지 같이
    바뀌면 결과 차이가 모델 탓인지 생성 자체가 바뀐 탓인지 구분되지 않는다.
    기본값(빈 문자열)에서는 분기가 아예 없어야 한다 — 프로덕션 동작 불변이 이 노브의 전제다.
    """
    from app.workers.mannequin_job import tier_for_job

    regen = {"payload": {"mode": "regenerate"}}
    gen = {"payload": {"mode": "generate"}}

    off = types.SimpleNamespace(mannequin_tier="image_high", mannequin_adjust_tier="")
    assert tier_for_job(off, regen) == "image_high", "미설정이면 조정도 분기 없음"
    assert tier_for_job(off, gen) == "image_high"

    on = types.SimpleNamespace(mannequin_tier="image_high", mannequin_adjust_tier="image_light")
    assert tier_for_job(on, regen) == "image_light", "조정만 다른 tier"
    assert tier_for_job(on, gen) == "image_high", "초기 생성은 고정 — 비교의 기준선"

    # payload 가 없거나 job 자체가 없어도 죽지 않는다(구 잡·직접 호출 경로)
    assert tier_for_job(on, {}) == "image_high"
    assert tier_for_job(on, {"payload": None}) == "image_high"
    assert tier_for_job(on, None) == "image_high"
    # 설정 객체에 필드가 없는 호출자(구 설정)도 분기 없이 동작
    legacy = types.SimpleNamespace(mannequin_tier="image_high")
    assert tier_for_job(legacy, regen) == "image_high"


def test_adjust_tier_loader_falls_back_on_typo(monkeypatch):
    """오타는 조용히 ""(분기 없음)로 떨어져야 한다 — 알 수 없는 tier 로 resolve 하면 터진다."""
    from app.config import load_settings

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("MANNEQUIN_ADJUST_TIER", "image_ligth")  # 오타
    assert load_settings().mannequin_adjust_tier == ""
    monkeypatch.setenv("MANNEQUIN_ADJUST_TIER", "image_light")
    assert load_settings().mannequin_adjust_tier == "image_light"


def test_untuck_pass_gate_and_single_task_call():
    """untuck 패스 — top/outer + 매칭 하의 첨부에서만, 생성본 1장·과제 1개로 호출된다.

    프롬프트 5회 강화와 QC 재생성이 모두 소진된 뒤의 구조 변경(2026-08-01). QC 검출이
    불안정해(같은 유형을 잡기도 02:05, 놓치기도 04:57) 게이트로 쓰지 않고 항상 1회 돈다.
    bust v3 안에 untuck 이 부차 지시로 있을 땐 실패했다 — 단일 과제 분리가 변수다.
    """
    import asyncio
    from app.agents import mannequin_untuck
    from app.workers import mannequin_job as mj

    # 게이트
    assert mannequin_untuck.should_apply("on", "top", True)
    assert mannequin_untuck.should_apply("on", "outer", True)
    assert not mannequin_untuck.should_apply("off", "top", True), "플래그 off 면 안 돈다"
    assert not mannequin_untuck.should_apply("on", "bottom", True), "하의 상품은 방향이 다르다(WS4)"
    assert not mannequin_untuck.should_apply("on", "dress", True), "원피스는 매칭 하의가 없다"
    assert not mannequin_untuck.should_apply("on", "top", False), "하의가 화면에 없으면 tuck 이 없다"

    sent = {}

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
            sent["images"] = images; sent["prompt"] = prompt; sent["size"] = size
            return types.SimpleNamespace(image=b"untucked", mime="image/png")

    async def fake_emit(pool, job_id, et, payload):
        sent.setdefault("events", []).append(payload)

    s = types.SimpleNamespace(
        mannequin_untuck_pass="on", mannequin_max_attempts=5, mannequin_image_size="2K",
        mannequin_aspect_ratio="2:3", model_image_high="gemini-3-pro-image",
        model_image_light="gemini-3.1-flash-image", model_text="gpt-5.4-mini")
    mj._emit = fake_emit
    res = types.SimpleNamespace(image=b"cut", mime="image/png")
    match = mj.InlineImage("image/png", b"bottom")

    out, spent = asyncio.run(mj._apply_untuck_pass(
        budget=make_image_budget_gate(),
        pool=None, gemini=_Gemini(), s=s, job_id="j1", candidate="A", attempt=1,
        res=res, match_img=match, calls_spent=0, clothing_type="top", image_size="4K"))

    assert spent is True and out.image == b"untucked"
    assert len(sent["images"]) == 1 and sent["images"][0].data == b"cut", \
        "이미지 1장·과제 1개 — 매칭/상품 사진을 섞으면 과제가 흐려진다"
    assert sent["size"] == "4K", "승급 해상도를 편집에서도 유지"
    assert "unbroken visible line" in sent["prompt"], "관측 가능한 목표가 있어야 한다"
    assert "return it unchanged" in sent["prompt"], "이미 빠져 있으면 무변경 — no-op 계약"
    assert any(e.get("status") == "untuck_pass" and e.get("outcome") == "applied"
               for e in sent["events"])


def test_bottom_product_manifest_and_prompt_keep_the_product_visible():
    """하의 상품 + 매칭 상의 — 매니페스트가 상의를 '하의'라고 잘못 알려주지 않는다(WS4).

    예전 매니페스트는 무조건 "matching BOTTOM" 이라, 하의 상품에서 첨부된 매칭 '상의' 이미지를
    하의라고 서술했다. 프롬프트에도 매칭 상의 규칙이 전무해 모델이 상의를 길게 그려 상품(바지)
    허리를 가렸다(2026-08-01 셀러 보고).
    """
    from app.workers.mannequin_job import _build_manifest
    from app.agents.prompts import load_prompt_template
    from conftest import make_settings

    prod = [{"slot": "Front"}]
    bottom = _build_manifest(prod, True, "bottom")
    top = _build_manifest(prod, True, "top")
    assert "matching TOP garment" in bottom and "fully visible" in bottom
    assert "matching BOTTOM garment" in top, "상의 상품 경로는 불변"
    assert _build_manifest(prod, False, "bottom").count("matching") == 0

    template = load_prompt_template(make_settings())
    assert "MATCHING TOP (if attached" in template
    assert "waistband, closure and belt loops are visible" in template, \
        "관측 가능한 목표 — 상품 허리 전부 노출"
    assert "unless a matching-top length is declared" in template, \
        "셀러가 조정하면(WS2 스텝) 선언이 이긴다"
