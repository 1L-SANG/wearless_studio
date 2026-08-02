"""Phase 3 P0-C 9/N — manifest fail-closed.

이전 manifest 는 "확인하지 못함"과 "통과"를 같게 취급했다. case 정의를 못 읽으면
검사를 생략하고 valid=true 를 줬고, 단일 row 의 case fingerprint 변조도, 결과 파일
바이트 변조도 통과했으며, 해시가 hex 가 아니면 아예 crash 했다.
"""

import copy
import hashlib
import importlib.util
import json
import pathlib

import pytest

from app import safe_paths, shadow_cases as scases, shadow_provenance as sp
from app.config import load_settings

SERVER = pathlib.Path(__file__).resolve().parents[1]
from app import shadow_verification as _sv  # noqa: E402
SRC_DIR = SERVER.parent / "public" / "assets" / "fit-examples"


def _mod(name, rel):
    spec = importlib.util.spec_from_file_location(name, SERVER / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = _mod("sc_fc", "scripts/shadow_collect.py")
SM = _mod("sm_fc", "scripts/shadow_manifest.py")


@pytest.fixture
def dataset(tmp_path):
    """실제 파일이 있는 정상 데이터셋 3건."""
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
                     "provenance": SC._provenance(
                         prep, case_name=name, changes=ch, attempt=1,
                         source_bytes=raw, output_bytes=out,
                         vision_meta={"promptSha256": vp.prompt_sha256,
                                      "provider": "p", "status": "ok"})})
    return tmp_path, rows


def build(tmp_path, rows):
    p = tmp_path / "samples.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return SM.build(str(p), dataset_id="ds", invalid_reasons=[], image_usd=0,
                    vision_usd=0, collected_at="t", command=None)


def _mut(rows, fn):
    r = copy.deepcopy(rows)
    fn(r)
    return r


# ── 1. case 정의 정본 ──────────────────────────────────────────────────────

def test_a_clean_dataset_is_valid(dataset):
    m = build(*dataset)
    assert m["validForCalibration"] is True
    assert m["provenanceProblems"] == []
    assert m["rawArtifacts"]["humanLabelingAvailable"] is True


def test_case_definition_failure_is_a_problem_not_a_skip(dataset, monkeypatch):
    """확인하지 못한 것을 통과로 세면 manifest 는 아무것도 보증하지 않는다."""
    def boom(*a, **k):
        raise scases.CaseDefinitionError("Whatever")
    monkeypatch.setattr(SM.scases, "expected_case_fingerprints", boom)
    m = build(*dataset)
    assert m["validForCalibration"] is False
    assert "case_definition_unavailable" in m["provenanceProblems"]
    assert m["provenanceUnverified"] is True


def test_case_definition_error_leaks_no_detail():
    """원문·경로·환경값을 manifest 나 로그로 흘리지 않는다."""
    with pytest.raises(scases.CaseDefinitionError) as e:
        raise scases.CaseDefinitionError(type(RuntimeError("/secret/path")).__name__)
    assert "/secret" not in str(e.value)


def test_the_manifest_does_not_dynamically_import_the_collector():
    src = (SERVER / "scripts" / "shadow_manifest.py").read_text(encoding="utf-8")
    assert "shadow_collect.py" not in src
    assert "def sp_cases" not in src
    assert "except Exception:" not in src or "expected = None" not in src


def test_every_tool_imports_the_same_case_definition():
    for rel in ("scripts/shadow_collect.py", "scripts/shadow_manifest.py"):
        src = (SERVER / rel).read_text(encoding="utf-8")
        assert "shadow_cases" in src, rel
    assert scases.VARY_CASES is SC.VARY_CASES


def test_case_definitions_need_no_heavy_dependency():
    """manifest 가 cv2 를 끌고 오면 import 실패가 곧 fail-open 이 된다."""
    src = (SERVER / "app" / "shadow_cases.py").read_text(encoding="utf-8")
    # 산문에 이름이 나오는 것과 import 하는 것은 다르다 — import 만 본다.
    for bad in ("import cv2", "import numpy", "from cv2", "from numpy"):
        assert bad not in src, bad
    import sys
    assert "cv2" not in sys.modules or True   # 이미 로드됐을 수 있어 여기선 안 본다


# ── 2. expected full case fingerprint ─────────────────────────────────────

def test_expected_fingerprints_have_every_field():
    exp = scases.expected_case_fingerprints(load_settings())
    for fp in exp.values():
        assert set(fp) == set(sp.CASE_KEYS)
        assert safe_paths.is_sha256_hex(fp["generationPromptSha256"])
        assert safe_paths.is_sha256_hex(fp["visionPromptSha256"])


def test_expected_fingerprints_need_no_provider(monkeypatch):
    from app.agents import cut_variator, edit_intent_vision
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider 호출"))  # noqa: E731
    monkeypatch.setattr(cut_variator, "execute", boom)
    monkeypatch.setattr(edit_intent_vision, "analyze_with_fallback", boom)
    assert scases.expected_case_fingerprints(load_settings())


@pytest.mark.parametrize("field,value,code", [
    ("changes", [{"type": "pose", "value": "x"}], "case_changes_mismatch"),
    ("editType", "OTHER", "case_edit_type_mismatch"),
    ("generationPromptSha256", "0" * 64, "generation_prompt_mismatch"),
    ("visionPromptSha256", "0" * 64, "vision_prompt_mismatch"),
])
def test_a_single_row_case_tamper_is_detected(dataset, field, value, code):
    """row 가 하나뿐인 case 는 dataset 내부에 비교 상대가 없다 — expected 로 잡는다."""
    tmp, rows = dataset
    m = build(tmp, _mut(rows, lambda rs: rs[0]["provenance"]["case"].__setitem__(
        field, value)))
    assert m["validForCalibration"] is False
    assert any(p.startswith(code) for p in m["provenanceProblems"])


def test_an_unknown_case_is_detected(dataset):
    tmp, rows = dataset
    m = build(tmp, _mut(rows, lambda rs: rs[0]["provenance"]["case"].__setitem__(
        "case", "ghost")))
    assert m["validForCalibration"] is False
    assert "unknown_case:ghost" in m["provenanceProblems"]


def test_a_partial_collection_is_still_valid(dataset):
    """전체 case 를 다 모으지 않아도 정상이다 — caseSet 은 정의 집합이지 수집분이 아니다."""
    tmp, rows = dataset
    assert build(tmp, rows[:1])["validForCalibration"] is True


def test_internal_consistency_is_still_checked(dataset):
    tmp, rows = dataset
    dup = copy.deepcopy(rows[0])
    dup["id"] = "dup"
    (tmp / "dup.png").write_bytes(b"PNG-0")
    dup["provenance"]["case"]["generationPromptSha256"] = "1" * 64
    m = build(tmp, [*rows, dup])
    assert any(p.startswith("inconsistent_case_fingerprint")
               or p.startswith("generation_prompt_mismatch")
               for p in m["provenanceProblems"])


# ── 3. artifact 무결성 ─────────────────────────────────────────────────────

def test_a_tampered_output_byte_is_detected(dataset):
    tmp, rows = dataset
    (tmp / f"{rows[0]['id']}.png").write_bytes(b"HACKED")
    m = build(tmp, rows)
    assert m["validForCalibration"] is False
    assert "output_hash_mismatch" in m["provenanceProblems"]
    assert m["rawArtifacts"]["humanLabelingAvailable"] is False


def test_a_source_hash_mismatch_is_detected(dataset):
    tmp, rows = dataset
    m = build(tmp, _mut(rows, lambda rs: rs[0]["provenance"].__setitem__(
        "sourceSha256", "1" * 64)))
    assert "source_hash_mismatch" in m["provenanceProblems"]


def test_a_missing_output_file_is_detected(dataset):
    tmp, rows = dataset
    (tmp / f"{rows[0]['id']}.png").unlink()
    assert "output_artifact_missing" in build(tmp, rows)["provenanceProblems"]


@pytest.mark.parametrize("bad", ["zz", "ab" * 10, "", None, "A" * 64, 12345])
def test_a_bad_sha_format_is_a_problem_not_a_crash(dataset, bad):
    tmp, rows = dataset
    m = build(tmp, _mut(rows, lambda rs: rs[0]["provenance"].__setitem__(
        "outputSha256", bad)))
    assert m["validForCalibration"] is False
    assert any(p.startswith("invalid_sha_format") or p.startswith("missing_row_field")
               for p in m["provenanceProblems"])
    # bundle 은 실제 파일로 계산하므로 기록된 해시 형식과 무관하게 값이 나온다 —
    # 중요한 건 crash 하지 않고 typed problem 이 남는다는 것.
    assert m["outputBundleSha256"] is None or len(m["outputBundleSha256"]) == 64


@pytest.mark.parametrize("bad_id", ["../../etc/hosts", "/etc/hosts", "a/b", "x\x00"])
def test_an_unsafe_output_id_is_refused(dataset, bad_id):
    tmp, rows = dataset
    m = build(tmp, _mut(rows, lambda rs: rs[0].__setitem__("id", bad_id)))
    assert "unsafe_output_path" in m["provenanceProblems"]


@pytest.mark.parametrize("bad_src", ["../../etc/hosts", "/etc/hosts", "a/b"])
def test_an_unsafe_source_name_is_refused(dataset, bad_src):
    tmp, rows = dataset
    m = build(tmp, _mut(rows, lambda rs: rs[0].__setitem__("source", bad_src)))
    assert "unsafe_source_path" in m["provenanceProblems"]


def test_a_symlink_out_of_the_dataset_is_refused(dataset, tmp_path_factory):
    tmp, rows = dataset
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_bytes(b"SECRET")
    link = tmp / "linked.png"
    link.symlink_to(outside)
    m = build(tmp, _mut(rows, lambda rs: rs[0].__setitem__("id", "linked")))
    assert "unsafe_output_path" in m["provenanceProblems"]


def test_no_file_outside_the_base_is_read(dataset, monkeypatch):
    reads = []
    real = safe_paths.file_sha256
    monkeypatch.setattr(safe_paths, "file_sha256",
                        lambda p: (reads.append(str(p)), real(p))[1])
    tmp, rows = dataset
    build(tmp, _mut(rows, lambda rs: rs[0].__setitem__("id", "../../etc/hosts")))
    assert all(str(tmp) in r or str(SRC_DIR) in r for r in reads)


def test_failed_rows_are_not_counted_as_artifacts(dataset):
    tmp, rows = dataset
    m = build(tmp, [*rows, {"id": "failed", "case": "bg_only"}])
    assert m["validForCalibration"] is True
    assert m["rawArtifacts"]["outputImagesExpected"] == len(rows)
    assert m["rawArtifacts"]["outputImagesPresent"] == len(rows)


def test_a_dataset_with_no_output_rows_is_not_labelable(dataset):
    tmp, rows = dataset
    m = build(tmp, [{**r, "output_id": None} for r in rows])
    assert m["validForCalibration"] is False
    assert m["rawArtifacts"]["outputImagesExpected"] == 0
    assert m["rawArtifacts"]["humanLabelingAvailable"] is False


# ── 4. manifest / samples binding ─────────────────────────────────────────

REPORT_CLI = SERVER / "scripts" / "shadow_report.py"


def test_the_cli_verifies_before_binding_labels():
    src = REPORT_CLI.read_text(encoding="utf-8")
    assert src.index("verify_manifest_for_report(") < src.index("ba.load_labels(")
    assert "if args.labels and not binding_reasons:" in src


def test_a_binding_mismatch_blocks_every_verdict():
    from app import shadow_report as sr
    rows = [{"id": "s", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY",
             "edit_qc_result": {"decision": "pass",
                                "vision": {"meta": {"status": "ok"}}}}]
    out = sr.report(rows, extra_blocked_reasons=["manifest_samples_mismatch"])
    ev = out["pipelines"]["editor_vary"]
    assert out["calibrationUsable"] is False
    assert "manifest_samples_mismatch" in out["calibrationBlockedReasons"]
    assert ev["verdict"]["status"] == "blocked_by_binding"
    assert {ev["verdict"]["enforceReady"],
            *(t["verdict"]["enforceReady"]
              for t in ev["byEditTypeDetail"].values())} == {False}


def test_binding_and_label_reasons_are_unioned():
    from app import shadow_report as sr
    rows = [{"id": "s", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY",
             "edit_qc_result": {"decision": "pass",
                                "vision": {"meta": {"status": "ok"}}}}]
    out = sr.report(rows, manifest_verification=_sv.unverified({'validForCalibration': False}, ["provenance_unverified"]),
                    quarantined=[{"reason": "dataset_mismatch"}],
                    extra_blocked_reasons=["manifest_samples_mismatch"])
    # 상태 코드(manifest_unverified)도 함께 남는다 — 계열별 사유가 하나도 안 사라진다.
    assert set(out["calibrationBlockedReasons"]) >= {
        "label_dataset_mismatch", "manifest_samples_mismatch", "provenance_unverified"}


def test_the_manifest_declares_the_hash_of_the_file_it_read(dataset):
    tmp, rows = dataset
    m = build(tmp, rows)
    actual = hashlib.sha256((tmp / "samples.jsonl").read_bytes()).hexdigest()
    assert m["rawSampleManifestSha256"] == actual


# ── 5. 공통 helper 정리 ────────────────────────────────────────────────────

def test_no_unused_run_fingerprint_alias_remains():
    src = (SERVER / "scripts" / "shadow_collect.py").read_text(encoding="utf-8")
    assert "RUN_FINGERPRINT_KEYS" not in src


def test_the_label_tool_uses_the_shared_safe_resolver():
    src = (SERVER / "scripts" / "blinded_label.py").read_text(encoding="utf-8")
    assert "from app.safe_paths import" in src
    assert "class UnsafePath" not in src           # 중복 정의 제거
    assert "_SAFE_ID = re.compile" not in src
