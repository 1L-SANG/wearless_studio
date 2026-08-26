# Detail Worker Stability Implementation Plan

> **Spec:** `docs/research/2026-08-26-ecs-fargate-cost-rightsizing.md`

**Goal:** Keep five detail-image provider calls in flight without starving `/healthz`, then move only `detail_page` jobs to an x86 Fargate Spot service that scales from zero.

**Constraints:** Preserve the confirmed-GPT exact-byte contract, retain the 3-second launch stagger and provider concurrency 5, serialize only local CPU image work, keep API `DB_POOL_MAX_SIZE=3`, add no NAT/load balancer for the worker, and do not deploy production from this branch.

**Architecture:** PR1 offloads synchronous image encode/parse/decode bundles with `asyncio.to_thread`, reuses per-job normalized OpenAI references, and finalizes cancelled detail jobs through the existing fenced refund path. PR2 filters the existing dispatcher by environment, runs the same application image in a worker role, and generalizes the existing SAM2 ECS desired-count adapter/reconciler for `detail-worker` demand (`pending + running`).

---

## Task 1: Lock PR1 behavior with regression tests

**Files:**
- Modify: `server/tests/test_gemini_image.py`
- Modify: `server/tests/test_pose_crop.py`
- Modify: `server/tests/test_vision_llm.py`
- Modify: `server/tests/test_detail_page.py`

1. Add a heartbeat regression showing Gemini/OpenAI request construction and response decoding do not block the event loop.
2. Add a regression proving generic OpenAI inputs normalize once per job while confirmed inputs preserve original MIME and bytes.
3. Add a pose-crop and vision-body heartbeat regression around their synchronous PIL/base64 work.
4. Add a cancellation regression proving `CancelledError` finalizes the leased detail job as failed/refunded and is re-raised.
5. Run the targeted tests and confirm they fail for the intended missing behavior.

## Task 2: Implement and verify PR1

**Files:**
- Modify: `server/app/agents/gemini_image.py`
- Modify: `server/app/agents/pose_crop.py`
- Modify: `server/app/agents/vision_llm.py`
- Modify: `server/app/workers/detail_page_job.py`

1. Offload each synchronous encode/parse/decode bundle with `asyncio.to_thread` and one process-local CPU semaphore.
2. Pre-normalize unique generic OpenAI references once per detail job; keep provider awaits outside the CPU semaphore.
3. Catch `asyncio.CancelledError`, shield the existing lease-fenced failure/refund finalizer, then re-raise.
4. Run the four targeted test modules, then the detail-page/confirmed-GPT regression group.
5. Commit PR1 with a Lore decision-record message.

## Task 3: Lock PR2 behavior with regression tests

**Files:**
- Add: `server/tests/test_detail_worker.py`
- Modify: `server/tests/test_sam_autoscale_adapter.py`
- Modify: `server/tests/test_sam_autoscaler.py`
- Modify: `server/tests/test_deploy_manifest_qc_flags.py`

1. Test dispatcher kind filtering: default all, API excludes `detail_page`, worker includes only `detail_page`, unknown configured kinds fail fast.
2. Test worker role lifecycle starts only the dispatcher and skips API-only reconcilers.
3. Test ECS discovery/desired-count parameterization for `detail-worker` in `us-east-1`.
4. Test demand counts both pending and running jobs, prewarms immediately, and never scales down while a job is running.
5. Test the Copilot worker contract: backend service, x86, 1 vCPU/4 GB, Spot count zero, no ALB, worker pool two.
6. Run the targeted tests and confirm they fail for the intended missing behavior.

## Task 4: Implement and verify PR2

**Files:**
- Modify: `server/app/workers/dispatcher.py`
- Modify: `server/app/main.py`
- Modify: `server/app/services/sam_autoscale.py`
- Modify: `server/app/workers/sam_autoscaler.py`
- Modify: `server/app/routes.py`
- Add: `copilot/detail-worker/manifest.yml`
- Modify: `copilot/api/manifest.yml`

1. Parse dispatcher include/exclude environment values with default-all compatibility and validate names against the existing worker registry.
2. Gate API-only background services when `JOB_KINDS` selects only `detail_page`; configure API to exclude it and the worker to include only it.
3. Parameterize the existing ECS adapter/reconciler for service tags/region; derive detail demand from `pending + running`.
4. Reuse the existing route wake hook to request detail-worker prewarm immediately after job commit.
5. Add the minimal x86 Spot-zero Copilot backend manifest with public networking and `DB_POOL_MAX_SIZE=2`.
6. Run targeted dispatcher/autoscale/route/manifest tests and commit PR2 with a Lore message.

## Task 5: Final verification

1. Run all changed-module tests and server static checks.
2. Run the broader server test suite if the targeted suite is green and runtime is reasonable.
3. Review the diff against the constraints: five provider calls remain possible; only local CPU work is serialized; API pool remains three; no NAT/ALB is introduced for the worker.
4. Report local branch/commits, fresh verification evidence, and any production-only validation gap. Stop without pushing or deploying.
