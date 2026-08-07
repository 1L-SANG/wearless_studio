"""No image-producing provider call may skip the budget gate.

A budget is only worth what its weakest call site is. `untuck`, `bust` and the axis edit
each had their own reading of `calls_spent`, and the base loop had a third; the way that
became 10 provider calls was not a broken rule but a call site that answered to a
different counter. This checks the shape of the code rather than its behaviour, so a
NEW unguarded call fails here even if no test happens to drive it.

Parsed with AST, not grep: a mention inside a comment or a docstring is not a call.
"""
import ast
import pathlib

import pytest

SERVER = pathlib.Path(__file__).resolve().parents[1]
WORKER = SERVER / "app/workers/mannequin_job.py"

#: the single provider entry point that returns newly generated image bytes
PROVIDER_METHOD = "generate_content_image"
#: the gate every one of those calls has to pass
GATE = "_require_image_slot"


def _tree(path):
    return ast.parse(path.read_text())


def _call_name(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def _functions_with(tree, name):
    """(function node, [calls to `name`]) for every function containing such a call."""
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        hits = [n for n in ast.walk(fn)
                if isinstance(n, ast.Call) and _call_name(n) == name]
        if hits:
            out.append((fn, hits))
    return out


def test_the_worker_has_exactly_the_provider_call_sites_this_patch_guarded():
    """A new call site is a deliberate act; it has to be added here and guarded."""
    sites = _functions_with(_tree(WORKER), PROVIDER_METHOD)
    names = sorted(fn.name for fn, _ in sites)
    assert names == ["_apply_axis_qc", "_apply_bust_pass", "_apply_untuck_pass",
                     "_run_baseline_edit", "_run_candidate"], names


@pytest.mark.parametrize("function_name", [
    "_apply_axis_qc", "_apply_bust_pass", "_apply_untuck_pass",
    "_run_baseline_edit", "_run_candidate",
])
def test_every_provider_call_site_reserves_a_slot_first(function_name):
    tree = _tree(WORKER)
    fn = next(f for f, _ in _functions_with(tree, PROVIDER_METHOD)
              if f.name == function_name)
    gate_calls = [n for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and _call_name(n) == GATE]
    provider_calls = [n for n in ast.walk(fn)
                      if isinstance(n, ast.Call) and _call_name(n) == PROVIDER_METHOD]
    assert gate_calls, f"{function_name} calls the provider without reserving a slot"
    # every provider call must be preceded in the source by a reservation
    first_gate = min(n.lineno for n in gate_calls)
    for call in provider_calls:
        assert first_gate < call.lineno, (
            f"{function_name}: provider call at line {call.lineno} runs before the gate")


def test_the_gate_denies_when_it_was_not_given_one():
    """`_require_image_slot(None, ...)` must return denied, never fall through to allow."""
    tree = _tree(WORKER)
    fn = next(f for f in ast.walk(tree)
              if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == GATE)
    src = ast.get_source_segment(WORKER.read_text(), fn)
    assert "BudgetDecision(\n            False" in src or "BudgetDecision(False" in src, src


def test_no_other_module_gained_a_mannequin_image_call():
    """The other image callers are different job kinds; this pins the set so a new one
    inside the mannequin worker cannot hide in a helper module."""
    callers = set()
    for path in (SERVER / "app").rglob("*.py"):
        if _functions_with(_tree(path), PROVIDER_METHOD):
            callers.add(str(path.relative_to(SERVER)))
    assert callers == {
        "app/agents/cut_generator.py",       # editor cut job
        "app/agents/cut_variator.py",        # editor vary job
        "app/agents/mannequin_adjuster.py",  # mannequin_adjust job
        "app/workers/mannequin_job.py",      # guarded above
        "app/workers/personalization_generation_job.py",
    }, sorted(callers)


def test_no_call_site_keeps_its_own_image_call_counter():
    """`calls_spent` may still pace attempts, but it must not be the authority.

    The authority is the job row; a helper that decided on its own count is how the
    per-candidate reset happened in the first place.
    """
    source = WORKER.read_text()
    tree = ast.parse(source)
    for fn, provider_calls in _functions_with(tree, PROVIDER_METHOD):
        segment = ast.get_source_segment(source, fn)
        assert GATE in segment, fn.name
        # the gate's decision, not a local integer, is what stands between the code and
        # the provider: an `allowed` check has to exist in the same function
        assert "reservation.allowed" in segment, fn.name
