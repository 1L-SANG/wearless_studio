"""LoRA 학습 큐 — 한 건이 GPU 파드를 3~4시간, 약 $11 쓴다. 그래서 여기서 지키는 건 **돈과 정리**다.

  · 동시 1건: 두 번째는 DB 인덱스가 막는다(전역 partial unique). 코드가 세어 보고 정하지 않는다.
  · 어떤 경로로 빠져나가도(성공·실패·타임아웃·취소·예외) **파드를 지운다**. 삭제 실패는 CRITICAL.
  · 잔액을 못 읽으면 시작하지 않는다 — 상한 없는 요금을 감수하지 않는다.
  · presigned URL·토큰이 로그·결과·DB 어디에도 안 남는다.
GPU·RunPod 는 쓰지 않는다 — REST 클라이언트와 R2 를 가짜로 갈아 끼운다.
"""
import asyncio
import json
import pathlib

import pytest

from app.services import lora_train_pod as ltp
from app.workers import lora_training_reconciler as ltr

MIGRATION = (pathlib.Path(__file__).resolve().parents[2]
             / "supabase/migrations/20260916110000_fm_lora_training_runs.sql")


class _Settings:
    face_runpod_api_key = "rp-secret-key"
    fm_lora_max_seconds = 3600
    fm_lora_min_balance_usd = 0.0          # 기본은 검사 끔(테스트가 따로 켠다)
    fm_lora_training = "on"


class _R2:
    """presigned 를 흉내내되 **어떤 URL 을 만들었는지 기록**한다 — 유출 검사에 쓴다."""

    def __init__(self, done_after: int = 0, uploaded=()):
        self.done_after = done_after
        self.calls = 0
        self.uploaded = set(uploaded)
        self.issued: list[str] = []

    def presigned_put(self, key, mime, expires=300):
        url = f"https://r2.test/{key}?X-Amz-Signature=SECRET"
        self.issued.append(url)
        return url

    def preview_url(self, key, expires=3600):
        url = f"https://r2.test/{key}?X-Amz-Signature=SECRET"
        self.issued.append(url)
        return url

    def head(self, key):
        if key.endswith("/DONE"):
            self.calls += 1
            return {"size": 3} if self.calls > self.done_after else None
        return {"size": 1} if key in self.uploaded else None

    def get_bytes(self, key):
        return b"OK 2026-09-16T00:00:00Z"


class _Client:
    """RunPod REST 대역. 생성·삭제를 기록한다."""

    def __init__(self, *, create_fails=0, delete_raises=False):
        self.create_fails = create_fails
        self.delete_raises = delete_raises
        self.created: list[dict] = []
        self.deleted: list[str] = []

    class _Res:
        def __init__(self, body, status=200):
            self._body = body
            self.status_code = status
            self.content = b"x"

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def post(self, path, json=None):
        if len(self.created) < self.create_fails:
            self.created.append(json)
            raise RuntimeError("no instances currently available")
        self.created.append(json)
        return self._Res({"id": f"pod-{len(self.created)}"})

    def delete(self, path):
        if self.delete_raises:
            raise RuntimeError("runpod down")
        self.deleted.append(path)
        return self._Res({}, 204)


def _spec():
    return ltp.PodSpec(run_id="11111111-2222-3333-4444-555555555555", model_id="m1",
                       config_name="fm_abc", dataset_key="facemarket/models/m1/training/r1/dataset.tgz",
                       prefix="facemarket/models/m1/training/r1")


def _pod(r2, client, **over):
    settings = _Settings()
    for key, value in over.items():
        setattr(settings, key, value)
    return ltp.LoraTrainPod(settings, r2, client=client,
                            sleep=lambda _s: asyncio.sleep(0), now=_clock())


def _clock(step: float = 30.0):
    state = {"t": 0.0}

    def now():
        state["t"] += step
        return state["t"]

    return now


# ── 동시 1건 ───────────────────────────────────────────────────────────────
def test_the_migration_blocks_a_second_live_run():
    """★ 세어 보고 정하면 두 프로세스가 같은 순간에 '자리 있음'을 본다 — 인덱스는 그럴 수 없다."""
    sql = MIGRATION.read_text()
    assert "create unique index if not exists fm_lora_training_runs_one_active" in sql
    assert "on public.fm_lora_training_runs ((true))" in sql
    assert "where status in ('preparing', 'training', 'scoring')" in sql
    # 모델당이 아니라 **전역**이다 — model_id 가 인덱스 식에 들어가면 안 된다.
    active = sql.split("fm_lora_training_runs_one_active")[1].split(";")[0]
    assert "model_id" not in active


def test_the_migration_does_not_use_the_jobs_table():
    """jobs.project_id 가 not null 인데 학습은 프로젝트가 아니라 사람 단위다 — 억지로 끼우면
    크레딧·정산이 엉뚱한 프로젝트에 묶인다."""
    sql = MIGRATION.read_text().lower()
    for forbidden in ("insert into jobs", "from jobs", "references public.jobs"):
        assert forbidden not in sql, forbidden
    assert "references public.fm_models(id) on delete cascade" in sql


# ── 파드 정리 ──────────────────────────────────────────────────────────────
def test_a_normal_run_deletes_its_pod():
    r2, client = _R2(done_after=1), _Client()
    outcome = asyncio.run(_pod(r2, client).run(_spec(), (), poll_seconds=0))

    assert outcome.status == "OK"
    assert client.deleted == ["/pods/pod-1"]


def test_a_timeout_deletes_its_pod():
    """상한을 넘긴 파드는 학습이 안 끝났어도 지운다 — 잊힌 파드가 하루면 $38~84 다."""
    r2, client = _R2(done_after=10_000), _Client()
    outcome = asyncio.run(_pod(r2, client, fm_lora_max_seconds=60).run(_spec(), (), poll_seconds=0))

    assert outcome.status == "timeout"
    assert client.deleted == ["/pods/pod-1"]


def test_an_exception_mid_watch_still_deletes_the_pod(monkeypatch):
    r2, client = _R2(), _Client()
    pod = _pod(r2, client)

    async def boom(*a, **k):
        raise RuntimeError("watch 폭발")

    monkeypatch.setattr(pod, "watch", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(pod.run(_spec(), (), poll_seconds=0))
    assert client.deleted == ["/pods/pod-1"]


def test_a_cancelled_run_still_deletes_the_pod():
    """★ 배포·재기동으로 루프가 취소돼도 파드는 남으면 안 된다.

    취소된 태스크의 finally 안에서 그냥 await 하면 그 await 가 **다시** 취소돼 삭제가 안 끝난다
    (shield 가 그걸 막는다). 파드가 하나 남으면 하루 $38~84 다.
    """
    r2, client = _R2(done_after=10_000), _Client()
    pod = _pod(r2, client, fm_lora_max_seconds=0)
    started = asyncio.Event()

    async def on_started(pod_id, gpu_type):
        started.set()

    async def scenario():
        task = asyncio.create_task(pod.run(_spec(), (), poll_seconds=5, on_started=on_started))
        await asyncio.wait_for(started.wait(), 2)     # 파드가 생긴 뒤에 취소한다
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert client.deleted == ["/pods/pod-1"]


def test_a_failed_delete_is_critical(caplog):
    r2, client = _R2(done_after=1), _Client(delete_raises=True)
    with caplog.at_level("CRITICAL"):
        asyncio.run(_pod(r2, client).run(_spec(), (), poll_seconds=0))
    assert any(record.levelname == "CRITICAL" and "삭제 실패" in record.getMessage()
               for record in caplog.records)


def test_no_pod_is_left_when_every_card_is_out_of_stock():
    r2, client = _R2(), _Client(create_fails=len(ltp.GPU_PRIORITY))
    outcome = asyncio.run(_pod(r2, client).run(_spec(), (), poll_seconds=0))

    assert outcome.status == "create_failed"
    assert client.deleted == []          # 만들어진 게 없으니 지울 것도 없다
    assert len(client.created) == len(ltp.GPU_PRIORITY)


def test_the_cards_are_tried_in_price_order():
    assert [gpu for gpu, _price in ltp.GPU_PRIORITY][0] == "NVIDIA A100 80GB PCIe"
    assert [price for _gpu, price in ltp.GPU_PRIORITY] == sorted(
        price for _gpu, price in ltp.GPU_PRIORITY), "싼 카드부터 시도한다"


# ── 잔액 ──────────────────────────────────────────────────────────────────
def test_an_unreadable_balance_stops_the_run(monkeypatch):
    """★ 못 읽었다를 '0 이겠거니' 로 바꾸지 않는다 — 상한 없는 GPU 요금이 걸려 있다."""
    r2, client = _R2(), _Client()
    pod = _pod(r2, client, fm_lora_min_balance_usd=20.0)
    monkeypatch.setattr(pod, "_balance_sync", lambda: None)

    with pytest.raises(ltp.BalanceTooLow, match="읽지 못했다"):
        asyncio.run(pod.run(_spec(), ()))
    assert client.created == []


def test_a_low_balance_stops_the_run(monkeypatch):
    r2, client = _R2(), _Client()
    pod = _pod(r2, client, fm_lora_min_balance_usd=20.0)
    monkeypatch.setattr(pod, "_balance_sync", lambda: 3.5)

    with pytest.raises(ltp.BalanceTooLow, match=r"\$3\.50"):
        asyncio.run(pod.run(_spec(), ()))
    assert client.created == []


def test_the_balance_check_can_be_turned_off(monkeypatch):
    """임계 0 이하 = 검사 안 함. API 가 바뀌어도 운영자가 빠져나갈 길이 있어야 한다."""
    r2, client = _R2(done_after=1), _Client()
    pod = _pod(r2, client, fm_lora_min_balance_usd=0)

    def never(*a, **k):
        raise AssertionError("검사를 껐는데 잔액을 물었다")

    monkeypatch.setattr(pod, "_balance_sync", never)
    assert asyncio.run(pod.run(_spec(), (), poll_seconds=0)).status == "OK"


# ── 유출 ──────────────────────────────────────────────────────────────────
def test_urls_and_tokens_never_leave_the_pod_env():
    """presigned 는 RunPod 요청 본문에만 있고 결과·로그에는 없어야 한다."""
    r2, client = _R2(done_after=1), _Client()
    outcome = asyncio.run(_pod(r2, client).run(_spec(), (), poll_seconds=0))

    body = json.dumps(client.created[-1])
    assert "X-Amz-Signature" in body, "전제: 요청 본문에는 있다"
    text = json.dumps({"status": outcome.status, "pod": outcome.pod_id, "gpu": outcome.gpu_type,
                       "ckpt": list(outcome.ckpt_keys), "detail": outcome.detail})
    assert "X-Amz" not in text and "http" not in text
    assert _Settings.face_runpod_api_key not in text


def test_the_result_carries_keys_not_bytes():
    r2 = _R2(done_after=1, uploaded={f"facemarket/models/m1/training/r1/{name}"
                                     for name in ltp.checkpoint_names("fm_abc")})
    outcome = asyncio.run(_pod(r2, _Client()).run(_spec(), (), poll_seconds=0))

    assert outcome.ckpt_keys
    assert all(key.startswith("facemarket/models/m1/training/r1/") for key in outcome.ckpt_keys)
    assert outcome.ckpt_keys[-1].endswith("_000001800.safetensors")


def test_the_boot_script_is_an_argv_array():
    """RunPod REST 의 dockerStartCmd 는 배열만 받는다 — 문자열이면 400 으로 생성 자체가 실패한다."""
    assert isinstance(ltp.POD_ARGS, tuple) and ltp.POD_ARGS[:2] == ("bash", "-c")
    # 서명된 URL 은 스크립트 본문이 아니라 env 로만 온다(공개 git·HF 주소는 본문에 있어도 된다).
    assert "X-Amz" not in ltp.POD_BOOT_SCRIPT
    assert "r2.cloudflarestorage" not in ltp.POD_BOOT_SCRIPT
    for name in ("DATASET_URL", "CKPT_URLS", "DONE_PUT_URL", "RUN_YAML"):
        assert name in ltp.POD_BOOT_SCRIPT


# ── 학습 설정 ─────────────────────────────────────────────────────────────
def test_the_training_config_matches_the_frozen_recipe():
    """정본 armA_v7.yaml — rank 16 · lr 1e-4 · 1024 · batch 1 · bf16 · 마스크 손실. 바꾸지 말 것."""
    import yaml

    process = yaml.safe_load(ltp.render_yaml("fm_x", ()))["config"]["process"][0]
    assert process["network"] == {"type": "lora", "linear": 16, "linear_alpha": 16}
    assert process["train"]["steps"] == 1800 and process["save"]["save_every"] == 300
    # yaml 1.1 은 `1e-4` 를 문자열로 읽는다. 정본(armA_v7.yaml)이 그렇게 적혀 있고
    # ai-toolkit 이 float 로 받는다 — **바꾸지 말 것** 이라 표기를 그대로 두고 값만 본다.
    assert float(process["train"]["lr"]) == 1e-4 and process["train"]["dtype"] == "bf16"
    assert process["train"]["batch_size"] == 1 and process["model"]["quantize"] is False
    assert process["datasets"][0]["resolution"] == [1024]
    assert process["datasets"][0]["mask_path"] == "/root/lora_train/mask"
    assert process["train"]["inverted_mask_prior"] is True


def test_the_checkpoint_names_match_the_save_schedule():
    names = ltp.checkpoint_names("fm_x")
    assert len(names) == ltp.TOTAL_STEPS // ltp.SAVE_EVERY == 6
    assert names[0].endswith("_000000300.safetensors")


def test_samples_come_from_the_registrants_own_reference_shots():
    import yaml

    samples = yaml.safe_load(ltp.render_yaml("fm_x", ("sh_front2", "sh_gaze_right")))[
        "config"]["process"][0]["sample"]["samples"]
    assert [s["ctrl_img_1"] for s in samples] == [
        "/root/lora_train/samples_ctrl/sh_front2.png",
        "/root/lora_train/samples_ctrl/sh_gaze_right.png"]


# ── 큐 ────────────────────────────────────────────────────────────────────
def test_the_queue_does_not_run_when_the_flag_is_off():
    class _App:
        class state:
            settings = _Settings()

    _App.state.settings.fm_lora_training = "off"
    assert ltr.LoraTrainingReconciler(_App()).enabled is False
    _App.state.settings.fm_lora_training = "on"
    assert ltr.LoraTrainingReconciler(_App()).enabled is True


def test_a_failure_is_retried_once_then_alerted():
    assert ltr.MAX_ATTEMPTS == 2
