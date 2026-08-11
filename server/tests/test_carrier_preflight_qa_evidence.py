"""A rejected carrier has to leave enough behind to judge the rejection.

Before this, a cape rejection recorded one string: `carrier_silhouette_cape`. Two
different checks raise it — a Vision silhouette label and the hem/shoulder ratio —
and the paid carrier bytes were discarded, so nobody could tell which check fired
or look at what it fired on. These tests pin the evidence, not the policy: every
threshold and every accept/reject outcome must come out identical.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from app.services.hybrid_composite import carrier_preflight as cp
from app.workers import mannequin_job

CAPE_LIMIT = cp.MAX_CAPE_HEM_TO_SHOULDER


def _reasons(*, labels=None, geometry=None, inventory=None):
    return cp._silhouette_reasons(
        {"silhouette": list(labels or [])}, {}, geometry or {}, inventory or {})


def _detail(reasons, code, origin):
    hits = [r.detail_record() for r in reasons
            if r.code == code and r.origin == origin]
    assert len(hits) == 1, f"expected exactly one {code}/{origin}, got {len(hits)}"
    return hits[0]


# ------------------------------------------------------------------ Test A

def test_a_a_vision_cape_label_records_the_label_path():
    reasons = _reasons(labels=["cape"])
    d = _detail(reasons, "carrier_silhouette_cape", cp.ORIGIN_VISION_LABEL)
    assert d["matchedLabels"] == ["cape"]
    assert d["metrics"] == {}
    assert d["thresholds"] == {}
    assert d["comparison"] == ""


@pytest.mark.parametrize("label", sorted(cp.CAPE_LABELS))
def test_a_every_cape_label_is_reported_by_name(label):
    d = _detail(_reasons(labels=[label]), "carrier_silhouette_cape", cp.ORIGIN_VISION_LABEL)
    assert d["matchedLabels"] == [label]


# ------------------------------------------------------------------ Test B

def test_b_a_ratio_over_the_limit_records_the_metric_path():
    reasons = _reasons(geometry={"hemToShoulderRatio": 1.36})
    d = _detail(reasons, "carrier_silhouette_cape", cp.ORIGIN_GEOMETRY_METRIC)
    assert d["metrics"]["hemToShoulderRatio"] == 1.36
    assert d["thresholds"]["MAX_CAPE_HEM_TO_SHOULDER"] == CAPE_LIMIT
    assert d["comparison"] == ">"
    assert d["matchedLabels"] == []


def test_b_the_reported_threshold_is_the_one_the_check_used():
    """A copied constant would let the report drift from the decision."""
    src = inspect.getsource(cp._silhouette_reasons)
    assert "MAX_CAPE_HEM_TO_SHOULDER" in src
    assert "1.35" not in src, "the limit must be read, not restated"


def test_b_a_ratio_at_the_limit_still_passes():
    assert _reasons(geometry={"hemToShoulderRatio": CAPE_LIMIT}) == []


# ------------------------------------------------------------------ Test C

def test_c_both_paths_are_kept_as_two_separate_records():
    reasons = _reasons(labels=["cape"], geometry={"hemToShoulderRatio": 1.42})
    cape = [r for r in reasons if r.code == "carrier_silhouette_cape"]
    assert len(cape) == 2
    assert {r.origin for r in cape} == {cp.ORIGIN_VISION_LABEL, cp.ORIGIN_GEOMETRY_METRIC}
    label_rec = _detail(reasons, "carrier_silhouette_cape", cp.ORIGIN_VISION_LABEL)
    metric_rec = _detail(reasons, "carrier_silhouette_cape", cp.ORIGIN_GEOMETRY_METRIC)
    assert label_rec["matchedLabels"] == ["cape"] and not label_rec["metrics"]
    assert metric_rec["metrics"]["hemToShoulderRatio"] == 1.42 and not metric_rec["matchedLabels"]


def test_c_slab_also_distinguishes_its_two_paths():
    labelled = _reasons(labels=["slab_torso"])
    assert _detail(labelled, "carrier_silhouette_slab_torso",
                   cp.ORIGIN_VISION_LABEL)["matchedLabels"] == ["slab_torso"]
    measured = _reasons(geometry={"sideEdgeDelta": 0.01}, inventory={"torso_aspect": 2.7})
    d = _detail(measured, "carrier_silhouette_slab_torso", cp.ORIGIN_GEOMETRY_METRIC)
    assert d["metrics"]["sideEdgeDelta"] == 0.01
    assert d["thresholds"]["MIN_SLAB_TORSO_ASPECT"] == cp.MIN_SLAB_TORSO_ASPECT


# ------------------------------------------------------------------ Test D

def test_d_the_reason_code_list_is_unchanged():
    reasons = _reasons(labels=["cape"], geometry={"hemToShoulderRatio": 1.42})
    assert [r.code for r in reasons] == ["carrier_silhouette_cape",
                                         "carrier_silhouette_cape"]


def test_d_slab_threshold_behaviour_is_bit_for_bit_what_it_was():
    """2.4 was a bare literal; naming it must not move the boundary."""
    assert cp.MIN_SLAB_TORSO_ASPECT == 2.4
    just_over = _reasons(geometry={"sideEdgeDelta": 0.05}, inventory={"torso_aspect": 2.41})
    at_limit = _reasons(geometry={"sideEdgeDelta": 0.05}, inventory={"torso_aspect": 2.4})
    assert [r.code for r in just_over] == ["carrier_silhouette_slab_torso"]
    assert at_limit == []


@pytest.mark.parametrize("ratio,expected", [
    (2.171, []), (2.702, ["carrier_silhouette_slab_torso"]), (2.196, [])])
def test_d_the_three_real_carriers_from_job_da98aa2a_still_score_the_same(ratio, expected):
    """The observed torso aspects from the real failure, replayed through the check."""
    got = _reasons(geometry={"sideEdgeDelta": 0.01}, inventory={"torso_aspect": ratio})
    assert [r.code for r in got] == expected


# ------------------------------------------------------------------ Test E

def test_e_no_qa_directory_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("HYBRID_COMPOSITE_ARTIFACT_DIR", raising=False)
    out = mannequin_job._capture_rejected_carrier(
        carrier_bytes=b"\x89PNG carrier", carrier_mime="image/png", carrier_bgr=None,
        carrier_sha256="a" * 64, source_sha256="b" * 64, job_id="job-1",
        candidate="A", attempt=1,
        preflight=cp.CarrierPreflightResult(cp.REJECT), preflight_summary={},
        landmarks={})
    assert out == {"enabled": False, "carrierPath": None,
                   "metadataPath": None, "captureError": None}
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ Test F

def _reject_result():
    return cp.CarrierPreflightResult(
        cp.REJECT,
        reasons=(cp.CarrierPreflightReason(
            "carrier_silhouette_cape", "hem/shoulder ratio 1.42 > 1.35",
            metrics={"hemToShoulderRatio": 1.42},
            origin=cp.ORIGIN_GEOMETRY_METRIC,
            thresholds={"MAX_CAPE_HEM_TO_SHOULDER": CAPE_LIMIT}, comparison=">"),),
        metrics={"geometry": {"hemToShoulderRatio": 1.42, "sideEdgeDelta": 0.03}})


def test_f_the_exact_provider_bytes_are_written_and_hash_to_the_same_digest(
        tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))
    payload = b"\x89PNG\r\n\x1a\n not really a png, but exact bytes matter"
    digest = hashlib.sha256(payload).hexdigest()
    out = mannequin_job._capture_rejected_carrier(
        carrier_bytes=payload, carrier_mime="image/png", carrier_bgr=None,
        carrier_sha256=digest, source_sha256="b" * 64, job_id="job-1",
        candidate="A", attempt=1, preflight=_reject_result(),
        preflight_summary=_reject_result().summary(), landmarks={})
    assert out["enabled"] is True and out["captureError"] is None
    written = Path(out["carrierPath"]).read_bytes()
    assert written == payload, "the carrier must be stored byte-for-byte"
    assert hashlib.sha256(written).hexdigest() == digest
    assert Path(out["carrierPath"]).name == "carrier_original.png"


def test_f_the_sidecar_records_the_origin_and_the_measurement(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))
    result = _reject_result()
    out = mannequin_job._capture_rejected_carrier(
        carrier_bytes=b"bytes", carrier_mime="image/png", carrier_bgr=None,
        carrier_sha256="c" * 64, source_sha256="d" * 64, job_id="job-1",
        candidate="B", attempt=3, preflight=result,
        preflight_summary=result.summary(), landmarks={})
    meta = json.loads(Path(out["metadataPath"]).read_text())
    assert meta["carrierSha256"] == "c" * 64
    assert meta["carrierShaBasis"] == "encoded_memory_bytes"
    assert meta["sourceSha256"] == "d" * 64
    assert meta["preflight"]["reasons"] == ["carrier_silhouette_cape"]
    detail = meta["preflight"]["reasonDetails"][0]
    assert detail["origin"] == cp.ORIGIN_GEOMETRY_METRIC
    assert detail["metrics"]["hemToShoulderRatio"] == 1.42
    assert detail["thresholds"]["MAX_CAPE_HEM_TO_SHOULDER"] == CAPE_LIMIT


def test_f_absent_request_fields_are_null_with_a_reason_not_invented(
        tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))
    out = mannequin_job._capture_rejected_carrier(
        carrier_bytes=b"bytes", carrier_mime="image/png", carrier_bgr=None,
        carrier_sha256="e" * 64, source_sha256="f" * 64, job_id="j", candidate="A",
        attempt=1, preflight=_reject_result(),
        preflight_summary=_reject_result().summary(), landmarks={})
    meta = json.loads(Path(out["metadataPath"]).read_text())
    for field, reason in [("generationConfig", "generationConfigMissingReason"),
                          ("safetySettings", "safetySettingsMissingReason")]:
        assert meta[field] is None
        assert meta[reason]
    assert meta["model"] is None and meta["promptSha256"] is None
    assert meta["requestFieldsMissingReason"]


@pytest.mark.parametrize("mime,ext", [("image/png", "png"), ("image/jpeg", "jpg"),
                                      ("image/webp", "webp"), (None, "bin")])
def test_f_the_extension_follows_the_provider_mime(tmp_path, monkeypatch, mime, ext):
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))
    out = mannequin_job._capture_rejected_carrier(
        carrier_bytes=b"x", carrier_mime=mime, carrier_bgr=None,
        carrier_sha256="a" * 64, source_sha256="b" * 64, job_id="j", candidate="A",
        attempt=1, preflight=_reject_result(),
        preflight_summary=_reject_result().summary(), landmarks={})
    assert Path(out["carrierPath"]).name == f"carrier_original.{ext}"


def test_f_a_decoded_preview_is_never_called_the_original():
    src = inspect.getsource(mannequin_job._capture_rejected_carrier)
    assert "carrier_original." in src and "carrier_preview.png" in src
    # imwrite re-encodes; it must only ever produce the preview
    imwrite_targets = [ln for ln in src.splitlines() if "imwrite" in ln]
    assert imwrite_targets and all("original" not in ln for ln in imwrite_targets)


# ------------------------------------------------------------------ Test G

def test_g_candidates_and_attempts_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))
    paths = []
    for candidate, attempt, payload in [("A", 1, b"one"), ("B", 2, b"two"),
                                        ("B", 3, b"three")]:
        out = mannequin_job._capture_rejected_carrier(
            carrier_bytes=payload, carrier_mime="image/png", carrier_bgr=None,
            carrier_sha256=hashlib.sha256(payload).hexdigest(), source_sha256="b" * 64,
            job_id="job-1", candidate=candidate, attempt=attempt,
            preflight=_reject_result(), preflight_summary=_reject_result().summary(),
            landmarks={})
        paths.append(out["carrierPath"])
    assert len(set(paths)) == 3
    assert Path(paths[0]).read_bytes() == b"one"
    assert Path(paths[2]).read_bytes() == b"three"


def test_g_two_jobs_stay_apart(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))
    seen = set()
    for job in ("job-1", "job-2"):
        out = mannequin_job._capture_rejected_carrier(
            carrier_bytes=b"x", carrier_mime="image/png", carrier_bgr=None,
            carrier_sha256="a" * 64, source_sha256="b" * 64, job_id=job,
            candidate="A", attempt=1, preflight=_reject_result(),
            preflight_summary=_reject_result().summary(), landmarks={})
        seen.add(out["carrierPath"])
    assert len(seen) == 2


# ------------------------------------------------------------------ Test H

def test_h_an_unwritable_directory_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", "/proc/nonexistent-qa-root")
    out = mannequin_job._capture_rejected_carrier(
        carrier_bytes=b"x", carrier_mime="image/png", carrier_bgr=None,
        carrier_sha256="a" * 64, source_sha256="b" * 64, job_id="j", candidate="A",
        attempt=1, preflight=_reject_result(),
        preflight_summary=_reject_result().summary(), landmarks={})
    assert out["enabled"] is True
    assert out["captureError"] is not None
    assert out["carrierPath"] is None


def test_h_a_broken_preflight_object_cannot_escape_the_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))

    class Exploding:
        passed = False
        decision = "reject"
        reasons = ()
        metrics = {}

        def reason_details(self):
            raise RuntimeError("boom")

    out = mannequin_job._capture_rejected_carrier(
        carrier_bytes=b"x", carrier_mime="image/png", carrier_bgr=None,
        carrier_sha256="a" * 64, source_sha256="b" * 64, job_id="j", candidate="A",
        attempt=1, preflight=Exploding(), preflight_summary={}, landmarks={})
    assert out["captureError"] is not None


def test_h_capture_is_called_after_the_decision_and_feeds_nothing_back():
    src = inspect.getsource(mannequin_job._apply_hybrid_composite)
    block = src.split("qa_capture = {", 1)[1].split("await emit(", 1)[0]
    for forbidden in ("preflight =", "soft_continue =", "return ", "retry"):
        assert forbidden not in block, forbidden
    # the decision is already fixed before capture runs
    assert src.index("preflight = hc_preflight.preflight_carrier_quality") < \
        src.index("qa_capture = {")
    steering = [ln.strip() for ln in src.splitlines()
                if "qa_capture" in ln and ln.strip().startswith(
                    ("if ", "elif ", "while ", "return ", "raise ", "assert "))]
    assert steering == [], steering


# ------------------------------------------------------------------ Test I

def _code_only(fn) -> str:
    """Source with comments and docstring stripped.

    A comment naming GeminiImageResult is documentation, not a call; scanning raw
    text would fail on the explanation while missing a real call written tersely.
    """
    src = inspect.getsource(fn)
    body = src.split('"""', 2)[-1] if src.count('"""') >= 2 else src
    return "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())


def test_i_capture_touches_neither_the_database_nor_r2():
    src = _code_only(mannequin_job._capture_rejected_carrier)
    for forbidden in ("repo.", "pool", "r2.", "R2Client", "presigned", "put_bytes",
                      "upload", "execute(", "commit"):
        assert forbidden not in src, forbidden


def _called_names(fn) -> set[str]:
    """Every callee name in the function, from the parse tree.

    Text scanning cannot tell a call from a string that names one — this function
    documents `GeminiImageClient._body` in a missing-reason message and must not be
    accused of calling it.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, ast.Attribute)
                      else getattr(f, "id", ""))
        if isinstance(node, ast.Await):
            names.add("<await>")
    return {n.lower() for n in names if n}


def test_i_capture_makes_no_provider_call():
    called = _called_names(mannequin_job._capture_rejected_carrier)
    assert "<await>" not in called, "capture must stay synchronous and I/O-local"
    for name in called:
        for forbidden in ("gemini", "generate", "provider", "retry", "upload", "put_"):
            assert forbidden not in name, f"{name} looks like {forbidden}"


def test_i_capture_only_writes_below_the_configured_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))
    out = mannequin_job._capture_rejected_carrier(
        carrier_bytes=b"x", carrier_mime="image/png", carrier_bgr=None,
        carrier_sha256="a" * 64, source_sha256="b" * 64, job_id="j", candidate="A",
        attempt=1, preflight=_reject_result(),
        preflight_summary=_reject_result().summary(), landmarks={})
    assert Path(out["carrierPath"]).is_relative_to(tmp_path)
    assert Path(out["metadataPath"]).is_relative_to(tmp_path)


# --------------------------------------------------------- event payload shape

def test_the_event_carries_the_origin_and_the_capture_status():
    src = inspect.getsource(mannequin_job._apply_hybrid_composite)
    emit = src.split('"hybrid_carrier_preflight",', 1)[1].split(
        "artifact_capture=qa_capture,", 1)[0]
    assert "artifact_capture=qa_capture," in src   # the boundary itself
    for field in ("reasons=", "reason_details=", "geometry=", "vision=",
                  "carrier_sha256=", "source_sha256=", "candidate=", "attempt="):
        assert field in emit, field


def test_the_event_carries_no_prompt_text_or_signed_url():
    src = inspect.getsource(mannequin_job._apply_hybrid_composite)
    emit = src.split('"hybrid_carrier_preflight",', 1)[1].split(
        "artifact_capture=qa_capture,", 1)[0]
    for leak in ("prompt", "url", "token", "signed", "api_key"):
        assert leak not in emit.lower(), leak
    # the Vision free-text `evidence` field is the actual leak risk: it can carry
    # provider prose and URLs. It is stripped upstream and must not be re-read here.
    assert '.get("evidence")' not in emit and '"evidence":' not in emit
    upstream = inspect.getsource(mannequin_job._apply_hybrid_composite)
    assert 'if key != "evidence"' in upstream, "the strip must still happen upstream"


def test_the_reported_labels_come_from_the_same_reader_the_check_uses():
    check = inspect.getsource(cp._silhouette_reasons)
    reporter = inspect.getsource(cp.silhouette_labels_observed)
    for field in ("shirtSilhouette", "garment_shape", "artifact_defects"):
        assert field in check and field in reporter, field


def test_geometry_observed_exposes_what_the_cape_check_read():
    result = _reject_result()
    geo = result.geometry_observed()
    assert geo["hemToShoulderRatio"] == 1.42
    assert geo["sideEdgeDelta"] == 0.03


# ------------------------------------------------------------ no policy change

def test_no_threshold_moved():
    assert cp.MAX_CAPE_HEM_TO_SHOULDER == 1.35
    assert cp.MAX_SLAB_EDGE_RATIO == 0.08
    assert cp.MIN_SLAB_TORSO_ASPECT == 2.4
    assert cp.MIN_GEOMETRY_CONFIDENCE == 0.62


def test_the_decision_path_is_untouched_by_evidence_capture():
    src = inspect.getsource(mannequin_job._apply_hybrid_composite)
    fail_block = src.split("if not preflight.passed and not soft_continue:", 1)[1][:400]
    assert "carrier_preflight_rejected" in fail_block
    assert "qa_capture" not in fail_block
