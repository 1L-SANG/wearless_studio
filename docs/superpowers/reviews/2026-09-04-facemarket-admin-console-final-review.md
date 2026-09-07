# Final whole-branch review — FaceMarket 관리자 콘솔

**Diff:** `ac0fae10..f7b2f19b` (41 commits, both lanes merged)
**Reviewer scope:** the branch as one system — cross-lane contracts, authorization, transaction integrity, aggregation correctness, cold-read maintainability.

---

## Merge readiness

**Ready after Important fixes.**

No Critical issues. The backend is unusually careful — the guard/audit seam, the compare-and-set on suspend, and the single ordered lock on role change all hold up under the concurrency cases I could construct. What the per-lane reviews could not see is the seam between them and the seam between the aggregation and the rest of the schema: two dashboard numbers are silently wrong in ways no test in this repo can catch, one model status exists in the backend but renders as a blank badge in the frontend, and five *pre-existing* admin routes now hard-depend on a table that must be migrated before the app image ships.

Findings: **0 Critical · 5 Important · 9 Minor.**

---

## How I reviewed this

The diff is 9,463 lines. I did not read it front to back; I sectioned it by *what a defect there would cost*, and read the sections in that order:

1. **Authority first** — `docs/superpowers/specs/…-design.md` (diff L3830–4130) in full, before any code, so every later judgment is against the spec rather than against the code's own comments. The plan doc (L184–3830) I skipped entirely: the brief says the design doc wins, and 3,600 lines of task briefs restate what the code already shows.
2. **Backend write paths** (L5028–6168) — `admin_guard.py`, `facemarket_admin.py`, the six migrated call sites, the migration. Read line by line, twice: once for logic, once for "what does this do with two callers".
3. **Cross-lane contract** (L8024–8840 frontend screens × L5157–5827 backend routes) — read side by side, key by key. This is where I spent the most time and where three of the five Important findings came from.
4. **Tests** (L6168–7193 backend, L8887–9463 frontend) — read to find assertions that encode a *wrong belief*, not to check coverage. One does (`test_email_failed_counts_latest_row_per_application_not_ever_failed` asserts parity that does not hold; see Important 1).
5. **Build/style plumbing** (L104–184, L4130–5028, L7193–7635, L9280–9463) — skimmed, since the caller verified the built CSS and bundle separation empirically, which is stronger evidence than reading.

**Read outside the diff, each for a named risk:**

| Risk I could name | What I opened |
|---|---|
| Does the queue's email rule actually match the list badge? | `server/app/facemarket_applications.py:745–790, 976–1003` — the list's `left join lateral` and `_dispatch_decision_email`'s post-commit insert |
| Is `applicationsRejected` complete? | `server/app/facemarket_enrollment.py:928–975` — the auto-reject path |
| Does the resend route really commit implicitly? | `server/app/db.py:41–56` — `get_conn` yields a pooled connection; `psycopg_pool` commits on clean exit |
| Do the dashboard buckets cover the schema's status domains? | `supabase/migrations/20260821010100_…` (fm_models, fm_biometric_enrollments), `20260709000000_facemarket_core.sql` (fm_licenses, fm_settlements), `20260620100000_credit_subscription_system.sql` (payment_history, refund_requests), `20260902120000_…applications.sql` |
| `profiles.updated_at`, `fm_models.updated_at`, `profiles.role` nullability, FKs that could deadlock against `FOR UPDATE` | `20260612090000_init.sql:20–29`, `20260613041903_profiles_role_and_signup_trigger.sql`; grepped for `references public.profiles` → none |
| Does the audit `before` ever describe a transition that didn't happen? | `server/app/repo.py:3017–3065` — `_load_refund_for_resolve` guards `status != 'pending'` → 409 before any write |
| Does `e.message` in the screens carry the server's Korean copy? | `src/lib/api/httpAdapter.js:59–135`, `server/app/main.py:285–356` — envelope shapes match |
| Could `absolutizeAssetUrls` mangle admin payloads? | `src/lib/assetUrl.js:44–48` — only touches `/v1/assets/…`; safe |
| Are `useToast`/`useAuth` the shapes the screens destructure? | `src/components/ui.jsx:330–387`, `src/features/auth/AuthProvider.jsx:164–173` — yes |
| Can a non-admin reach a screen? | `src/apps/guards.jsx` — `RequireAuth` gates session only, per design; every API 403s |
| Is `auth.users.email` actually populated? | `AuthProvider.jsx:110–112` (Google/Kakao OAuth only), `20260902120000_…applications.sql:23` ("auth 엔 이메일 없음"), `shell.jsx:185` (defensive fallback) |

I ran no tests and made no writes; every command was a read.

---

## Cross-lane contract check

The frontend was written against a documented contract on a separate branch and never ran against the backend. I checked every key each screen reads against what the route actually emits, including casing and nesting.

### `GET /admin/overview` → `AdminDashboard.jsx`

| Key the screen reads | Route emits (`facemarket_admin.py:145–180`) | Verdict |
|---|---|---|
| `queue.applicationsUnderReview` / `.identityMismatch` / `.emailFailed` / `.refundsPending` | all four, same casing | ✅ |
| `kpi.applicationsSubmitted` / `.applicationsApproved` / `.applicationsRejected` | ✅ shape | ⚠️ **values wrong** — Important 2 |
| `kpi.licensesIssued` / `.creditRevenueKrw` / `.settlementAmountKrw` / `.settlementFailed` | ✅ | ✅ |
| `series[].applications` / `.licenses` / `.settlementAmountKrw` | ✅ (`date` emitted, unread — fine) | ✅ |
| `distribution.models.pending/verified/suspended` | ✅ | ✅ |
| `distribution.models.reverificationRequired` | ✅ emitted since `a8e7d6b3`; screen guards `?? 0` | ✅ |
| `distribution.enrollments.passed/inFlight/failed` | ✅ | ✅ |
| `period.{days,from,to}` | emitted at `:152` | ⚠️ **no consumer anywhere** — Minor 3 |

`days` query param: screen sends only 7/30/90 (`PERIODS`), route allows exactly those. ✅

### `GET /admin/models` → `AdminModels.jsx` list

| Key | Emitted by `_model_row` (`:256–265`) | Verdict |
|---|---|---|
| `items[].id` / `.displayName` / `.status` / `.email` / `.licenseCount` / `.lastSettlementAt` | ✅ all, camelCase | ✅ |
| `.createdAt` | emitted | not rendered — Minor 5 |
| `status` value domain | route allows 4 (`MODEL_STATUSES:220`), screen maps 3 (`AdminModels.jsx:25–26`) | ❌ **Important 3** |
| `q`/`status`/`limit` params | route reads all three | ✅ (no `cursor` — Minor 4) |

### `GET /admin/models/{id}` → `Detail`

| Key | Emitted (`model_detail:299–336`) | Verdict |
|---|---|---|
| `model.*` | same `_model_row` shape | ✅ |
| `licenses[].id/.status/.unitPrice/.validUntil` | ✅ (`vcId` emitted, unread) | ✅ |
| `settlements[].id/.createdAt/.totalAmount/.chainStatus` | ✅ (`txHash` emitted, unread) | ✅ |
| `enrollment.status/.completedAt`, null when absent | ✅ — screen renders `'기록 없음'` | ✅ |

### `POST …/suspend` · `…/unsuspend` → `Detail.act`

`{ reason }` body matches `SuspendRequest` (CamelModel, single field). Errors surface via `e.message`, which `httpAdapter.js:127` fills from `{error:{message}}`, which `main.py:355` produces from `HTTPException(detail={"code","message"})`. The 409 `already_suspended` / 400 `not_suspended` copy reaches the toast intact. ✅

### `GET /admin/staff` · `POST …/role` · `GET /admin/audit` → `AdminStaff.jsx`

| Key | Emitted | Verdict |
|---|---|---|
| `admins[].userId/.email/.displayName`, `matches[].userId/.email/.role` | `_staff_row:517–523` ✅ | ✅ |
| `a.userId === user?.id` self-check | `profiles.user_id` = `auth.users.id` = Supabase session `user.id` ✅ | ✅ |
| `{ role }` body → `RoleRequest` | ✅ | ✅ |
| `audit[].id/.action/.targetId/.actorEmail/.note/.createdAt` | `list_audit:611–623` ✅ | ✅ |
| `ACTION_LABEL` keys vs emitted `action` strings | all nine match exactly (`application.approve|reject|resend_email`, `staff.role.grant|revoke`, `model.suspend|unsuspend`, `refund.approve|reject`) | ✅ |
| `limit`/`targetType`/`targetId` params | route aliases match the client's `URLSearchParams` keys exactly | ✅ |
| design §7 "마지막 조치 시각" column | not emitted, not rendered | Minor 5 |

**Verdict:** the contract holds field-for-field with two exceptions — the `status` value domain (Important 3) and `period` having no consumer (Minor 3). Casing and nesting are correct everywhere. That is a good result for two branches that never met.

---

## Findings

### Critical

None.

---

### Important

#### I-1. The dashboard's "결정 메일 미발송" undercounts — it never sees an application whose email row was never created

`server/app/facemarket_admin.py:64` (`join lateral`) vs `server/app/facemarket_applications.py:764` (`left join lateral`) vs `src/features/admin/AdminApplications.jsx:88`

The list badge fires when `lastEmailStatus === 'failed'` **or** when the application is decided and has *no* email row at all. The queue uses an **inner** `join lateral`, so an application with zero email rows is dropped from the count entirely.

That case is reachable and is exactly what the badge's second clause was written for: `_dispatch_decision_email` (`facemarket_applications.py:976–1003`) runs **after** the decision commits, on its own connection, and inserts the `pending` row itself. If that insert fails — pool exhaustion, a DB blip, task cancellation — the application is approved with no email row and no email sent. The list shows 메일 미발송 with a resend button; the dashboard says 0.

The pending→failed 2-minute coercion *is* identical on both sides (I checked character by character), so the only divergence is the join type. But the design doc pins parity as a requirement (§5.2: "화면의 '메일 미발송' 배지와 같은 원천"), the code comment at `:57–62` asserts "목록의 규칙과 한 글자도 다르면 안 된다", and `test_admin_overview.py::test_email_failed_counts_latest_row_per_application_not_ever_failed` asserts parity by grepping for the lateral — so the test passes while the parity it claims to protect is broken. The earlier fix (2772e5f0) closed the over-count direction and opened the under-count one.

Under-counting is the worse direction for a queue whose whole job is "손댈 일". **Fix:** make it a `left join lateral` and count `em.last_status = 'failed' or (a.status in ('approved','rejected') and em.last_status is null)`; extend the test to assert the left join and the null clause, not just the lateral.

#### I-2. `applicationsRejected` silently drops every auto-rejected application

`server/app/facemarket_admin.py:79–81`

```sql
(select count(*) from fm_model_applications
   where status = 'rejected' and reviewed_at >= %(from_ts)s) as applications_rejected
```

The 3-strike identity-mismatch auto-reject (`server/app/facemarket_enrollment.py:940–944`) sets `status = 'rejected'`, `reject_reason = '정보 불일치'`, `terminated_at = now()` — and **never sets `reviewed_at`** (there is no admin reviewer). Those rows are counted in `applicationsSubmitted` (created_at based, `:77`) and then vanish from both decision buckets. The funnel doesn't add up and the operator has no way to see why: 제출 12 / 승인 7 / 거절 3 leaves two applications unexplained, and the missing two are precisely the ones that failed ID verification — the population an operator most wants to know about.

This is the "a status the schema allows but the query never counts" case, one level down: the status *is* counted, the timestamp column isn't populated on that path.

**Fix:** `coalesce(reviewed_at, terminated_at) >= %(from_ts)s`, or split into 관리자 거절 / 자동 거절 and show both. If counting only admin decisions is deliberate, the Stat label at `AdminDashboard.jsx:126` must say 관리자 승인/거절 and §5.2 must record the definition — right now nothing anywhere says these numbers exclude auto-rejections.

#### I-3. `reverification_required` models render a blank badge and cannot be filtered

`src/features/admin/AdminModels.jsx:19–26`, used at `:100` and `:234`

`fm_models_status_check` allows four values (`20260821010100_facemarket_biometric_runtime.sql:2–3`), the backend whitelist has all four (`facemarket_admin.py:220`), the dashboard grew the fourth bucket in `a8e7d6b3`, and `unsuspend` can now *restore* a model to it (`RESTORABLE_MODEL_STATUSES:225`). The models screen was never updated:

- `STATUS_LABEL[m.status]` → `undefined` → `<Badge>` renders an empty pill (a solid `bg-primary` blob with no text) in both the table row and the detail header.
- `STATUS_VARIANT[m.status]` → `undefined` → cva falls through to `default`, so the blank pill is styled as though the model were verified.
- `STATUS_FILTERS` has no entry, so there is no way to list them.

`ModelHub.jsx:44` already carries the label `'재검증 필요'`, so this is an oversight, not a decision. It is also the one status a console operator most needs to spot, since it means a verified model's biometrics need re-doing.

**Fix:** add `reverification_required: '재검증 필요'` to both maps, a `secondary`-variant filter chip, and a fallback (`STATUS_LABEL[s] || s`) so the next status added to the check constraint degrades to the raw string instead of a blank pill.

#### I-4. Five pre-existing admin routes now hard-fail if `admin_audit_log` isn't migrated first

`supabase/migrations/20260904100000_admin_audit_log.sql`; call sites `facemarket_applications.py:809, 856, 911`, `routes.py:697, 733`

This is correct design, not a bug: `write_audit` runs inside the action's transaction, so if the INSERT raises, `get_conn` unwinds and rolls the action back. But the consequence is that application approve / reject / resend-email and refund approve / reject — routes that work today — start returning 500 and refusing to do their job the moment the app image is deployed against a database where this migration hasn't landed.

Given this repo's history (`ci-migration-wrong-db`: CI's `SUPABASE_DB_URL` pointed at a different database from the app's, and prod went down), that is not a theoretical ordering hazard.

**Fix (release gate, not code):** confirm `admin_audit_log` exists in the *app's* database (`ftjxwxuactfjopbokbni`, per project notes — not whatever the CI secret points at) before the backend deploy, and verify the fallback: a failed audit insert rolls back the approval rather than approving without a record. Worth one line in the deploy runbook, because the failure is total and immediate.

#### I-5. The console's only human identifier is `auth.users.email`, which this repo's own schema says may be absent

`facemarket_admin.py:242 (models list), 291 (detail), 504/510 (staff), 603 (audit)`

Every account in the console is identified by `auth.users.email` and nothing else. Three pieces of evidence in this repo say that column is not reliably populated:

- `supabase/migrations/20260902120000_facemarket_model_applications.sql:23` — `contact_email text not null, -- 승인/거절 메일 수신처(auth 엔 이메일 없음, T2-A)`. The application form collects an email *because* auth doesn't have one.
- `AuthProvider.jsx:110–112` — sign-in is Google **or Kakao** OAuth only. Kakao's email scope is optional consent; without it Supabase creates the user with a null email.
- `src/features/shell/shell.jsx:185` — `session?.user?.email || meta.email || ''`. The existing app already treats it as possibly empty.

If it is null for a meaningful share of accounts, the blast radius is not cosmetic:

- **`/admin/staff` search becomes a dead end.** `SEARCH_USER_SQL:510` is an inner join on `auth.users` with `lower(u.email) = ?`. A Kakao account with no email can never be found, so it can never be promoted — the branch's success criterion #1 ("관리자가 콘솔에서 다른 계정을 관리자로 올리고 내릴 수 있다") silently fails, and the screen shows no matches with no explanation.
- **Model search by email never matches.** The operator knows the applicant's `fm_model_applications.contact_email`; the query compares against `auth.users.email`. These are different columns and may hold different values.
- **The models list 계정 column and the audit 사람 column** show `-`.

**Fix / verify:** run `select count(*), count(email) from auth.users;` against the app DB before ship. If the gap is nonzero, the design doc's own §12 fallback (mirror the email onto `profiles` via the signup trigger) is the right answer; a cheaper interim is to also match and display `fm_model_applications.contact_email` in the models list. This is distinct from — and additional to — the "can the role *read* `auth.users`" gate triaged below.

---

### Minor

#### M-1. "연결된 계정 없음 (플랫폼 온보딩)" is asserted on the wrong condition

`src/features/admin/AdminModels.jsx:102` — `{model.email || '연결된 계정 없음 (플랫폼 온보딩)'}`

The test is on `email`, the claim is about `user_id`. A model whose account exists but has no email (I-5) is reported to the operator as having no linked account at all — a confident false statement about the data, on the screen whose purpose is to tell the truth about the data. The backend can't help: `_model_row` (`facemarket_admin.py:256–265`) doesn't return `userId`, so the frontend has no way to distinguish the two cases.

**Fix:** emit `userId` from `_model_row` and branch on it: `model.userId ? (model.email || '이메일 없음') : '연결된 계정 없음 (플랫폼 온보딩)'`.

#### M-2. Two queue cards navigate somewhere that doesn't answer the number

`src/features/admin/AdminDashboard.jsx:97–98`

- `결정 메일 미발송 → /applications?status=approved`. The count spans approved *and* rejected applications; the destination shows only approved. Click a "3" and see two.
- `환불 요청 대기 → /applications`. There is no refunds screen (design §5.4 named `/refunds`; it was never built), so this lands on the application review queue with no filter — a completely unrelated list, with no indication that's what happened.

**Fix:** point 메일 미발송 at a filter that matches its rule, and make the refunds card non-navigable (render the `<Card>` without the `<Link>`) until a refunds screen exists.

#### M-3. `period` is emitted and consumed by nobody

`server/app/facemarket_admin.py:134, 152`; `fba6fa74` added `to` specifically so a client seeing a 30-second-cached response could learn the true end boundary. Grep of `src/` finds zero readers. The reasoning in the commit is right; the screen simply never displays the window. Either render it (`"9/4 00:00 – 18:35 기준"` under the 기간 toggle, which is genuinely useful when the numbers are cached) or drop the field.

#### M-4. The model list caps at 50 with no pagination and no truncation signal

`facemarket_admin.py:235–253` (`LIST_MODELS_SQL`, no cursor), `src/lib/api/facemarket.js:196` (`limit = 50`)

Design §6 specified `?…&cursor=`; it wasn't built, which is a reasonable YAGNI call at current scale. What isn't reasonable is that the screen gives no signal: with 51 models the operator sees 50 and has no way to know. **Fix:** cheapest is to request `limit: 51`, render 50, and show "50개 이상 — 검색으로 좁혀 주세요" when 51 come back.

#### M-5. Two columns the design doc specified are missing

Design §6 lists 생성일 for the model table (`createdAt` is emitted at `_model_row:264` and never rendered); design §7 lists 마지막 조치 시각 for the admin list (never queried — `LIST_ADMINS_SQL:503` selects four columns). Neither is load-bearing; note them so the next maintainer doesn't read the doc as describing the code.

#### M-6. The "only `admin_guard` may call `repo.is_admin`" rule is enforced against a hardcoded file list

`server/tests/test_admin_guard_adoption.py:18–27`

The test names four files. A *new* module that calls `repo.is_admin` directly — the exact regression the module docstring says it exists to prevent ("새 라우트를 추가할 때 가드를 빼먹어도 아무도 몰랐다") — passes. **Fix:** glob `APP.glob("*.py")`, exclude `admin_guard.py` and `repo.py`. I verified the current tree is clean (only `admin_guard.is_admin_user` at `facemarket_cutover.py:372` and the definition at `repo.py:2814`), so this is prevention, not repair.

#### M-7. `AdminApplications` reads `?status=` once, unvalidated

`src/features/admin/AdminApplications.jsx:173–174`

`useState(searchParams.get('status') || 'under_review')` — initial state only, so the URL and the visible filter can diverge after any chip click (the address bar keeps saying `?status=approved` while the list shows 거절). And an unknown value is passed straight to the server, which 400s (`facemarket_applications.py:750–753`) into a generic "문제가 발생했어요" card while every chip renders unselected. **Fix:** validate against `STATUS_FILTERS` on read, and either sync the param via `setSearchParams` or drop it from the URL after consuming it.

#### M-8. `set_role` writes an audit row for a no-op

`server/app/facemarket_admin.py:547–596` — granting `admin` to an account that is already `admin` passes every guard, runs the UPDATE, and records `staff.role.grant` with `before == after == {"role":"admin"}`. Not reachable from the console (the promote button only renders for `m.role !== 'admin'`, `AdminStaff.jsx:175`), but the API allows it and the ledger then contains an entry describing a transition that didn't occur — which is the one property the ledger is supposed to guarantee. **Fix:** `if previous == role: return {...}` before the UPDATE, or record it as a distinct no-op action.

#### M-9. `package.json`'s `pnpm.onlyBuiltDependencies` carries no explanation at the point of use

`package.json:36–38` vs `pnpm-workspace.yaml:1–12`

The workspace file explains beautifully why it exists and why the package.json field is dead for pnpm 11.9.0 — but that explanation lives in the *other* file. Keeping both is correct (pnpm 10 reads the package.json field; pnpm 11 reads `allowBuilds`), and a maintainer who opens only `package.json` will delete the field as dead. One comment line, or a `"//pnpm"` note, closes it.

*(Unverifiable here, flagged for the first deploy rather than as a finding: adding a tracked `pnpm-workspace.yaml` makes this a pnpm workspace root. The lockfile still has exactly one importer (`.`), and local pnpm is 11.9.0, so installs are fine — but Vercel's install step has not been exercised on this branch. Watch the first Vercel build.)*

---

## Deferred-item triage

### 1. Extended audit-ordering test — **closed** ✅

`server/tests/test_admin_guard_adoption.py::test_audit_write_happens_before_commit` now covers seven routes, in two groups:

- **Four with inline `write_audit` + explicit commit** — `admin_approve_application`, `admin_reject_application` (`facemarket_applications.py`), `approve_refund`, `reject_refund` (`routes.py`) — compared by text position within the route body.
- **Three that delegate** — `admin_set_role`, `admin_suspend_model`, `admin_unsuspend_model` — asserted by checking the helper call precedes `conn.commit()`, with a correct justification (`await` is sequential, so wherever `write_audit` sits inside the helper, it completes before the next statement).

The two-group split with a written rationale for why the second group needs a different assertion is better than what was asked for.

### 2. Comment on the implicitly-committing route — **closed** ✅

`server/app/facemarket_applications.py:918`:
> `# 명시적 commit 없음 — 다른 관리자 라우트 넷과 달리 여기선 get_conn 스코프가 정상 종료 시 커밋한다.`

I verified the claim rather than taking it: `get_conn` (`server/app/db.py:41–56`) yields from `pool.connection()`, and `psycopg_pool`'s connection context manager commits on clean exit and rolls back on exception. So the audit row does commit, and the `_err(...)` raises that this diff moved *inside* the block (`:894–897`) correctly roll back instead of leaking a half-written transaction. The test docstring at `test_admin_guard_adoption.py:60–65` names this route as the deliberate exclusion. Genuinely closed.

### 3. Can the app's DB role read `auth.users`? — **still unverified; here is the blast radius**

**Endpoints that fail (4 of 8 new):**

| Endpoint | Join | Failure |
|---|---|---|
| `GET /admin/models` | `left join auth.users` (`:242`) | total — no list at all |
| `GET /admin/models/{id}` | `left join auth.users` (`:291`) | total — no detail |
| `GET /admin/staff` | `left join` + inner `join` (`:504`, `:510`) | total — both the admin list and search |
| `GET /admin/audit` | `left join auth.users` (`:603`) | total — no ledger view |

**Unaffected:** `/admin/overview`, suspend, unsuspend, `POST /staff/{id}/role` (the write path touches only `profiles`), and every pre-existing application/refund route. So the dashboard and the application review screen keep working — which makes the failure look partial and puzzling rather than obviously systemic.

**Is it a clean error or a 500?** A 500, but a *clean, loud* one. psycopg raises `InsufficientPrivilege` (SQLSTATE 42501); nothing catches it; `main.py:285–302`'s `unhandled_exception_envelope` middleware turns it into `500 {"error":{"code":"internal_error","message":"서버 오류가 발생했어요…"}}` with a full traceback logged at ERROR, and `http_error_log` (`:305–334`) logs `status=500 method=GET path=/v1/facemarket/admin/models`. Per this project's CloudWatch→Slack wiring, a 42501 would page. So: **not silent, not a hang, not a crash — a generic 500 in the UI and an unmistakable log line.**

**What the operator sees:** three of four screens degrade gracefully rather than freezing — `AdminModels` shows its `listError` card with 다시 시도 (`AdminModels.jsx:212–217`), `AdminStaff` shows `dataError` and `auditError` separately (`:143`, `:186`) while keeping the search card usable. That resilience is a direct product of the round-2 fixes, and it is what makes this failure survivable. The message is generic, so the cause is not diagnosable from the UI — only from the log.

**Assessment:** likely fine. Supabase's `DATABASE_URL` connects as the `postgres` role, which owns the database and reads the `auth` schema; `db.py`'s own docstring confirms the app connects service-role and bypasses RLS. But "likely" is not "verified", and the check costs one query. Pair it with I-5 — run both against the app DB in one go:

```sql
select count(*) as total, count(email) as with_email from auth.users;
```

If it errors → §12 item 1 is confirmed and the `profiles` email-mirror fallback is required. If it succeeds but `with_email < total` → I-5 is confirmed. Either outcome points at the same fix (mirror the email onto `profiles`), which is the argument for doing it before ship rather than after.

### Also still unverified from design §12

- **Item 2 — do `fm_models` rows with `user_id IS NULL` exist?** The SQL is already correct (`left join`, asserted by `test_admin_models.py::test_list_joins_auth_users_for_email`); only the display copy is at issue (M-1).
- **Item 3 — how many prod admins are there?** If it is one, the last-admin guard (`:578–581`) fires on the very first demotion attempt. Working as designed, but create the second admin by hand before anyone tries.

---

## What is well built

Specific things the next maintainer should not "simplify":

- **`server/app/admin_guard.py:32–52` — `write_audit` deliberately does not commit.** The docstring says why. This one decision is what makes the ledger trustworthy: I traced it through all seven write paths and in every one, a failed audit insert unwinds the action instead of leaving an unlogged change. Adding a `conn.commit()` here would quietly break that for all of them at once.

- **`facemarket_admin.py:396–413` — the double-suspend pre-check.** Rejecting an already-suspended model at 409 *before* the UPDATE is not defensive noise; it protects the restore chain. Without it, a second suspend overwrites `before.status` with `"suspended"`, and the verified badge is unrecoverable forever. `test_admin_model_suspend.py::test_restore_chain_survives_suspend_unsuspend_suspend_unsuspend_cycle` pins the whole four-step cycle, and the test's comment about `write_audit` consuming a slot in the fake row queue will save the next person twenty minutes.

- **`facemarket_admin.py:405–411` and `:449–458` — compare-and-set on both transitions.** `where id = %s and status = %s returning 1`, then treat 0 rows as a conflict. I worked the interleaving: under READ COMMITTED two concurrent suspends both read `verified`, the second blocks on the row lock, re-evaluates after the first commits, finds `suspended ≠ verified`, gets 0 rows, and 409s — with no audit row written (asserted at `test_suspend_rejects_when_status_changes_between_read_and_write`). Correct, and the comment explains the *consequence* of getting it wrong (a false `before` that a later unsuspend would restore) rather than just the mechanism.

- **`facemarket_admin.py:560–585` — one ordered lock instead of two.** `select … where role = 'admin' or user_id = %s order by user_id for update` collapses what was a lock-ordering cycle (A demotes B while B demotes A) into a queue. Postgres puts `LockRows` above `Sort`, so rows really are locked in `user_id` order regardless of the access path. Counting admins from the already-locked result set instead of issuing a second `count(*)` — with the comment saying exactly why a second query would reintroduce the hazard — is the part most likely to be "cleaned up" by someone who doesn't see it. `test_role_change_locks_admins_and_target_in_one_ordered_pass` asserts *one* select, which is the right invariant to pin. I confirmed no table FKs to `public.profiles`, so the `FOR UPDATE` can't collide with FK `FOR KEY SHARE` locks either.

- **`facemarket_admin.py:228–233` — the comment on why `LIST_MODELS_SQL` is not sliced for reuse.** `split("where")[0]` would cut inside the `fm_licenses` subquery, not at the top-level `where`. Duplicating ~10 lines of SQL to avoid a silently-wrong string operation is the right trade, and writing down the reason is what stops someone from "de-duplicating" it next quarter.

- **The migration corrected the design doc.** §4.3's SQL says `actor_user_id uuid not null … on delete set null`, which is self-contradictory — the FK action would violate the constraint on the first admin deletion. `20260904100000_admin_audit_log.sql:12` drops the `not null` and keeps the intent, and `test_admin_audit_migration.py::test_actor_survives_account_deletion` pins the FK clause. Implementations that notice their spec is wrong and fix it are rarer than they should be.

- **`tests/frontend/admin-style-isolation.test.mjs:47–105` — a CSS parser, not a grep.** It walks `admin.css` tracking brace depth and validates every top-level statement, so a bare `.foo { }` or an `@import` stripped of its `layer(...)` fails the build. Commit `180ffa67` is the one to read: the earlier version had an `@import` branch that could never execute (imports end in `;`, never `{`), so the check was dead code that passed. Catching your own dead assertion and saying so in the comment is the behavior to keep.

- **`tests/frontend/admin-shell.test.mjs:20–65` — tag pairing instead of `split('</Route>')[1]`.** The old form indexed into the gap *between* two closing tags and would have passed even with an admin screen moved outside `RequireAuth`. The replacement counts nesting depth and skips self-closing routes. The comment explains the old bug concretely enough that nobody will regress it.

- **`server/tests/test_admin_guard_adoption.py` as a category** — source-contract tests that assert *wiring* (every admin route is gated, every write is audited, audit precedes commit). Unit tests cannot see a missing decorator; these can. See M-6 for the one place to make it durable.

- **The round-2 error-state work across all three new screens.** `AdminStaff.jsx:76–89` distinguishes `null` (never loaded) from `[]` (loaded, empty) so the audit card never says "기록 없음" while still loading — and the comment records that this defect only became *visible* after the whole-screen gating was removed. `AdminModels.jsx:70–86` keeps the panel frame on failure with a retry. `AdminDashboard.jsx:52–58` explains React's functional-update bail-out and why 다시 시도 needs its own `retryKey`. This is the class of bug that ships silently; three screens now fail the same, legible way. It is also what keeps the `auth.users` failure mode survivable rather than a frozen console.

- **`facemarket_admin.py:40–47` — `_period_start`.** KST midnight computed in Python and converted to UTC for the `>=` comparisons, while the series groups with `at time zone 'Asia/Seoul'` in SQL. Both halves agree on the boundary, and the docstring says why UTC would be wrong ("오전 9시 전에 만든 지원서가 어제로 잡혀"). I verified the enrollment and model distribution buckets partition their full status domains with no gap: `not in ('passed','failed','cancelled','expired')` catches every current *and future* in-flight status, and the four model buckets are exactly `fm_models_status_check`. `payment_id not like 'sim:%'` and `provider <> 'test'` both match the design's exclusion rules; the `%%` escaping is correct for the params-bearing statements and absent where no params are passed.
