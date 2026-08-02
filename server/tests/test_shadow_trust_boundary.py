"""Phase 3 P0-C 9/N — calibration trust boundary.

manifest dict 하나 넘겨준 것을 신뢰의 증거로 삼으면 `{}` 로도 enforce 가 켜진다.
실제로 켜졌다. trust 는 추론하는 게 아니라 **전달받는** 것이다.
"""

import copy
import importlib.util
import json
import pathlib
import shutil
import subprocess

import pytest

from app import shadow_cases as scases, shadow_provenance as sp, shadow_report as sr
from app.config import load_settings

SERVER = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = SERVER.parent / "public" / "assets" / "fit-examples"


def _mod(name, rel):
    spec = importlib.util.spec_from_file_location(name, SERVER / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = _mod("sc_tb", "scripts/shadow_collect.py")
SM = _mod("sm_tb", "scripts/shadow_manifest.py")

# 실제 필수 스키마를 만족하는 manifest — 값 비교는 report 밖에서 끝났다고 가정한다.
FULL_MANIFEST = {"datasetId": "ds", "rawSampleManifestSha256": "a" * 64,
                 "outputBundleSha256": "b" * 64,
                 "sourceDataset": {"sha256": "c" * 64},
                 "validForCalibration": True, "provenanceUnverified": False,
                 "provenanceProblems": []}


def rows(n=60, **kw):
    return [{"id": f"s{i}", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY", "image_calls": 1, "vision_calls": 1,
             "human_label": "fidelity_pass",
             "edit_qc_result": {"decision": "pass",
                                "vision": {"meta": {"status": "ok"}}}, **kw}
            for i in range(n)]


def _ready(out):
    ev = out["pipelines"]["editor_vary"]
    return {ev["verdict"]["enforceReady"]} | {
        t["verdict"]["enforceReady"] for t in ev["byEditTypeDetail"].values()}


# ── 1. report() trust 계약 ────────────────────────────────────────────────

@pytest.mark.parametrize("manifest", [
    {}, {"validForCalibration": True}, {"foo": "bar", "validForCalibration": True},
    dict(FULL_MANIFEST),
])
def test_a_manifest_alone_never_grants_trust(manifest):
    """검증 근거 없이 dict 만 넘기면 calibration 이 아니다 — FULL 스키마여도 그렇다."""
    out = sr.report(rows(), manifest=manifest)
    assert out["reportKind"] == "distribution_only"
    assert out["manifestTrust"] == "unverified"
    assert out["calibrationUsable"] is False
    assert "manifest_unverified" in out["calibrationBlockedReasons"]
    assert _ready(out) == {False}


def test_no_manifest_is_distribution_only():
    out = sr.report(rows())
    assert out["reportKind"] == "distribution_only"
    assert out["manifestTrust"] == "absent"
    assert "manifest_absent" in out["calibrationBlockedReasons"]
    assert _ready(out) == {False}


def test_a_verified_manifest_is_the_only_path_to_calibration():
    out = sr.report(rows(), manifest=FULL_MANIFEST, manifest_verified=True)
    assert out["reportKind"] == "calibration"
    assert out["manifestTrust"] == "trusted"
    assert out.get("calibrationUsable") is None
    assert True in _ready(out)


def test_a_verified_but_self_invalid_manifest_is_still_blocked():
    m = {**FULL_MANIFEST, "validForCalibration": False,
         "invalidReasons": ["provenance_unverified"]}
    out = sr.report(rows(), manifest=m, manifest_verified=True)
    assert out["manifestTrust"] == "invalid"
    assert out["calibrationUsable"] is False
    assert _ready(out) == {False}


def test_trust_is_never_inferred_from_the_object():
    """`manifest is not None` / `bool(manifest)` 로 trust 를 되살리지 못하게 고정."""
    src = (SERVER / "app" / "shadow_report.py").read_text(encoding="utf-8")
    assert "manifest_verified" in src
    assert "trusted = manifest is not None and manifest_verified" in src
    # 예전 추론식이 남아 있으면 안 된다.
    assert "trustworthy = manifest is not None and not invalid_manifest" not in src


def test_only_the_cli_grants_trust_and_only_after_binding():
    src = (SERVER / "scripts" / "shadow_report.py").read_text(encoding="utf-8")
    assert "manifest_verified=bool(manifest is not None and not binding_reasons)" in src


def test_distribution_numbers_are_unchanged_without_trust():
    trusted = sr.report(rows(), manifest=FULL_MANIFEST, manifest_verified=True)
    plain = sr.report(rows())
    for out in (trusted, plain):
        ev = out["pipelines"]["editor_vary"]
        assert ev["samples"] == 60
        assert ev["decisionRates"]["n"] == 60
        assert ev["calibrationConfusion"]["graded"] == 60


def test_graded_and_false_pass_survive_the_trust_gate():
    r = rows(3)
    r[0]["human_label"] = "fidelity_fail"
    out = sr.report(r, manifest=FULL_MANIFEST, manifest_verified=True)
    cal = out["pipelines"]["editor_vary"]["calibrationConfusion"]
    assert cal["graded"] == 3 and cal["falsePass"] == 1


# ── 2. manifest 상태 모순 ─────────────────────────────────────────────────

@pytest.mark.parametrize("over", [
    {"provenanceUnverified": True},
    {"provenanceProblems": ["case_set_mismatch"]},
    {"invalidReasons": ["provenance_unverified"]},
])
def test_a_contradictory_calibration_state_is_refused(over):
    problems = sp.manifest_binding_problems({**FULL_MANIFEST, **over})
    assert "manifest_binding_invalid:calibration_state" in problems


def test_an_honest_invalid_manifest_passes_schema():
    """false + unverified=true 는 모순이 아니라 정직한 진술이다."""
    m = {**FULL_MANIFEST, "validForCalibration": False,
         "provenanceUnverified": True, "provenanceProblems": ["x"],
         "invalidReasons": ["x"]}
    assert sp.manifest_binding_problems(m) == []


def test_a_consistent_manifest_has_no_problems():
    assert sp.manifest_binding_problems(FULL_MANIFEST) == []


def test_empty_invalid_reasons_are_fine_with_true():
    assert sp.manifest_binding_problems({**FULL_MANIFEST, "invalidReasons": []}) == []


# ── 3. malformed manifest CLI ─────────────────────────────────────────────

def _cli(tmp, manifest_text=None, *, mode=None):
    (tmp / "samples.jsonl").write_text(json.dumps(
        {"id": "a", "output_id": "o", "source_kind": "editor_asset",
         "edit_type": "BACKGROUND_ONLY",
         "edit_qc_result": {"decision": "pass"}}) + "\n")
    mp = tmp / "m.json"
    if manifest_text is not None:
        mp.write_text(manifest_text)
    if mode is not None:
        mp.chmod(mode)
    r = subprocess.run(
        [".venv/bin/python", "scripts/shadow_report.py", "--jsonl",
         str(tmp / "samples.jsonl"), "--manifest", str(mp), "--json"],
        cwd=str(SERVER), capture_output=True, text=True)
    if mode is not None:
        mp.chmod(0o644)
    return r


@pytest.mark.parametrize("text,code", [
    ("[1]", "manifest_not_object"),
    ('"hello"', "manifest_not_object"),
    ("42", "manifest_not_object"),
    ("null", "manifest_not_object"),
    ("{not json", "manifest_not_json"),
])
def test_a_malformed_manifest_fails_closed(tmp_path, text, code):
    r = _cli(tmp_path, text)
    assert r.returncode == 5
    assert "Traceback" not in (r.stdout + r.stderr)
    assert str(tmp_path) not in r.stderr
    assert code in r.stderr


def test_an_unreadable_manifest_fails_closed(tmp_path):
    r = _cli(tmp_path, "{}", mode=0o000)
    assert r.returncode == 5
    assert "Traceback" not in (r.stdout + r.stderr)
    assert "manifest_unreadable" in r.stderr
    assert str(tmp_path) not in r.stderr


def test_a_malformed_manifest_report_has_no_enforce_ready(tmp_path):
    r = _cli(tmp_path, "[1]")
    out = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    assert out.get("calibrationUsable") is False
    assert _ready(out) == {False}


def test_the_type_is_checked_before_get_is_called():
    src = (SERVER / "scripts" / "shadow_report.py").read_text(encoding="utf-8")
    load = src[src.index("manifest_load_error = None"):src.index("dataset_id = args.dataset_id")]
    assert "isinstance(parsed, dict)" in load
    # 파싱 결과의 타입을 확인하기 전에는 .get() 을 부르지 않는다.
    assert ".get(" not in load
    # 그 뒤 manifest 는 dict 또는 None 으로 확정돼 있다.
    assert "(manifest or {}).get(" in src


# ── 4. 실제 source 바이트 변조 E2E ────────────────────────────────────────

@pytest.fixture
def live(tmp_path):
    """source 를 **사본**으로 두고 실제 바이트를 바꿔 볼 수 있는 데이터셋."""
    from app import blinded_audit as ba
    src_copy = tmp_path / "sources"
    src_copy.mkdir()
    origin = sorted(SRC_DIR.glob("*.jpg"))[0]
    shutil.copy(origin, src_copy / origin.name)
    ds = tmp_path / "ds"
    ds.mkdir()
    s = load_settings()
    raw = (src_copy / origin.name).read_bytes()
    out_rows = []
    for i, (name, ch) in enumerate(scases.VARY_CASES[:2]):
        prep = scases.generation_prepared(s, ch)
        vp = scases.vision_prepared(ch)
        ob = b"PNG-" + bytes([i])
        (ds / f"{name}.png").write_bytes(ob)
        out_rows.append({"id": name, "output_id": f"o{i}", "case": name,
                         "source": origin.name, "source_kind": "editor_asset",
                         "edit_type": SC.editor_vary.edit_type_for(ch),
                         "image_calls": 1, "vision_calls": 1,
                         "edit_qc_result": {"decision": "pass",
                                            "vision": {"meta": {"status": "ok"}}},
                         "provenance": SC._provenance(
                             prep, case_name=name, changes=ch, attempt=1,
                             source_bytes=raw, output_bytes=ob,
                             vision_meta={"promptSha256": vp.prompt_sha256,
                                          "provider": "p", "status": "ok"})})
    (ds / "samples.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out_rows))
    m = SM.build(str(ds / "samples.jsonl"), dataset_id="ds", invalid_reasons=[],
                 image_usd=0, vision_usd=0, collected_at="t", command=None,
                 source_dir=src_copy)
    (ds / "manifest.json").write_text(json.dumps(m, ensure_ascii=False))
    for r, lab in zip(out_rows, ("fidelity_pass", "fidelity_fail")):
        ba.append_label(str(ds / "labels.jsonl"),
                        ba.make_label(sample=r, label=lab, reviewer_id="t",
                                      dataset_id="ds"))
    return ds, src_copy, origin.name, out_rows


def _run(ds, src_copy):
    r = subprocess.run(
        [".venv/bin/python", "scripts/shadow_report.py",
         "--jsonl", str(ds / "samples.jsonl"), "--manifest", str(ds / "manifest.json"),
         "--labels", str(ds / "labels.jsonl"), "--dataset-id", "ds",
         "--source-dir", str(src_copy), "--json"],
        cwd=str(SERVER), capture_output=True, text=True)
    out = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    return r.returncode, out


def test_a_clean_live_dataset_reports_normally(live):
    ds, src_copy, _, _ = live
    code, out = _run(ds, src_copy)
    assert code == 0
    assert out["reportKind"] == "calibration" and out["manifestTrust"] == "trusted"
    assert out["pipelines"]["editor_vary"]["calibrationConfusion"]["graded"] == 2


def test_a_real_source_byte_change_is_blocked(live):
    """manifest SHA 를 손대지 않고 **파일 바이트**를 바꾼다."""
    ds, src_copy, name, _ = live
    (src_copy / name).write_bytes((src_copy / name).read_bytes() + b"TAMPER")
    code, out = _run(ds, src_copy)
    assert code == 5
    reasons = out["calibrationBlockedReasons"]
    assert any(r in reasons for r in ("source_hash_mismatch", "source_bundle_mismatch"))
    assert out["calibrationUsable"] is False
    assert _ready(out) == {False}


def test_a_deleted_source_is_blocked(live):
    ds, src_copy, name, _ = live
    (src_copy / name).unlink()
    code, out = _run(ds, src_copy)
    assert code == 5
    assert "source_artifact_missing" in out["calibrationBlockedReasons"]
    assert _ready(out) == {False}


def test_artifacts_are_verified_before_labels(live):
    src = (SERVER / "scripts" / "shadow_report.py").read_text(encoding="utf-8")
    assert src.index("artifact_problems(") < src.index("ba.load_labels(")


def test_no_file_outside_the_source_dir_is_read(live, monkeypatch):
    from app import safe_paths
    ds, src_copy, name, out_rows = live
    reads = []
    real = safe_paths.file_sha256
    monkeypatch.setattr(safe_paths, "file_sha256",
                        lambda p: (reads.append(str(p)), real(p))[1])
    sp.artifact_problems(out_rows, dataset_dir=ds, source_dir=src_copy)
    assert all(str(ds) in r or str(src_copy) in r for r in reads)


# ── 구조 정리 ─────────────────────────────────────────────────────────────

def test_dead_helpers_are_gone():
    prov = (SERVER / "app" / "shadow_provenance.py").read_text(encoding="utf-8")
    assert "_DATASET_ID" not in prov
    man = (SERVER / "scripts" / "shadow_manifest.py").read_text(encoding="utf-8")
    assert "def _bundle_sha" not in man
    rep = (SERVER / "app" / "shadow_report.py").read_text(encoding="utf-8")
    assert "trustworthy" not in rep


def test_the_module_docstring_matches_the_implementation():
    """'파일 I/O 를 하지 않는다'는 문장은 거짓이었다 — 파일을 읽는다."""
    prov = (SERVER / "app" / "shadow_provenance.py").read_text(encoding="utf-8")
    head = prov[:prov.index('"""', 3)]
    assert "파일·네트워크에 손대지 않는다" not in head
    assert "artifact" in head and "binding" in head
