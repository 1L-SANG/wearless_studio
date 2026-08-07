"""Three image-producing provider calls per job — counted where a restart cannot forget.

The failure this closes is measured, not hypothetical: `generation_runs` shows job
da98aa2a spending 10 provider calls (A: generate/untuck/bust/generate/untuck, B:
generate/generate/untuck/bust/generate) because `calls_spent` was a local variable inside
`_run_candidate`. Candidate B started from zero, and a restarted worker would have too.

The store below implements the same predicate as the SQL — a conditional write guarded by
lease ownership and the sequence number read a moment earlier — so the rule can be driven
without a database. It is NOT a Postgres emulator: `test_reservation_sql_is_a_single_
guarded_update` checks that the real statement carries those guards.
"""
import asyncio

import pytest

from app.services import image_budget as ib
from app.workers import mannequin_job


class FakeJobRow:
    """One job row with a metadata blob, updated only through a compare-and-swap.

    Mirrors `repo.reserve_image_call`: read (metadata, locked_by) → plan → write only if
    the lease still matches and `seq` has not moved.
    """

    def __init__(self, *, job_id="job-1", lease_token="worker-a:lease-1"):
        self.job_id = job_id
        self.lease_token = lease_token
        self.metadata = {}
        self.writes = 0
        self.cas_conflicts = 0
        self.before_write = None          # test hook to interleave a racer

    async def reserve(self, *, job_id, lease_token, request, operation,
                      candidate=None, attempt=None):
        for _ in range(5):
            if job_id != self.job_id or lease_token != self.lease_token:
                return ib.BudgetDecision(False, reason=ib.REASON_LEASE_NOT_OWNED)
            current = self.metadata.get(ib.METADATA_KEY)
            decision = ib.plan(current, request=request, operation=operation,
                               candidate=candidate, attempt=attempt)
            if not decision.allowed:
                return decision
            if self.before_write is not None:
                hook, self.before_write = self.before_write, None
                await hook()
            live = ib.normalise(self.metadata.get(ib.METADATA_KEY))
            if live["seq"] != decision.budget_before["seq"]:
                self.cas_conflicts += 1
                continue                  # someone else took a slot — plan again
            self.metadata[ib.METADATA_KEY] = decision.budget_after
            self.writes += 1
            return decision
        return ib.BudgetDecision(False, reason=ib.REASON_CONTENTION)

    def gate(self, *, lease_token=None, emit=None):
        return mannequin_job._ImageBudgetGate(
            reserve_fn=self.reserve, job_id=self.job_id,
            lease_token=lease_token or self.lease_token, emit=emit)


def reserve(gate, request, operation, *, candidate="A", attempt=1):
    return asyncio.run(gate.reserve(request=request, operation=operation,
                                    candidate=candidate, attempt=attempt))


GEN = ib.REQUEST_GENERATION
FIX = ib.REQUEST_TARGETED_CORRECTION


# ---------- A/B/C/D: the slot invariants ----------

def test_base_slot_is_taken_once_and_only_by_the_first_generation():
    row = FakeJobRow()
    gate = row.gate()
    first = reserve(gate, GEN, "generate", candidate="A", attempt=1)
    second = reserve(gate, GEN, "generate", candidate="A", attempt=2)
    assert first.allowed and first.kind == ib.KIND_BASE
    # the second generation is a regeneration by definition — it does not get another BASE
    assert second.allowed and second.kind == ib.KIND_FULL_REGENERATION
    assert row.metadata[ib.METADATA_KEY]["used"][ib.KIND_BASE] == 1


def test_untuck_and_bust_and_axis_share_one_correction_slot():
    """B + E — three edits, one slot. Giving each its own is how edits alone reach 3."""
    row = FakeJobRow()
    gate = row.gate()
    untuck = reserve(gate, FIX, "untuck_edit")
    bust = reserve(gate, FIX, "bust_edit")
    axis = reserve(gate, FIX, "axis_edit")
    assert untuck.allowed and untuck.kind == ib.KIND_TARGETED_CORRECTION
    assert not bust.allowed and bust.exhausted == ib.EXHAUSTED_SLOT
    assert not axis.allowed and axis.exhausted == ib.EXHAUSTED_SLOT
    assert row.metadata[ib.METADATA_KEY]["used"][ib.KIND_TARGETED_CORRECTION] == 1


def test_full_regeneration_slot_is_taken_once():
    row = FakeJobRow()
    gate = row.gate()
    reserve(gate, GEN, "generate", attempt=1)                    # BASE
    assert reserve(gate, GEN, "generate", attempt=2).kind == ib.KIND_FULL_REGENERATION
    third = reserve(gate, GEN, "generate", attempt=3)
    assert not third.allowed and third.exhausted == ib.EXHAUSTED_SLOT


def test_total_never_exceeds_three_whatever_the_mix():
    row = FakeJobRow()
    gate = row.gate()
    allowed = [reserve(gate, GEN, "generate", attempt=1),
               reserve(gate, FIX, "untuck_edit", attempt=1),
               reserve(gate, GEN, "generate", attempt=2)]
    assert [d.kind for d in allowed] == [
        ib.KIND_BASE, ib.KIND_TARGETED_CORRECTION, ib.KIND_FULL_REGENERATION]
    for op in ("bust_edit", "axis_edit"):
        assert not reserve(gate, FIX, op, attempt=3).allowed
    assert not reserve(gate, GEN, "generate", attempt=4).allowed
    assert row.metadata[ib.METADATA_KEY]["total"] == ib.MAX_TOTAL == 3


# ---------- F: candidates ----------

def test_candidate_b_cannot_open_a_second_base_slot():
    row = FakeJobRow()
    gate = row.gate()
    a = reserve(gate, GEN, "generate", candidate="A", attempt=1)
    b = reserve(gate, GEN, "generate", candidate="B", attempt=1)
    assert a.kind == ib.KIND_BASE
    # B is a different candidate, not a different job — it takes what is left
    assert b.kind == ib.KIND_FULL_REGENERATION
    assert row.metadata[ib.METADATA_KEY]["used"][ib.KIND_BASE] == 1
    assert not reserve(gate, GEN, "generate", candidate="B", attempt=2).allowed


# ---------- G/H: retry and restart ----------

def test_repeating_a_logical_call_spends_again_rather_than_replaying():
    """A matching key is NOT a free pass — the first request already went out.

    Reservations are taken in the instruction before the provider call, so the same
    (candidate, attempt, operation) appearing twice means two requests on the wire. The
    slot is shared, so the repeat is denied here rather than quietly authorised.
    """
    row = FakeJobRow()
    gate = row.gate()
    first = reserve(gate, FIX, "untuck_edit", candidate="A", attempt=1)
    again = reserve(gate, FIX, "untuck_edit", candidate="A", attempt=1)
    assert first.allowed and first.kind == ib.KIND_TARGETED_CORRECTION
    assert not again.allowed and again.exhausted == ib.EXHAUSTED_SLOT
    assert row.writes == 1
    assert row.metadata[ib.METADATA_KEY]["total"] == 1


def test_the_ledger_records_which_loop_position_spent_each_slot():
    row = FakeJobRow()
    gate = row.gate()
    reserve(gate, GEN, "generate", candidate="A", attempt=1)
    reserve(gate, GEN, "generate", candidate="B", attempt=1)
    entries = row.metadata[ib.METADATA_KEY]["reservations"]
    assert len(entries) == 2
    assert sorted(e["logicalCallKey"] for e in entries.values()) == [
        "A:1:GENERATION:generate", "B:1:GENERATION:generate"]


def test_worker_restart_reloads_the_spent_budget():
    """§19 — execution 1 spends two slots, the row remembers, execution 2 gets one."""
    row = FakeJobRow()
    first_gate = row.gate()
    assert reserve(first_gate, GEN, "generate", candidate="A", attempt=1).allowed
    assert reserve(first_gate, FIX, "untuck_edit", candidate="A", attempt=1).allowed
    assert row.metadata[ib.METADATA_KEY]["total"] == 2

    # the worker dies; recovery requeues the job and a new worker claims a new lease
    row.lease_token = "worker-b:lease-2"
    second_gate = row.gate()

    assert not reserve(second_gate, FIX, "bust_edit", candidate="A", attempt=1).allowed
    # execution 2 restarts candidate A at attempt 1. That is a NEW provider request — the
    # first one's image died with the worker — so it takes the remaining slot rather than
    # replaying the BASE reservation that already paid for a request.
    regen = reserve(second_gate, GEN, "generate", candidate="A", attempt=1)
    assert regen.allowed and regen.kind == ib.KIND_FULL_REGENERATION
    assert not reserve(second_gate, GEN, "generate", candidate="B", attempt=1).allowed
    assert row.metadata[ib.METADATA_KEY]["total"] == 3


def test_lease_recovery_does_not_clear_the_budget_key():
    """The recovery statement rewrites metadata; it must not drop what was spent."""
    row = FakeJobRow()
    reserve(row.gate(), GEN, "generate")
    spent = dict(row.metadata[ib.METADATA_KEY])
    # what recover_stale_leases does to metadata: jsonb_set of one unrelated key
    row.metadata["leaseRecoveries"] = 1
    assert row.metadata[ib.METADATA_KEY] == spent


# ---------- I: stale lease ----------

def test_stale_worker_cannot_reserve_after_losing_the_lease():
    row = FakeJobRow()
    stale = row.gate(lease_token="worker-a:lease-1")
    row.lease_token = "worker-b:lease-2"       # B reclaimed the job
    denied = reserve(stale, GEN, "generate")
    assert not denied.allowed
    assert denied.reason == ib.REASON_LEASE_NOT_OWNED
    assert row.writes == 0
    fresh = reserve(row.gate(), GEN, "generate")
    assert fresh.allowed and fresh.kind == ib.KIND_BASE


# ---------- J: the final slot under contention ----------

def test_two_reservations_racing_for_the_final_slot_leave_exactly_one_winner():
    row = FakeJobRow()
    gate = row.gate()
    reserve(gate, GEN, "generate", attempt=1)
    reserve(gate, GEN, "generate", attempt=2)
    assert ib.remaining(row.metadata[ib.METADATA_KEY]) == 1

    outcomes = []

    async def race():
        async def racer_b():
            outcomes.append(await gate.reserve(
                request=FIX, operation="bust_edit", candidate="B", attempt=1))
        # B slips in between A's plan and A's write — the interleaving the CAS exists for
        row.before_write = racer_b
        outcomes.append(await gate.reserve(
            request=FIX, operation="untuck_edit", candidate="A", attempt=1))

    asyncio.run(race())
    assert row.cas_conflicts == 1, "the losing writer must have seen a moved seq"
    assert sum(1 for d in outcomes if d.allowed) == 1
    assert row.metadata[ib.METADATA_KEY]["total"] == 3
    assert ib.remaining(row.metadata[ib.METADATA_KEY]) == 0


def test_reservation_sql_is_a_single_guarded_update():
    """The in-memory store proves the RULE; this proves the STATEMENT that enforces it.

    Atomicity here rests on one conditional UPDATE of one row: concurrent writers block,
    then re-evaluate the WHERE under READ COMMITTED, and the loser matches zero rows.
    """
    import inspect

    from app import repo

    src = inspect.getsource(repo.reserve_image_call)
    update = src[src.index("update jobs"):src.index("returning")]
    assert update.count("update jobs") == 1, "one statement, not a read-then-write pair"
    assert "and locked_by = %s" in update, "lease fencing must be in the WHERE"
    assert "->>'seq')::int, 0) = %s" in update, "compare-and-swap must be in the WHERE"


# ---------- gate behaviour ----------

def test_missing_gate_denies_rather_than_allowing():
    """A call site that forgets the gate must not become a free provider call."""
    decision = asyncio.run(mannequin_job._require_image_slot(
        None, request=GEN, operation="generate"))
    assert not decision.allowed
    assert decision.reason == ib.REASON_LEASE_NOT_OWNED


def test_storage_failure_denies_and_does_not_raise():
    async def boom(**_):
        raise RuntimeError("database is down")

    gate = mannequin_job._ImageBudgetGate(
        reserve_fn=boom, job_id="job-1", lease_token="t")
    decision = asyncio.run(gate.reserve(request=GEN, operation="generate"))
    assert not decision.allowed and decision.reason == ib.REASON_CONTENTION


def test_gate_emits_an_observation_without_leaking_the_lease_token():
    events = []

    async def emit(payload):
        events.append(payload)

    row = FakeJobRow()
    gate = row.gate(emit=emit)
    reserve(gate, GEN, "generate", candidate="A", attempt=1)
    assert len(events) == 1
    ev = events[0]
    assert ev["status"] == "image_budget" and ev["budgetKind"] == ib.KIND_BASE
    assert ev["budgetBefore"]["total"] == 0 and ev["budgetAfter"]["total"] == 1
    assert row.lease_token not in repr(ev)


def test_budget_survives_metadata_that_predates_this_patch():
    row = FakeJobRow()
    row.metadata = {"leaseRecoveries": 1}          # an in-flight job from before
    assert reserve(row.gate(), GEN, "generate").kind == ib.KIND_BASE
    assert row.metadata["leaseRecoveries"] == 1


# ---------- K/§23: the historical 10-call job, replayed ----------

HISTORICAL_DA98AA2A = [
    ("A", 1, GEN, "generate"), ("A", 1, FIX, "untuck_edit"), ("A", 1, FIX, "bust_edit"),
    ("A", 2, GEN, "generate"), ("A", 2, FIX, "untuck_edit"),
    ("B", 1, GEN, "generate"), ("B", 2, GEN, "generate"), ("B", 2, FIX, "untuck_edit"),
    ("B", 2, FIX, "bust_edit"), ("B", 3, GEN, "generate"),
]


def test_the_historical_ten_call_job_now_stops_at_three():
    """§23 — the exact call sequence generation_runs recorded for job da98aa2a."""
    row = FakeJobRow()
    gate = row.gate()
    provider_calls = []
    for candidate, attempt, request, operation in HISTORICAL_DA98AA2A:
        d = reserve(gate, request, operation, candidate=candidate, attempt=attempt)
        if d.allowed:
            provider_calls.append((candidate, attempt, operation, d.kind))

    assert len(HISTORICAL_DA98AA2A) == 10
    assert len(provider_calls) == 3
    assert provider_calls == [
        ("A", 1, "generate", ib.KIND_BASE),
        ("A", 1, "untuck_edit", ib.KIND_TARGETED_CORRECTION),
        ("A", 2, "generate", ib.KIND_FULL_REGENERATION)]
    assert row.metadata[ib.METADATA_KEY]["total"] == 3


@pytest.mark.parametrize("job,sequence", [
    ("da98aa2a", HISTORICAL_DA98AA2A),
    ("e80c999f", [("A", 1, GEN, "generate"), ("A", 1, FIX, "untuck_edit"),
                  ("A", 1, FIX, "bust_edit"), ("A", 2, GEN, "generate"),
                  ("A", 3, GEN, "generate"), ("B", 1, GEN, "generate"),
                  ("B", 2, GEN, "generate"), ("B", 3, GEN, "generate"),
                  ("B", 3, FIX, "untuck_edit"), ("B", 4, GEN, "generate")]),
])
def test_every_historical_ten_call_job_is_capped(job, sequence):
    row = FakeJobRow()
    gate = row.gate()
    granted = sum(1 for c, a, r, o in sequence
                  if reserve(gate, r, o, candidate=c, attempt=a).allowed)
    assert len(sequence) == 10, job
    assert granted == 3, job


# ---- when exactly a slot is spent -------------------------------------------------

def test_a_slot_is_spent_at_the_reservation_not_at_the_provider_call():
    """Killed after the CAS commits, before the provider runs: the slot stays spent.

    There is no release path, so the honest description of the consumption point is the
    successful persistent reservation — the instruction before the provider call, not the
    provider call. Over-counting by one costs an unused generation; under-counting lets a
    request the provider may already have billed fall outside the ledger.
    """
    row = FakeJobRow()
    granted = reserve(row.gate(), GEN, "generate", candidate="A", attempt=1)
    assert granted.allowed
    assert row.metadata[ib.METADATA_KEY]["total"] == 1     # persisted before any call

    # the provider is never invoked; a new worker picks the job up
    row.lease_token = "worker-b:lease-2"
    assert ib.remaining(row.metadata[ib.METADATA_KEY]) == 2
    assert row.metadata[ib.METADATA_KEY]["used"][ib.KIND_BASE] == 1


def test_nothing_in_the_module_can_give_a_slot_back():
    """A release helper would be the hole; assert none exists rather than trusting it."""
    exported = {n for n in dir(ib) if not n.startswith("_")}
    for forbidden in ("release", "refund", "rollback", "give_back", "restore"):
        assert not any(forbidden in n.lower() for n in exported), forbidden
    from app import repo
    repo_budget_fns = {n for n in dir(repo)
                       if not n.startswith("_")
                       and "image" in n.lower() and "budget" in n.lower()
                       or n in ("reserve_image_call", "read_image_budget")}
    assert repo_budget_fns == {"reserve_image_call", "read_image_budget"}, repo_budget_fns
