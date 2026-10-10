import pytest

from app.agents import outer_closure as oc


def closure(kind="button", range="graded", **changes):
    value = {"version": 1, "kind": kind, "range": range, "secondary": None,
             "evidenceSlots": ["Front", "Detail"], "note": "Visible front buttons"}
    value.update(changes)
    return value


def test_states_follow_slider_order_and_expose_four_levels():
    assert oc.STATES == ("closed", "openToChest", "partial", "openToWaist", "open")
    assert oc.DEFAULT == "open"
    assert oc.EXPOSED == {"openToChest", "partial", "openToWaist", "open"}


@pytest.mark.parametrize("state", ["closed", "openToChest", "partial", "openToWaist", "open"])
def test_normalize_keeps_all_five_states(state):
    assert oc.normalize_state(state) == state


@pytest.mark.parametrize("value", [None, "bad", "OPEN", {}, [], 1, True])
def test_normalize_unknown_defaults_open(value):
    assert oc.normalize_state(value) == "open"


@pytest.mark.parametrize("analysis", [None, [], {}, {"frontClosure": None},
                                     {"frontClosure": []}, {"frontClosure": "none"},
                                     {"frontClosure": {"kind": "none"}}])
def test_front_closure_missing_or_malformed_defaults_unknown(analysis):
    assert oc.front_closure_of(analysis) == {
        "version": 1, "kind": "unknown", "range": "graded", "secondary": None,
        "evidenceSlots": [], "note": None,
    }


@pytest.mark.parametrize("changes", [
    {"version": 2}, {"version": True}, {"kind": []}, {"kind": "zip"},
    {"range": []}, {"range": None}, {"kind": "zipper", "range": "binary"},
    {"kind": "belt", "range": "graded"}, {"kind": "none", "range": "graded"},
    {"secondary": "button"}, {"secondary": []}, {"evidenceSlots": "Front"},
    {"evidenceSlots": [1]}, {"evidenceSlots": ["Fit"]}, {"note": []},
    {"note": "a" * 81}, {"kind": "unknown", "evidenceSlots": ["Front"]},
    {"kind": "none", "range": None, "secondary": "belt"},
])
def test_front_closure_invalid_saved_fields_defaults_unknown(changes):
    assert oc.front_closure_of({"frontClosure": closure(**changes)}) == oc.front_closure_of({})


@pytest.mark.parametrize("kind,range", [("zipper", "graded"), ("button", "graded"),
                                      ("button", "binary"), ("snap", "graded"),
                                      ("snap", "binary"), ("belt", "binary"),
                                      ("none", None), ("unknown", "graded")])
def test_front_closure_valid_saved_contract_is_copied(kind, range):
    value = closure(kind, range, evidenceSlots=[] if kind == "unknown" else ["Front"])
    result = oc.front_closure_of({"frontClosure": value})
    assert result == value
    assert result is not value and result["evidenceSlots"] is not value["evidenceSlots"]


@pytest.mark.parametrize("requested", ["closed", "openToChest", "partial", "openToWaist", "open", "bad"])
def test_effective_none_is_always_open(requested):
    assert oc.effective_state(requested, closure("none", None)) == "open"


@pytest.mark.parametrize("requested,expected", [("closed", "closed"), ("openToChest", "closed"),
                                                ("partial", "closed"), ("openToWaist", "open"),
                                                ("open", "open"), ("bad", "open")])
def test_effective_binary_mapping(requested, expected):
    assert oc.effective_state(requested, closure("button", "binary")) == expected


@pytest.mark.parametrize("requested", ["closed", "openToChest", "partial", "openToWaist", "open"])
def test_effective_graded_keeps_requested_level(requested):
    assert oc.effective_state(requested, closure()) == requested
    assert oc.effective_state(requested, oc.front_closure_of({})) == requested
