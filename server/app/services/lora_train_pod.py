"""인물 LoRA 학습 파드 — 만들고, 지켜보고, **어떤 경로로든 지운다**.

패턴은 services/face_autoscale.py 를 그대로 베낀다(REST 주소·UA·argv 배열 규칙까지). 다른 점은
수명이다: 렌더 파드는 계속 떠 있지만 학습 파드는 **한 런을 위해 만들고 끝나면 지운다.**
시간당 $1.59~3.49 라, 지우는 걸 잊은 파드 하나가 하루면 $38~84 다.

파드에 R2 자격증명을 주지 않는다. 데이터셋은 presigned GET, 가중치·로그·DONE 은 presigned PUT
으로만 오간다(얼굴 렌더 파드와 같은 규칙). URL 은 만들어서 곧장 RunPod 에만 주고 로그·DB 어디에도
남기지 않는다.

진행 판정은 **파드가 아니라 R2** 를 본다. 파드 REST 의 runtime 은 살아 있는 동안에도 null 로
오는 게 실측이고(face_autoscale 주석), 파드가 죽으면 아무것도 못 묻는다. 우리 버킷의 DONE 키는
파드가 죽어도 남는다.

기동 흐름의 정본은 v6_kit 의 setup_ci.sh · v6_go.sh · push_ckpt_v6.sh 다(ssh 대신 서버가 REST 로).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

log = logging.getLogger("wearless.lora_train_pod")

RUNPOD_API_BASE = "https://rest.runpod.io/v1"
#: UA 없는 요청이 거부된 실측이 있다(face_autoscale 주석) — 고정 문자열로 박아 둔다.
USER_AGENT = "wearless-lora-train/1"
REQUEST_TIMEOUT = 20.0
#: 잔액은 REST v1 에 **없다**(2026-09-16 OpenAPI 확인: /billing/* 은 과거 청구 집계뿐).
#: 구 GraphQL 만 계정 잔액을 준다.
GRAPHQL_URL = "https://api.runpod.io/graphql"

#: 학습 카드 우선순위와 시간당 가격(USD). 60GB 미만은 안 쓴다 — 1024·rank16 이 안 올라간다.
#: 실측: 1800 스텝이 RTX PRO 6000 에서 약 3시간 40분·약 $11.
GPU_PRIORITY: tuple[tuple[str, float], ...] = (
    ("NVIDIA A100 80GB PCIe", 1.59),
    ("NVIDIA RTX PRO 6000 Blackwell Server Edition", 2.09),
    ("NVIDIA H100 80GB HBM3", 3.49),
)
POD_IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
POD_DISK_GB = 200          # 베이스 모델 54GB + 체크포인트 12개 + 여유
#: 0 = 네트워크 볼륨 없음. 명시하지 않으면 계정 기본값(20GB)이 붙고 HF_HOME 이 거기로 향한다.
POD_VOLUME_GB = 0
POD_PORTS = ("22/tcp",)

#: 저장 주기·총 스텝. armA_v7.yaml 과 **같은 값**이어야 한다 — 여기서 PUT URL 을 미리 만든다.
SAVE_EVERY = 300
TOTAL_STEPS = 1800
#: presigned PUT 만료. 학습이 ~4시간이라 그보다 넉넉해야 마지막 체크포인트가 올라간다.
PUT_EXPIRES_SECONDS = 8 * 3600
GET_EXPIRES_SECONDS = 2 * 3600


def checkpoint_names(name: str) -> tuple[str, ...]:
    """ai-toolkit 이 저장하는 파일 이름. `{config.name}_{step:09d}.safetensors`."""
    return tuple(f"{name}_{step:09d}.safetensors"
                 for step in range(SAVE_EVERY, TOTAL_STEPS + 1, SAVE_EVERY))


def run_prefix(model_id: str, run_id: str) -> str:
    return f"facemarket/models/{model_id}/training/{run_id}"


def result_key(model_id: str, run_id: str) -> str:
    """파드가 올리는 채점 입력(후보별 렌더 임베딩·게이트). lora_scoring.evaluate 가 읽는다."""
    return f"{run_prefix(model_id, run_id)}/result.json"


@dataclass(frozen=True)
class PodSpec:
    """이 런이 파드에 줄 것 전부. **URL 은 여기 안 남는다** — 만들 때 즉시 env 로 들어간다."""

    run_id: str
    model_id: str
    config_name: str
    dataset_key: str
    prefix: str


class PodCreateFailed(RuntimeError):
    """카드 전부에서 파드를 못 만들었다(재고 없음 등)."""


class BalanceTooLow(RuntimeError):
    """잔액이 임계 미만이거나 **읽지 못했다**. 둘 다 시작하지 않는다."""


# ── 파드 부팅 스크립트 ──────────────────────────────────────────────────────
#
# 정본은 v6_kit 의 setup_ci.sh(세팅) + v6_go.sh(수신·검증·학습·결과 push) 다. 하나로 합치고
# ssh·tsv 대신 env 로 URL 을 받는다. URL 은 **여기 문자열에 안 들어간다** — env 로만 온다.
#
# ★ argv 배열이어야 한다. RunPod REST 의 dockerStartCmd 는 배열만 받는다 — 한 문자열로 보내면
#   카드와 무관하게 400 "got string, want array" 로 파드 생성 자체가 실패한다(2026-09-11 실측).
POD_BOOT_SCRIPT = r"""
set -u
export PIP_BREAK_SYSTEM_PACKAGES=1 HF_HOME=/root/hf HF_XET_HIGH_PERFORMANCE=1 PYTHONUNBUFFERED=1
L=/root/train.log; log(){ echo "$(date -u +%H:%M:%S) $*" >> "$L"; }
put(){ curl -sS --retry 3 --max-time 1800 -X PUT -T "$2" "$1" >/dev/null; }   # $1=URL $2=파일
finish(){
  tar czf /root/logs.tgz -C /root train.log run.yaml 2>/dev/null
  [ -n "${LOGS_PUT_URL:-}" ] && put "$LOGS_PUT_URL" /root/logs.tgz && log "logs push"
  echo "$1 $(date -u +%Y-%m-%dT%H:%M:%SZ)" > /root/DONE
  [ -n "${DONE_PUT_URL:-}" ] && put "$DONE_PUT_URL" /root/DONE && log "DONE push ($1)"
  exit 0
}
mkdir -p /root/hf /root/output /root/data
log "setup start"
pip install -q "huggingface_hub[cli]" hf_xet >>"$L" 2>&1 || log "hf cli install 경고"
( hf download Qwen/Qwen-Image-Edit-2509 >>"$L" 2>&1; echo $? > /root/hf_exit ) &
DL=$!
python3 -m venv --system-site-packages /root/venv >>"$L" 2>&1
cd /root && git clone -q https://github.com/ostris/ai-toolkit.git >>"$L" 2>&1 \
  && cd /root/ai-toolkit && git submodule update --init --recursive -q >>"$L" 2>&1
/root/venv/bin/pip install -q -r /root/ai-toolkit/requirements.txt >>"$L" 2>&1 || finish "ABNORMAL deps"
log "dataset 수신"
curl -sSL --retry 3 --max-time 1800 -o /root/data/dataset.tgz "$DATASET_URL" || finish "ABNORMAL download"
tar xzf /root/data/dataset.tgz -C /root --no-same-owner --no-same-permissions || finish "ABNORMAL extract"
rm -f /root/data/dataset.tgz
D=/root/lora_train
T=$(ls $D/target/*.png 2>/dev/null | wc -l); X=$(ls $D/target/*.txt 2>/dev/null | wc -l)
C=$(ls $D/control/*.png 2>/dev/null | wc -l); M=$(ls $D/mask/*.png 2>/dev/null | wc -l)
log "데이터: target $T txt $X control $C mask $M"
[ "$T" -gt 0 ] && [ "$T" = "$X" ] && [ "$T" = "$C" ] && [ "$T" = "$M" ] || finish "ABNORMAL verify"
printf '%s' "$RUN_YAML" > /root/run.yaml
wait $DL; log "base model exit=$(cat /root/hf_exit 2>/dev/null)"
[ "$(cat /root/hf_exit 2>/dev/null)" = "0" ] || finish "ABNORMAL model"
# 체크포인트가 생기는 대로 올린다 — 파드가 죽어도 거기까지는 남는다.
( while true; do
    for f in /root/output/*/*.safetensors; do
      [ -f "$f" ] || continue
      b=$(basename "$f"); m=/root/pushed_$b
      [ -f "$m" ] && continue
      sleep 20; [ -f "$f" ] || continue
      u=$(printf '%s' "$CKPT_URLS" | awk -F'\t' -v k="$b" '$1==k{print $2}')
      [ -z "$u" ] && continue
      put "$u" "$f" && touch "$m" && log "push $b"
    done
    [ -f /root/train_exit ] && exit 0
    sleep 60
  done ) &
log "train start"
cd /root/ai-toolkit && /root/venv/bin/python -u run.py /root/run.yaml >>"$L" 2>&1
code=$?; echo $code > /root/train_exit
log "train exit=$code"
for i in $(seq 1 60); do pgrep -f "pushed_" >/dev/null 2>&1 || break; sleep 10; done
sleep 90
if [ "$code" = "0" ] && ls /root/output/*/*.safetensors >/dev/null 2>&1; then
  # 학습이 끝난 그 파드에서 이어서 후보를 그린다 — 가중치가 이미 여기 있고 파이프라인도 떠 있다.
  # 따로 파드를 또 만들면 54GB 적재를 한 번 더 한다(콜드스타트 ~10분 + 요금).
  if [ -n "${VERIFY_PUT_URL:-}" ] && [ -f /root/verify.py ]; then
    log "verify start"
    /root/venv/bin/python -u /root/verify.py >>"$L" 2>&1 || log "verify 실패(학습 결과는 그대로)"
    [ -f /root/verify/result.json ] && put "$VERIFY_PUT_URL" /root/verify/result.json \
      && log "verify push"
  fi
  finish "OK"
fi
finish "ABNORMAL exit=$code"
"""
POD_ARGS: tuple[str, ...] = ("bash", "-c", POD_BOOT_SCRIPT)


# ── 학습 설정 ──────────────────────────────────────────────────────────────
#
# 정본은 v6_kit/armA_v7.yaml 이다 — rank 16 · lr 1e-4 · 1024 · batch 1 · bf16 · quantize false ·
# 마스크 손실. **바꾸지 말 것**(v6 와 같은 조건이라 결과를 비교할 수 있다).
# 바뀐 것은 둘뿐이다: 폴더 이름(lora_train_v7 → lora_train)과 표본(맥 로컬 prod 6장이 없다 —
# 등록자 본인의 기준 사진에서 딴 control 만 쓴다).
RUN_YAML_TEMPLATE = """---
job: extension
config:
  name: "{name}"
  process:
    - type: 'diffusion_trainer'
      training_folder: "/root/output"
      device: cuda:0
      network:
        type: "lora"
        linear: 16
        linear_alpha: 16
      save:
        dtype: float16
        save_every: {save_every}
        max_step_saves_to_keep: 12
      datasets:
        - folder_path: "/root/lora_train/target"
          control_path:
            - "/root/lora_train/control"
          caption_ext: "txt"
          mask_path: "/root/lora_train/mask"
          mask_min_value: 0.0
          caption_dropout_rate: 0.05
          resolution: [ 1024 ]
      train:
        batch_size: 1
        cache_text_embeddings: true
        steps: {steps}
        skip_first_sample: true
        gradient_accumulation: 1
        timestep_type: "weighted"
        train_unet: true
        inverted_mask_prior: true
        inverted_mask_prior_multiplier: 0.5
        train_text_encoder: false
        gradient_checkpointing: true
        noise_scheduler: "flowmatch"
        optimizer: "adamw8bit"
        lr: 1e-4
        lr_scheduler: "cosine"
        lr_scheduler_params:
          eta_min: 1.0e-5
        dtype: bf16
      model:
        name_or_path: "Qwen/Qwen-Image-Edit-2509"
        arch: "qwen_image_edit_plus"
        quantize: false
        qtype: "qfloat8"
        quantize_te: true
        qtype_te: "qfloat8"
        low_vram: false
      sample:
        neg: ""
        sampler: "flowmatch"
        sample_every: 500
        sample_start_step: 0
        width: 1024
        height: 1024
        seed: 42
        walk_seed: true
        guidance_scale: 4
        sample_steps: 25
        samples:
{samples}
"""
_SAMPLE_BLOCK = """          - prompt: "ohwx man, facing the camera, photograph"
            ctrl_img_1: "/root/lora_train/samples_ctrl/{slot}.png"
"""


def render_yaml(config_name: str, sample_slots) -> str:
    """학습 설정 한 벌. 표본이 하나도 없으면 sample 블록을 비운다(학습은 그대로 돈다)."""
    samples = "".join(_SAMPLE_BLOCK.format(slot=slot) for slot in sample_slots) or "          []\n"
    return RUN_YAML_TEMPLATE.format(name=config_name, save_every=SAVE_EVERY,
                                    steps=TOTAL_STEPS, samples=samples)


# ── 파드 수명 ──────────────────────────────────────────────────────────────


@dataclass
class RunOutcome:
    """런 결과. **키와 숫자만** — 바이트도 URL 도 안 싣는다."""

    status: str                    # "OK" | "ABNORMAL …" | "timeout" | "create_failed"
    pod_id: str | None = None
    gpu_type: str | None = None
    price_per_hour: float | None = None
    elapsed_seconds: int = 0
    ckpt_keys: tuple[str, ...] = ()
    detail: str | None = None


class LoraTrainPod:
    """한 런의 파드. `run()` 하나로 만들고·지켜보고·지운다.

    ★ 어떤 경로로 빠져나가도(성공·실패·타임아웃·취소·예외) finally 에서 파드를 지운다.
      삭제가 실패하면 CRITICAL 로 알린다 — 조용히 넘어가면 시간당 요금이 계속 나간다.
    """

    def __init__(self, settings, r2_face, *, client=None, sleep=None, now=None):
        self._settings = settings
        self._r2 = r2_face
        self._api_key = (getattr(settings, "face_runpod_api_key", None) or "").strip() or None
        self._client = client
        self._sleep = sleep or asyncio.sleep
        self._now = now or time.monotonic
        self._max_seconds = int(getattr(settings, "fm_lora_max_seconds", 5 * 3600) or 0)
        self._min_balance = float(getattr(settings, "fm_lora_min_balance_usd", 0) or 0)

    # ── HTTP ──
    def _http(self):
        if self._client is not None:
            return self._client
        import httpx

        self._client = httpx.Client(
            base_url=RUNPOD_API_BASE, timeout=REQUEST_TIMEOUT,
            headers={"Authorization": f"Bearer {self._api_key}",
                     "User-Agent": USER_AGENT, "Content-Type": "application/json"})
        return self._client

    def _post_json_sync(self, path: str, body: dict) -> dict:
        res = self._http().post(path, json=body)
        res.raise_for_status()
        return res.json() if res.content else {}

    def _delete_sync(self, path: str) -> None:
        res = self._http().delete(path)
        if res.status_code not in (200, 204, 404):
            res.raise_for_status()

    # ── 잔액 ──
    def _balance_sync(self) -> float | None:
        """계정 잔액(USD) 또는 None(못 읽음).

        ★ REST v1 에는 잔액 엔드포인트가 **없다**(2026-09-16 OpenAPI 확인 — /billing/* 은 과거
          청구 집계뿐이다). 구 GraphQL 만 계정을 준다. 이 쿼리는 **살아 있는 키로 실측 확인이
          안 됐다** — 지금 맥의 키는 401 이고 SSM 키는 옛 계정이다(RunPod 키 교체가 선행 조건).
          그래서 못 읽으면 "0 이겠거니" 하지 않고 **못 읽었다**고 말한다(호출자가 시작을 막는다).
        """
        import httpx

        try:
            res = httpx.post(GRAPHQL_URL, params={"api_key": self._api_key},
                             json={"query": "{ myself { clientBalance } }"},
                             headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
            value = (((res.json() or {}).get("data") or {}).get("myself") or {}).get("clientBalance")
        except Exception as exc:  # noqa: BLE001
            log.warning("lora train: 잔액 조회 실패 (%s)", type(exc).__name__)
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def assert_balance(self) -> float | None:
        """임계 미만이거나 못 읽으면 BalanceTooLow. 임계 0 이하면 검사 자체를 안 한다."""
        if self._min_balance <= 0:
            return None
        balance = await asyncio.to_thread(self._balance_sync)
        if balance is None:
            raise BalanceTooLow("잔액을 읽지 못했다 — 상한 없는 GPU 요금을 감수하고 시작하지 않는다")
        if balance < self._min_balance:
            raise BalanceTooLow(f"잔액 ${balance:.2f} < 임계 ${self._min_balance:.2f}")
        log.info("lora train: 잔액 $%.2f (임계 $%.2f)", balance, self._min_balance)
        return balance

    # ── URL ──
    def _env(self, spec: PodSpec, sample_slots) -> dict[str, str]:
        """파드에 줄 env. presigned 는 **여기서 만들어 곧장 넘긴다** — 로그·DB 어디에도 안 남는다."""
        put = self._r2.presigned_put
        ckpt_lines = "\n".join(
            f"{name}\t{put(f'{spec.prefix}/{name}', 'application/octet-stream', PUT_EXPIRES_SECONDS)}"
            for name in checkpoint_names(spec.config_name))
        return {
            "DATASET_URL": self._r2.preview_url(spec.dataset_key, expires=GET_EXPIRES_SECONDS),
            "CKPT_URLS": ckpt_lines,
            "LOGS_PUT_URL": put(f"{spec.prefix}/logs.tgz", "application/gzip", PUT_EXPIRES_SECONDS),
            "VERIFY_PUT_URL": put(f"{spec.prefix}/result.json", "application/json",
                                  PUT_EXPIRES_SECONDS),
            "DONE_PUT_URL": put(f"{spec.prefix}/DONE", "text/plain", PUT_EXPIRES_SECONDS),
            "RUN_YAML": render_yaml(spec.config_name, sample_slots),
        }

    # ── 생성·삭제 ──
    async def create(self, spec: PodSpec, sample_slots) -> tuple[str, str, float]:
        """카드 우선순위대로 만든다. 전부 실패하면 PodCreateFailed. 반환 (pod_id, gpu, 시간당 가격)."""
        env = self._env(spec, sample_slots)
        errors: list[str] = []
        for gpu_type, price in GPU_PRIORITY:
            body = {"name": f"lora-train-{spec.run_id[:8]}", "imageName": POD_IMAGE,
                    "cloudType": "SECURE", "containerDiskInGb": POD_DISK_GB,
                    "volumeInGb": POD_VOLUME_GB, "ports": list(POD_PORTS),
                    "gpuTypeIds": [gpu_type], "gpuCount": 1,
                    "dockerStartCmd": list(POD_ARGS), "env": env}
            try:
                created = await asyncio.to_thread(self._post_json_sync, "/pods", body)
            except Exception as exc:  # noqa: BLE001 — 재고 없음도 여기로 온다
                errors.append(f"{gpu_type}: {type(exc).__name__}")
                continue
            pod_id = str((created or {}).get("id") or "").strip()
            if not pod_id:
                errors.append(f"{gpu_type}: id 없음")
                continue
            log.info("lora train: 파드 %s (%s, $%.2f/h) run=%s", pod_id, gpu_type, price, spec.run_id)
            return pod_id, gpu_type, price
        raise PodCreateFailed("모든 카드에서 파드 생성 실패: " + ", ".join(errors))

    async def terminate(self, pod_id: str) -> None:
        """파드를 지운다. **실패는 CRITICAL** — 남은 파드는 시간당 요금이다."""
        try:
            await asyncio.to_thread(self._delete_sync, f"/pods/{pod_id}")
            log.info("lora train: 파드 %s 삭제", pod_id)
        except Exception as exc:  # noqa: BLE001
            log.critical("lora train alert: 파드 %s 삭제 실패 (%s) — 콘솔에서 직접 지울 것",
                         pod_id, type(exc).__name__)

    # ── 감시 ──
    def _done_sync(self, prefix: str) -> str | None:
        """R2 의 DONE 내용(상태 문자열) 또는 None(아직).

        파드가 아니라 **우리 버킷**을 본다 — 파드 REST 의 runtime 은 살아 있는 동안에도 null 로
        오고(실측), 파드가 죽으면 아무것도 못 묻는다. DONE 은 파드가 죽어도 남는다.
        """
        try:
            if self._r2.head(f"{prefix}/DONE") is None:
                return None
            text = self._r2.get_bytes(f"{prefix}/DONE").decode("utf-8", "replace").strip()
            # 파드는 "<상태> <UTC 시각>" 으로 쓴다. 상태만 돌려준다 — 시각까지 실어 보내면
            # 호출자의 `status == "OK"` 가 영원히 거짓이 된다.
            return text.split(None, 1)[0] if text else None
        except Exception as exc:  # noqa: BLE001 — 조회 실패는 "아직" 으로 본다
            log.info("lora train: DONE 조회 실패 (%s)", type(exc).__name__)
            return None

    def _uploaded_sync(self, prefix: str, names) -> tuple[str, ...]:
        """실제로 올라온 체크포인트 키. 파드가 중간에 죽어도 여기까지는 쓸 수 있다."""
        out = []
        for name in names:
            key = f"{prefix}/{name}"
            try:
                if self._r2.head(key) is not None:
                    out.append(key)
            except Exception:  # noqa: BLE001
                continue
        return tuple(out)

    async def watch(self, spec: PodSpec, *, poll_seconds: int = 60) -> tuple[str, int]:
        """DONE 이 뜰 때까지 기다린다. 반환 (상태, 걸린 초). 상한을 넘으면 ("timeout", 초)."""
        started = self._now()
        while True:
            elapsed = int(self._now() - started)
            if self._max_seconds and elapsed >= self._max_seconds:
                return "timeout", elapsed
            status = await asyncio.to_thread(self._done_sync, spec.prefix)
            if status:
                return status, elapsed
            await self._sleep(poll_seconds)

    async def run(self, spec: PodSpec, sample_slots, *, poll_seconds: int = 60,
                  on_started=None) -> RunOutcome:
        """만들고 → 지켜보고 → **반드시** 지운다. 예외를 밖으로 던지지 않는다(결과로 말한다)."""
        await self.assert_balance()
        pod_id = gpu_type = None
        price = None
        started = self._now()
        try:
            pod_id, gpu_type, price = await self.create(spec, sample_slots)
            if on_started is not None:
                await on_started(pod_id, gpu_type)
            status, elapsed = await self.watch(spec, poll_seconds=poll_seconds)
            names = checkpoint_names(spec.config_name)
            keys = await asyncio.to_thread(self._uploaded_sync, spec.prefix, names)
            log.info("lora train: run=%s %s %ds ckpt=%d gpu=%s $%.2f/h (≈$%.2f)",
                     spec.run_id, status, elapsed, len(keys), gpu_type, price or 0,
                     (price or 0) * elapsed / 3600)
            return RunOutcome(status, pod_id, gpu_type, price, elapsed, keys)
        except PodCreateFailed as exc:
            return RunOutcome("create_failed", None, None, None,
                              int(self._now() - started), (), str(exc))
        except asyncio.CancelledError:
            # 배포·재기동으로 루프가 취소돼도 파드는 지운다(아래 finally). 취소는 그대로 올린다.
            raise
        finally:
            if pod_id:
                # ★ shield: 취소된 태스크의 finally 안에서 그냥 await 하면 그 await 가 **다시**
                #   취소돼 삭제가 안 끝난다. 파드가 남으면 시간당 요금이 계속 나간다.
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(asyncio.ensure_future(self.terminate(pod_id)))
