# PR 593 review fixes

> **For agentic workers:** Apply test-driven development to each task. The user authorized fixing and updating PR 593, not merging, deploying, or enabling features.

**Goal:** Fix the five confirmed Cafe24 review findings while preserving disabled feature defaults.

**Architecture:** Restore unresolved registration work before starting a new request, reuse the server's ready publication list, and derive connection display from current server data. Resolve accepted registration keys before new-request checks. Serialize install admission with disconnect and account closure.

**Tech Stack:** React, Node test runner, FastAPI, psycopg, PostgreSQL.

**Spec:** `documents/common_data_contract.md`, Cafe24 connection and registration sections; the five findings reported in this chat against `bf830da5`.

## Constraints and review focus

- Do not launch browsers, call Cafe24 or paid APIs, mutate production, merge, or enable flags.
- Existing migrations are append-only. Avoid schema changes unless a confirmed fix requires one.
- Unknown and duplicate-suspected work must remain recoverable after closing and reopening the modal.
- Loading recovery history must not silently fall back to a new product on failure.
- The real publication list has no `status` field and already contains only ready publications.
- Installation success must not conceal a later disconnected or reconnect-required server response.
- Replaying an accepted key must preserve its job even after quota exhaustion, publication deletion, or connection changes; changed input must still conflict.
- Install admission and disconnect must have one consistent account and mall lock order.

## Tasks

- [x] Frontend: write failing modal and connection regressions, implement recovery and display fixes, and run the related frontend tests.
- [x] Backend: write failing replay and install-race regressions, implement lookup ordering and transaction fences, and verify unit plus real PostgreSQL cases.
- [x] Integration: update the canonical contract where behavior changes, review the combined diff independently, and run relevant checks.

After local verification, commit and push the existing PR branch and inspect the full CI. Keep the PR open.
