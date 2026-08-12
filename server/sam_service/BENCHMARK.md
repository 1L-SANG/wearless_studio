# SAM2 service — Fargate benchmark procedure

Compute sizing for `copilot/sam2/manifest.yml` is **provisional** (`cpu: 2048`, `memory: 8192`).
Nothing in it was chosen from a measurement on the target. This is the procedure that replaces
the guess, run once deployment permission exists.

Run it before any main-backend integration depends on this service's latency.

## What must be measured

| Phase | Definition | Sizes |
|---|---|---|
| cold | container start → model resident → first successful segmentation | healthcheck `start_period`, task warmup, autoscaling |
| warm | request start → response | the per-view figure the SAM fallback path waits on |

For **Front only** and **Front + Back**, at concurrency 1.

Record for each: latency, CPU utilisation, RSS, and what happens at the timeout boundary.

## 1. Deploy to a staging environment

```bash
export AWS_PROFILE=wearless AWS_REGION=ap-northeast-2
# NOTE: the AWS Copilot binary here is `copilot-aws`. Plain `copilot` on this machine is the
# GitHub Copilot CLI — running it by mistake does something else entirely.
copilot-aws svc init --name sam2          # once: creates the ECR repo only
copilot-aws svc deploy --name sam2 --env prod
```

Create the shared secret FIRST or the task fails to start with
`ResourceInitializationError ... unable to retrieve secrets from ssm`:

```bash
copilot-aws secret init --name SAM_INTERNAL_TOKEN --values prod="$(openssl rand -hex 32)"
```

Use `copilot-aws secret init`, **not** `aws ssm put-parameter`. A parameter created raw has no
Copilot tags, so the task's execution role cannot read it and the service crash-loops — the
same trap already documented in `copilot/api/manifest.yml` from 2026-07-17.

## 2. Cold path

Force a fresh task, then time from start to first success:

```bash
copilot svc restart --name sam2 --env staging
# from a task in the same VPC (copilot svc exec, or the api task):
time curl -sf http://sam2:8080/health
# repeat until {"modelLoaded": false} answers — that is "container up, model not yet loaded"
time curl -sf -X POST http://sam2:8080/segment-garment \
  -H "Authorization: Bearer $SAM_INTERNAL_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"views":{"Front":{"key":"users/<uuid>/projects/<uuid>/uploads/front.jpg"}}}' \
  -o /dev/null -w '%{time_total}\n'
```

The first POST includes the model load; `/health` reporting `modelLoaded: true` afterwards
confirms it stayed resident.

## 3. Warm path, in-task

The benchmark script runs the same code path the service runs, so it isolates inference from
HTTP and R2:

```bash
copilot svc exec --name sam2 --env staging
# inside the task:
python -m sam_service.benchmark --image /tmp/front.jpg --views Front --http --json
python -m sam_service.benchmark --image /tmp/front.jpg --views Front,Back --http --json
```

Get a source file into the task with whatever is convenient (`curl` a presigned GET, or copy
from R2 with the credentials the task already has).

Output fields: `coldLoadSeconds`, per-view `warmInferenceSeconds`, `http.requestSeconds`,
`rssAfterLoadMb`, `rssPeakMb`.

## 4. CPU and memory under load

```bash
aws ecs describe-tasks --cluster <cluster> --tasks <task-arn> \
  --query 'tasks[0].{cpu:cpu,memory:memory}'
# CloudWatch, during the run:
#   ECS/ContainerInsights CpuUtilized, MemoryUtilized  (1-minute resolution)
```

`rssPeakMb` from the script is the number that sets `memory:`. Add headroom — an OOM kill
mid-segmentation looks identical to a hang from the caller's side.

## 5. Timeout behaviour

`VIEW_TIMEOUT_S` in `sam_service/api.py` is 600s. Confirm the observed warm latency leaves a
wide margin. If a real Front approaches it, the timeout is not the problem — the compute
target is.

## Decision rule

Classify the measured warm per-view latency:

- **ACCEPTABLE FOR ASYNC PREPROCESSING** — preprocessing reliably finishes before a generation
  needs the fallback. Keep CPU Fargate.
- **BORDERLINE — NEEDS OPTIMIZATION** — finishes, but often after the baseline QC verdict, so
  the fallback frequently finds `PENDING`. Revisit prompting cost or model variant *then*, as
  a separate decision with its own evidence.
- **NOT VIABLE ON CPU FARGATE** — multi-minute per view. Escalate the compute target rather
  than degrading segmentation quality; the 1280px downscale is already ruled out because it
  produced an incorrect mask.

If escalation is needed, the smallest step is ECS-EC2 with a GPU instance type reusing this
same image and manifest shape — not a rewrite, not Kubernetes, not a managed endpoint.

## Recorded so far

| Environment | Device | Cold load | Warm Front | HTTP | Peak RSS | Notes |
|---|---|---|---|---|---|---|
| Apple Silicon, local | MPS | ~6s | ~94s | ~170s | — | machine under contention for the HTTP figure |
| Apple Silicon, local | CPU | 4.9s | 37.2s | 34.2s | **~15.7 GB** | arm64, 14 cores, torch 2.13, 10 threads |
| Fargate staging | CPU | **unmeasured** | **unmeasured** | — | — | this procedure |

Two findings from the local CPU run:

1. **CPU beat MPS** — 37s vs 94s per view. 64 point prompts batched 16 at a time are many small
   graph executions, which MPS dispatch overhead punishes. So the CPU target is not the
   handicap it looked like; do not assume GPU is required.
2. **Peak RSS ~15.7 GB**, confirmed by two independent methods (`ru_maxrss` and sampling `ps`
   from a second thread). `memory: 8192` would OOM-kill the task mid-segmentation. The driver
   is batching: the processor replicates the full-resolution frame per prompt and
   `post_process_masks` returns full-resolution masks for all of them. Batch size is the
   obvious lever if this needs to come down — a separate decision with its own evidence.

arm64 vs x86_64 caveat: latency will differ on Fargate (different core count, no unified
memory, different BLAS). The memory figure is driven by tensor shapes and should transfer more
faithfully than the timing does, but neither is confirmed until this procedure runs.
