"""Phase 3 P0-C 9/N — artifact/binding 최종 fail-closed.

manifest 는 "이 표본·이 파일들"에 대한 진술이다. 진술을 만든 뒤 파일이 바뀌면
그 진술은 과거에 대한 것이고, 진술의 필드가 비어 있으면 비교 자체가 생략된다.
둘 다 통과시키고 있었다.
"""

import copy
import hashlib
import importlib.util
import json
import pathlib
import subprocess

import pytest

from app import safe_paths, shadow_cases as scases, shadow_provenance as sp
from app.config import load_settings
from app.safe_paths import UnsafePath, UnsafePathReason
from app import shadow_verification as _sv

SERVER = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = SERVER.parent / "public" / "assets" / "fit-examples"


def _mod(name, rel):
    spec = importlib.util.spec_from_file_location(name, SERVER / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = _mod("sc_ab", "scripts/shadow_collect.py")
SM = _mod("sm_ab", "scripts/shadow_manifest.py")


@pytest.fixture
def ds(tmp_path):
    """실제 파일 + samples + manifest + labels 가 갖춰진 데이터셋."""
    from app import blinded_audit as ba
    s = load_settings()
    src = sorted(SRC_DIR.glob("*.jpg"))[0]
    raw = src.read_bytes()
    rows = []
    for i, (name, ch) in enumerate(scases.VARY_CASES[:3]):
        prep = scases.generation_prepared(s, ch)
        vp = scases.vision_prepared(ch)
        out = b"PNG-" + bytes([i])
        (tmp_path / f"{name}.png").write_bytes(out)
        rows.append({"id": name, "output_id": f"o{i}", "case": name,
                     "source": src.name, "source_kind": "editor_asset",
                     "edit_type": SC.editor_vary.edit_type_for(ch),
                     "image_calls": 1, "vision_calls": 1,
                     "edit_qc_result": {"decision": "pass",
                                        "vision": {"meta": {"status": "ok"}}},
                     "provenance": SC._provenance(
                         prep, case_name=name, changes=ch, attempt=1,
                         source_bytes=raw, output_bytes=out,
                         vision_meta={"promptSha256": vp.prompt_sha256,
                                      "provider": "p", "status": "ok"})})
    (tmp_path / "samples.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    m = SM.build(str(tmp_path / "samples.jsonl"), dataset_id="ds", invalid_reasons=[],
                 image_usd=0, vision_usd=0, collected_at="t", command=None)
    (tmp_path / "manifest.json").write_text(json.dumps(m, ensure_ascii=False))
    for r, lab in zip(rows, ("fidelity_pass", "fidelity_fail", "fidelity_pass")):
        ba.append_label(str(tmp_path / "labels.jsonl"),
                        ba.make_label(sample=r, label=lab, reviewer_id="t",
                                      dataset_id="ds"))
    return tmp_path, rows, m


def report(tmp, *, manifest=True, labels=True, dataset_id="ds"):
    cmd = [".venv/bin/python", "scripts/shadow_report.py", "--jsonl",
           str(tmp / "samples.jsonl"), "--json"]
    if manifest:
        cmd += ["--manifest", str(tmp / "manifest.json")]
    if labels:
        cmd += ["--labels", str(tmp / "labels.jsonl")]
    if dataset_id:
        cmd += ["--dataset-id", dataset_id]
    r = subprocess.run(cmd, cwd=str(SERVER), capture_output=True, text=True)
    out = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    ev = out.get("pipelines", {}).get("editor_vary", {})
    ready = {ev.get("verdict", {}).get("enforceReady")} | {
        t["verdict"]["enforceReady"] for t in ev.get("byEditTypeDetail", {}).values()}
    return r.returncode, out, ready


def assert_blocked(code, out, ready, *, contains=None):
    assert code != 0
    assert out.get("calibrationUsable") is False
    assert ready == {False}
    if contains:
        assert any(contains in r for r in out.get("calibrationBlockedReasons") or []), \
            out.get("calibrationBlockedReasons")


# ── 2. typed safe-path reason ─────────────────────────────────────────────

def test_unsafe_path_carries_a_typed_reason(tmp_path):
    (tmp_path / "ok.png").write_bytes(b"x")
    with pytest.raises(UnsafePath) as e:
        safe_paths.safe_resolve(tmp_path, "../../etc/hosts", safe_paths.SAFE_ID)
    assert e.value.reason is UnsafePathReason.INVALID_NAME


def test_a_missing_file_reason_differs_from_an_unsafe_one(tmp_path):
    with pytest.raises(UnsafePath) as e:
        safe_paths.safe_resolve(tmp_path, "nope", safe_paths.SAFE_ID, suffix=".png")
    assert e.value.reason is UnsafePathReason.NOT_REGULAR_FILE


def test_a_symlink_out_of_base_is_outside_base(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("out") / "secret.txt"
    outside.write_bytes(b"S")
    (tmp_path / "link.png").symlink_to(outside)
    with pytest.raises(UnsafePath) as e:
        safe_paths.safe_resolve(tmp_path, "link", safe_paths.SAFE_ID, suffix=".png")
    assert e.value.reason is UnsafePathReason.OUTSIDE_BASE


def test_the_message_carries_no_path(tmp_path):
    with pytest.raises(UnsafePath) as e:
        safe_paths.safe_resolve(tmp_path, "/etc/hosts", safe_paths.SAFE_FILENAME)
    assert str(tmp_path) not in str(e.value) and "etc" not in str(e.value)


def test_no_caller_parses_the_message_string():
    for rel in ("app/shadow_provenance.py", "scripts/blinded_label.py"):
        src = (SERVER / rel).read_text(encoding="utf-8")
        assert '"regular file" in str(e)' not in src, rel


# ── 1. dataset checksum 경로 봉쇄 ─────────────────────────────────────────

def _escape_name():
    depth = len(SRC_DIR.resolve().parts) - 1
    return "/".join([".."] * depth) + "/etc/hosts"


def test_a_traversal_source_is_never_read(ds, monkeypatch):
    """artifact_problems 가 거부한 source 를 checksum 이 그 뒤에서 읽고 있었다."""
    tmp, rows, _ = ds
    reads = []
    real = safe_paths.file_sha256
    monkeypatch.setattr(safe_paths, "file_sha256",
                        lambda p: (reads.append(str(p)), real(p))[1])
    orig = pathlib.Path.read_bytes
    monkeypatch.setattr(pathlib.Path, "read_bytes",
                        lambda self, *a, **k: (reads.append(str(self)), orig(self))[1])
    bad = copy.deepcopy(rows)
    bad[0]["source"] = _escape_name()
    (tmp / "samples.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in bad))
    m = SM.build(str(tmp / "samples.jsonl"), dataset_id="ds", invalid_reasons=[],
                 image_usd=0, vision_usd=0, collected_at="t", command=None)
    assert not [r for r in reads if "etc/hosts" in r]
    assert m["validForCalibration"] is False
    assert "unsafe_source_path" in m["provenanceProblems"]


@pytest.mark.parametrize("bad", ["/etc/hosts", "a/b", "x\x00", ".."])
def test_unsafe_source_names_yield_a_null_checksum(ds, bad):
    tmp, rows, _ = ds
    r = copy.deepcopy(rows)
    r[0]["source"] = bad
    (tmp / "samples.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in r))
    m = SM.build(str(tmp / "samples.jsonl"), dataset_id="ds", invalid_reasons=[],
                 image_usd=0, vision_usd=0, collected_at="t", command=None)
    assert m["sourceDataset"]["sha256"] is None      # 일부만 넣은 체크섬은 없다
    assert m["validForCalibration"] is False


def test_the_manifest_never_joins_paths_by_hand():
    src = (SERVER / "scripts" / "shadow_manifest.py").read_text(encoding="utf-8")
    assert "base / name" not in src
    assert 'base / f"{r.get(\'id\')}.png"' not in src
    assert "safe_resolve" in src or "sp.source_bundle_sha256" in src


def test_bundles_are_computed_from_real_files(ds):
    tmp, rows, m = ds
    assert m["outputBundleSha256"] == sp.output_bundle_sha256(rows, tmp)
    assert m["sourceDataset"]["sha256"] == sp.source_bundle_sha256(rows, SRC_DIR)
    (tmp / f"{rows[0]['id']}.png").write_bytes(b"CHANGED")
    assert sp.output_bundle_sha256(rows, tmp) != m["outputBundleSha256"]


# ── 5. provenance SHA 전체 형식 검증 ──────────────────────────────────────

SHA_FIELDS = [("row", "sourceSha256"), ("row", "outputSha256"),
              ("run", "generationTemplateSha256"), ("run", "visionTemplateSha256"),
              ("run", "caseSetSha256"),
              ("case", "generationPromptSha256"), ("case", "visionPromptSha256")]


@pytest.mark.parametrize("where,field", SHA_FIELDS)
@pytest.mark.parametrize("bad", ["ab", "zz" * 32, "A" * 64, 12345, "0" * 63])
def test_every_canonical_sha_is_format_checked(ds, where, field, bad):
    tmp, rows, _ = ds
    r = copy.deepcopy(rows)
    target = (r[0]["provenance"] if where == "row"
              else r[0]["provenance"][where])
    target[field] = bad
    problems = sp.validate_dataset(r)
    key = field if where == "row" else f"{where}.{field}"
    assert f"invalid_sha_format:{key}" in problems, problems


@pytest.mark.parametrize("where,field", SHA_FIELDS)
def test_a_valid_lowercase_hex_passes(ds, where, field):
    tmp, rows, _ = ds
    r = copy.deepcopy(rows)
    target = (r[0]["provenance"] if where == "row" else r[0]["provenance"][where])
    target[field] = "a" * 64
    problems = sp.validate_dataset(r)
    key = field if where == "row" else f"{where}.{field}"
    assert f"invalid_sha_format:{key}" not in problems


def test_format_checks_do_not_crash(ds):
    tmp, rows, _ = ds
    r = copy.deepcopy(rows)
    r[0]["provenance"]["outputSha256"] = {"nested": "junk"}
    assert sp.validate_dataset(r)          # 예외 없이 문제 목록


# ── 4. manifest 필수 schema ───────────────────────────────────────────────

@pytest.mark.parametrize("manifest,code", [
    ({}, "manifest_binding_missing:datasetId"),
    ({"validForCalibration": True}, "manifest_binding_missing:rawSampleManifestSha256"),
    (None, "manifest_absent"),
])
def test_a_malformed_manifest_is_refused(manifest, code):
    assert code in sp.manifest_binding_problems(manifest)


def test_binding_requires_every_declared_field():
    good = {"datasetId": "ds", "rawSampleManifestSha256": "a" * 64,
            "outputBundleSha256": "b" * 64,
            "sourceDataset": {"sha256": "c" * 64},
            "validForCalibration": True, "provenanceUnverified": False,
            "provenanceProblems": []}
    assert sp.manifest_binding_problems(good) == []
    for field in ("datasetId", "rawSampleManifestSha256", "outputBundleSha256",
                  "sourceDataset", "validForCalibration", "provenanceUnverified",
                  "provenanceProblems"):
        bad = {k: v for k, v in good.items() if k != field}
        assert any(field in p for p in sp.manifest_binding_problems(bad)), field


@pytest.mark.parametrize("field,value", [
    ("datasetId", ""), ("datasetId", 5), ("datasetId", "../x"),
    ("rawSampleManifestSha256", "zz"), ("outputBundleSha256", "A" * 64),
    ("validForCalibration", "yes"), ("provenanceUnverified", 1),
    ("provenanceProblems", "none"),
])
def test_invalid_binding_types_are_refused(field, value):
    good = {"datasetId": "ds", "rawSampleManifestSha256": "a" * 64,
            "outputBundleSha256": "b" * 64,
            "sourceDataset": {"sha256": "c" * 64},
            "validForCalibration": True, "provenanceUnverified": False,
            "provenanceProblems": []}
    problems = sp.manifest_binding_problems({**good, field: value})
    assert any(f"manifest_binding_invalid:{field}" in p for p in problems), problems


def test_output_bundle_is_only_required_when_outputs_exist():
    m = {"datasetId": "ds", "rawSampleManifestSha256": "a" * 64,
         "sourceDataset": {"sha256": "c" * 64}, "validForCalibration": False,
         "provenanceUnverified": True, "provenanceProblems": []}
    assert sp.manifest_binding_problems(m, has_output_rows=False) == []
    assert "manifest_binding_missing:outputBundleSha256" in \
        sp.manifest_binding_problems(m, has_output_rows=True)


def test_a_non_dict_manifest_is_refused_by_the_verifier(tmp_path):
    """`if manifest:` 로 판단하면 `{}` 가 falsy 라 검사 자체가 생략된다."""
    from app import shadow_verification as _sv2
    for bad in ({}, [1], "x", 42):
        v = _sv2.verify_dataset(
            manifest=bad, rows=[], samples_path=tmp_path / "n.jsonl")
        assert not v.trusted


# ── 3. report artifact 재검증 (실행) ──────────────────────────────────────

def test_a_clean_dataset_reports_normally(ds):
    tmp, rows, _ = ds
    code, out, ready = report(tmp)
    assert code == 0
    ev = out["pipelines"]["editor_vary"]
    assert ev["calibrationConfusion"]["graded"] == 3
    assert ev["calibrationConfusion"]["falsePass"] == 1
    assert out["reportKind"] == "calibration"
    assert out.get("calibrationUsable") is None


def test_an_output_changed_after_the_manifest_is_blocked(ds):
    tmp, rows, _ = ds
    (tmp / f"{rows[0]['id']}.png").write_bytes(b"HACKED")
    assert_blocked(*report(tmp), contains="output_hash_mismatch")


def test_a_deleted_output_is_blocked(ds):
    tmp, rows, _ = ds
    (tmp / f"{rows[0]['id']}.png").unlink()
    assert_blocked(*report(tmp), contains="output_artifact_missing")


def test_a_symlinked_output_is_blocked(ds):
    tmp, rows, _ = ds
    p = tmp / f"{rows[0]['id']}.png"
    p.unlink()
    p.symlink_to("/etc/hosts")
    assert_blocked(*report(tmp), contains="unsafe_output_path")


def test_a_changed_source_is_blocked(ds, monkeypatch, tmp_path_factory):
    """source 디렉터리는 레포 정본이라 파일을 바꾸지 않고 해시를 어긋뜨린다."""
    tmp, rows, m = ds
    m2 = dict(m)
    m2["sourceDataset"] = {**m["sourceDataset"], "sha256": "d" * 64}
    (tmp / "manifest.json").write_text(json.dumps(m2, ensure_ascii=False))
    assert_blocked(*report(tmp), contains="source_bundle_mismatch")


def test_a_changed_samples_file_is_blocked(ds):
    tmp, _, _ = ds
    p = tmp / "samples.jsonl"
    p.write_text(p.read_text().replace('"image_calls": 1', '"image_calls": 2', 1))
    assert_blocked(*report(tmp), contains="manifest_samples_mismatch")


def test_artifacts_are_checked_before_labels_are_bound():
    src = (SERVER / "scripts" / "shadow_report.py").read_text(encoding="utf-8")
    assert src.index("verify_dataset(") < src.index("ba.load_labels(")


def test_the_blocked_status_names_artifacts():
    from app import shadow_report as sr
    rows = [{"id": "s", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY",
             "edit_qc_result": {"decision": "pass",
                                "vision": {"meta": {"status": "ok"}}}}]
    from app import shadow_verification as _sv2
    out = sr.report(_sv2.unverified_dataset(
        rows, {"validForCalibration": True}, ["output_hash_mismatch"]),
        extra_blocked_reasons=["output_hash_mismatch"])
    assert out["pipelines"]["editor_vary"]["verdict"]["status"] == "blocked_by_artifacts"


# ── 6. manifest 없는 report 정책 ──────────────────────────────────────────

def test_a_report_without_a_manifest_is_distribution_only(ds):
    from app import shadow_report as sr
    tmp, rows, _ = ds
    out = sr.report(_sv.distribution_dataset(rows))
    assert out["reportKind"] == "distribution_only"
    assert out["calibrationUsable"] is False
    assert "manifest_absent" in out["calibrationBlockedReasons"]
    ev = out["pipelines"]["editor_vary"]
    assert {ev["verdict"]["enforceReady"],
            *(t["verdict"]["enforceReady"]
              for t in ev["byEditTypeDetail"].values())} == {False}


def test_distribution_numbers_survive_without_a_manifest(ds):
    """DB 분포 조회는 기존 계약대로 숫자를 계속 낸다 — 판정 플래그만 닫힌다."""
    from app import shadow_report as sr
    tmp, rows, _ = ds
    out = sr.report(_sv.distribution_dataset(rows))
    ev = out["pipelines"]["editor_vary"]
    assert ev["samples"] == len(rows)
    assert ev["decisionRates"]["n"] == len(rows)
    assert ev["latencySeconds"]["n"] >= 0


# ── 7. helper 정리 ────────────────────────────────────────────────────────

def test_no_dead_aliases_remain():
    src = (SERVER / "scripts" / "shadow_collect.py").read_text(encoding="utf-8")
    for dead in ("case_set_sha256 = scases", "run_fingerprint = scases",
                 "_canonical = sp.canonical"):
        assert dead not in src, dead


def test_surviving_aliases_have_callers():
    src = (SERVER / "scripts" / "shadow_collect.py").read_text(encoding="utf-8")
    for alias in ("normalized_cases", "vision_prepared", "_sha"):
        assert src.count(alias) >= 2, alias
