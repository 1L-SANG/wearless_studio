"""One job, three image-producing provider calls, counted where a restart cannot forget.

The worker already had a budget — `calls_spent` against `mannequin_max_attempts` — but it
lived in a local variable inside `_run_candidate`, so it was per candidate and per process.
Two candidates at MANNEQUIN_MAX_ATTEMPTS=5 is 10 calls, and job da98aa2a spent exactly
that: A took generate/untuck/bust/generate/untuck, B took generate/generate/untuck/bust/
generate. A worker restart would have started a third set from zero.

This module holds the decision only. It never touches the database and never calls a
provider: it maps a request onto a slot and says yes or no, so the rule can be tested
without either. `repo.reserve_image_call` supplies the atomicity, and the job row supplies
the memory.

Slots are intents, not call-site names:

    BASE                 the first image of the job
    TARGETED_CORRECTION  every bounded edit of an existing image — untuck, bust, axis —
                         sharing ONE slot between them, not one each
    FULL_REGENERATION    starting a fresh candidate over, which is what the base loop's
                         second and later `mannequin_generate` calls actually are

A generation request therefore takes BASE while it is free and FULL_REGENERATION
afterwards; once both are gone no further image is generated for that job, whichever
candidate asks.

When a slot is spent
--------------------
On the **successful persistent reservation**, which happens immediately before the
provider is invoked — not on the provider request itself. The distinction is visible: a
worker killed after the compare-and-swap commits but before `generate_content_image`
runs has spent the slot and nothing gives it back, because no release path exists.

That is deliberate. A released slot would mean the ledger reads two while the provider
has been asked three times, and the provider may bill for a request whose response never
reached us. Erring toward over-counting costs at most one unused generation; erring the
other way costs real money and reopens the overrun this module exists to close.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SCHEMA = "image_budget_v1"

#: the key inside `jobs.metadata`. Chosen over a new table because the recovery path
#: already keeps a durable per-job counter here (`leaseRecoveries`), so this storage is
#: known to survive requeue.
METADATA_KEY = "imageBudget"

KIND_BASE = "BASE"
KIND_TARGETED_CORRECTION = "TARGETED_CORRECTION"
KIND_FULL_REGENERATION = "FULL_REGENERATION"

#: what a caller asks for. A generation does not name its slot — which one it lands on
#: depends on whether this job has generated anything yet.
REQUEST_GENERATION = "GENERATION"
REQUEST_TARGETED_CORRECTION = "TARGETED_CORRECTION"

SLOT_LIMITS: Mapping[str, int] = {
    KIND_BASE: 1,
    KIND_TARGETED_CORRECTION: 1,
    KIND_FULL_REGENERATION: 1,
}
MAX_TOTAL = 3

#: a generation takes the base slot first and the regeneration slot after it
GENERATION_SLOT_ORDER = (KIND_BASE, KIND_FULL_REGENERATION)

REASON_BUDGET_EXHAUSTED = "IMAGE_PROVIDER_BUDGET_EXHAUSTED"
REASON_LEASE_NOT_OWNED = "IMAGE_PROVIDER_BUDGET_LEASE_NOT_OWNED"
REASON_CONTENTION = "IMAGE_PROVIDER_BUDGET_CONTENTION"

EXHAUSTED_SLOT = "slot"
EXHAUSTED_TOTAL = "total"


def empty_budget() -> dict:
    return {"schema": SCHEMA, "seq": 0, "total": 0,
            "used": {k: 0 for k in SLOT_LIMITS},
            "maxTotal": MAX_TOTAL, "slotLimits": dict(SLOT_LIMITS),
            "reservations": {}}


def normalise(budget: Any) -> dict:
    """Whatever is on the row, read as a budget. A job predating this patch has none."""
    if not isinstance(budget, Mapping):
        return empty_budget()
    base = empty_budget()
    used = budget.get("used")
    if isinstance(used, Mapping):
        base["used"] = {k: int(used.get(k, 0) or 0) for k in SLOT_LIMITS}
    base["seq"] = int(budget.get("seq", 0) or 0)
    base["total"] = int(budget.get("total", sum(base["used"].values())) or 0)
    res = budget.get("reservations")
    base["reservations"] = dict(res) if isinstance(res, Mapping) else {}
    return base


def remaining(budget: Mapping) -> int:
    b = normalise(budget)
    return max(0, MAX_TOTAL - b["total"])


def generation_available(budget: Any) -> bool:
    """Could another image still be GENERATED for this job? Read-only — reserves nothing.

    The worker decides "retry or salvage the best rejected candidate" before it asks for a
    slot, and that decision used to consult `mannequin_max_attempts` alone. With the budget
    at three and MANNEQUIN_MAX_ATTEMPTS at five the two disagreed: job 75c375da believed a
    third generation was available, skipped the salvage branch, and only then had the
    reservation denied — so the loop broke without ever taking the salvage path.

    Only the two generation slots count. A free TARGETED_CORRECTION slot does not make a
    generation possible; borrowing it would break `FULL_REGENERATION <= 1`.
    """
    b = normalise(budget)
    if b["total"] >= MAX_TOTAL:
        return False
    return any(b["used"][slot] < SLOT_LIMITS[slot] for slot in GENERATION_SLOT_ORDER)


@dataclass(frozen=True)
class BudgetDecision:
    """What the caller gets. Never an exception — an exhausted budget is not a failure."""
    allowed: bool
    kind: str | None = None
    reason: str | None = None
    exhausted: str | None = None
    idempotency_key: str | None = None
    budget_before: dict = field(default_factory=dict)
    budget_after: dict = field(default_factory=dict)

    def as_event(self) -> dict:
        """The shape that goes into a job event. No bytes, no prompt, no token."""
        return {"allowed": self.allowed, "budgetKind": self.kind, "reason": self.reason,
                "exhausted": self.exhausted, "idempotencyKey": self.idempotency_key,
                "budgetBefore": {"total": self.budget_before.get("total"),
                                 "used": self.budget_before.get("used")},
                "budgetAfter": {"total": self.budget_after.get("total"),
                                "used": self.budget_after.get("used")}}


def logical_call_key(*, candidate: str | None, attempt: int | None, request: str,
                     operation: str) -> str:
    """Identity of a LOGICAL call — which loop position asked, in the worker's own terms.

    Deliberately NOT used to grant a free replay. A reservation is only ever taken in the
    instruction before the provider request, so a key showing up twice does not mean "the
    same call retried before spending anything"; it means the first request already went
    out and a restarted worker is about to send a second one. Waving that through on the
    strength of a matching key would put a fourth request on the wire while the ledger
    still read three — the exact overrun this patch exists to stop.

    So the key is recorded, not honoured: it says which loop position spent which slot,
    and a repeat spends another slot or is denied. `test_worker_restart_reloads_the_spent_
    budget` pins that behaviour.
    """
    return f"{candidate or '-'}:{attempt if attempt is not None else '-'}:{request}:{operation}"


def plan(budget: Any, *, request: str, operation: str, candidate=None,
         attempt=None, key: str | None = None) -> BudgetDecision:
    """Resolve a request against the current budget. Pure — the caller persists it.

    Returns the budget as it would be AFTER the reservation, which is what the atomic
    update writes; if the write loses its compare-and-swap the caller plans again.
    """
    before = normalise(budget)
    k = key or logical_call_key(candidate=candidate, attempt=attempt,
                                request=request, operation=operation)

    if before["total"] >= MAX_TOTAL:
        return BudgetDecision(False, reason=REASON_BUDGET_EXHAUSTED,
                              exhausted=EXHAUSTED_TOTAL, idempotency_key=k,
                              budget_before=before, budget_after=before)

    order = (GENERATION_SLOT_ORDER if request == REQUEST_GENERATION
             else (KIND_TARGETED_CORRECTION,))
    slot = next((s for s in order if before["used"][s] < SLOT_LIMITS[s]), None)
    if slot is None:
        return BudgetDecision(False, reason=REASON_BUDGET_EXHAUSTED,
                              exhausted=EXHAUSTED_SLOT, idempotency_key=k,
                              budget_before=before, budget_after=before)

    after = normalise(before)
    after["used"][slot] += 1
    after["total"] += 1
    after["seq"] = before["seq"] + 1
    after["reservations"] = {
        # keyed by sequence so a repeat of the same logical call appends a second entry
        # instead of silently replacing the first — the ledger has to show both requests
        **before["reservations"],
        f"{k}#{after['seq']}": {
            "kind": slot, "request": request, "operation": operation,
            "candidate": candidate, "attempt": attempt, "seq": after["seq"],
            "logicalCallKey": k},
    }
    return BudgetDecision(True, kind=slot, idempotency_key=k,
                          budget_before=before, budget_after=after)
