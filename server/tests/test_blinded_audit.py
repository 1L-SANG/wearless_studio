"""Phase 3 P0-C 9/N 보정 — blinded audit.

false pass 는 기계가 통과시킨 것을 사람이 봐야만 드러난다. 그래서 이 경로는
pass 도 라벨할 수 있어야 하고, 라벨러에게 기계 판정이 새면 안 되고, 판단이 바뀌어도
이전 기록이 남아야 한다.
"""

import json

import pytest

from app import blinded_audit as ba

SAMPLE = {"id": "s1", "case": "bg_only", "source": "a.jpg",
          "edit_type": "BACKGROUND_ONLY", "status": "pass", "output_id": "o1",
          "machine_decision": "pass", "edit_qc_result": {"decision": "pass"},
          "provenance": {"sourceSha256": "aa" * 32, "outputSha256": "bb" * 32}}
DS = "d1"


def lab(sample=SAMPLE, label="fidelity_pass", reviewer="me", dataset_id=DS, now=1.0):
    return ba.make_label(sample=sample, label=label, reviewer_id=reviewer,
                         dataset_id=dataset_id, now=now)


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
    rec = lab(label="fidelity_fail")
    assert rec["label"] == "fidelity_fail" and rec["sampleId"] == "s1"


@pytest.mark.parametrize("label", ["accepted", "pass", "", None, "FIDELITY_PASS"])
def test_unknown_labels_are_refused(label):
    with pytest.raises(ValueError):
        lab(label=label)


def test_a_label_without_a_reviewer_is_refused():
    with pytest.raises(ValueError):
        lab(reviewer="  ")


def test_a_label_carries_policy_version_and_time():
    rec = lab(now=100.0)
    assert rec["policyVersion"] == ba.POLICY_VERSION and rec["labeledAt"] == 100.0
    assert rec["datasetId"] == "d1" and rec["reviewerScheme"] == ba.REVIEWER_SCHEME
    assert rec["artifactKind"] == "local_calibration_audit"


def test_sample_sha_binds_the_output_image():
    other = {**SAMPLE, "provenance": {**SAMPLE["provenance"], "outputSha256": "cc" * 32}}
    assert ba.sample_sha256(SAMPLE) != ba.sample_sha256(other)


def test_sample_sha_binds_the_request_and_source():
    for k, v in (("case", "shot"), ("edit_type", "X"), ("source", "b.jpg")):
        assert ba.sample_sha256({**SAMPLE, k: v}) != ba.sample_sha256(SAMPLE)
    alt = {**SAMPLE, "provenance": {**SAMPLE["provenance"], "sourceSha256": "dd" * 32}}
    assert ba.sample_sha256(alt) != ba.sample_sha256(SAMPLE)


def test_a_sample_without_an_output_hash_cannot_be_labeled():
    with pytest.raises(ValueError) as e:
        lab(sample={**SAMPLE, "provenance": {}})
    assert "outputSha256" in str(e.value)


def test_labels_are_append_only(tmp_path):
    path = str(tmp_path / "labels.jsonl")
    ba.append_label(path, lab(now=1))
    ba.append_label(path, lab(label="fidelity_fail", now=2))
    lines = open(path, encoding="utf-8").read().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["label"] == "fidelity_pass"   # 첫 판단이 남아 있다


def test_the_latest_label_wins_and_the_change_is_visible(tmp_path):
    path = str(tmp_path / "l.jsonl")
    for lb, t in (("fidelity_pass", 1), ("fidelity_fail", 2)):
        ba.append_label(path, lab(label=lb, now=t))
    eff = ba.effective_labels(ba.load_labels(path))
    assert eff[(DS, "s1")]["label"] == "fidelity_fail"
    assert eff[(DS, "s1")]["changed"] is True and len(eff[(DS, "s1")]["history"]) == 2


def test_a_repeated_identical_label_is_not_a_change(tmp_path):
    path = str(tmp_path / "l.jsonl")
    for t in (1, 2):
        ba.append_label(path, lab(now=t))
    eff = ba.effective_labels(ba.load_labels(path))
    assert eff[(DS, "s1")]["changed"] is False
    assert len(eff[(DS, "s1")]["history"]) == 2


def test_labels_never_overwrite_the_machine_decision():
    out, q = ba.apply_labels([dict(SAMPLE)], {(DS, "s1"): lab()}, dataset_id=DS)
    assert not q
    assert out[0]["human_label"] == "fidelity_pass"
    assert out[0]["machine_decision"] == "pass"          # 기계 판정 불변
    assert out[0]["edit_qc_result"] == {"decision": "pass"}


def test_coverage_reports_pass_samples_separately():
    rows = [{**SAMPLE, "id": f"p{i}", "output_id": "o",
             "edit_qc_result": {"decision": "pass"}} for i in range(4)]
    rows += [{**SAMPLE, "id": "r1", "output_id": "o",
              "edit_qc_result": {"decision": "review_required"}}]
    cov = ba.coverage(rows, {(DS, "p0"): {}, (DS, "p1"): {}, (DS, "r1"): {}})
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
    assert any("false pass 미측정" in b for b in before["blockers"])
    labeled = [{**r, "human_label": "fidelity_pass"} for r in rows]
    after = sr.report(labeled)["pipelines"]["editor_vary"]["byEditTypeDetail"][
        "BACKGROUND_ONLY"]["humanLabels"]
    assert after["passLabeled"] == 40 and after["sufficient"] is True


# ── binding 강제 (9/N 최종 보정) ────────────────────────────────────────────

def test_a_label_from_another_dataset_is_quarantined_not_applied():
    """sampleId 만으로 다른 데이터셋 라벨을 합치면 다른 이미지에 대한 판단이 섞인다."""
    out, q = ba.apply_labels([dict(SAMPLE)], {("other", "s1"): lab(dataset_id="other")},
                             dataset_id=DS, strict=False)
    assert "human_label" not in out[0]
    assert q == [{"datasetId": "other", "sampleId": "s1", "reason": "dataset_mismatch"}]


@pytest.mark.parametrize("mutate,reason", [
    ({"sampleSha256": "ff" * 32}, "sample_hash_mismatch"),
    ({"outputSha256": "ff" * 32}, "output_hash_mismatch"),
    ({"policyVersion": "blinded_audit_v99"}, "policy_version_unsupported"),
])
def test_mismatched_labels_are_quarantined(mutate, reason):
    out, q = ba.apply_labels([dict(SAMPLE)], {(DS, "s1"): {**lab(), **mutate}},
                             dataset_id=DS, strict=False)
    assert "human_label" not in out[0]
    assert q[0]["reason"] == reason


def test_strict_mode_raises_instead_of_silently_dropping():
    with pytest.raises(ValueError) as e:
        ba.apply_labels([dict(SAMPLE)], {(DS, "s1"): {**lab(), "outputSha256": "ff" * 32}},
                        dataset_id=DS)
    assert "라벨 결합 실패" in str(e.value)


def test_a_label_for_a_missing_sample_is_quarantined():
    out, q = ba.apply_labels([], {(DS, "zz"): lab()}, dataset_id=DS, strict=False)
    assert out == [] and q[0]["reason"] == "sample_not_found"


# ── hash chain ─────────────────────────────────────────────────────────────

def test_each_event_links_to_the_previous_one(tmp_path):
    path = str(tmp_path / "l.jsonl")
    a = ba.append_label(path, lab(now=1))
    b = ba.append_label(path, lab(label="fidelity_fail", now=2))
    assert a["previousEventHash"] == ba.GENESIS
    assert b["previousEventHash"] == a["eventHash"]
    ba.verify_chain(ba.load_labels(path))


def test_editing_a_middle_row_is_detected(tmp_path):
    import json as _j
    path = tmp_path / "l.jsonl"
    for t in (1, 2, 3):
        ba.append_label(str(path), lab(now=t))
    lines = path.read_text().splitlines()
    row = _j.loads(lines[1]); row["label"] = "fidelity_fail"
    lines[1] = _j.dumps(row, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ba.LabelChainError):
        ba.load_labels(str(path))


def test_deleting_a_row_is_detected(tmp_path):
    path = tmp_path / "l.jsonl"
    for t in (1, 2, 3):
        ba.append_label(str(path), lab(now=t))
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    with pytest.raises(ba.LabelChainError):
        ba.load_labels(str(path))


def test_reordering_rows_is_detected(tmp_path):
    path = tmp_path / "l.jsonl"
    for t in (1, 2):
        ba.append_label(str(path), lab(now=t))
    lines = path.read_text().splitlines()
    path.write_text("\n".join(reversed(lines)) + "\n")
    with pytest.raises(ba.LabelChainError):
        ba.load_labels(str(path))


def test_a_duplicate_event_id_is_detected(tmp_path):
    import json as _j
    path = tmp_path / "l.jsonl"
    ba.append_label(str(path), lab(now=1))
    line = path.read_text().splitlines()[0]
    path.write_text(line + "\n" + line + "\n")
    with pytest.raises(ba.LabelChainError):
        ba.load_labels(str(path))


def test_the_artifact_is_named_a_local_calibration_audit():
    """운영 승인 이력이라고 부르지 않는다 — reviewer 는 자기 신고 식별자다."""
    assert ba.ARTIFACT_KIND == "local_calibration_audit"
    assert ba.REVIEWER_SCHEME == "local_self_declared"
    assert lab()["reviewerScheme"] == "local_self_declared"


def test_presentation_validates_itself():
    """assert_blinded 를 호출자가 잊어도 새지 않게 만드는 자리에서 검증한다."""
    import inspect
    src = inspect.getsource(ba.presentation)
    assert "assert_blinded(view)" in src
