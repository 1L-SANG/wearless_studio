"""Phase 3 P0-C 9/N — 검증된 데이터셋과 집계 대상의 결합.

직전 구조는 manifest 만 소유했다. 2행을 검증해 얻은 trusted 결과에 전혀 다른 60행을
붙이면 total=60·trusted·graded=60·enforceReady=true 가 나왔다 — 검증한 것과 집계하는
것이 다르면 검증은 장식이다.
"""

import copy
import json
import pathlib
from dataclasses import replace

import pytest

from app import shadow_report as sr
from app import shadow_verification as sv

SERVER = pathlib.Path(__file__).resolve().parents[1]


def _ready(out):
    ev = out["pipelines"]["editor_vary"]
    return {ev["verdict"]["enforceReady"]} | {
        t["verdict"]["enforceReady"] for t in ev["byEditTypeDetail"].values()}


def _rows_b(n=60):
    return [{"id": f"FAKE-{i}", "output_id": f"o{i}", "source_kind": "editor_asset",
             "edit_type": "BACKGROUND_ONLY", "image_calls": 1, "vision_calls": 1,
             "human_label": "fidelity_pass",
             "edit_qc_result": {"decision": "pass",
                                "vision": {"meta": {"status": "ok"}}}}
            for i in range(n)]


# ── 1. A/B 결합 불가 ──────────────────────────────────────────────────────

def test_report_rejects_loose_rows(shadow_dataset):
    """rows 를 따로 넘기는 형태가 아예 없다."""
    with pytest.raises(TypeError):
        sr.report(_rows_b())


def test_report_rejects_the_old_verification_kwarg(shadow_dataset):
    built = shadow_dataset(n=2)
    with pytest.raises(TypeError):
        sr.report(_rows_b(), manifest_verification=built["verification"])


def test_a_trusted_dataset_reports_only_its_own_rows(shadow_dataset):
    built = shadow_dataset(n=2)
    out = sr.report(built["verification"])
    assert out["total"] == 2
    assert out["reportKind"] == "calibration"
    assert {r["id"] for r in built["verification"].rows} == {"bg_only", "shot"}


def test_a_rebuilt_trusted_dataset_is_refused(shadow_dataset):
    """replace() 로 rows 만 갈아끼우려 해도 봉인 토큰이 없어 만들어지지 않는다."""
    built = shadow_dataset(n=2)
    with pytest.raises(ValueError):
        replace(built["verification"], rows=tuple(_rows_b()), _token=None)


# ── 2. 검증 이후 정본 변조 ────────────────────────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("id", "HACKED"), ("output_id", "OTHER"), ("edit_type", "CUSTOM_REVIEW_REQUIRED"),
    ("source_kind", "approved_baseline"), ("source", "other.jpg"),
    ("provenance", {}), ("edit_qc_result", {"decision": "pass"}),
])
def test_in_place_tampering_of_a_protected_field_is_detected(shadow_dataset,
                                                             field, value):
    built = shadow_dataset(n=2)
    ds = built["verification"]
    assert sr.report(ds)["reportKind"] == "calibration"
    ds.rows[0][field] = value           # tuple 은 불변이지만 안의 dict 는 가변
    out = sr.report(ds)
    assert out["reportKind"] == "distribution_only"
    assert "verified_rows_tampered" in out["calibrationBlockedReasons"]
    assert _ready(out) == {False}


def test_adding_a_label_field_without_binding_is_blocked(shadow_dataset):
    """typed bind 밖에서 붙은 라벨은 출처를 증명할 수 없다."""
    built = shadow_dataset(n=2)
    ds = built["verification"]
    ds.rows[0]["human_label"] = "fidelity_pass"
    out = sr.report(ds)
    assert out["reportKind"] == "distribution_only"
    assert "verified_rows_tampered" in out["calibrationBlockedReasons"]


def test_the_original_row_list_can_change_without_effect(shadow_dataset):
    built = shadow_dataset(n=2)
    built["rows"][0]["id"] = "MUTATED-OUTSIDE"
    assert sr.report(built["verification"])["reportKind"] == "calibration"


# ── 3. 라벨 결합 ──────────────────────────────────────────────────────────

def _labels_for(rows, dataset_id="ds", label="fidelity_pass"):
    from app import blinded_audit as ba
    return {(dataset_id, str(r["id"])): ba.make_label(
        sample=r, label=label, reviewer_id="t", dataset_id=dataset_id, now=1.0)
        for r in rows}


def test_binding_labels_keeps_the_dataset_trusted(shadow_dataset):
    built = shadow_dataset(n=2)
    ds = built["verification"]
    bound, q = sv.bind_verified_labels(ds, _labels_for(ds.rows))
    assert not q and bound.trusted and bound.labels_bound
    out = sr.report(bound)
    assert out["reportKind"] == "calibration"
    assert out["pipelines"]["editor_vary"]["calibrationConfusion"]["graded"] == 2


def test_binding_only_adds_allowed_fields(shadow_dataset):
    built = shadow_dataset(n=2)
    ds = built["verification"]
    bound, _ = sv.bind_verified_labels(ds, _labels_for(ds.rows))
    for before, after in zip(ds.rows, bound.rows):
        assert set(after) - set(before) <= set(sv.LABEL_FIELDS)
        assert {k: v for k, v in after.items() if k not in sv.LABEL_FIELDS} == before


def test_a_mismatched_dataset_id_quarantines_and_blocks(shadow_dataset):
    built = shadow_dataset(n=2)
    ds = built["verification"]
    labels = _labels_for(ds.rows, dataset_id="other")
    bound, q = sv.bind_verified_labels(ds, labels)
    assert [x["reason"] for x in q] == ["dataset_mismatch"] * 2
    assert not bound.trusted
    out = sr.report(bound, quarantined=q)
    assert out["calibrationUsable"] is False and _ready(out) == {False}


def test_a_tampered_label_hash_is_quarantined(shadow_dataset):
    built = shadow_dataset(n=2)
    ds = built["verification"]
    labels = _labels_for(ds.rows)
    for k in labels:
        labels[k] = {**labels[k], "outputSha256": "f" * 64}
    bound, q = sv.bind_verified_labels(ds, labels)
    assert {x["reason"] for x in q} == {"output_hash_mismatch"}
    assert not bound.trusted


def test_labels_cannot_smuggle_arbitrary_rows(shadow_dataset):
    """호출자가 임의 rows 를 '라벨 결과'라고 주장해 넣을 통로가 없다."""
    built = shadow_dataset(n=2)
    ds = built["verification"]
    # bind 는 dataset.rows 만 다룬다 — 외부 rows 를 받을 인자가 없다.
    import inspect
    sig = inspect.signature(sv.bind_verified_labels)
    assert list(sig.parameters) == ["dataset", "effective_labels"]


def test_binding_refuses_to_change_protected_fields(shadow_dataset, monkeypatch):
    built = shadow_dataset(n=2)
    ds = built["verification"]
    from app import blinded_audit as ba

    labels = _labels_for(ds.rows)
    real = ba.sample_sha256

    def sneaky(row):
        row["edit_type"] = "CUSTOM_REVIEW_REQUIRED"   # 결합 도중 정본을 건드린다
        return real(row)

    monkeypatch.setattr(ba, "sample_sha256", sneaky)
    with pytest.raises(sv.LabelBindingError):
        sv.bind_verified_labels(ds, labels)


# ── 4. distribution 경로 ─────────────────────────────────────────────────

def test_a_distribution_dataset_is_never_trusted(shadow_dataset):
    rows = _rows_b(10)
    ds = sv.distribution_dataset(rows)
    assert not ds.trusted
    out = sr.report(ds)
    assert out["reportKind"] == "distribution_only"
    assert _ready(out) == {False}


def test_distribution_numbers_are_intact():
    rows = _rows_b(30)
    out = sr.report(sv.distribution_dataset(rows))
    ev = out["pipelines"]["editor_vary"]
    assert ev["samples"] == 30 and ev["decisionRates"]["n"] == 30
    assert ev["calibrationConfusion"]["graded"] == 30


def test_graded_and_false_pass_survive(shadow_dataset):
    built = shadow_dataset(n=2)
    labels = _labels_for(built["verification"].rows)
    first = next(iter(labels))
    labels[first] = {**labels[first], "label": "fidelity_fail"}
    bound, q = sv.bind_verified_labels(built["verification"], labels)
    assert not q
    out = sr.report(bound)
    cal = out["pipelines"]["editor_vary"]["calibrationConfusion"]
    assert cal["graded"] == 2 and cal["falsePass"] == 1


# ── 5. 중첩 malformed schema 조기 중단 ───────────────────────────────────

BASE = {"datasetId": "ds", "rawSampleManifestSha256": "a" * 64,
        "outputBundleSha256": "b" * 64, "sourceDataset": {"sha256": "c" * 64},
        "validForCalibration": True, "provenanceUnverified": False,
        "provenanceProblems": []}


@pytest.mark.parametrize("over,code", [
    ({"sourceDataset": [{"sha256": "x"}]}, "manifest_binding_invalid:sourceDataset"),
    ({"sourceDataset": "not-a-dict"}, "manifest_binding_invalid:sourceDataset"),
    ({"sourceDataset": 1}, "manifest_binding_invalid:sourceDataset"),
    ({"sourceDataset": None}, "manifest_binding_invalid:sourceDataset"),
    ({"provenanceProblems": {}}, "manifest_binding_invalid:provenanceProblems"),
    ({"provenanceProblems": "x"}, "manifest_binding_invalid:provenanceProblems"),
    ({"invalidReasons": 1}, "manifest_binding_invalid:calibration_state"),
])
def test_a_nested_shape_problem_stops_before_file_access(tmp_path, monkeypatch,
                                                        over, code):
    from app import safe_paths, shadow_provenance as sp
    calls = []
    monkeypatch.setattr(safe_paths, "file_sha256",
                        lambda p: calls.append(("sha", str(p))) or "0" * 64)
    monkeypatch.setattr(sp, "artifact_problems",
                        lambda *a, **k: calls.append(("artifact",)) or [])
    (tmp_path / "s.jsonl").write_text("")
    ds = sv.verify_dataset(manifest={**BASE, **over}, rows=[],
                           samples_path=tmp_path / "s.jsonl")
    assert not ds.trusted
    assert code in ds.problems, ds.problems
    assert calls == [], calls          # 파일에 손대지 않았다


def test_a_shape_problem_never_crashes(tmp_path):
    (tmp_path / "s.jsonl").write_text("")
    for over in ({"sourceDataset": [{}]}, {"sourceDataset": "s"},
                 {"provenanceProblems": {}}, {"datasetId": 5}):
        ds = sv.verify_dataset(manifest={**BASE, **over}, rows=[],
                               samples_path=tmp_path / "s.jsonl")
        assert ds.state is sv.UNVERIFIED


def test_manifest_state_is_a_str_enum():
    assert isinstance(sv.TRUSTED, str) and sv.TRUSTED.value == "trusted"
    assert {s.value for s in sv.ManifestState} == {
        "absent", "unverified", "invalid", "trusted"}
