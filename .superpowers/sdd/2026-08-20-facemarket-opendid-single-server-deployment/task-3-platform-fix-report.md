# Task 3 platform fix report

Implementation commit: `0c8de39b`

## Change

- `deploy/opendid/export-state.sh` no longer requires `systemctl` unconditionally.
- Linux/systemd hosts still refuse export when any configured OpenDID app service is active.
- Legacy hosts without `systemctl` fail closed with `lsof` checks for writer ports `8090 8091 8094 8100 9001`.
- Export refuses with an actionable error when neither `systemctl` nor `lsof` is available.
- Besu running checks, Docker volume checks, pg_dump, and source-file archiving were not changed.

## RED evidence

Before production edits, `deploy/opendid/test-export-state.sh` failed against the new macOS-like regression:

```text
REFUSING: systemctl not found
```

After making the harness continue on expected failures, the same RED run reported:

```text
FAIL legacy source export succeeds with writer ports closed
FAIL legacy source export succeeds with writer ports closed
PASS legacy source export refuses listening writer port
PASS legacy listening refusal creates no dump
FAIL legacy listening refusal names writer port
PASS export refuses when neither systemctl nor lsof exists
FAIL missing freeze tool error is actionable
```

## GREEN evidence

Fresh verification after the fix:

```text
deploy/opendid/test-export-state.sh: exit 0
bash -n deploy/opendid/export-state.sh deploy/opendid/test-export-state.sh: exit 0
git diff --check: exit 0
deploy/opendid/test-restore-state.sh: exit 0
deploy/opendid/test-restore-postgres-integration.sh: exit 0, SKIP set OPENDID_RUN_POSTGRES_INTEGRATION=1 to run OpenDID export/restore proof
```

Key passing regression lines:

```text
PASS legacy source export command succeeds with writer ports closed
PASS legacy source export creates dump with writer ports closed
PASS legacy source export refuses listening writer port
PASS legacy listening refusal creates no dump
PASS legacy listening refusal names writer port
PASS export refuses when neither systemctl nor lsof exists
PASS missing freeze tool error is actionable
```

## Self-review

- Scope stayed limited to `deploy/opendid/export-state.sh`, `deploy/opendid/test-export-state.sh`, and this required report.
- The systemd refusal behavior remains service-name based and is still covered by `PASS export refuses active systemd service`.
- The legacy fallback runs before `pg_dumpall`; the listening-port test confirms no dump is created on refusal.
- No dependency, runbook, Task5, or global plan changes.
