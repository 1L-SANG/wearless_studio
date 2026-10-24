# PR 588 review fixes

> **For agentic workers:** Use test-driven development for each owned task and review the combined diff before pushing. The user authorized implementation and PR updates, not merging.

**Goal:** Fix the five confirmed review findings without enabling either feature.

**Architecture:** Preserve the publication and mall API contracts. Serialize account quota and subscription decisions in PostgreSQL, retain deletion cleanup until outstanding uploads are safe to reclaim, and reject stale credential changes. Scope UI save completion to the form instance that initiated it.

**Tech Stack:** FastAPI, psycopg, PostgreSQL, React, Node test runner.

**Spec:** `documents/common_data_contract.md`, publication and mall connection sections; the five findings reported in this chat.

## Constraints and review focus

- Do not enable feature flags, call paid APIs, mutate production, or launch browsers.
- Existing committed migrations are append-only. Use a new migration only if required.
- Late upload after deletion must remain eligible for cleanup, including account closure.
- Subscription renewal must exclude an expiry deletion that has not already claimed the account.
- User-wide publication quota must hold across concurrent projects.
- Revocation or replacement must invalidate an older in-flight key rotation.
- A save response from an unmounted form must preserve the currently edited form, including switching back to the same section.

## Tasks

- [x] Publication worker: add failing regressions for late uploads, renewal races, and concurrent quota checks; implement minimal backend fixes; verify targeted tests and real PostgreSQL concurrency.
- [x] Mall worker: add failing rotation and revocation regressions; bind rotation to the original connection and validate atomically; verify targeted tests.
- [x] Root: reproduce the settings form race in the existing React harness; prevent stale form completion; verify current and switched-back forms.
- [x] Root: review combined changes and run relevant checks, including independent verification of overlapping cleanup retries.

After committing, push to the existing PR branch and inspect its full CI. Do not merge.
