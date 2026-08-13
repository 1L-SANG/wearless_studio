"""마네킹 워커 협조적 취소: 비싼 호출·R2·finalize 경계와 lease 펜스."""

import asyncio
import contextlib
import types

import pytest

from app import repo
from app.workers import mannequin_job
from conftest import make_settings


_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000002000000020802000000"
    "fdd49a730000001349444154789c63fcffff3f0303031303180000240603"
    "015da24e880000000049454e44ae426082"
)
_PROFILE = {
    "category": "top",
    "gender": "women",
    "source": "seller",
    "axes": {"fit": "slim"},
    "version": 1,
}


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


class _R2:
    def __init__(self):
        self.puts = []

    def get_bytes(self, key):
        return b"input"

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, data, mime))


def _wire_worker(monkeypatch, *, state, gemini=None, candidate_runner=None, with_product=True):
    calls = {"success": [], "failure": [], "emits": [], "cancel_checks": 0}

    async def get_product(conn, project_id):
        images = [{"id": "prod", "slot": "Front"}] if with_product else []
        return {
            "name": "티셔츠",
            "clothing_type": "top",
            "colors": [{"isBase": True, "images": images}],
        }

    async def get_analysis(conn, project_id):
        return {"targetGenders": ["women"], "fit": "regular"}

    async def get_asset_for_user(conn, user_id, asset_id):
        return {"id": asset_id, "mime_type": "image/png", "r2_key": f"{asset_id}.png"}

    async def is_job_cancelled(conn, job_id):
        calls["cancel_checks"] += 1
        return bool(state["cancelled"])

    async def finalize_success(conn, **kwargs):
        calls["success"].append(kwargs)
        return {"cuts": kwargs["candidates"], "available": 6}

    async def finalize_failure(conn, **kwargs):
        calls["failure"].append(kwargs)
        return True

    async def fake_emit(pool, job_id, event_type, payload):
        calls["emits"].append((event_type, dict(payload)))

    async def no_style_refs(*args, **kwargs):
        return [], []

    for name, fn in (
        ("get_product", get_product),
        ("get_analysis", get_analysis),
        ("get_asset_for_user", get_asset_for_user),
        ("is_job_cancelled", is_job_cancelled),
        ("finalize_mannequin_success", finalize_success),
        ("finalize_mannequin_failure", finalize_failure),
    ):
        monkeypatch.setattr(mannequin_job.repo, name, fn)
    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    monkeypatch.setattr(mannequin_job, "_load_style_refs", no_style_refs)
    if candidate_runner is not None:
        monkeypatch.setattr(mannequin_job, "_run_candidate", candidate_runner)

    settings = make_settings(
        base_mannequin_women_asset_id="base-women",
        base_mannequin_men_asset_id="base-men",
        mannequin_axis_qc="off",
        mannequin_bust_pass="off",
        mannequin_fabric_pass="off",
        mannequin_untuck_pass="off",
        image_qc="off",
        r2_bucket="bucket",
    )
    r2 = _R2()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=r2, gemini=gemini,
    ))
    job = {
        "id": "job-1",
        "user_id": "user-1",
        "project_id": "project-1",
        "lease_token": "worker:lease-1",
        "credits_reserved": 2,
        "payload": {"mode": "generate"},
    }
    return app, job, r2, calls


def test_cancel_after_generation_result_discards_bytes_and_skips_finalize(monkeypatch):
    state = {"cancelled": False}

    class _Gemini:
        async def generate_content_image(self, *args, **kwargs):
            state["cancelled"] = True
            return types.SimpleNamespace(image=_PNG_1PX, mime="image/png")

    app, job, r2, calls = _wire_worker(monkeypatch, state=state, gemini=_Gemini())

    asyncio.run(mannequin_job.run_mannequin_job(app, job))

    assert calls["cancel_checks"] >= 2  # 생성 직전 false → 결과 직후 true
    assert r2.puts == []
    assert calls["success"] == [] and calls["failure"] == []


def test_candidate_cancel_sentinel_is_not_swallowed_as_generation_failure(monkeypatch):
    state = {"cancelled": False}

    async def cancelled_candidate(**kwargs):
        raise mannequin_job._MannequinJobCancelled

    app, job, _r2, calls = _wire_worker(
        monkeypatch, state=state, candidate_runner=cancelled_candidate,
    )

    asyncio.run(mannequin_job.run_mannequin_job(app, job))

    assert calls["success"] == [] and calls["failure"] == []


@pytest.mark.parametrize(
    ("cancel_at", "expected_passes"),
    [
        # untuck 은 편집 체인에서 빠졌다(2026-08-12) — 저장 직전 전용 post-pass 로 이동.
        # 체인은 axis → bust → fabric, 각 패스 앞뒤 체크포인트 6개.
        (1, []),
        (2, ["axis"]),
        (3, ["axis"]),
        (4, ["axis", "bust"]),
        (5, ["axis", "bust"]),
        (6, ["axis", "bust", "fabric"]),
    ],
)
def test_edit_orchestration_checks_before_and_after_each_pass(
    monkeypatch, cancel_at, expected_passes,
):
    passes = []
    checks = 0
    res = types.SimpleNamespace(image=_PNG_1PX, mime="image/png")

    async def cancel_check():
        nonlocal checks
        checks += 1
        return checks == cancel_at

    def fake_pass(name):
        async def _run(**kwargs):
            passes.append(name)
            return kwargs["res"], False

        return _run

    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", fake_pass("axis"))
    monkeypatch.setattr(mannequin_job, "_apply_bust_pass", fake_pass("bust"))
    monkeypatch.setattr(mannequin_job, "_apply_fabric_pass", fake_pass("fabric"))

    with pytest.raises(mannequin_job._MannequinJobCancelled):
        asyncio.run(mannequin_job._apply_edits(
            pool=None,
            gemini=None,
            s=make_settings(),
            job_id="job-1",
            candidate="A",
            attempt=1,
            model="model",
            res=res,
            p2=None,
            prod_imgs=[],
            match_img=None,
            fit_profile=None,
            profile_hash="hash",
            base_gender="women",
            calls_spent=0,
            cancel_check=cancel_check,
        ))

    assert passes == expected_passes


@pytest.mark.parametrize(
    ("answers", "expected_image_calls"),
    [([True], 0), ([False, True], 1)],
)
def test_axis_edit_gemini_has_its_own_before_and_after_checkpoints(
    monkeypatch, answers, expected_image_calls,
):
    checks = iter(answers)
    image_calls = 0
    judge_calls = 0

    async def cancel_check():
        return next(checks)

    async def fake_emit(*args, **kwargs):
        return None

    async def fake_verdict(*args, **kwargs):
        nonlocal judge_calls
        judge_calls += 1
        return {
            "identityPass": True,
            "mismatches": [],
            "axisPass": [{
                "axis": "fit",
                "target": "slim",
                "pass": False,
                "visible": True,
                "observedLandmark": "regular fit",
            }],
        }

    class _Gemini:
        async def generate_content_image(self, *args, **kwargs):
            nonlocal image_calls
            image_calls += 1
            return types.SimpleNamespace(image=_PNG_1PX + b"edited", mime="image/png")

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    monkeypatch.setattr(mannequin_job.mannequin_fit_qc, "verdict", fake_verdict)
    monkeypatch.setattr(mannequin_job, "_MANNEQUIN_AXIS_QC_ENFORCEMENT_READY", True)

    with pytest.raises(mannequin_job._MannequinJobCancelled):
        asyncio.run(mannequin_job._apply_axis_qc(
            pool=None,
            gemini=_Gemini(),
            s=make_settings(mannequin_axis_qc="enforce", mannequin_max_attempts=3),
            job_id="job-1",
            candidate="A",
            attempt=1,
            model="model",
            res=types.SimpleNamespace(image=_PNG_1PX, mime="image/png"),
            prod_imgs=[],
            match_img=None,
            fit_profile=_PROFILE,
            profile_hash="hash",
            calls_spent=1,
            cancel_check=cancel_check,
        ))

    assert image_calls == expected_image_calls
    assert judge_calls == 1  # 결과 뒤 취소면 편집본 재판정에도 들어가지 않는다


@pytest.mark.parametrize(
    ("answers", "expected_puts"),
    [([True], 0), ([False, True], 1)],
)
def test_r2_save_checks_before_and_after_put(answers, expected_puts):
    checks = iter(answers)
    r2 = _R2()

    async def cancel_check():
        return next(checks)

    with pytest.raises(mannequin_job._MannequinJobCancelled):
        asyncio.run(mannequin_job._save_cut(
            s=make_settings(r2_bucket="bucket"),
            r2=r2,
            user_id="user-1",
            project_id="project-1",
            job_id="job-1",
            candidate="A",
            base_fit="regular",
            res=types.SimpleNamespace(image=_PNG_1PX, mime="image/png"),
            qc_scores=None,
            cancel_check=cancel_check,
        ))

    assert len(r2.puts) == expected_puts


def test_success_finalize_checkpoint_keeps_cancelled_job_terminal(monkeypatch):
    state = {"cancelled": False}

    async def finished_candidate(**kwargs):
        state["cancelled"] = True
        return {
            "asset_id": "asset-1",
            "bucket": "bucket",
            "key": "orphan.png",
            "mime": "image/png",
            "size": 3,
            "width": 1,
            "height": 1,
            "candidate": "A",
            "base_fit": "regular",
        }

    app, job, _r2, calls = _wire_worker(
        monkeypatch, state=state, candidate_runner=finished_candidate,
    )

    asyncio.run(mannequin_job.run_mannequin_job(app, job))

    assert calls["success"] == [] and calls["failure"] == []


def test_failure_finalize_checkpoint_keeps_cancelled_job_terminal(monkeypatch):
    state = {"cancelled": True}
    app, job, _r2, calls = _wire_worker(
        monkeypatch, state=state, with_product=False,
    )

    asyncio.run(mannequin_job.run_mannequin_job(app, job))

    assert calls["success"] == [] and calls["failure"] == []


class _CancelledRepoCursor:
    def __init__(self, executed):
        self.executed = executed

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.executed.append((normalized, params))
        if "select id from jobs" in normalized:
            assert "status = 'running'" in normalized

    async def fetchone(self):
        # 실제 cancelled 행은 위 status='running' lease fence와 매치되지 않는다.
        return None


class _CancelledRepoConn:
    def __init__(self, executed):
        self.executed = executed

    def cursor(self):
        return _CancelledRepoCursor(self.executed)


def test_repo_success_finalize_does_not_overwrite_cancelled_or_settle(monkeypatch):
    executed = []

    async def unexpected_consume(*args, **kwargs):
        raise AssertionError("cancelled job must not be charged by worker finalize")

    monkeypatch.setattr(repo, "_consume_buckets", unexpected_consume)
    result = asyncio.run(repo.finalize_mannequin_success(
        _CancelledRepoConn(executed),
        job_id="job-1",
        lease_token="worker:lease-1",
        user_id="user-1",
        project_id="project-1",
        candidates=[{
            "asset_id": "asset-1",
            "bucket": "bucket",
            "key": "cut.png",
            "mime": "image/png",
            "candidate": "A",
            "base_fit": "regular",
        }],
        reserved=2,
        charge=2,
        metadata={},
    ))

    assert result is None
    assert len(executed) == 1 and "select id from jobs" in executed[0][0]


def test_repo_failure_finalize_does_not_overwrite_cancelled_or_release(monkeypatch):
    executed = []

    async def unexpected_settle(*args, **kwargs):
        raise AssertionError("cancelled job must not be released by worker finalize")

    monkeypatch.setattr(repo, "_settle_credits", unexpected_settle)
    result = asyncio.run(repo.finalize_mannequin_failure(
        _CancelledRepoConn(executed),
        job_id="job-1",
        lease_token="worker:lease-1",
        user_id="user-1",
        project_id="project-1",
        reserved=2,
        settle_key="credit:job:job-1:settle",
        message="failed",
        metadata={},
    ))

    assert result is False
    assert len(executed) == 1 and "select id from jobs" in executed[0][0]
