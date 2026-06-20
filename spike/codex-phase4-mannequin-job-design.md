> ⚠️ SUPERSEDED (2026-06-20): 이 설계 초안의 일부 env는 구현에서 바뀌었다 — Flash→Pro 승격·
> `MANNEQUIN_DEFAULT_TIER`/`UPGRADE_TIER`/`FLASH_MAX`/`PRO_MAX`는 폐기되고, AG-04는 단일
> `MANNEQUIN_TIER`(기본 image_high=Gemini 3 Pro) + `MANNEQUIN_MAX_ATTEMPTS`로 통합(사용자 결정).
> 정본은 `server/app/`(config·workers·repo) 실제 코드.

# AG-04 Mannequin Generation Backend AI Job Design

Scope: design only. This document designs the backend AI job for `generateMannequins`; it does not implement code. Source grounding is shown inline with file/section citations. Any choice not directly specified by the source documents is marked **NEW DECISION** with a one-line rationale.

## 1. Job Lifecycle

The lifecycle follows the job-shaped HTTP boundary from `documents/backend_integration_plan.md` §4, the Postgres queue/lease dispatcher from §5, the reserve-confirm credit model from §6, AG-04 from `documents/ai_agent_modules.md` §3, and PL-2 from `documents/ai_pipeline_spec.md` §3.

1. `POST /v1/projects/{projectId}/mannequins:generate`
   - Route authenticates with `require_user`, checks project ownership through `repo.get_project`, and never calls Gemini inside the request handler. This matches the existing route/repo ownership pattern in `server/app/routes.py` and `server/app/repo.py`, and the "request handler outside execution" rule in `backend_integration_plan.md` §5.
   - If `mannequin_cuts` already exist for the project, return `200 { data: MannequinCut[], credits }` without creating a job. This implements the completed-job idempotency rule in `common_data_contract.md` §6 and `ai_pipeline_spec.md` §3 PL-2.
   - If a `pending` or `running` `kind='mannequin'` job exists, return `202 { jobId }` for the existing job. This is the join rule in `common_data_contract.md` §6 and the active-job unique index in `supabase/migrations/20260612090000_init.sql`.
   - If the previous job ended in `error`, create a new job row with `status='pending'`, `kind='mannequin'`, `payload={ "mode": "generate" }`, and `dedupe_key='project:{projectId}:mannequin:generate'`. Failed jobs are not reused per `common_data_contract.md` §6.

2. Dispatcher claim
   - The FastAPI lifespan starts a dispatcher task when `DATABASE_URL` is configured. This extends the existing lifespan in `server/app/main.py` and follows `backend_integration_plan.md` §5.
   - Claim query:

```sql
with next_job as (
  select id
  from public.jobs
  where status = 'pending'
    and kind in ('mannequin')
  order by created_at
  for update skip locked
  limit 1
)
update public.jobs j
set status = 'running',
    locked_by = %(worker_id)s,
    locked_at = now(),
    started_at = coalesce(started_at, now()),
    progress = greatest(progress, 5)
from next_job
where j.id = next_job.id
returning j.*;
```

3. Worker credit reservation
   - The worker opens a transaction, locks `credit_accounts` with `FOR UPDATE`, verifies `available = balance - reserved`, reserves the placeholder maximum, updates `jobs.credits_reserved`, and appends a `progress` event. The credit structure comes from `backend_integration_plan.md` §6 and `supabase/migrations/20260612090000_init.sql`.
   - **NEW DECISION:** reservation happens in the worker after `202`, not in the route. Rationale: the requested lifecycle explicitly places `credit reserve` inside the worker, and the job event stream can still surface insufficient-credit failure to the HTTP adapter.

4. Input assembly
   - Load `products.colors[0].images` as the base color product photos, sorted by `slot` order `Front`, `Back`, `Detail`, `Fit`, then stable id order. Product images come from `Product.colors[].images[].src` in `common_data_contract.md` §3.1.
   - Load `analyses.payload` for `targetGenders`, `fit`, `materials`, `sellingPoints`, `aiSuggestedPoints`, `subCategory`, and `suggestedName`, matching AG-04 input and analysis ownership in `ai_agent_modules.md` §3 and `common_data_contract.md` §3.2.
   - Select one fixed base mannequin asset by gender. AG-04 requires the analysis-selected gender to choose the base and A/B to use the same gender base (`ai_agent_modules.md` §3 AG-04).
   - **NEW DECISION:** `targetGenders === ['men']` selects the male base; every other case, including mixed or empty gender, selects the female base. Rationale: deterministic MVP behavior until model-selection UX is backed by server catalogs.

5. A/B generation
   - Run two candidate tasks concurrently, one for `candidate='A'` and one for `candidate='B'`, bounded by `MANNEQUIN_CANDIDATE_CONCURRENCY=2`.
   - Each candidate task performs exactly one Gemini `generateContent` call per attempt with parts `[prompt text, base mannequin image, product images...]`, `generationConfig.responseModalities=['TEXT','IMAGE']`, and `imageConfig.imageSize='1K'`. The request shape is productionized from `spike/spike.js`, and `1K` is locked by the task decision because the 2K server path produced ghosts.
   - Candidate A uses `baseFit = analysis.fit`. Candidate B uses an adjacent contrast fit: `slim -> regular`, `regular -> semi_over`, `semi_over -> regular`, `over -> semi_over`. AG-04 says A/B differ by `baseFit` variation (`ai_agent_modules.md` §3).
   - **NEW DECISION:** A/B are two parallel Gemini calls, not one call asking for two images. Rationale: PL-2 already defines `AG-04 ×2`, and per-image QC plus Flash-to-Pro escalation requires independent attempt histories.

6. QC, retry, and Pro upgrade
   - The first tier is `image_light` (Flash). QC failure retries once on Flash with the QC failure reasons injected into the next prompt.
   - After two failed Flash attempts, that candidate upgrades to `image_high` (Pro). Pro also gets two attempts. This implements the locked decision "Flash 기본 + QC 실패 시 이미지별 Pro 승격" and uses the tier model from `ai_agent_modules.md` §1.
   - Each attempt appends a `step` event with `candidate`, `tier`, `model`, `attempt`, and QC result.

7. R2 asset store and `mannequin_cuts`
   - For each passed candidate, compute image dimensions/checksum, write bytes to R2 under `users/{userId}/projects/{projectId}/ai/{jobId}/{assetId}.{ext}`, insert an `assets` row with `source='ai'`, and insert a `mannequin_cuts` row with `candidate`, `version`, `asset_id`, `base_fit`, and null adjustment fields. This follows the R2 key rule in `backend_integration_plan.md` §3 and existing `assets`/`mannequin_cuts` schema in `20260612090000_init.sql`.
   - API `MannequinCut.id` is derived as `{candidate}-{version}` even though the DB row has a UUID primary key. This matches `common_data_contract.md` §3.3 and the `projects.selected_mannequin_id` comment in the migration.

8. Credit confirm/release and job finish
   - If at least one candidate passes, confirm credits for persisted candidates, release any unused reservation, set `jobs.status='done'`, set `jobs.result={ "data": cuts, "credits": availableAfter }`, and append `done`.
   - If no candidate passes, release the reservation, set `jobs.status='error'`, archive the failed dedupe key, and append `error`. The frontend receives an `Error` with the Korean message from the event, matching `common_data_contract.md` §6.
   - Partial success is a successful job: one candidate is returned and the failed candidate can be regenerated later. This follows PL-2 partial-success behavior in `ai_pipeline_spec.md` §3.

## 2. Exact file/function list

New backend files under `server/app/`:

- `server/app/agents/__init__.py`
  - Package marker for agent helpers.
- `server/app/agents/model_routing.py`
  - `resolve_model(settings, tier: str) -> str`
  - `model_routing_snapshot(settings) -> dict`
  - Owns tier-to-model lookup so AG-04 declares tiers only, per `ai_agent_modules.md` §1.
- `server/app/agents/prompts.py`
  - `load_prompt_template(path: str) -> str`
  - `render_mannequin_prompt(template: str, context: MannequinPromptContext) -> str`
  - `prompt_version(settings) -> str`
  - Productionizes the spike `--prompt-file` pattern from `spike/spike.js`.
- `server/app/agents/gemini_image.py`
  - `class GeminiImageClient`
  - `async generate_content_image(model: str, prompt: str, images: list[InlineImage], image_size: str) -> GeminiImageResult`
  - Uses async HTTP for Gemini and records latency/usage for observation logs from `ai_agent_modules.md` §6.
- `server/app/agents/mannequin.py`
  - `select_base_gender(analysis: dict) -> Literal["men","women"]`
  - `candidate_specs(fit: str) -> list[CandidateSpec]`
  - `build_product_context(product: dict, analysis: dict) -> dict`
  - `build_inline_images(base_asset, product_assets) -> list[InlineImage]`
  - Contains AG-04-specific prompt context only; no DB writes.
- `server/app/services/qc.py`
  - `evaluate_mannequin_qc(base_bytes: bytes, generated_bytes: bytes) -> QcResult`
  - `format_qc_feedback(result: QcResult) -> str`
  - Implements the cheap full-body/aspect/ghost gate described in §5.
- `server/app/services/assets.py`
  - `async load_asset_bytes(conn, r2, asset_id: str) -> LoadedAsset`
  - `async store_ai_image_asset(conn, r2, *, user_id, project_id, job_id, image) -> dict`
  - `asset_file_url(asset_id: str) -> str`
  - Keeps R2 byte movement and asset row creation out of worker code.
- `server/app/services/credits.py`
  - `async reserve_job_credits(conn, *, user_id, project_id, job_id, amount, action_key, version) -> CreditReservation`
  - `async confirm_job_credits(conn, *, user_id, project_id, job_id, reserved, charge, action_key, idempotency_key, metadata) -> int`
  - `async release_job_credits(conn, *, user_id, job_id, reserved) -> int`
  - Encapsulates reserve-confirm and ledger idempotency from `backend_integration_plan.md` §6.
- `server/app/workers/__init__.py`
  - Package marker for job workers.
- `server/app/workers/dispatcher.py`
  - `class JobDispatcher`
  - `async start()`, `async stop()`, `async run_loop()`
  - `async claim_next_job(pool, worker_id) -> dict | None`
  - `async recover_stale_leases(pool, lease_timeout_seconds) -> list[dict]`
  - Owns `FOR UPDATE SKIP LOCKED` and stale lease recovery from `backend_integration_plan.md` §5.
- `server/app/workers/mannequin_job.py`
  - `async run_mannequin_job(app, job: dict) -> None`
  - `async process_candidate(ctx, spec: CandidateSpec) -> CandidateResult`
  - `async persist_candidate(ctx, candidate: CandidateResult) -> dict`
  - `async finalize_success(ctx, cuts: list[dict]) -> None`
  - `async finalize_failure(ctx, message: str, details: dict) -> None`
  - Owns the AG-04 orchestration; provider, QC, assets, and credits are delegated.

Changed backend files:

- `server/app/config.py`
  - Add settings:
    - `gemini_api_key`
    - `vertex_project`
    - `vertex_location`
    - `model_routing_image_light`
    - `model_routing_image_high`
    - `mannequin_default_tier`
    - `mannequin_upgrade_tier`
    - `mannequin_image_size`
    - `mannequin_prompt_file`
    - `mannequin_prompt_version`
    - `mannequin_base_men_asset_id`
    - `mannequin_base_women_asset_id`
    - `mannequin_flash_max_attempts`
    - `mannequin_pro_max_attempts`
    - `job_dispatcher_enabled`
    - `job_poll_interval_seconds`
    - `job_lease_timeout_seconds`
    - `job_worker_id`
    - `credit_cost_version`
    - `credit_cost_mannequin_generate`
- `server/app/main.py`
  - In lifespan, open pool as today, then start `JobDispatcher(app)` after `pool.open()`.
  - Stop dispatcher before closing the pool.
  - Do not start dispatcher when `pool is None` or `JOB_DISPATCHER_ENABLED=false`.
- `server/app/routes.py`
  - Add `POST /v1/projects/{project_id}/mannequins:generate`.
  - Add `GET /v1/projects/{project_id}/mannequins`.
  - Add `GET /v1/jobs/{job_id}`.
  - Add `GET /v1/jobs/{job_id}/events`.
  - Add `GET /v1/assets/{asset_id}/file` for stable app URLs, per `backend_integration_plan.md` §3.
- `server/app/models.py`
  - Add `MannequinCut`, `JobAccepted`, `JobView`, `JobEventView`, and `CreditEnvelope[T]` models using the existing `CamelModel`.
  - `MannequinCut.id` is a derived client id string, not the DB UUID.
- `server/app/repo.py`
  - Add `list_mannequin_cuts(conn, user_id, project_id)`.
  - Add `create_mannequin_job(conn, user_id, project_id, idempotency_key)`.
  - Add `get_active_job(conn, user_id, project_id, kind)`.
  - Add `get_job_for_user(conn, user_id, job_id)`.
  - Add `append_job_event(conn, job_id, event_type, payload)`.
  - Add `list_job_events(conn, user_id, job_id, after_id)`.
  - Add `insert_mannequin_cut(conn, project_id, candidate, version, asset_id, base_fit)`.
  - Add `next_mannequin_version(conn, project_id, candidate)`.
  - Add `archive_failed_dedupe_key(conn, job_id)`.
  - Add credit account helpers used by `services/credits.py`.
- `server/app/r2.py`
  - Add `get_bytes(key: str) -> bytes`.
  - Add `presigned_get(key: str, expires: int = 300) -> str`.
  - Keep existing `put_bytes`; call boto3 methods through `asyncio.to_thread` from services/workers as current routes already do.

Required non-`server/app` implementation changes:

- `server/pyproject.toml`
  - Move `httpx>=0.28` into production dependencies for async Gemini calls.
  - Add `pillow>=10` for image metadata and QC pixel heuristics.
- `server/.env.example`
  - Add the new Gemini, model routing, prompt, base asset, worker, lease, and credit-cost keys.
- `server/prompts/mannequin_generate_v1.md`
  - Prompt template stored outside code, loaded by `MANNEQUIN_PROMPT_FILE`.
  - **NEW DECISION:** use a file path from env rather than a DB prompt table for MVP. Rationale: it exactly matches the successful spike workflow and avoids adding admin CRUD before prompt operations exist.

Prompt externalization mechanism:

- Default file: `server/prompts/mannequin_generate_v1.md`.
- Env override: `MANNEQUIN_PROMPT_FILE=/app/prompts/mannequin_generate_v2.md`.
- Version recorded in `jobs.metadata.promptVersion` from `MANNEQUIN_PROMPT_VERSION`.
- Template variables: `${clothingType}`, `${productCount}`, `${candidate}`, `${baseFit}`, `${baseGender}`.
- Product/analysis context is appended as structured text, following `spike/spike.js` `productBlock()` and AG-04 input in `ai_agent_modules.md` §3.

A/B candidate handling:

- Two Gemini calls, one per candidate, run in parallel.
- Candidate rows:
  - A: `candidate='A'`, `version=1`, `base_fit=analysis.fit`.
  - B: `candidate='B'`, `version=1`, `base_fit=adjacent contrast fit`.
- Stored as separate `assets` rows and separate `mannequin_cuts` rows.
- Response order is always A then B; if one fails QC after all retries, return the passing candidate only.

AG-05 plug-in path:

- AG-05 will add `server/app/workers/mannequin_adjust_job.py` and `server/app/agents/mannequin_adjust.py`.
- It reuses `GeminiImageClient`, `model_routing.py`, `prompts.py`, `services/qc.py`, `services/assets.py`, `services/credits.py`, `JobDispatcher`, and `job_events`.
- No refactor is needed because AG-04 stores immutable `mannequin_cuts` versions and AG-05 already takes `baseImageUrl` plus adjustment dimensions in `ai_agent_modules.md` §3.

Stale lease recovery:

- `JobDispatcher` runs `recover_stale_leases()` once on startup and then every `JOB_POLL_INTERVAL_SECONDS=5` loop when at least `JOB_SWEEP_INTERVAL_SECONDS=60` has elapsed.
- Lease timeout default: `JOB_LEASE_TIMEOUT_SECONDS=900`.
- Query:

```sql
with stale as (
  select
    id,
    coalesce((metadata->>'leaseRecoveries')::int, 0) as recoveries
  from public.jobs
  where status = 'running'
    and locked_at < now() - (%(lease_timeout_seconds)s::text || ' seconds')::interval
  for update skip locked
),
updated as (
  update public.jobs j
  set status = case when stale.recoveries >= 1 then 'error' else 'pending' end,
      locked_by = null,
      locked_at = null,
      error_message = case when stale.recoveries >= 1 then '작업 서버가 응답하지 않아 작업을 중단했어요. 다시 시도해 주세요.' else null end,
      metadata = jsonb_set(j.metadata, '{leaseRecoveries}', to_jsonb(stale.recoveries + 1), true),
      finished_at = case when stale.recoveries >= 1 then now() else null end
  from stale
  where j.id = stale.id
  returning j.*
)
select * from updated;
```

- Requeued jobs get a `progress` event with `{ phase: "lease_requeued" }`.
- Jobs errored by the second stale lease get an `error` event and `release_job_credits()` if `credits_reserved > 0`.

Frontend `lib/api` wiring:

- `src/lib/api/httpAdapter.js` adds:
  - `async generateMannequins(projectId, { onProgress } = {})`
  - `async getMannequins(projectId)`
  - `async waitForJob(jobId, { onProgress, onStep })`
  - `async streamJobEvents(jobId, callbacks)`
  - `async pollJob(jobId, callbacks)`
- `generateMannequins` posts to `/v1/projects/${projectId}/mannequins:generate`; if it receives `{ data, credits }`, it returns immediately; if it receives `{ jobId }`, it waits for `done` or `error`.
- Use `fetch` streaming for SSE instead of browser `EventSource` because the existing auth contract uses `Authorization: Bearer` in `httpAdapter.js`, and native `EventSource` cannot set that header.
- Polling fallback calls `GET /v1/jobs/{jobId}` every 1500 ms.

## 3. Schema

Existing relevant tables are already present in `supabase/migrations/20260612090000_init.sql`; no new table migration is required for AG-04 MVP. The append-only migration rule in `agents.md` means any future schema change must be a new forward migration.

Existing `assets` table:

```sql
create table public.assets (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users (id) on delete cascade,
  project_id uuid references public.projects (id) on delete set null,
  source text not null check (source in ('upload', 'ai', 'export', 'seed')),
  visibility text not null default 'private' check (visibility in ('private', 'public')),
  r2_bucket text not null,
  r2_key text not null unique,
  mime_type text not null,
  byte_size bigint,
  width integer,
  height integer,
  checksum text,
  original_filename text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  deleted_at timestamptz,
  check (user_id is not null or source = 'seed')
);
```

Existing `mannequin_cuts` table:

```sql
create table public.mannequin_cuts (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects (id) on delete cascade,
  candidate text not null check (candidate in ('A', 'B')),
  version integer not null default 1 check (version >= 1),
  asset_id uuid not null references public.assets (id),
  base_fit text not null,
  fit_adjust text,
  length_adjust text,
  match_adjust jsonb,
  created_at timestamptz not null default now(),
  unique (project_id, candidate, version)
);
```

Existing `jobs` table:

```sql
create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  project_id uuid not null references public.projects (id) on delete cascade,
  kind text not null check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image')),
  status text not null default 'pending' check (status in ('pending', 'running', 'done', 'error')),
  progress integer not null default 0 check (progress between 0 and 100),
  steps jsonb not null default '[]'::jsonb,
  payload jsonb not null default '{}'::jsonb,
  result jsonb,
  error_message text,
  dedupe_key text unique,
  idempotency_key text unique,
  credits_reserved integer not null default 0,
  credits_charged integer,
  locked_by text,
  locked_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  updated_at timestamptz not null default now(),
  finished_at timestamptz
);
create unique index jobs_active_unique_idx on public.jobs (project_id, kind)
  where status in ('pending', 'running') and kind <> 'editor_image';
create index jobs_pending_idx on public.jobs (status, created_at) where status = 'pending';
```

Existing `job_events` table:

```sql
create table public.job_events (
  id bigint generated always as identity primary key,
  job_id uuid not null references public.jobs (id) on delete cascade,
  event_type text not null check (event_type in ('progress', 'step', 'done', 'error')),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index job_events_job_idx on public.job_events (job_id, id);
```

Existing `credit_accounts` table:

```sql
create table public.credit_accounts (
  user_id uuid primary key references auth.users (id) on delete cascade,
  balance integer not null default 0 check (balance >= 0),
  reserved integer not null default 0 check (reserved >= 0),
  updated_at timestamptz not null default now(),
  check (reserved <= balance)
);
```

Existing `credit_ledger` table:

```sql
create table public.credit_ledger (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id),
  project_id uuid references public.projects (id) on delete set null,
  job_id uuid references public.jobs (id) on delete set null,
  action_key text not null,
  delta integer not null,
  balance_after integer not null,
  available_after integer not null,
  idempotency_key text unique,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
```

New migrations needed:

- None for AG-04 job execution.
- No `prompt_templates` table in MVP; prompts are external files via env.
- No table for base mannequins; use existing `assets` seed rows plus env-configured asset ids.

Required operational seed data:

- Insert or upload two 1K base mannequin assets:
  - `MANNEQUIN_BASE_MEN_ASSET_ID`
  - `MANNEQUIN_BASE_WOMEN_ASSET_ID`
- Recommended row metadata:

```json
{
  "kind": "base_mannequin",
  "gender": "men|women",
  "resolution": "1K",
  "sourceRun": "spike/base"
}
```

## 4. Model routing

AG-04 does not store model names. It requests a tier, and `server/app/agents/model_routing.py` maps tiers to model ids, matching `ai_agent_modules.md` §1 and `ai_pipeline_spec.md` §6.

Runtime settings:

```env
MODEL_ROUTING_IMAGE_LIGHT=gemini-3.1-flash-image
MODEL_ROUTING_IMAGE_HIGH=gemini-3-pro-image
MANNEQUIN_DEFAULT_TIER=image_light
MANNEQUIN_UPGRADE_TIER=image_high
MANNEQUIN_IMAGE_SIZE=1K
```

Selection rule per candidate:

1. Attempts 1-2 use `MANNEQUIN_DEFAULT_TIER=image_light` -> Flash.
2. Attempts 3-4 use `MANNEQUIN_UPGRADE_TIER=image_high` -> Pro.
3. A candidate can upgrade without upgrading the other candidate. This is required by the locked "이미지별 Pro 승격" decision.

One-line swap:

- Change `MODEL_ROUTING_IMAGE_LIGHT` to replace Flash for default attempts.
- Change `MODEL_ROUTING_IMAGE_HIGH` to replace Pro for upgraded attempts.
- No worker, prompt, or route code changes.

Observation log in `jobs.metadata.agentCalls[]`:

```json
{
  "agentId": "AG-04",
  "candidate": "A",
  "tier": "image_light",
  "model": "gemini-3.1-flash-image",
  "imageSize": "1K",
  "attempt": 1,
  "latencyMs": 31000,
  "tokenOrImageCount": { "promptTokens": 0, "imageOutputs": 1 },
  "qc": { "verdict": "retry", "reasons": ["full_body_crop"] }
}
```

This implements the observation requirement in `ai_agent_modules.md` §6 and `backend_integration_plan.md` §5.

## 5. QC gate spec

The MVP QC gate is non-AI and cheap by design. It uses Pillow to inspect image dimensions and pixels. It catches the known failure modes from the spike: crop/framing drift and ghost/artifact output. Semantic product identity remains the later AG-P2 slot described in `ai_agent_modules.md` §5.

Inputs:

- Base mannequin image bytes.
- Generated image bytes.
- Candidate metadata: `candidate`, `tier`, `attempt`, `baseFit`.

Preprocessing:

1. Decode image with Pillow. Fail with `decode_failed` if unsupported or corrupt.
2. Convert to RGB.
3. Estimate background color as the median RGB of a 5 px border.
4. Foreground mask = pixels whose Euclidean RGB distance from background is greater than `28`.
5. Remove tiny connected components smaller than `0.15%` of image area before bbox checks.

Checks:

1. Output size sanity
   - `width >= 768` and `height >= 768`.
   - Fail reason: `too_small`.

2. Portrait aspect ratio
   - `0.70 <= width / height <= 0.82`.
   - Also require `abs((width / height) - base_ratio) <= 0.06`.
   - Fail reason: `bad_aspect_ratio`.

3. Full-body framing
   - Generated foreground bbox top must be `<= 0.14 * height`.
   - Generated foreground bbox bottom must be `>= 0.88 * height`.
   - Generated foreground bbox height must be `>= 0.76 * height`.
   - Bbox center x must be between `0.42 * width` and `0.58 * width`.
   - Generated bbox vs base bbox IoU must be `>= 0.70`.
   - Top drift and bottom drift versus base bbox must each be `<= 0.08 * height`.
   - Fail reason: `full_body_crop`.

4. Lower-body/feet presence
   - The bottom 12% of the image must contain foreground pixels in at least `1.5%` of total image pixels.
   - Fail reason: `missing_lower_body`.

5. Ghost/artifact heuristic
   - Largest foreground connected component area divided by total foreground area must be `>= 0.82`.
   - No secondary connected component may exceed `8%` of total foreground area.
   - Number of connected components larger than `0.5%` of image area must be `<= 3`.
   - Edge density outside the base bbox expanded by 8% must be `<= 0.04`.
   - Fail reason: `ghost_or_artifact`.

Pass/fail:

- All checks must pass.
- QC output:

```json
{
  "verdict": "pass|retry",
  "reasons": ["full_body_crop"],
  "metrics": {
    "aspectRatio": 0.75,
    "bboxTop": 0.08,
    "bboxBottom": 0.94,
    "bboxIoU": 0.82,
    "largestComponentShare": 0.91
  }
}
```

Retry policy:

- `MANNEQUIN_FLASH_MAX_ATTEMPTS=2`.
- `MANNEQUIN_PRO_MAX_ATTEMPTS=2`.
- Attempt 1: Flash.
- Attempt 2: Flash with `format_qc_feedback()` appended to the prompt.
- Attempt 3: Pro with the original prompt plus QC feedback.
- Attempt 4: Pro with latest QC feedback.

Final outcome:

- Candidate passes: store it.
- Candidate fails after Pro attempt 4: mark that candidate failed and continue the other candidate.
- One passed candidate: job succeeds with partial result and charges one placeholder credit.
- Zero passed candidates: job errors, releases all reserved credits, and returns a Korean retry message.

## 6. Credit accounting

The credit design follows `backend_integration_plan.md` §6, `common_data_contract.md` §6, `ai_pipeline_spec.md` §5, and the placeholder values in `src/lib/limits.js`.

Placeholder amounts:

- `credit_cost_mannequin_generate = 2`.
- Reserve amount = `2`.
- **NEW DECISION:** confirm amount = number of persisted candidates, so A+B charges 2 and partial success charges 1. Rationale: the backend plan says successful portions are confirmed and failures are released; A/B candidates are independent outputs.

Reservation transaction:

```sql
select balance, reserved
from public.credit_accounts
where user_id = %(user_id)s
for update;
```

- If `balance - reserved < 2`, set job to `error`, append `error { code: "insufficient_credits" }`, and do not call Gemini.
- Else:

```sql
update public.credit_accounts
set reserved = reserved + 2
where user_id = %(user_id)s;

update public.jobs
set credits_reserved = 2,
    metadata = jsonb_set(metadata, '{creditCostVersion}', to_jsonb(%(credit_cost_version)s::text), true)
where id = %(job_id)s;
```

Confirm transaction:

```sql
update public.credit_accounts
set balance = balance - %(charge)s,
    reserved = reserved - %(reserved)s
where user_id = %(user_id)s
returning balance, reserved, balance - reserved as available_after;

insert into public.credit_ledger
  (user_id, project_id, job_id, action_key, delta, balance_after, available_after, idempotency_key, metadata)
values
  (%(user_id)s, %(project_id)s, %(job_id)s, 'mannequinGenerate', -%(charge)s,
   %(balance_after)s, %(available_after)s, %(ledger_idempotency_key)s, %(metadata)s)
on conflict (idempotency_key) do nothing;
```

- Ledger idempotency key: `credit:job:{jobId}:confirm:mannequinGenerate:v1`.
- `metadata` includes `reserved=2`, `charged`, `candidateCount`, `promptVersion`, `modelRouting`, and `creditCostVersion`.
- If `charge=0`, skip ledger insert and release only.

Release transaction:

```sql
update public.credit_accounts
set reserved = greatest(0, reserved - %(reserved)s)
where user_id = %(user_id)s
returning balance - reserved as available_after;
```

- No ledger row is written for pure release because balance did not move. The job metadata records release details.

Job idempotency:

- Active duplicate: join by `jobs_active_unique_idx` and return existing `jobId`.
- Completed duplicate: return existing `mannequin_cuts` and current available credits.
- Failed duplicate: `archive_failed_dedupe_key()` changes the failed row's dedupe key to `failed:{jobId}:project:{projectId}:mannequin:generate`, then a new job can reuse the canonical dedupe key.
- Explicit HTTP `Idempotency-Key` is stored as `idempotency_key='http:{userId}:{headerValue}'`. The frontend should generate a fresh key per user action; automatic remount joins are handled by project/kind dedupe.

## 7. SSE/polling

The source of truth is `job_events`, as required by `backend_integration_plan.md` §5 and `ai_pipeline_spec.md` §4.

Routes:

- `GET /v1/jobs/{jobId}` returns the latest job snapshot:

```json
{
  "id": "uuid",
  "projectId": "uuid",
  "kind": "mannequin",
  "status": "pending|running|done|error",
  "progress": 0,
  "steps": [],
  "result": null,
  "errorMessage": null,
  "creditsCharged": null,
  "createdAt": "ISO",
  "updatedAt": "ISO"
}
```

- `GET /v1/jobs/{jobId}/events` streams Server-Sent Events. It reads `Last-Event-ID` or `?after=<id>` and replays rows from `job_events` ordered by `id`.

Event format:

```text
id: 42
event: progress
data: {"progress":25,"phase":"candidate_running","candidate":"A","message":"마네킹컷을 만들고 있어요"}
```

Events appended:

1. Job created:
   - `progress { progress: 0, phase: "queued", message: "작업을 준비하고 있어요" }`
2. Dispatcher claimed:
   - `progress { progress: 5, phase: "claimed", workerId }`
3. Credits reserved:
   - `progress { progress: 10, phase: "credits_reserved", creditsReserved: 2 }`
4. Candidate attempt start:
   - `step { candidate: "A", status: "running", tier, model, attempt }`
5. Candidate attempt QC fail:
   - `step { candidate: "A", status: "retrying", tier, attempt, qc }`
6. Candidate Pro upgrade:
   - `step { candidate: "A", status: "upgraded", fromTier: "image_light", toTier: "image_high" }`
7. Candidate stored:
   - `step { candidate: "A", status: "done", clientId: "A-1", assetId }`
8. Partial candidate failure:
   - `step { candidate: "B", status: "failed", qc }`
9. Job done:
   - `done { data: MannequinCut[], credits, creditsCharged }`
10. Job error:
   - `error { code, message, details }`

Progress mapping for PL-2:

- 0: queued.
- 5: claimed.
- 10: credits reserved.
- 15: inputs loaded.
- 20-80: candidate attempts. A and B each contribute 30 points, with retry attempts updating inside the same span.
- 85: QC complete.
- 92: assets stored.
- 97: credits confirmed/released.
- 100: done.

Frontend behavior:

- `httpAdapter.generateMannequins(projectId, { onProgress })` posts the job request.
- If the response is `202 { jobId }`, it calls `waitForJob`.
- `waitForJob` first tries authenticated fetch-stream SSE and replays events into `onProgress`.
- If fetch streaming fails or stalls for 20 seconds, it switches to polling `GET /v1/jobs/{jobId}` every 1500 ms.
- On `done`, resolve `{ data, credits }`.
- On `error`, throw `Error(message)` so existing UI toast behavior remains compatible with `common_data_contract.md` §6.

## 8. Open risks and mitigations

1. Missing source file: `memory/spike-flash-mannequin-findings.md`
   - Risk: detailed Flash/1K observations from this session are not available in the repo at the requested path.
   - Mitigation: this design treats the user's locked decisions as binding, stores all routing/image-size choices in env, and records per-attempt metadata so the first implementation run can reconstruct Flash vs Pro behavior from job logs.

2. Cheap QC can reject good images or pass subtle garment mismatches
   - Risk: pixel heuristics catch full-body/ghost failures but cannot prove logo, material, or seam fidelity.
   - Mitigation: keep QC reasons and metrics in `jobs.metadata`, allow partial success, and reserve AG-P2 semantic image QC exactly at the post-generation gate described in `ai_agent_modules.md` §5.

3. Base mannequin seed assets are operationally required
   - Risk: worker cannot run if `MANNEQUIN_BASE_MEN_ASSET_ID` or `MANNEQUIN_BASE_WOMEN_ASSET_ID` points to a missing/private-broken asset.
   - Mitigation: dispatcher startup validates both asset ids and logs a fatal configuration error while leaving HTTP routes alive; `/healthz` stays basic but `/v1/jobs` will not claim mannequin jobs until base assets validate.

4. Failed-job dedupe conflicts with the current unique `dedupe_key`
   - Risk: a failed job retaining the canonical dedupe key would block the contract-required retry-after-error behavior.
   - Mitigation: `finalize_failure()` archives the failed row's dedupe key before commit. This uses existing nullable unique `dedupe_key` and requires no migration.

5. Retry and Pro escalation can multiply provider cost
   - Risk: worst case is 8 Gemini calls for one A/B request: 2 candidates × (2 Flash + 2 Pro).
   - Mitigation: hard attempt caps, candidate-level concurrency of 2, job-level project/kind dedupe, and metadata logs with model/tier/latency/candidate outcome. Product launch can lower attempts or disable Pro escalation by env without code changes.

6. SSE auth cannot use native `EventSource`
   - Risk: the existing frontend auth helper sends `Authorization: Bearer`; native `EventSource` cannot set this header.
   - Mitigation: implement SSE over `fetch` streaming in `httpAdapter`, with polling fallback. This preserves the existing Supabase session flow in `src/lib/api/httpAdapter.js`.

7. Worker-in-web deployment competes with request handling
   - Risk: Gemini/R2 latency and Pillow QC can consume resources in the FastAPI web process.
   - Mitigation: provider calls use async HTTP, boto3 calls run through `to_thread`, Pillow work is bounded to 1K images, and the dispatcher can be disabled or moved to a separate Railway worker later because `jobs` remains the queue source per `backend_integration_plan.md` §5.
