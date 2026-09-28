"""인물 LoRA 자동 큐잉 + 집은 뒤 학습셋 준비 — 회차 흐름(2026-09-28).

조건 SQL 자체는 test_lora_auto_enqueue_db.py 가 실제 Postgres 로 본다. 여기서는 회차가
그 조각들을 **어떤 순서로** 부르고, 돈이 나가기 전에 어디서 멈추는지를 본다.
  · 매 회차 첫머리에 자동 큐잉(스윕)이 돈다 — 큐가 비어 있어도. 첫 회차가 곧 백필이다.
  · 스윕이 깨져도 큐 소비는 계속한다.
  · 집은 런은 학습셋을 만들고(키를 행에 적고) 표본 칸을 파드에 넘긴다.
  · 잔액 미달이면 학습셋도 만들지 않고, 시도로 세지 않고 큐로 돌려놓는다.
  · 조건이 깨졌거나(정지 등) 학습셋을 못 만들면 파드 없이 곧장 failed — 재시도 없음.
  · 예상 못 한 예외에도 행이 preparing 에 남지 않는다(남으면 동시 1건 인덱스가 큐를 영영 막는다).
GPU·RunPod·DB·R2 는 전부 가짜다.
"""
import asyncio
import contextlib
import logging

import pytest

from app.services import lora_dataset, lora_train_pod as ltp
from app.workers import lora_training_reconciler as ltr

RUN_ID = "11111111-2222-3333-4444-555555555555"
MODEL_ID = "ebb2001e-9b56-4976-a201-f97201ae967e"
ENROLLMENT_ID = "33b035ac-8cc5-49ae-b407-293988474bcb"
PHOTOS = [{"angle": "sh_front", "r2_key": "orig/a.jpg", "normalized_r2_key": "norm/a.png",
           "qc_status": "passed", "storage_state": "approved"}]


class _Settings:
    fm_lora_training = "on"
    fm_face_qc_dir = None


class _Pool:
    @contextlib.asynccontextmanager
    async def connection(self):
        yield object()


class _App:
    def __init__(self):
        class state:
            settings = _Settings()
            pool = _Pool()
            r2_face = object()
        self.state = state


class _Pod:
    def __init__(self, *, balance_error=None, outcome="OK"):
        self.balance_error = balance_error
        self.outcome = outcome
        self.runs: list[tuple] = []

    async def assert_balance(self):
        if self.balance_error:
            raise ltp.BalanceTooLow(self.balance_error)

    async def run(self, spec, sample_slots, *, on_started=None):
        self.runs.append((spec, tuple(sample_slots)))
        return ltp.RunOutcome(self.outcome, "pod-1", "NVIDIA H100 80GB HBM3", 3.49, 60, ())


class _Recorder:
    """reconciler 모듈의 DB 함수를 갈아 끼우고 무엇이 불렸는지 적는다."""

    def __init__(self, monkeypatch, *, claimed=None, source="default", enqueue_error=None,
                 build_error=None):
        self.calls: list[tuple] = []
        self.finished: list[tuple] = []
        self.released: list[tuple] = []
        self.dataset_keys: list[tuple] = []
        self.builds: list[dict] = []
        self._claimed = claimed
        self._source = ({"enrollment_id": ENROLLMENT_ID, "photo_review_status": "approved",
                         "gender": "female", "photos": PHOTOS} if source == "default" else source)

        async def enqueue_ready(conn, *, limit):
            self.calls.append(("enqueue", limit))
            if enqueue_error:
                raise enqueue_error
            return [{"id": "new-run", "model_id": MODEL_ID, "enrollment_id": ENROLLMENT_ID}]

        async def claim_next(conn):
            self.calls.append(("claim",))
            claimed, self._claimed = self._claimed, None
            return claimed

        async def load_training_source(conn, model_id, enrollment_id):
            self.calls.append(("source", model_id, enrollment_id))
            if isinstance(self._source, Exception):
                raise self._source
            return self._source

        async def set_dataset_key(conn, run_id, key):
            self.dataset_keys.append((run_id, key))

        async def finish_run(conn, run_id, outcome, *, attempts):
            self.finished.append((run_id, outcome.status, attempts))

        async def release_run(conn, run_id, detail):
            self.released.append((run_id, detail))

        def build_and_upload(r2_face, **kwargs):
            self.builds.append(kwargs)
            if build_error:
                raise build_error
            return {"key": lora_dataset.dataset_key(kwargs["model_id"], kwargs["run_id"]),
                    "bytes": 10, "sha256": "0" * 64, "files": 52, "train": 12, "samples": 2,
                    "sample_slots": ["sh_front2", "sh_gaze_left"],
                    "trigger_token": kwargs["trigger_token"], "applied": kwargs["apply"]}

        for name, fn in (("enqueue_ready", enqueue_ready), ("claim_next", claim_next),
                         ("load_training_source", load_training_source),
                         ("set_dataset_key", set_dataset_key), ("finish_run", finish_run),
                         ("release_run", release_run)):
            monkeypatch.setattr(ltr, name, fn)
        monkeypatch.setattr(lora_dataset, "build_and_upload", build_and_upload)


def _claimed(**over):
    row = {"id": RUN_ID, "model_id": MODEL_ID, "enrollment_id": ENROLLMENT_ID,
           "dataset_key": None, "attempts": 1}
    row.update(over)
    return row


def _tick(pod=None):
    pod = pod or _Pod()
    rec = ltr.LoraTrainingReconciler(_App(), pod_factory=lambda settings, r2: pod)
    return asyncio.run(rec.tick()), pod


# ── 자동 큐잉 ──────────────────────────────────────────────────────────────
def test_every_tick_sweeps_before_claiming_even_when_the_queue_is_empty(monkeypatch):
    """첫 회차가 곧 백필이다 — 큐를 켜고 배포하면 준비된 사람(정*정)이 60초 안에 들어간다."""
    rec = _Recorder(monkeypatch, claimed=None)
    result, _ = _tick()
    assert result == "idle"
    assert rec.calls == [("enqueue", ltr.ENQUEUE_LIMIT), ("claim",)]


def test_a_broken_sweep_does_not_stop_the_queue(monkeypatch, caplog):
    rec = _Recorder(monkeypatch, claimed=None, enqueue_error=RuntimeError("relation missing"))
    with caplog.at_level(logging.WARNING, logger="wearless.lora_training_reconciler"):
        result, _ = _tick()
    assert result == "idle"
    assert [call[0] for call in rec.calls] == ["enqueue", "claim"]
    assert "자동 큐잉 실패" in caplog.text


def test_the_sweep_does_not_run_while_the_queue_is_off():
    """큐가 off 면 루프 자체가 안 뜬다 — 자동 큐잉도, RunPod 호출도 없다."""
    app = _App()
    app.state.settings.fm_lora_training = "off"
    rec = ltr.LoraTrainingReconciler(app)
    asyncio.run(rec.start())
    assert rec._task is None


# ── 집은 뒤 학습셋 ─────────────────────────────────────────────────────────
def test_a_claimed_run_gets_its_dataset_built_and_its_samples_passed(monkeypatch):
    rec = _Recorder(monkeypatch, claimed=_claimed())
    result, pod = _tick()

    assert result == f"{RUN_ID}:OK"
    (build,) = rec.builds
    assert build["apply"] is True and build["review_status"] == "approved"
    assert build["rows"] == PHOTOS and build["model_id"] == MODEL_ID and build["run_id"] == RUN_ID
    key = lora_dataset.dataset_key(MODEL_ID, RUN_ID)
    assert rec.dataset_keys == [(RUN_ID, key)]                 # 행에 적는다
    ((spec, samples),) = pod.runs
    assert spec.dataset_key == key
    assert samples == ("sh_front2", "sh_gaze_left")            # 학습셋에 실제로 든 표본 칸
    assert rec.finished == [(RUN_ID, "OK", 1)]


@pytest.mark.parametrize("gender,trigger", [("female", "ohwx woman"), ("male", "ohwx man")])
def test_the_trigger_comes_from_the_registrants_gender(monkeypatch, gender, trigger):
    """★ 학습셋 캡션과 파드의 표본 프롬프트가 **같은** 트리거를 받는다(정*정 = ohwx woman)."""
    rec = _Recorder(monkeypatch, claimed=_claimed(), source={
        "enrollment_id": ENROLLMENT_ID, "photo_review_status": "approved",
        "gender": gender, "photos": PHOTOS})
    _, pod = _tick()
    assert rec.builds[0]["trigger_token"] == trigger
    assert pod.runs[0][0].trigger_token == trigger


def test_an_unknown_gender_stops_before_anything_is_built(monkeypatch):
    """성별을 모르면 추정하지 않는다 — 틀린 어휘로 $11 을 쓰지 않는다."""
    rec = _Recorder(monkeypatch, claimed=_claimed(), source={
        "enrollment_id": ENROLLMENT_ID, "photo_review_status": "approved",
        "gender": None, "photos": PHOTOS})
    result, pod = _tick()
    assert result == f"{RUN_ID}:{ltr.STATUS_DATASET_FAILED}"
    assert rec.builds == [] and pod.runs == []
    assert rec.finished == [(RUN_ID, ltr.STATUS_DATASET_FAILED, ltr.MAX_ATTEMPTS)]


def test_our_own_dataset_key_is_rebuilt_on_a_retry(monkeypatch):
    """재시도에서 다시 만들어도 같은 사진이면 같은 바이트 — 표본 칸을 알기 위해 다시 만든다."""
    rec = _Recorder(monkeypatch, claimed=_claimed(
        dataset_key=lora_dataset.dataset_key(MODEL_ID, RUN_ID), attempts=2))
    _, pod = _tick()
    assert len(rec.builds) == 1
    assert pod.runs[0][1] == ("sh_front2", "sh_gaze_left")


def test_a_hand_built_dataset_is_used_as_is(monkeypatch):
    """사람이 따로 만들어 넣은 학습셋 — 그대로 쓰고, 칸을 모르는 표본은 비운다."""
    rec = _Recorder(monkeypatch, claimed=_claimed(dataset_key="manual/elsewhere/dataset.tgz"))
    _, pod = _tick()
    assert rec.builds == [] and rec.dataset_keys == []
    ((spec, samples),) = pod.runs
    assert spec.dataset_key == "manual/elsewhere/dataset.tgz" and samples == ()
    assert spec.trigger_token == "ohwx woman"


def test_a_hand_inserted_run_without_an_enrollment_uses_the_current_one(monkeypatch):
    rec = _Recorder(monkeypatch, claimed=_claimed(enrollment_id=None))
    _tick()
    assert ("source", MODEL_ID, None) in rec.calls
    assert len(rec.builds) == 1


# ── 돈이 나가기 전에 멈춘다 ─────────────────────────────────────────────────
def test_a_model_that_is_no_longer_eligible_fails_without_a_pod_or_a_retry(monkeypatch, caplog):
    """큐에서 기다리는 사이 정지·해지·재촬영 — 학습셋도 파드도 없이 곧장 failed."""
    rec = _Recorder(monkeypatch, claimed=_claimed(), source=None)
    with caplog.at_level(logging.CRITICAL, logger="wearless.lora_training_reconciler"):
        result, pod = _tick()
    assert result == f"{RUN_ID}:{ltr.STATUS_INELIGIBLE}"
    assert pod.runs == [] and rec.builds == []
    assert rec.finished == [(RUN_ID, ltr.STATUS_INELIGIBLE, ltr.MAX_ATTEMPTS)]
    assert "lora training alert" in caplog.text


def test_a_dataset_that_cannot_be_built_fails_without_a_pod_or_a_retry(monkeypatch):
    """얼굴을 못 찾은 사진 등 — 다시 해도 같다. 반쪽 학습셋으로 $11 을 쓰지 않는다."""
    rec = _Recorder(monkeypatch, claimed=_claimed(),
                    build_error=lora_dataset.DatasetBuildError("얼굴을 못 찾았다: sh_34"))
    result, pod = _tick()
    assert result == f"{RUN_ID}:{ltr.STATUS_DATASET_FAILED}"
    assert pod.runs == [] and rec.dataset_keys == []
    assert rec.finished == [(RUN_ID, ltr.STATUS_DATASET_FAILED, ltr.MAX_ATTEMPTS)]


def test_a_low_balance_puts_the_run_back_without_spending_an_attempt(monkeypatch):
    """잔액 대기는 실패가 아니다. 시도로 세면 두 번 흔들린 것만으로 그 사람은 영영 학습되지 않는다
    (스윕은 한 번 실패한 등록을 다시 넣지 않는다)."""
    rec = _Recorder(monkeypatch, claimed=_claimed())
    result, pod = _tick(_Pod(balance_error="잔액 $3.00 < 임계 $20.00"))
    assert result == f"{RUN_ID}:{ltr.STATUS_BALANCE}"
    assert rec.builds == [] and pod.runs == []                 # 학습셋도 안 만든다
    assert rec.finished == []
    assert rec.released == [(RUN_ID, "balance_too_low 잔액 $3.00 < 임계 $20.00")]


def test_an_unexpected_error_still_finishes_the_run(monkeypatch):
    """★ 예외로 행이 preparing 에 남으면 동시 1건 인덱스가 큐를 영영 막는다."""
    rec = _Recorder(monkeypatch, claimed=_claimed(), source=RuntimeError("db blip"))
    result, pod = _tick()
    assert result == f"{RUN_ID}:error"
    assert pod.runs == []
    assert rec.finished == [(RUN_ID, "error", 1)]              # 재시도 한 번은 남는다


@pytest.mark.parametrize("status", sorted(ltr.NON_RETRYABLE))
def test_non_retryable_failures_are_the_ones_that_cost_nothing(status):
    """재시도를 건너뛰는 건 **파드를 만들기 전에** 멈춘 것들뿐이다."""
    assert status in (ltr.STATUS_INELIGIBLE, ltr.STATUS_DATASET_FAILED)
