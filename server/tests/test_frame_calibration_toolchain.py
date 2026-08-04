import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

from app import frame_calibration as fc, repo

SERVER = pathlib.Path(__file__).resolve().parents[1]
PNG = b"\x89PNG" + b"x" * 64


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, SERVER / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run():
    return fc.run_fingerprint(
        generation_model="gemini-image",
        generation_prompt_version="frame_lock_v2",
        generation_prompt="generate prompt",
        frame_vision_prompt_version="frame_qc_v1",
        frame_vision_prompt="vision prompt",
        code_commit="abc123",
        image_size="1K",
        image_size_cap="1K",
        aspect_ratio="2:3",
        frame_qc_mode="shadow",
        hybrid_composite_mode="off",
        texture_projection_mode="off",
    )


def _row(*, dataset_id="ds", rep=0, machine="pass", image_calls=1):
    prov = fc.provenance(
        run=_run(),
        source_bytes=b"source",
        output_bytes=f"output-{rep}".encode(),
        base_bytes=b"base",
        call_attempt_index=1,
        frame_qc_result={
            "decision": machine,
            "criticalErrors": [],
            "warnings": [],
            "checks": {},
            "metrics": {},
            "visionMeta": {"status": "ok"},
        },
    )
    return fc.sample_row(
        dataset_id=dataset_id,
        arm="goldenset-top",
        project_id="project-1",
        rep=rep,
        source_name="source.png",
        output_name=f"out-{rep}.png",
        base_name="base.png",
        prov=prov,
        image_calls_attempted=image_calls,
    )


def test_env_preflight_requires_1k_shadow_and_single_call_runtime():
    env = {
        "MANNEQUIN_IMAGE_SIZE": "1K",
        "MANNEQUIN_IMAGE_SIZE_CAP": "1K",
        "MANNEQUIN_FRAME_QC": "shadow",
        "MANNEQUIN_HYBRID_COMPOSITE": "off",
        "MANNEQUIN_TEXTURE_PROJECTION_2D": "off",
        "MANNEQUIN_MAX_ATTEMPTS": "1",
        "MANNEQUIN_UNTUCK_PASS": "off",
        "MANNEQUIN_BUST_PASS": "off",
        "MANNEQUIN_AXIS_QC": "shadow",
        "GARMENT_QC_EXTRA_CANDIDATES": "0",
        "GARMENT_QC_MODE": "off",
        "IMAGE_QC": "off",
        "MANNEQUIN_QC_ENABLED": "false",
        "RETRIEVAL_REFIMAGES": "off",
        "MANNEQUIN_STRUCTURED_QC": "off",
        "JOB_DISPATCHER_ENABLED": "false",
        "FRAME_CALIBRATION_INLINE_JOBS": "true",
        "FRAME_CALIBRATION_INLINE_SECRET": "test-frame-secret",
    }
    assert fc.env_preflight(env, require_inline=True) == []
    env["MANNEQUIN_MAX_ATTEMPTS"] = "5"
    env["MANNEQUIN_UNTUCK_PASS"] = "on"
    assert fc.env_preflight(env, require_inline=True) == [
        "max_attempts_not_1", "untuck_pass_not_off"
    ]


def _projection_smoke_env():
    return {
        "MANNEQUIN_IMAGE_SIZE": "1K",
        "MANNEQUIN_IMAGE_SIZE_CAP": "1K",
        "MANNEQUIN_FRAME_QC": "shadow",
        "MANNEQUIN_HYBRID_COMPOSITE": "shadow",
        "MANNEQUIN_TEXTURE_PROJECTION_2D": "shadow",
        "MANNEQUIN_MAX_ATTEMPTS": "1",
        "MANNEQUIN_UNTUCK_PASS": "off",
        "MANNEQUIN_BUST_PASS": "off",
        "MANNEQUIN_AXIS_QC": "off",
        "GARMENT_QC_EXTRA_CANDIDATES": "0",
        "GARMENT_QC_MODE": "off",
        "IMAGE_QC": "off",
        "MANNEQUIN_QC_ENABLED": "false",
        "RETRIEVAL_REFIMAGES": "off",
        "MANNEQUIN_STRUCTURED_QC": "off",
        "JOB_DISPATCHER_ENABLED": "false",
        "FRAME_CALIBRATION_INLINE_JOBS": "true",
        "FRAME_CALIBRATION_INLINE_SECRET": "test-frame-secret",
    }


def test_projection_smoke_preflight_requires_shadow_projection_runtime():
    env = _projection_smoke_env()
    assert fc.projection_smoke_env_preflight(env, require_inline=True) == []

    cases = {
        "MANNEQUIN_HYBRID_COMPOSITE": ("off", "hybrid_composite_not_shadow"),
        "MANNEQUIN_TEXTURE_PROJECTION_2D": ("off", "texture_projection_not_shadow"),
        "MANNEQUIN_FRAME_QC": ("off", "frame_qc_not_shadow"),
        "MANNEQUIN_MAX_ATTEMPTS": ("2", "max_attempts_not_1"),
        "GARMENT_QC_EXTRA_CANDIDATES": ("1", "extra_candidates_not_0"),
    }
    for key, (bad_value, expected_problem) in cases.items():
        broken = dict(env)
        broken[key] = bad_value
        assert expected_problem in fc.projection_smoke_env_preflight(
            broken, require_inline=True
        )


def test_projection_smoke_shape_requires_exactly_one_arm_and_rep():
    collect = _load("frame_collect_projection_shape", "scripts/frame_shadow_collect.py")
    one = [{"arm": "stripe"}]
    assert collect._validate_projection_smoke_shape(one, reps=1) == []
    assert collect._validate_projection_smoke_shape(one, reps=2) == [
        "projection_smoke_requires_one_rep"
    ]
    assert collect._validate_projection_smoke_shape(one * 2, reps=1) == [
        "projection_smoke_requires_one_arm"
    ]


def test_projection_smoke_summary_is_bounded_and_reports_quality():
    collect = _load("frame_collect_projection_summary", "scripts/frame_shadow_collect.py")
    summary = collect._projection_smoke_summary([
        {
            "status": "hybrid_composite_started", "mode": "shadow",
            "pipeline_version": "hc-v1", "signedUrl": "secret",
        },
        {
            "status": "hybrid_texture_projection_plan", "mode": "shadow",
            "ok": True, "targetPeriodPx": 13.2, "targetAxis": "vertical",
            "confidence": 0.9, "version": "tp-v1", "prompt": "secret",
        },
        {
            "status": "hybrid_deterministic_qc", "passed": True,
            "failures": [], "metrics": {
                "period_rel_err_max": 0.03, "outside_drift_frac": 0.0,
                "failure_details": [{"raw": "secret"}], "unknown": 12,
            },
        },
        {
            "status": "hybrid_composite_completed", "outcome": "would_apply",
            "mode": "shadow", "fail_closed": False, "coverage": 0.82,
            "output_hash": "abcd1234", "url": "secret",
        },
    ])

    assert summary["wiringPassed"] is True
    assert summary["qualityPassed"] is True
    assert summary["projection"]["targetPeriodPx"] == 13.2
    assert summary["deterministicQc"]["metrics"] == {
        "period_rel_err_max": 0.03,
        "outside_drift_frac": 0.0,
    }
    assert "secret" not in json.dumps(summary)


def test_projection_smoke_summary_fails_when_projection_never_runs():
    collect = _load("frame_collect_projection_missing", "scripts/frame_shadow_collect.py")
    with pytest.raises(RuntimeError, match="projection_smoke_projection_not_executed"):
        collect._projection_smoke_summary([
            {"status": "hybrid_composite_started", "mode": "shadow"},
            {"status": "hybrid_composite_completed", "outcome": "reference_insufficient"},
        ])


@pytest.mark.parametrize(("key", "value", "problem"), [
    ("IMAGE_QC", "shadow", "image_qc_not_off"),
    ("IMAGE_QC", "enforce", "image_qc_not_off"),
    ("GARMENT_QC_MODE", "bestof", "garment_qc_mode_not_off"),
    ("MANNEQUIN_QC_ENABLED", "true", "legacy_mannequin_qc_enabled"),
    ("RETRIEVAL_REFIMAGES", "on", "retrieval_refimages_not_off"),
    ("JOB_DISPATCHER_ENABLED", "true", "job_dispatcher_not_off"),
    ("FRAME_CALIBRATION_INLINE_JOBS", "false", "inline_jobs_not_enabled"),
    ("FRAME_CALIBRATION_INLINE_SECRET", "", "inline_secret_missing"),
])
def test_frame_only_preflight_blocks_other_qc_and_reference_paths(key, value, problem):
    env = {
        "MANNEQUIN_IMAGE_SIZE": "1K",
        "MANNEQUIN_IMAGE_SIZE_CAP": "1K",
        "MANNEQUIN_FRAME_QC": "shadow",
        "MANNEQUIN_HYBRID_COMPOSITE": "off",
        "MANNEQUIN_TEXTURE_PROJECTION_2D": "off",
        "MANNEQUIN_MAX_ATTEMPTS": "1",
        "MANNEQUIN_UNTUCK_PASS": "off",
        "MANNEQUIN_BUST_PASS": "off",
        "MANNEQUIN_AXIS_QC": "off",
        "GARMENT_QC_EXTRA_CANDIDATES": "0",
        "GARMENT_QC_MODE": "off",
        "IMAGE_QC": "off",
        "MANNEQUIN_QC_ENABLED": "false",
        "RETRIEVAL_REFIMAGES": "off",
        "MANNEQUIN_STRUCTURED_QC": "off",
        "JOB_DISPATCHER_ENABLED": "false",
        "FRAME_CALIBRATION_INLINE_JOBS": "true",
        "FRAME_CALIBRATION_INLINE_SECRET": "test-frame-secret",
    }
    env[key] = value
    assert fc.env_preflight(env, require_inline=True) == [problem]


def test_frame_only_preflight_blocks_structured_qc_candidate_expansion():
    env = {
        "MANNEQUIN_IMAGE_SIZE": "1K",
        "MANNEQUIN_IMAGE_SIZE_CAP": "1K",
        "MANNEQUIN_FRAME_QC": "shadow",
        "MANNEQUIN_HYBRID_COMPOSITE": "off",
        "MANNEQUIN_TEXTURE_PROJECTION_2D": "off",
        "MANNEQUIN_MAX_ATTEMPTS": "1",
        "MANNEQUIN_UNTUCK_PASS": "off",
        "MANNEQUIN_BUST_PASS": "off",
        "MANNEQUIN_AXIS_QC": "shadow",
        "GARMENT_QC_EXTRA_CANDIDATES": "0",
        "MANNEQUIN_STRUCTURED_QC": "shadow",
        # ENABLE_PRODUCT_TRUTH can remain enforce; the block is the policy layer
        # that can turn approved stripe truth into candidateCount=2.
        "ENABLE_PRODUCT_TRUTH": "enforce",
    }
    assert fc.env_preflight(env) == ["structured_qc_not_off"]


def test_success_row_carries_frame_provenance_without_editor_schema():
    row = _row()
    prov = row["provenance"]
    for key in (
        "sourceSha256",
        "outputSha256",
        "baseAssetSha256",
        "callAttemptIndex",
        "frameDeterministicDecision",
        "frameVisionStatus",
        "run",
        "frameQc",
    ):
        assert prov.get(key) is not None
    assert "edit_qc_result" not in row
    assert prov["run"]["frameQcMode"] == "shadow"


def test_resume_refuses_missing_or_changed_provenance(tmp_path):
    path = tmp_path / "samples.jsonl"
    fc.write_jsonl(path, [{"id": "legacy", "output_id": "out"}])
    with pytest.raises(fc.FrameCalibrationError, match="missing_provenance"):
        fc.assert_resumable(path, expected_run=_run())

    changed = _row()
    changed["provenance"]["run"]["generationModel"] = "other"
    fc.write_jsonl(path, [changed])
    with pytest.raises(fc.FrameCalibrationError, match="run_fingerprint_mismatch"):
        fc.assert_resumable(path, expected_run=_run())


def test_manifest_blocks_mixed_or_multi_call_datasets():
    rows = [_row(rep=0), _row(rep=1, image_calls=2)]
    problems = fc.dataset_problems(rows)
    assert "image_calls_not_one_per_sample" in problems
    manifest = fc.manifest_for_rows(dataset_id="ds", rows=rows)
    assert manifest["validForCalibration"] is False


def test_label_chain_is_append_only_and_detects_tamper(tmp_path):
    row = _row()
    record = fc.make_label(
        sample=row,
        reviewer_id="me",
        dataset_id="ds",
        pose_ok=True,
        view_family_ok=True,
        full_body_crop_ok=True,
        framing_ok=True,
        now=1.0,
    )
    path = tmp_path / "labels.jsonl"
    fc.append_label(path, record)
    fc.append_label(path, {**record, "labeledAt": 2.0})
    lines = path.read_text().splitlines()
    first = json.loads(lines[0])
    first["label"]["poseOk"] = False
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(fc.FrameCalibrationError, match="해시 불일치"):
        fc.load_labels(path)


def test_blinded_presentation_hides_machine_decision():
    view = fc.blind_presentation(_row(), seed="x")
    assert set(view) == {"sampleId", "images"}
    assert {i["slot"] for i in view["images"]} == {"A", "B"}
    assert "decision" not in json.dumps(view)


def test_report_allows_enforce_candidate_only_when_critical_false_pass_zero():
    rows = [_row(rep=0), _row(rep=1)]
    labels = [
        fc.make_label(sample=rows[0], reviewer_id="me", dataset_id="ds",
                      pose_ok=True, view_family_ok=True,
                      full_body_crop_ok=True, framing_ok=True, now=1.0),
        fc.make_label(sample=rows[1], reviewer_id="me", dataset_id="ds",
                      pose_ok=False, view_family_ok=True,
                      full_body_crop_ok=True, framing_ok=True, now=2.0),
    ]
    out = fc.report(rows, labels, manifest=fc.manifest_for_rows(dataset_id="ds", rows=rows))
    assert out["enforceReadyCandidate"] is False
    assert out["criticalFalsePassCount"] == 1

    labels[1] = fc.make_label(sample=rows[1], reviewer_id="me", dataset_id="ds",
                              pose_ok=True, view_family_ok=True,
                              full_body_crop_ok=True, framing_ok=True, now=3.0)
    out = fc.report(rows, labels, manifest=fc.manifest_for_rows(dataset_id="ds", rows=rows))
    assert out["enforceReadyCandidate"] is True


def test_collector_preflight_default_makes_zero_provider_calls(tmp_path):
    manifest = tmp_path / "arms.json"
    manifest.write_text(json.dumps({
        "arms": [{"arm": "goldenset-top", "project_id": "p1",
                  "metadata": {"goldenset": True}}]
    }))
    out = tmp_path / "out"
    cmd = [
        sys.executable, str(SERVER / "scripts/frame_shadow_collect.py"),
        "--manifest", str(manifest),
        "--dataset-id", "ds",
        "--out", str(out),
    ]
    env = {
        **dict(),
        "PYTHONPATH": str(SERVER),
        "MANNEQUIN_IMAGE_SIZE": "1K",
        "MANNEQUIN_IMAGE_SIZE_CAP": "1K",
        "MANNEQUIN_FRAME_QC": "shadow",
        "MANNEQUIN_HYBRID_COMPOSITE": "off",
        "MANNEQUIN_TEXTURE_PROJECTION_2D": "off",
        "MANNEQUIN_MAX_ATTEMPTS": "1",
        "MANNEQUIN_UNTUCK_PASS": "off",
        "MANNEQUIN_BUST_PASS": "off",
        "MANNEQUIN_AXIS_QC": "shadow",
        "GARMENT_QC_EXTRA_CANDIDATES": "0",
        "GARMENT_QC_MODE": "off",
        "IMAGE_QC": "off",
        "MANNEQUIN_QC_ENABLED": "false",
        "RETRIEVAL_REFIMAGES": "off",
        "MANNEQUIN_STRUCTURED_QC": "off",
        "SUPABASE_URL": "https://x.supabase.co",
        "JWT_AUDIENCE": "authenticated",
    }
    proc = subprocess.run(cmd, cwd=SERVER, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.returncode == 0, proc.stderr
    assert "provider calls=0" in proc.stdout


def test_prepare_test_truth_is_guarded_to_goldenset_only(tmp_path):
    collect = _load("frame_collect", "scripts/frame_shadow_collect.py")
    arms = [{"arm": "prod-arm", "project_id": "p1", "name": "normal"}]
    problems = collect._prepare_test_truth_guard(arms, actor_id=None)
    assert "prepare_test_truth_missing_actor" in problems
    assert "prepare_test_truth_not_test_arm:prod-arm" in problems


def test_execute_manifest_requires_five_test_arms_and_source_contract():
    collect = _load("frame_collect2", "scripts/frame_shadow_collect.py")
    arms = [{"arm": "a", "project_id": "legacy"}]
    problems = collect._validate_execute_manifest(arms)
    assert "manifest_less_than_5_arms" in problems
    assert "manifest_missing_sourcePath:a" in problems
    assert "manifest_missing_name:a" in problems
    assert "manifest_missing_clothingType:a" in problems
    assert "manifest_not_test_project:a" in problems


def test_execute_manifest_accepts_front_back_detail_source_images(tmp_path):
    collect = _load("frame_collect_source_images", "scripts/frame_shadow_collect.py")
    arms = []
    for i in range(5):
        paths = []
        for slot in ("Front", "Back", "Detail"):
            path = tmp_path / f"{i}-{slot}.heic"
            path.write_bytes(f"{i}-{slot}".encode())
            paths.append({"slot": slot, "path": str(path)})
        arms.append({
            "arm": f"a{i}",
            "sourceImages": paths,
            "name": f"goldenset-{i}",
            "clothingType": "top",
            "metadata": {"goldenset": True},
        })
    assert collect._validate_execute_manifest(arms) == []


def test_one_arm_heic_bundle_is_allowed_only_for_explicit_smoke(tmp_path):
    collect = _load("frame_collect_one_arm_smoke", "scripts/frame_shadow_collect.py")
    images = []
    for slot in ("Front", "Back", "Detail"):
        path = tmp_path / f"stripe-{slot}.heic"
        path.write_bytes(slot.encode())
        images.append({"slot": slot, "path": str(path)})
    arm = {
        "arm": "goldenset-stripe-shirt",
        "sourceImages": images,
        "name": "goldenset-stripe-shirt",
        "clothingType": "top",
        "metadata": {"goldenset": True},
    }
    assert "manifest_less_than_5_arms" in collect._validate_execute_manifest([arm])
    assert collect._validate_execute_manifest([arm], minimum_arms=1) == []
    assert collect._validate_collection_shape([arm], reps=1, smoke=True) == []
    assert collect._validate_collection_shape([arm], reps=3, smoke=True) == [
        "smoke_requires_one_rep"
    ]
    assert collect._validate_collection_shape([arm], reps=1, smoke=False) == [
        "calibration_less_than_5_arms", "calibration_reps_less_than_3"
    ]


def test_calibration_rejects_five_aliases_of_the_same_product(tmp_path):
    collect = _load("frame_collect_duplicate_sources", "scripts/frame_shadow_collect.py")
    source = tmp_path / "same-front.jpg"
    source.write_bytes(b"same")
    arms = [{
        "arm": f"goldenset-alias-{i}",
        "sourcePath": str(source),
        "name": f"goldenset-alias-{i}",
        "clothingType": "top",
        "metadata": {"goldenset": True},
    } for i in range(5)]
    assert collect._validate_collection_shape(arms, reps=3, smoke=False) == [
        "calibration_less_than_5_distinct_sources"
    ]


def test_execute_manifest_requires_complete_source_image_slots(tmp_path):
    collect = _load("frame_collect_source_images_missing", "scripts/frame_shadow_collect.py")
    arms = [{
        "arm": "goldenset-shirt",
        "sourceImages": [{"slot": "Front", "path": str(tmp_path / "front.heic")}],
        "name": "goldenset-shirt",
        "clothingType": "top",
        "metadata": {"goldenset": True},
    }]
    problems = collect._validate_execute_manifest(arms)
    assert "manifest_less_than_5_arms" in problems
    assert "manifest_missing_sourceImageSlot:Back:goldenset-shirt" in problems
    assert "manifest_missing_sourceImageSlot:Detail:goldenset-shirt" in problems
    assert "manifest_source_file_missing:Front:goldenset-shirt" in problems
    assert "manifest_missing_sourcePath:goldenset-shirt" not in problems


def test_execute_manifest_does_not_require_legacy_project_id(tmp_path):
    collect = _load("frame_collect_projectless", "scripts/frame_shadow_collect.py")
    for i in range(5):
        (tmp_path / f"{i}.jpg").write_bytes(b"source")
    arms = [
        {"arm": f"a{i}", "sourcePath": str(tmp_path / f"{i}.jpg"),
         "name": f"goldenset-{i}", "clothingType": "top",
         "metadata": {"goldenset": True}}
        for i in range(5)
    ]
    assert collect._validate_execute_manifest(arms) == []
    assert len(collect._arm_rows(arms, 3)) == 15


def test_prepare_arm_uploads_source_image_bundle_and_records_original_provenance(
        monkeypatch, tmp_path):
    collect = _load("frame_collect_bundle", "scripts/frame_shadow_collect.py")
    src = {}
    for slot in ("Front", "Back", "Detail"):
        path = tmp_path / f"{slot.lower()}.heic"
        path.write_bytes(f"original-{slot}".encode())
        src[slot] = path
    calls = []

    class Response:
        def __init__(self, content=b""):
            self.content = content

        def raise_for_status(self):
            return None

    class Session:
        base_url = "http://api.test"

    class Api:
        c = Session()

        def call(self, method, path, **kw):
            calls.append((method, path, kw.get("json")))
            if path == "/v1/projects":
                return {"id": "project-1"}
            if path == "/v1/assets/upload-url":
                return {
                    "assetId": f"asset-{len([c for c in calls if c[1] == path])}",
                    "uploadUrl": f"http://upload.test/{len(calls)}",
                }
            if path.startswith("/v1/assets/") and path.endswith("/complete"):
                asset_id = path.split("/")[3]
                return {"url": f"/v1/assets/{asset_id}/file"}
            if path == "/v1/projects/project-1/product":
                return {"ok": True}
            if path.endswith("/analyze"):
                return {}
            if path.endswith("/product-truth:draft"):
                return {"id": "truth-1"}
            if path.endswith("/product-truth/truth-1:approve"):
                return {"status": "approved"}
            raise AssertionError(path)

    class PutClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put(self, url, *, content, headers):
            calls.append(("PUT", url, content, headers))
            return Response()

    def convert(path, slot, *, force_jpeg=False):
        assert force_jpeg is True
        return collect._UploadImage(
            slot=slot,
            original_path=path,
            original_bytes=path.read_bytes(),
            original_mime="image/heic",
            upload_name=f"{path.stem}.jpg",
            upload_bytes=f"jpeg-{slot}".encode(),
            uploaded_mime="image/jpeg",
            conversion={"tool": "test", "from": "image/heic", "to": "image/jpeg"},
        )

    monkeypatch.setattr(collect, "_prepare_upload_image", convert)
    monkeypatch.setattr(collect.httpx, "Client", PutClient)
    prepared = __import__("asyncio").run(collect._prepare_arm(
        api=Api(),
        arm={
            "arm": "goldenset-shirt",
            "sourceImages": [{"slot": slot, "path": str(path)}
                             for slot, path in src.items()],
            "name": "goldenset-shirt",
            "clothingType": "top",
            "metadata": {"goldenset": True},
        },
    ))
    uploads = [c for c in calls if c[0] == "PUT"]
    assert [u[2] for u in uploads] == [b"jpeg-Front", b"jpeg-Back", b"jpeg-Detail"]
    assert {u[3]["Content-Type"] for u in uploads} == {"image/jpeg"}
    product = [c for c in calls if c[:2] == ("PATCH", "/v1/projects/project-1/product")][0][2]
    images = product["colors"][0]["images"]
    assert [i["slot"] for i in images] == ["Front", "Back", "Detail"]
    assert prepared["sourceImageBundleSha256"] == fc.sha256_hex(fc.canonical([
        {"slot": slot, "originalSha256": fc.sha256_hex(path.read_bytes())}
        for slot, path in src.items()
    ]))
    prov = prepared["sourceImageProvenance"]
    assert [p["slot"] for p in prov] == ["Front", "Back", "Detail"]
    assert all("path" not in p for p in prov)
    assert {p["originalName"] for p in prov} == {
        "front.heic", "back.heic", "detail.heic"
    }
    assert {p["originalMime"] for p in prov} == {"image/heic"}
    assert {p["uploadedMime"] for p in prov} == {"image/jpeg"}
    assert {p["conversion"]["tool"] for p in prov} == {"test"}


def test_prepare_arm_converts_legacy_heic_source_path_to_jpeg(monkeypatch, tmp_path):
    collect = _load("frame_collect_legacy_heic", "scripts/frame_shadow_collect.py")
    source = tmp_path / "stripe-front.heic"
    source.write_bytes(b"original-heic")
    observed = {}

    class Response:
        content = b""

        def raise_for_status(self):
            return None

    class Session:
        base_url = "http://api.test"

    class Api:
        c = Session()

        def call(self, method, path, **kw):
            if path == "/v1/projects":
                return {"id": "project-1"}
            if path == "/v1/assets/upload-url":
                return {"assetId": "asset-1", "uploadUrl": "http://upload.test/1"}
            if path == "/v1/assets/asset-1/complete":
                return {"url": "/v1/assets/asset-1/file"}
            if path == "/v1/projects/project-1/product":
                return {"ok": True}
            if path.endswith("/analyze"):
                return {}
            if path.endswith("/product-truth:draft"):
                return {"id": "truth-1"}
            if path.endswith("/product-truth/truth-1:approve"):
                return {"status": "approved"}
            raise AssertionError(path)

    class PutClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put(self, url, *, content, headers):
            observed["content"] = content
            observed["content_type"] = headers["Content-Type"]
            return Response()

    def prepare(path, slot, *, force_jpeg=False):
        observed["force_jpeg"] = force_jpeg
        return collect._UploadImage(
            slot=slot,
            original_path=path,
            original_bytes=path.read_bytes(),
            original_mime="image/heic",
            upload_name="stripe-front.jpg",
            upload_bytes=b"jpeg-front",
            uploaded_mime="image/jpeg",
            conversion={"tool": "test", "from": "image/heic", "to": "image/jpeg"},
        )

    monkeypatch.setattr(collect, "_prepare_upload_image", prepare)
    monkeypatch.setattr(collect.httpx, "Client", PutClient)
    __import__("asyncio").run(collect._prepare_arm(
        api=Api(),
        arm={
            "arm": "goldenset-stripe-front",
            "sourcePath": str(source),
            "name": "goldenset-stripe-front",
            "clothingType": "top",
            "metadata": {"goldenset": True},
        },
    ))

    assert observed == {
        "force_jpeg": True,
        "content": b"jpeg-front",
        "content_type": "image/jpeg",
    }


def test_source_upload_retries_one_transport_disconnect_with_identical_request(monkeypatch,
                                                                               tmp_path):
    collect = _load("frame_collect_upload_transport_retry", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"original")
    image = collect._UploadImage(
        slot="Front",
        original_path=source,
        original_bytes=b"original",
        original_mime="image/jpeg",
        upload_name="front.jpg",
        upload_bytes=b"jpeg-payload",
        uploaded_mime="image/jpeg",
        conversion={"tool": "none", "from": "image/jpeg", "to": "image/jpeg"},
    )
    api_calls = []
    put_calls = []

    class Api:
        def call(self, method, path, **kw):
            api_calls.append((method, path, kw.get("json")))
            if path == "/v1/assets/upload-url":
                return {"assetId": "asset-1", "uploadUrl": "https://signed.invalid/private-token"}
            if path == "/v1/assets/asset-1/complete":
                return {"url": "/v1/assets/asset-1/file"}
            raise AssertionError(path)

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

    class PutClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put(self, url, *, content, headers):
            put_calls.append((url, content, dict(headers)))
            if len(put_calls) == 1:
                raise collect.httpx.RemoteProtocolError(
                    "Server disconnected without sending a response"
                )
            return Response()

    monkeypatch.setattr(collect.httpx, "Client", PutClient)
    monkeypatch.setattr(collect.time, "sleep", lambda _seconds: None)

    result = collect._upload_source_image(Api(), project_id="project-1", image=image)

    assert result == {
        "slot": "Front",
        "id": "asset-1",
        "url": "/v1/assets/asset-1/file",
    }
    assert put_calls == [
        ("https://signed.invalid/private-token", b"jpeg-payload", {"Content-Type": "image/jpeg"}),
        ("https://signed.invalid/private-token", b"jpeg-payload", {"Content-Type": "image/jpeg"}),
    ]
    assert [call[:2] for call in api_calls] == [
        ("POST", "/v1/assets/upload-url"),
        ("POST", "/v1/assets/asset-1/complete"),
    ]


def test_source_upload_does_not_retry_http_4xx_or_complete_asset(monkeypatch, tmp_path):
    collect = _load("frame_collect_upload_4xx", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"original")
    image = collect._UploadImage(
        slot="Front",
        original_path=source,
        original_bytes=b"original",
        original_mime="image/jpeg",
        upload_name="front.jpg",
        upload_bytes=b"jpeg-payload",
        uploaded_mime="image/jpeg",
        conversion={"tool": "none", "from": "image/jpeg", "to": "image/jpeg"},
    )
    api_calls = []
    put_count = 0

    class Api:
        def call(self, method, path, **kw):
            api_calls.append((method, path))
            if path == "/v1/assets/upload-url":
                return {"assetId": "asset-1", "uploadUrl": "https://signed.invalid/private-token"}
            raise AssertionError("asset completion must not run after upload rejection")

    class Response:
        status_code = 403

        def raise_for_status(self):
            raise AssertionError("implementation must classify status before exposing httpx URL")

    class PutClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put(self, *args, **kwargs):
            nonlocal put_count
            put_count += 1
            return Response()

    monkeypatch.setattr(collect.httpx, "Client", PutClient)

    with pytest.raises(RuntimeError, match=r"^source_image_upload_failed:http_403$") as exc:
        collect._upload_source_image(Api(), project_id="project-1", image=image)

    assert "signed.invalid" not in str(exc.value)
    assert "private-token" not in str(exc.value)
    assert put_count == 1
    assert api_calls == [("POST", "/v1/assets/upload-url")]


def test_source_upload_retry_exhaustion_is_sanitized_and_never_completes(monkeypatch,
                                                                         tmp_path):
    collect = _load("frame_collect_upload_retry_exhausted", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"original")
    image = collect._UploadImage(
        slot="Front",
        original_path=source,
        original_bytes=b"original",
        original_mime="image/jpeg",
        upload_name="front.jpg",
        upload_bytes=b"jpeg-payload",
        uploaded_mime="image/jpeg",
        conversion={"tool": "none", "from": "image/jpeg", "to": "image/jpeg"},
    )
    api_calls = []
    put_count = 0

    class Api:
        def call(self, method, path, **kw):
            api_calls.append((method, path))
            if path == "/v1/assets/upload-url":
                return {"assetId": "asset-1", "uploadUrl": "https://signed.invalid/private-token"}
            raise AssertionError("asset completion must not run after upload failure")

    class PutClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put(self, *args, **kwargs):
            nonlocal put_count
            put_count += 1
            raise collect.httpx.RemoteProtocolError(
                "Server disconnected at https://signed.invalid/private-token"
            )

    monkeypatch.setattr(collect.httpx, "Client", PutClient)
    monkeypatch.setattr(collect.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match=r"^source_image_upload_failed:transport$") as exc:
        collect._upload_source_image(Api(), project_id="project-1", image=image)

    assert "signed.invalid" not in str(exc.value)
    assert "private-token" not in str(exc.value)
    assert put_count == 2
    assert api_calls == [("POST", "/v1/assets/upload-url")]


def test_prepare_arm_uses_route_preclaimed_analysis_lease(monkeypatch, tmp_path):
    collect = _load("frame_collect_preclaimed_analysis", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"source")
    calls = []

    class Api:
        def call(self, method, path, **kw):
            calls.append((method, path, kw.get("headers")))
            if path == "/v1/projects":
                return {"id": "project-1"}
            if path == "/v1/projects/project-1/product":
                return {"ok": True}
            if path.endswith("/analyze"):
                return {"jobId": "analysis-job", "leaseToken": "frame-calibration:lease"}
            if path.endswith("/product-truth:draft"):
                return {"id": "truth-1"}
            if path.endswith("/product-truth/truth-1:approve"):
                return {"status": "approved"}
            raise AssertionError(path)

        def poll_job(self, job_id, timeout_s=0):
            assert job_id == "analysis-job"
            return {"status": "done"}

    class Worker:
        async def run_preclaimed(self, job_id, lease_token):
            calls.append(("RUN_PRECLAIMED", job_id, lease_token))
            return "claimed"

        async def claim_and_run(self, job_id):
            raise AssertionError("preclaimed calibration must never enter pending claim path")

    monkeypatch.setenv("FRAME_CALIBRATION_INLINE_SECRET", "test-frame-secret")
    monkeypatch.setattr(
        collect,
        "_upload_source_image",
        lambda api, project_id, image: {
            "slot": image.slot, "id": "asset-1", "url": "/v1/assets/asset-1/file"
        },
    )
    __import__("asyncio").run(collect._prepare_arm(
        api=Api(),
        worker=Worker(),
        arm={
            "arm": "goldenset-shirt",
            "sourcePath": str(source),
            "name": "goldenset-shirt",
            "clothingType": "top",
            "metadata": {"goldenset": True},
        },
    ))

    analyze_call = next(call for call in calls if call[1].endswith("/analyze"))
    assert analyze_call[2] == {
        "X-Wearless-Frame-Calibration": "test-frame-secret"
    }
    assert ("RUN_PRECLAIMED", "analysis-job", "frame-calibration:lease") in calls


def test_generate_sample_uses_route_preclaimed_mannequin_lease(monkeypatch, tmp_path):
    collect = _load("frame_collect_preclaimed_mannequin", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"source")
    calls = []
    events = [
        {"status": "prompt_rendered"},
        {"status": "frame_qc", "phase": "pre", "decision": "pass"},
    ]

    class Api:
        def call(self, method, path, **kw):
            calls.append((method, path, kw.get("headers")))
            assert path == "/v1/projects/project-1/mannequins:generate"
            return {"jobId": "mannequin-job", "leaseToken": "frame-calibration:lease"}

        def poll_job(self, job_id, timeout_s=0):
            assert job_id == "mannequin-job"
            return {
                "status": "done",
                "result": {"data": [{"src": "/v1/assets/out/file"}]},
                "steps": events,
            }

    class R2:
        def get_bytes(self, key):
            return b"base-image"

    class Worker:
        app = type("App", (), {"state": type("State", (), {"r2": R2()})()})()

        async def run_preclaimed(self, job_id, lease_token):
            calls.append(("RUN_PRECLAIMED", job_id, lease_token))
            return "claimed"

        async def claim_and_run(self, job_id):
            raise AssertionError("preclaimed calibration must never enter pending claim path")

        async def job_events(self, job_id):
            return events

    monkeypatch.setenv("FRAME_CALIBRATION_INLINE_SECRET", "test-frame-secret")
    monkeypatch.setattr(collect, "_fetch_output_bytes", lambda api, src: PNG)
    run_dir = tmp_path / "missing" / "dataset"
    row = __import__("asyncio").run(collect._generate_sample(
        api=Api(),
        worker=Worker(),
        run_dir=run_dir,
        dataset_id="ds",
        prepared={
            "arm": {"arm": "goldenset-shirt", "targetGender": "women"},
            "projectId": "project-1",
            "sourcePath": source,
            "sourceBytes": b"source",
        },
        rep=0,
        expected_run=_run(),
    ))

    assert row["imageCallsAttempted"] == 1
    assert (run_dir / row["outputImage"]).read_bytes() == PNG
    generate_call = next(call for call in calls if call[1].endswith("mannequins:generate"))
    assert generate_call[2] == {
        "X-Wearless-Frame-Calibration": "test-frame-secret"
    }
    assert ("RUN_PRECLAIMED", "mannequin-job", "frame-calibration:lease") in calls


def test_repo_inline_preclaim_and_owned_lookup_keep_the_same_lease():
    executed = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql, params):
            executed.append((" ".join(sql.split()), params))

        async def fetchone(self):
            return {
                "id": "job-1",
                "status": "running",
                "lease_token": "frame-calibration:lease",
            }

    class Conn:
        def cursor(self):
            return Cursor()

    claimed = __import__("asyncio").run(repo.preclaim_job_for_inline_execution(
        Conn(), job_id="job-1", lease_token="frame-calibration:lease"
    ))
    owned = __import__("asyncio").run(repo.get_owned_running_job(
        Conn(), job_id="job-1", lease_token="frame-calibration:lease"
    ))

    assert claimed["lease_token"] == owned["lease_token"] == "frame-calibration:lease"
    assert "where id = %s and status = 'pending'" in executed[0][0]
    assert executed[0][1] == ("frame-calibration:lease", "job-1")
    assert "status = 'running' and locked_by = %s" in executed[1][0]
    assert executed[1][1] == ("job-1", "frame-calibration:lease")


def test_recover_sample_materializes_completed_job_without_provider_or_post(
        monkeypatch, tmp_path):
    collect = _load("frame_collect_recover_sample", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"source")
    calls = []
    events = [
        {"status": "prompt_rendered"},
        {"status": "frame_qc", "phase": "pre", "decision": "pass"},
    ]

    class Api:
        def call(self, method, path, **kw):
            calls.append((method, path))
            assert (method, path) == ("GET", "/v1/jobs/job-1")
            return {
                "id": "job-1",
                "projectId": "project-1",
                "kind": "mannequin",
                "status": "done",
                "result": {"data": [{"src": "/v1/assets/out/file"}]},
                "steps": events,
            }

    class R2:
        def get_bytes(self, key):
            return b"base-image"

    class Worker:
        app = type("App", (), {"state": type("State", (), {"r2": R2()})()})()

        async def job_events(self, job_id):
            assert job_id == "job-1"
            return events

    monkeypatch.setattr(collect, "_fetch_output_bytes", lambda api, src: PNG)
    run_dir = tmp_path / "recovered" / "ds"
    prepared = {
        "arm": {"arm": "goldenset-shirt", "name": "stripe", "targetGender": "women"},
        "projectId": "project-1",
        "sourcePath": source,
        "sourceBytes": b"source",
        "sourceImages": [],
        "sourceImageProvenance": [{"slot": "Front", "originalSha256": "abc"}],
    }
    row = __import__("asyncio").run(collect._recover_sample(
        api=Api(),
        worker=Worker(),
        run_dir=run_dir,
        dataset_id="ds",
        prepared=prepared,
        rep=0,
        job_id="job-1",
        expected_run=_run(),
    ))

    assert calls == [("GET", "/v1/jobs/job-1")]
    assert row["projectId"] == "project-1"
    assert row["imageCallsAttempted"] == 1
    assert row["provenance"]["sourceImages"][0]["slot"] == "Front"
    assert (run_dir / row["outputImage"]).read_bytes() == PNG


def test_collect_one_uses_http_truth_and_inline_generation_without_dispatcher(monkeypatch, tmp_path):
    collect = _load("frame_collect3", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"source-image")
    calls = []

    class Response:
        content = PNG

        def raise_for_status(self):
            return None

    class Session:
        base_url = "http://api.test"

        def get(self, path):
            calls.append(("GET-BYTES", path))
            return Response()

    class Api:
        c = Session()

        def call(self, method, path, **kw):
            calls.append((method, path, kw.get("json")))
            if path == "/v1/projects":
                return {"id": "project-1"}
            if path == "/v1/assets/upload-url":
                return {"assetId": "asset-1", "uploadUrl": "http://upload.test/put"}
            if path == "/v1/assets/asset-1/complete":
                return {"url": "/v1/assets/asset-1/file"}
            if path == "/v1/projects/project-1/product":
                return {"ok": True}
            if path.endswith("/analyze"):
                return {"jobId": "analysis-job"}
            if path.endswith("/product-truth:draft"):
                return {"id": "truth-1"}
            if path.endswith("/product-truth/truth-1:approve"):
                return {"status": "approved"}
            if path.endswith("/mannequins:generate"):
                return {"jobId": "mannequin-job"}
            raise AssertionError(path)

        def poll_job(self, job_id, timeout_s=0):
            calls.append(("POLL", job_id, timeout_s))
            if job_id == "analysis-job":
                return {"status": "done"}
            return {
                "status": "done",
                "result": {"data": [{"src": "/v1/assets/out-1/file"}]},
                "steps": [{
                    "status": "frame_qc",
                    "phase": "pre",
                    "decision": "pass",
                    "criticalErrors": [],
                    "warnings": [],
                    "checks": {"viewFamily": "ok"},
                    "visionMeta": {"status": "ok"},
                }],
            }

    class R2:
        def get_bytes(self, key):
            calls.append(("R2", key))
            return b"base-image"

    class Worker:
        app = type("App", (), {"state": type("State", (), {"r2": R2()})()})()

        async def claim_and_run(self, job_id):
            calls.append(("CLAIM", job_id))
            return "claimed"

        async def job_events(self, job_id):
            return [{"status": "prompt_rendered"},
                    {"status": "generated"},
                    {"status": "frame_qc", "phase": "pre", "decision": "pass",
                     "criticalErrors": [], "warnings": [],
                     "checks": {"viewFamily": "ok"}, "visionMeta": {"status": "ok"}}]

    class PutClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put(self, url, *, content, headers):
            calls.append(("PUT", url, content, headers))
            return Response()

        def get(self, url):
            calls.append(("FOLLOW-GET", url))
            return Response()

    monkeypatch.setattr(collect.httpx, "Client", PutClient)
    row = __import__("asyncio").run(collect._collect_one(
        api=Api(),
        worker=Worker(),
        run_dir=tmp_path,
        dataset_id="ds",
        arm={"arm": "goldenset-shirt", "sourcePath": str(source),
             "name": "goldenset-shirt", "clothingType": "top",
             "metadata": {"goldenset": True}},
        rep=0,
        expected_run=_run(),
    ))
    assert row["imageCallsAttempted"] == 1
    assert row["testProject"] is True
    assert row["provenance"]["sourceSha256"] == fc.sha256_hex(b"source-image")
    assert row["provenance"]["outputSha256"] == fc.sha256_hex(PNG)
    assert row["provenance"]["baseAssetSha256"] == fc.sha256_hex(b"base-image")
    assert row["provenance"]["frameDeterministicDecision"] == "pass"
    assert ("CLAIM", "mannequin-job") in calls
    assert (tmp_path / "goldenset-shirt_rep0.png").read_bytes() == PNG
    assert [c[:2] for c in calls if c[0] in ("POST", "PATCH")] == [
        ("POST", "/v1/projects"),
        ("POST", "/v1/assets/upload-url"),
        ("POST", "/v1/assets/asset-1/complete"),
        ("PATCH", "/v1/projects/project-1/product"),
        ("POST", "/v1/projects/project-1/analyze"),
        ("POST", "/v1/projects/project-1/product-truth:draft"),
        ("POST", "/v1/projects/project-1/product-truth/truth-1:approve"),
        ("POST", "/v1/projects/project-1/mannequins:generate"),
    ]


def test_prepare_arm_once_then_generate_each_rep(monkeypatch, tmp_path):
    collect = _load("frame_collect_prepare_once", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"source-image")
    calls = []

    class Response:
        content = PNG

        def raise_for_status(self):
            return None

    class Session:
        base_url = "http://api.test"

        def get(self, path):
            return Response()

    class Api:
        c = Session()

        def call(self, method, path, **kw):
            calls.append((method, path))
            if path == "/v1/projects":
                return {"id": "project-1"}
            if path == "/v1/assets/upload-url":
                return {"assetId": "asset-1", "uploadUrl": "http://upload.test/put"}
            if path == "/v1/assets/asset-1/complete":
                return {"url": "/v1/assets/asset-1/file"}
            if path == "/v1/projects/project-1/product":
                return {"ok": True}
            if path.endswith("/analyze"):
                return {"jobId": "analysis-job"}
            if path.endswith("/product-truth:draft"):
                return {"id": "truth-1"}
            if path.endswith("/product-truth/truth-1:approve"):
                return {"status": "approved"}
            if path.endswith("/mannequins:generate"):
                return {"jobId": f"job-{sum(1 for c in calls if c[1].endswith('/mannequins:generate'))}"}
            raise AssertionError(path)

        def poll_job(self, job_id, timeout_s=0):
            if job_id == "analysis-job":
                return {"status": "done"}
            return {"status": "done", "result": {"data": [{"src": "/v1/assets/out/file"}]},
                    "steps": [{"status": "prompt_rendered"},
                              {"status": "generated"},
                              {"status": "frame_qc", "phase": "pre", "decision": "pass"}]}

    class R2:
        def get_bytes(self, key):
            return b"base-image"

    class Worker:
        app = type("App", (), {"state": type("State", (), {"r2": R2()})()})()

        async def claim_and_run(self, job_id):
            calls.append(("CLAIM", job_id))
            return "claimed"

        async def job_events(self, job_id):
            return [{"status": "prompt_rendered"},
                    {"status": "generated"},
                    {"status": "frame_qc", "phase": "pre", "decision": "pass"}]

    class PutClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put(self, *a, **kw):
            return Response()

        def get(self, *a, **kw):
            return Response()

    monkeypatch.setattr(collect.httpx, "Client", PutClient)
    api, worker = Api(), Worker()
    arm = {"arm": "goldenset-shirt", "sourcePath": str(source),
           "name": "goldenset-shirt", "clothingType": "top",
           "metadata": {"goldenset": True}}
    prepared = __import__("asyncio").run(
        collect._prepare_arm(api=api, worker=worker, arm=arm)
    )
    for rep in range(3):
        __import__("asyncio").run(collect._generate_sample(
            api=api, worker=worker, run_dir=tmp_path, dataset_id="ds",
            prepared=prepared, rep=rep, expected_run=_run()))
    assert len([c for c in calls if c[1] == "/v1/projects"]) == 1
    assert len([c for c in calls if c[1].endswith("/analyze")]) == 1
    assert len([c for c in calls if c[1].endswith("/product-truth:draft")]) == 1
    assert len([c for c in calls if c[1].endswith("/mannequins:generate")]) == 3
    assert calls.count(("CLAIM", "analysis-job")) == 1


def test_prepare_arm_refuses_when_analysis_job_is_stolen(monkeypatch, tmp_path):
    collect = _load("frame_collect_analysis_stolen", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"source-image")
    truth_calls = []

    class Api:
        def call(self, method, path, **kw):
            if path == "/v1/projects":
                return {"id": "project-1"}
            if path == "/v1/projects/project-1/product":
                return {"ok": True}
            if path.endswith("/analyze"):
                return {"jobId": "analysis-job"}
            if "product-truth" in path:
                truth_calls.append(path)
                return {"id": "truth-1"}
            raise AssertionError(path)

    class Worker:
        async def claim_and_run(self, job_id):
            assert job_id == "analysis-job"
            return "stolen"

    monkeypatch.setattr(
        collect,
        "_upload_source_image",
        lambda api, project_id, image: {
            "slot": image.slot, "id": "asset-1", "url": "/v1/assets/asset-1/file"
        },
    )
    arm = {
        "arm": "goldenset-shirt",
        "sourcePath": str(source),
        "name": "goldenset-shirt",
        "clothingType": "top",
        "metadata": {"goldenset": True},
    }
    with pytest.raises(RuntimeError, match="analysis_job_not_inline_claimed"):
        __import__("asyncio").run(
            collect._prepare_arm(api=Api(), worker=Worker(), arm=arm)
        )
    assert truth_calls == []


def test_existing_resume_rows_are_skipped_and_preserved(monkeypatch, tmp_path):
    collect = _load("frame_collect_resume_skip", "scripts/frame_shadow_collect.py")
    source = tmp_path / "front.jpg"
    source.write_bytes(b"source-image")
    arms = [{"arm": "goldenset-shirt", "sourcePath": str(source),
             "name": "goldenset-shirt", "clothingType": "top",
             "metadata": {"goldenset": True}}]
    run_dir = tmp_path / "ds"
    existing = _row(rep=0)
    existing["arm"] = "goldenset-shirt"
    fc.write_jsonl(run_dir / "samples.jsonl", [existing])
    calls = []

    async def fake_generate(**kw):
        calls.append(kw["rep"])
        row = _row(rep=kw["rep"])
        row["arm"] = "goldenset-shirt"
        return row

    async def fake_prepare(**kw):
        return {"arm": arms[0], "projectId": "p"}

    monkeypatch.setattr(collect, "_prepare_arm", fake_prepare)
    monkeypatch.setattr(collect, "_generate_sample", fake_generate)

    class Worker:
        async def open(self):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(collect, "ensure_smoke_session", lambda: "tok")
    monkeypatch.setattr(collect, "Api", lambda base, token: object())
    monkeypatch.setattr(collect, "InlineWorker", lambda: Worker())
    rows = __import__("asyncio").run(collect._execute_collection(
        api_base="http://api", run_dir=run_dir, dataset_id="ds",
        arms=arms, reps=3, expected_run=_run(), existing_rows=[existing]))
    assert calls == [1, 2]
    assert len(rows) == 3
    assert fc.load_jsonl(run_dir / "samples.jsonl")[0]["id"] == existing["id"]


def test_image_call_count_is_measured_from_job_steps():
    collect = _load("frame_collect_calls", "scripts/frame_shadow_collect.py")
    assert collect._image_calls_attempted({"steps": [
        {"status": "prompt_rendered"}, {"status": "generated"}]}) == 1
    assert collect._image_calls_attempted({"steps": [
        {"status": "prompt_rendered"}, {"status": "generated"},
        {"status": "untuck_pass"}]}) == 2


def test_frame_qc_is_read_from_job_events_when_job_steps_are_empty():
    collect = _load("frame_collect_events", "scripts/frame_shadow_collect.py")
    job = {"steps": []}
    events = [{"status": "prompt_rendered"}, {"status": "generated"},
              {"status": "frame_qc", "phase": "pre", "decision": "pass",
               "criticalErrors": [], "warnings": ["w"],
               "checks": {"yaw": "ok"}, "visionMeta": {"status": "ok"}}]
    assert collect._image_calls_attempted(job, events) == 1
    assert collect._latest_frame_qc(events)["decision"] == "pass"


def test_output_fetch_follows_redirect_and_requires_image_magic(monkeypatch):
    collect = _load("frame_collect_output_fetch", "scripts/frame_shadow_collect.py")
    seen = {}

    class Api:
        c = type("Session", (), {"base_url": "http://api.test"})()

    class Client:
        def __init__(self, **kw):
            seen.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            seen["url"] = url
            return type("Resp", (), {
                "content": PNG,
                "raise_for_status": lambda self: None,
            })()

    monkeypatch.setattr(collect.httpx, "Client", Client)
    assert collect._fetch_output_bytes(Api(), "/v1/assets/a/file") == PNG
    assert seen["follow_redirects"] is True
    assert seen["url"] == "http://api.test/v1/assets/a/file"


def test_expected_run_hashes_actual_loaded_prompt_template(monkeypatch):
    collect = _load("frame_collect_prompt_hash", "scripts/frame_shadow_collect.py")

    class Settings:
        model_image_high = "m"
        model_image_light = "ml"
        mannequin_prompt_version = "frame_lock_v2"
        mannequin_image_size = "1K"
        mannequin_image_size_cap = "1K"
        mannequin_aspect_ratio = "2:3"
        mannequin_frame_qc = "shadow"
        mannequin_hybrid_composite = "off"
        mannequin_texture_projection_2d = "off"

    monkeypatch.setattr(collect, "load_prompt_template", lambda settings: "ACTUAL TEMPLATE")
    monkeypatch.setattr(collect, "_git_sha", lambda: "abc")
    out = collect._expected_run(Settings())
    assert out["generationPromptSha256"] == fc.sha256_hex(b"ACTUAL TEMPLATE")
    assert out["generationPromptSha256"] != fc.sha256_hex(b"builtin:frame_lock_v2")
