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
        app=app, pool=_FakePool(), s=make_settings(), job_id="j1", user_id="u1",
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
        app=app, pool=_FakePool(), s=make_settings(), job_id="j1", user_id="u1",
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

def test_series_reject_does_not_leave_orphan_r2_object(monkeypatch):
    """D축 재생성 분기는 **R2 저장 전에** 일어나야 한다.

    저장 후 continue 하면 재시도마다 아무도 참조하지 않는 객체가 버킷에 쌓인다(DB 행은
    최종 채택본만 생기므로 정리할 근거조차 남지 않는다). 소스 순서로 계약을 고정한다.
    """
    import inspect
    src = inspect.getsource(mannequin_job._run_candidate)
    reject_at = src.index('"status": "series_qc_reject"')
    put_at = src.index("r2.put_bytes")
    assert reject_at < put_at, "series_qc_reject 분기가 R2 저장보다 뒤에 있다 — 고아 객체가 쌓인다"
