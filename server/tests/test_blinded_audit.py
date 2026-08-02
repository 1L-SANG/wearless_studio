"""Phase 3 P0-C 9/N 보정 — blinded audit.

false pass 는 기계가 통과시킨 것을 사람이 봐야만 드러난다. 그래서 이 경로는
pass 도 라벨할 수 있어야 하고, 라벨러에게 기계 판정이 새면 안 되고, 판단이 바뀌어도
이전 기록이 남아야 한다.
"""

import json

import pytest

from app import blinded_audit as ba

SAMPLE = {"id": "s1", "case": "bg_only", "source": "a.jpg",
          "edit_type": "BACKGROUND_ONLY", "status": "pass",
          "machine_decision": "pass", "edit_qc_result": {"decision": "pass"}}


def test_presentation_hides_the_machine_verdict():
    view = ba.presentation(SAMPLE)
    ba.assert_blinded(view)
    for k in ("machine_decision", "status", "edit_qc_result", "decision"):
        assert k not in view


def test_presentation_still_shows_what_was_requested():
    """무엇을 요청했는지 모르면 '요청대로 됐나'를 사람도 판단할 수 없다."""
    view = ba.presentation(SAMPLE)
    assert view["editType"] == "BACKGROUND_ONLY" and view["requestedChanges"] == "bg_only"
    assert view["sourceImage"] and view["resultImage"]


def test_a_leaked_verdict_is_refused_not_ignored():
    with pytest.raises(ValueError) as e:
        ba.assert_blinded({**ba.presentation(SAMPLE), "machine_decision": "pass"})
    assert "blinded" in str(e.value)


def test_pass_samples_can_be_labeled():
    """운영 검수 API 는 review_required 전용이다 — 그래서 이 경로가 따로 필요하다."""
    rec = ba.make_label(sample=SAMPLE, label="fidelity_fail", reviewer="me",
                        dataset_id="d1")
    assert rec["label"] == "fidelity_fail" and rec["sampleId"] == "s1"


@pytest.mark.parametrize("label", ["accepted", "pass", "", None, "FIDELITY_PASS"])
def test_unknown_labels_are_refused(label):
    with pytest.raises(ValueError):
        ba.make_label(sample=SAMPLE, label=label, reviewer="me", dataset_id="d1")


def test_a_label_without_a_reviewer_is_refused():
    with pytest.raises(ValueError):
        ba.make_label(sample=SAMPLE, label="fidelity_pass", reviewer="  ",
                      dataset_id="d1")


def test_a_label_carries_policy_version_and_time():
    rec = ba.make_label(sample=SAMPLE, label="fidelity_pass", reviewer="me",
                        dataset_id="d1", now=100.0)
    assert rec["policyVersion"] == ba.POLICY_VERSION and rec["labeledAt"] == 100.0
    assert rec["datasetId"] == "d1"


def test_sample_sha_binds_the_image():
    a = ba.sample_sha256(SAMPLE, b"img-a")
    b = ba.sample_sha256(SAMPLE, b"img-b")
    assert a != b and a == ba.sample_sha256(SAMPLE, b"img-a")


def test_labels_are_append_only(tmp_path):
    path = str(tmp_path / "labels.jsonl")
    ba.append_label(path, ba.make_label(sample=SAMPLE, label="fidelity_pass",
                                        reviewer="me", dataset_id="d", now=1))
    ba.append_label(path, ba.make_label(sample=SAMPLE, label="fidelity_fail",
                                        reviewer="me", dataset_id="d", now=2))
    lines = open(path, encoding="utf-8").read().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["label"] == "fidelity_pass"   # 첫 판단이 남아 있다


def test_the_latest_label_wins_and_the_change_is_visible(tmp_path):
    path = str(tmp_path / "l.jsonl")
    for lab, t in (("fidelity_pass", 1), ("fidelity_fail", 2)):
        ba.append_label(path, ba.make_label(sample=SAMPLE, label=lab, reviewer="me",
                                            dataset_id="d", now=t))
    eff = ba.effective_labels(ba.load_labels(path))
    assert eff["s1"]["label"] == "fidelity_fail"
    assert eff["s1"]["changed"] is True and len(eff["s1"]["history"]) == 2


def test_a_repeated_identical_label_is_not_a_change(tmp_path):
    path = str(tmp_path / "l.jsonl")
    for t in (1, 2):
        ba.append_label(path, ba.make_label(sample=SAMPLE, label="fidelity_pass",
                                            reviewer="me", dataset_id="d", now=t))
    eff = ba.effective_labels(ba.load_labels(path))
    assert eff["s1"]["changed"] is False and len(eff["s1"]["history"]) == 2


def test_labels_never_overwrite_the_machine_decision():
    rows = [dict(SAMPLE)]
    out = ba.apply_labels(rows, {"s1": {"label": "fidelity_fail"}})
    assert out[0]["human_label"] == "fidelity_fail"
    assert out[0]["machine_decision"] == "pass"          # 기계 판정 불변
    assert out[0]["edit_qc_result"] == {"decision": "pass"}


def test_coverage_reports_pass_samples_separately():
    rows = [{**SAMPLE, "id": f"p{i}", "output_id": "o",
             "edit_qc_result": {"decision": "pass"}} for i in range(4)]
    rows += [{**SAMPLE, "id": "r1", "output_id": "o",
              "edit_qc_result": {"decision": "review_required"}}]
    cov = ba.coverage(rows, {"p0": {}, "p1": {}, "r1": {}})
    assert cov["byMachineDecision"]["pass"] == {"samples": 4, "labeled": 2}
    assert cov["passCoverage"] == 0.5


def test_coverage_is_none_when_there_are_no_pass_samples():
    rows = [{**SAMPLE, "output_id": "o", "edit_qc_result": {"decision": "reject"}}]
    assert ba.coverage(rows, {})["passCoverage"] is None


def test_labeled_pass_samples_unblock_the_coverage_gate():
    from app import shadow_report as sr
    rows = [{"id": f"p{i}", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY", "image_calls": 1, "vision_calls": 1,
             "edit_qc_result": {"decision": "pass",
                                "vision": {"meta": {"status": "ok"}}}}
            for i in range(40)]
    before = sr.report(rows)["pipelines"]["editor_vary"]["byEditTypeDetail"][
        "BACKGROUND_ONLY"]["verdict"]
    assert any("human label" in b for b in before["blockers"])
    labeled = ba.apply_labels(rows, {f"p{i}": {"label": "fidelity_pass"}
                                     for i in range(40)})
    after = sr.report(labeled)["pipelines"]["editor_vary"]["byEditTypeDetail"][
        "BACKGROUND_ONLY"]["humanLabels"]
    assert after["passLabeled"] == 40 and after["sufficient"] is True
