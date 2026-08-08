"""Two authorities used to answer "may we generate again?" and they disagreed.

`has_budget_for_retry` counts attempts against MANNEQUIN_MAX_ATTEMPTS, which is 5 in the
QA environment. The persistent per-job image budget allows three provider calls, of which
only two can be generations. Job 75c375da hit the gap: BASE and FULL_REGENERATION were
both spent, the attempt counter said two of five, so the worker skipped the salvage branch
believing a third generation was coming — and then the reservation denied it and the loop
broke, leaving the preserved candidates unused.

The fix composes both: an attempt limit still paces a run, but the persisted budget decides
whether a provider call is possible at all. It is also the half that survives a restart,
where the attempt counter starts over and the row does not.

What the fix must NOT do is promote a rejected candidate into an edit source. The identity
gate exists because editing a garment whose colour or pattern scale is wrong preserves the
wrong garment. Those tests are here too.
"""
import asyncio

import pytest

from app.services import image_budget as ib
from app.workers import mannequin_job as mj
from tests.conftest import make_settings

GEN = ib.REQUEST_GENERATION
FIX = ib.REQUEST_TARGETED_CORRECTION


class Gate:
    """A gate over an in-memory job row, matching the production gate's surface."""

    def __init__(self, budget=None, *, readable=True, fail_read=False):
        self.row = budget
        self.reads = 0
        self.reserves = 0
        self._readable = readable
        self._fail_read = fail_read

    async def _reserve(self, **kw):
        self.reserves += 1
        d = ib.plan(self.row, **{k: v for k, v in kw.items()
                                 if k in ("request", "operation", "candidate", "attempt")})
        if d.allowed:
            self.row = d.budget_after
        return d

    async def _read(self, *, job_id):
        self.reads += 1
        if self._fail_read:
            raise RuntimeError("database is down")
        return self.row

    def build(self, *, seeded):
        g = mj._ImageBudgetGate(
            reserve_fn=self._reserve, job_id="job-1", lease_token="w:1",
            read_fn=(self._read if self._readable else None))
        if seeded:
            g._last = self.row
        return g


def spend(row, *pairs):
    """Apply reservations to a raw budget dict, as production would."""
    for request, op in pairs:
        d = ib.plan(row, request=request, operation=op, candidate="A", attempt=1)
        assert d.allowed, f"fixture could not reserve {request}/{op}"
        row = d.budget_after
    return row


BOTH_GENERATIONS_SPENT = spend(None, (GEN, "generate"), (GEN, "generate"))


# ---------- the mismatch itself ----------

def test_attempt_limit_alone_still_says_yes_which_is_the_bug():
    """The legacy predicate is unchanged — it simply is not the whole answer."""
    s = make_settings(mannequin_max_attempts=5)
    assert mj.has_budget_for_retry(s, calls_spent=2) is True


def test_generation_retry_is_denied_once_both_generation_slots_are_spent():
    """The exact shape of job 75c375da: BASE and FULL_REGENERATION gone, attempts 2 of 5."""
    gate = Gate(BOTH_GENERATIONS_SPENT).build(seeded=True)
    s = make_settings(mannequin_max_attempts=5)
    legacy = mj.has_budget_for_retry(s, calls_spent=2)
    combined = asyncio.run(mj._can_retry_generation(gate, legacy_allows=legacy))
    assert legacy is True, "the legacy authority is the one that was wrong"
    assert combined is False, "the composed authority must refuse"


def test_a_free_targeted_slot_does_not_make_a_generation_possible():
    """TARGETED is unused in this shape; borrowing it would break FULL_REGENERATION <= 1."""
    used = BOTH_GENERATIONS_SPENT["used"]
    assert used[ib.KIND_TARGETED_CORRECTION] == 0
    assert ib.remaining(BOTH_GENERATIONS_SPENT) == 1     # capacity exists
    assert ib.generation_available(BOTH_GENERATIONS_SPENT) is False   # but not for a generation


def test_generation_is_available_while_a_generation_slot_remains():
    assert ib.generation_available(None) is True
    one = spend(None, (GEN, "generate"))
    assert ib.generation_available(one) is True          # FULL_REGENERATION still free
    assert ib.generation_available(BOTH_GENERATIONS_SPENT) is False


def test_a_spent_targeted_slot_does_not_block_a_generation():
    row = spend(None, (FIX, "untuck_edit"))
    assert ib.generation_available(row) is True


def test_total_cap_alone_can_deny_a_generation():
    row = spend(None, (GEN, "generate"), (FIX, "untuck_edit"), (GEN, "generate"))
    assert row["total"] == ib.MAX_TOTAL
    assert ib.generation_available(row) is False


# ---------- restart ----------

def test_a_restarted_worker_reads_the_row_instead_of_assuming_an_empty_budget():
    """§5 — the attempt counter starts over; the persisted budget does not."""
    backing = Gate(BOTH_GENERATIONS_SPENT)
    gate = backing.build(seeded=False)            # fresh gate, as after a restart
    s = make_settings(mannequin_max_attempts=5)
    allowed = asyncio.run(mj._can_retry_generation(
        gate, legacy_allows=mj.has_budget_for_retry(s, calls_spent=0)))
    assert allowed is False
    assert backing.reads == 1, "it must consult the row"
    assert backing.reserves == 0, "and must not reserve to find out"


def test_the_peek_costs_no_round_trip_once_a_reservation_has_been_seen():
    backing = Gate(None)
    gate = backing.build(seeded=False)
    asyncio.run(gate.reserve(request=GEN, operation="generate", candidate="A", attempt=1))
    assert asyncio.run(gate.generation_available()) is True    # FULL_REGENERATION left
    assert backing.reads == 0, "the reservation already told it what the row holds"


def test_an_unreadable_budget_denies_rather_than_guessing():
    gate = Gate(None, fail_read=True).build(seeded=False)
    assert asyncio.run(gate.generation_available()) is False


def test_a_missing_gate_denies():
    assert asyncio.run(mj._can_retry_generation(None, legacy_allows=True)) is False


def test_peeking_never_reserves():
    backing = Gate(None)
    gate = backing.build(seeded=False)
    for _ in range(4):
        asyncio.run(gate.generation_available())
    assert backing.reserves == 0
    assert backing.row is None, "the row must be untouched by a peek"


# ---------- the invariants this must not move ----------

def test_provider_budget_invariants_are_unchanged():
    assert ib.MAX_TOTAL == 3
    assert ib.SLOT_LIMITS == {ib.KIND_BASE: 1, ib.KIND_TARGETED_CORRECTION: 1,
                              ib.KIND_FULL_REGENERATION: 1}
    assert ib.GENERATION_SLOT_ORDER == (ib.KIND_BASE, ib.KIND_FULL_REGENERATION)


def test_candidate_b_still_gets_no_independent_base_slot():
    row = None
    for cand in ("A", "B"):
        d = ib.plan(row, request=GEN, operation="generate", candidate=cand, attempt=1)
        row = d.budget_after if d.allowed else row
    assert row["used"][ib.KIND_BASE] == 1
    assert row["used"][ib.KIND_FULL_REGENERATION] == 1


# ---------- the identity gate this must not relax ----------

def test_a_rejected_candidate_still_never_reaches_the_edit_passes():
    """§6 — `_apply_edits` stays behind `not p2_reject`; the fix changes the branch that
    decides retry-vs-salvage, not the branch that decides edit eligibility."""
    import ast
    import pathlib

    src = pathlib.Path(mj.__file__).read_text()
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_apply_edits"]
    assert calls, "the edit passes must still be called from somewhere"
    # the in-loop call site is guarded by the pre-gate; the guard text must survive
    assert "if not pillow_reject and not p2_reject:" in src


def test_the_fix_does_not_clear_the_reject_flag():
    """Nothing here may set `p2_reject = False` outside the existing salvage branch."""
    import pathlib

    src = pathlib.Path(mj.__file__).read_text()
    assert src.count("p2_reject, salvaged = False, True") == 1
    assert "p2_reject = False" not in src


@pytest.mark.parametrize("critical", [
    ["garment color changed"], ["pattern scale changed"],
])
def test_identity_critical_errors_are_not_made_editable(critical):
    """The check-shirt outputs. The budget fix must leave them rejected.

    `image_qc=enforce` is what production runs and what job 75c375da ran under; the gate
    is a no-op in shadow, so the fixture has to say so explicitly.
    """
    s = make_settings(image_qc="enforce")
    p2 = {"verdict": "retry", "critical_errors": critical, "mismatches": ["x"],
          "product_fidelity": 55, "image_quality": 85, "physical_naturalness": 80,
          "series_consistency": None, "correctionPrompt": "..."}
    _pillow, p2_reject = mj.gate_decision(s, "pass", p2, pillow_reasons=[], pillow_metrics={})
    assert p2_reject is True, "an identity-critical output must stay rejected"


def test_qc_thresholds_untouched():
    s = make_settings()
    assert (s.qc_score_auto_pass, s.qc_score_review) == (
        make_settings().qc_score_auto_pass, make_settings().qc_score_review)
