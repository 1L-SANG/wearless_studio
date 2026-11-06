# Toss Point Top-up Compliance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Wearless point top-ups enforce Toss' one-year, subscription-bound, original-payment refund rules across website, API, and evidence.

**Architecture:** Keep subscription billing and general top-up payments separate. Add server-side eligibility and lifecycle rules around the existing top-up bucket model, then make refund approval perform an idempotent Toss cancellation before finalizing the ledger. Publish matching legal and pricing copy in both repositories.

**Tech Stack:** Vite/React, FastAPI, psycopg/Postgres, Toss Payments REST API, Next.js landing page

**Spec:** `docs/superpowers/specs/2026-11-06-toss-point-topup-compliance-design.md`

## Global Constraints

- Existing migrations are append-only; avoid a migration unless the current schema cannot express the rule.
- Subscription-credit rollover must not change.
- Legacy top-ups with `period_end is null` stay grandfathered.
- Every money mutation is idempotent and fail-closed.
- Tests are written and observed failing before production changes.

## Review Focus

- Free and expired subscribers cannot create a top-up order.
- A canceled subscription remains usable until its paid-through date, but top-ups expire at final end.
- Legacy top-ups survive subscription end while post-policy top-ups expire.
- A Toss cancellation failure cannot mark the refund complete or restore spendable credits.
- Repeated refund approval cannot issue duplicate cancellations.

---

### Task 1: Publish consistent legal and pricing copy

**Files:** landing legal/FAQ/pricing files and seller pricing UI/tests in both repositories.

- [ ] Add failing tests for the required one-year sentence, subscription-end expiry, original-method refund, use purpose, and removal of “top-ups never expire.”
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Update legal, FAQ, pricing, and use-path copy.
- [ ] Run focused tests and builds.
- [ ] Commit separately in each repository.

### Task 2: Enforce top-up eligibility and amount limit

**Files:** `server/app/payments.py`, server payment tests, pricing UI/tests.

- [ ] Add failing tests for paid-through subscription eligibility and the KRW 100,000 server limit.
- [ ] Run tests and confirm failures.
- [ ] Add the minimal checkout guards and actionable error codes.
- [ ] Disable top-up buttons for ineligible accounts while keeping the review account path testable.
- [ ] Run focused tests and commit.

### Task 3: Expire post-policy top-ups when subscription access ends

**Files:** `server/app/repo.py`, subscription worker/tests, subscription UI summary/tests.

- [ ] Replace the existing preservation expectation with failing tests that distinguish legacy and expiring top-ups.
- [ ] Run tests and confirm failures.
- [ ] Expire active post-policy top-up buckets at final subscription end and include them in the expiry summary.
- [ ] Run focused tests and commit.

### Task 4: Refund through the original Toss payment

**Files:** payment/refund service module, `server/app/routes.py`, `server/app/repo.py`, refund tests.

- [ ] Add failing tests for Toss full cancellation, idempotency, gateway failure, and retry.
- [ ] Run tests and confirm failures.
- [ ] Call `POST /v1/payments/{paymentKey}/cancel` with `cancelReason` and refund-request idempotency key before finalizing the ledger.
- [ ] Leave failed cancellations pending and retryable; finalize only after Toss reports a completed cancellation.
- [ ] Run focused tests and commit.

### Task 5: Full verification and release preparation

- [ ] Run the relevant full frontend and server suites plus both production builds.
- [ ] Run whole-branch review and fix Critical/Important findings with RED-GREEN tests.
- [ ] Push both branches, open PRs, attach them to the task, merge after green checks, and verify deployments.
- [ ] Prepare the point-charging PPT checklist and the final Toss reply; do not send or purchase insurance without the user’s action-time confirmation.
