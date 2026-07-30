"""D축 시리즈 일관성 QC 워커 배선 — fail-open 규율과 점수 합류.

핵심 계약: 판정은 **관측이지 게이트가 아니다**. 기존 컷 조회 실패·R2 미스·모델 오류·첫 컷
어느 경우에도 생성이 멈추면 안 된다(_apply_axis_qc·_apply_bust_pass 와 같은 규율).
"""
import asyncio
import contextlib
import types

from app.agents import mannequin_series_qc
from app.workers import mannequin_job
from conftest import make_settings


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

    async def fake_p2(s, prods, gen, *, scored=False):
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

    async def fake_p2(s, prods, gen, *, scored=False):
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


def test_loop_terminates_when_every_attempt_rejects(monkeypatch):
    """전 attempt 가 D축 거절이어도 루프는 max_attempts 에서 끝난다 — 무한 루프 방지.

    `continue` 경로가 예산 조건을 잘못 읽으면 생성 콜이 무한히 늘 수 있다. 상한을 못박는다.
    """
    result, g, r2, emits = _run_loop(
        monkeypatch, max_attempts=3,
        series_scores=[{"consistency": 10, "inconsistencies": ["다름"]}] * 3)
    assert len(g.calls) == 3, f"생성 콜 {len(g.calls)}회 — max_attempts(3) 를 넘었다"
    assert len(r2.puts) == 1                      # 마지막 1건만 저장
    assert result is not None                      # 구제 출고
    assert result["qc_scores"]["salvaged"] is True
    rejects = [p for _t, p in emits if p.get("status") == "final_qc_reject"]
    assert len(rejects) == 2                       # 1·2회차만 거절, 3회차는 구제


def test_feedback_reaches_every_subsequent_attempt(monkeypatch):
    """거절 사유가 다음 attempt 프롬프트에 계속 실린다 — 중간에 끊기면 재생성이 무의미해진다."""
    _r, g, _r2, _e = _run_loop(
        monkeypatch, max_attempts=3,
        series_scores=[{"consistency": 10, "inconsistencies": ["배경 어두움"]},
                       {"consistency": 12, "inconsistencies": ["여백 다름"]},
                       {"consistency": 99, "inconsistencies": []}])
    assert "배경 어두움" in g.calls[1]["prompt"]
    assert "여백 다름" in g.calls[2]["prompt"]     # 2회차 사유가 3회차로
    assert "배경 어두움" not in g.calls[2]["prompt"]  # 낡은 사유는 갈아탄다


# 고아 객체 계약은 test_low_series_score_actually_rerolls_and_stores_once 의
# `len(r2.puts) == 1` 이 행동으로 잠근다(소스 문자열 순서를 보는 테스트는 구현 복사라 제거).
