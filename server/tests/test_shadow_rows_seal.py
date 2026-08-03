"""Phase 3 P0-C 9/N — 행 전체 봉인 · cross-dataset replay · DB 분포 경로.

직전 봉인은 PROTECTED_FIELDS 만 해시했다. 그래서 human_label·review_decision·
has_pattern_or_logo·analysis·pattern_source 를 trusted 상태에서 그냥 고칠 수 있었고,
목록에 없는 미래 필드는 영원히 무방비였다. dataset_id 도 호출자가 정할 수 있어
다른 데이터셋의 라벨을 그대로 붙일 수 있었다.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from app import blinded_audit as ba
from app import shadow_report as sr
from app import shadow_verification as sv

SERVER = pathlib.Path(__file__).resolve().parents[1]


def _state(ds):
    out = sr.report(ds)
    ev = out["pipelines"]["editor_vary"]
    ready = {ev["verdict"]["enforceReady"]} | {
        t["verdict"]["enforceReady"] for t in ev["byEditTypeDetail"].values()}
    return out, ready


def _blocked(ds, *, contains=None):
    out, ready = _state(ds)
    assert out["reportKind"] == "distribution_only", out["reportKind"]
    assert out["manifestTrust"] != "trusted"
    assert out["calibrationUsable"] is False
    assert ready == {False}
    if contains:
        assert contains in out["calibrationBlockedReasons"], \
            out["calibrationBlockedReasons"]
    return out


def _labels(rows, *, dataset_id="ds", label="fidelity_pass", note=None):
    return {(dataset_id, str(r["id"])): ba.make_label(
        sample=r, label=label, reviewer_id="rev", dataset_id=dataset_id,
        note=note, now=1.0) for r in rows}


# ── 1. 행 전체 봉인 ───────────────────────────────────────────────────────

def test_a_clean_dataset_is_calibration(shadow_dataset):
    out, _ = _state(shadow_dataset(n=2)["verification"])
    assert out["reportKind"] == "calibration" and out["manifestTrust"] == "trusted"


def test_inserting_a_human_label_directly_is_blocked(shadow_dataset):
    """a. trusted dataset 에 human_label 직접 추가."""
    ds = shadow_dataset(n=2)["verification"]
    ds.rows[0]["human_label"] = "fidelity_pass"
    out = _blocked(ds, contains="verified_rows_tampered")
    assert "unbound_label_fields" in out["calibrationBlockedReasons"]


def test_changing_a_bound_label_is_blocked(shadow_dataset):
    """b. typed bind 후 human_label 값 직접 변경."""
    built = shadow_dataset(n=2, label_all="fidelity_pass")
    bound = built["labeled"]
    assert bound.trusted
    bound.rows[0]["human_label"] = "fidelity_fail"
    _blocked(bound, contains="verified_rows_tampered")


@pytest.mark.parametrize("field,value", [
    ("review_decision", "accepted"),          # c
    ("has_pattern_or_logo", True),            # d
    ("analysis", {"pattern": "stripe"}),      # d
    ("pattern_source", "structured"),         # d
    ("totally_new_field_2027", "zzz"),        # e — 목록에 없는 미래 필드
])
def test_any_field_mutation_is_blocked(shadow_dataset, field, value):
    ds = shadow_dataset(n=2)["verification"]
    ds.rows[0][field] = value
    _blocked(ds, contains="verified_rows_tampered")


@pytest.mark.parametrize("field", ["edit_type", "source_kind", "image_calls",
                                   "edit_qc_result"])
def test_deleting_a_field_is_blocked(shadow_dataset, field):
    """f. 필드 삭제."""
    ds = shadow_dataset(n=2)["verification"]
    del ds.rows[0][field]
    _blocked(ds, contains="verified_rows_tampered")


def test_raw_rows_with_labels_cannot_be_trusted(shadow_dataset):
    """g. raw samples 에 human_label 이 이미 있으면 trusted 불가."""
    built = shadow_dataset(n=2, mutate_rows=lambda rs: rs[0].__setitem__(
        "human_label", "fidelity_pass"))
    ds = built["verification"]
    assert not ds.trusted
    assert "raw_rows_contain_label_fields" in ds.problems
    _blocked(ds)


def test_an_unbound_label_field_is_caught_even_without_a_digest():
    """report 가 labels_bound=False + LABEL_FIELDS 조합을 따로 막는다."""
    rows = [{"id": "s0", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY", "human_label": "fidelity_pass",
             "edit_qc_result": {"decision": "pass",
                                "vision": {"meta": {"status": "ok"}}}}]
    ds = sv.distribution_dataset(rows)
    assert "unbound_label_fields" in ds.integrity_problems()


def test_a_distribution_dataset_seals_its_own_rows():
    rows = [{"id": "s0", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY",
             "edit_qc_result": {"decision": "pass"}}]
    ds = sv.distribution_dataset(rows)
    assert ds.rows_digest
    ds.rows[0]["id"] = "changed"
    assert "verified_rows_tampered" in ds.integrity_problems()


def test_a_normal_bind_stays_calibration(shadow_dataset):
    """h. 정상 bind → calibration 유지."""
    built = shadow_dataset(n=2)
    bound, q = sv.bind_verified_labels(built["verification"],
                                       _labels(built["verification"].rows))
    assert not q and bound.trusted and bound.labels_bound
    out, _ = _state(bound)
    assert out["reportKind"] == "calibration"
    assert out["pipelines"]["editor_vary"]["calibrationConfusion"]["graded"] == 2


def test_the_bind_result_is_resealed(shadow_dataset):
    """i. bind 결과를 다시 직접 변경 → blocked."""
    built = shadow_dataset(n=2)
    bound, _ = sv.bind_verified_labels(built["verification"],
                                       _labels(built["verification"].rows))
    assert bound.rows_digest != built["verification"].rows_digest
    bound.rows[0]["label_reviewer_id"] = "someone-else"
    _blocked(bound, contains="verified_rows_tampered")


# ── 2. cross-dataset replay ──────────────────────────────────────────────

def test_a_foreign_dataset_label_is_quarantined(shadow_dataset):
    """a. dataset real-ds + label evil-ds."""
    built = shadow_dataset(n=2, dataset_id="ds")
    ds = built["verification"]
    evil = _labels(ds.rows, dataset_id="evil-ds")
    bound, q = sv.bind_verified_labels(ds, evil)
    assert [x["reason"] for x in q] == ["dataset_mismatch"] * 2
    assert not bound.trusted
    _blocked(bound)


def test_no_api_lets_the_caller_choose_the_dataset_id():
    """b. 호출자가 dataset id 를 우회 지정하는 API 가 없다."""
    import inspect
    sig = inspect.signature(sv.bind_verified_labels)
    assert list(sig.parameters) == ["dataset", "effective_labels"]
    with pytest.raises(TypeError):
        sv.bind_verified_labels(sv.distribution_dataset([]), {}, dataset_id="x")


def test_a_matching_dataset_id_binds(shadow_dataset):
    """c. 동일 dataset id → 정상 결합."""
    built = shadow_dataset(n=2, dataset_id="ds")
    bound, q = sv.bind_verified_labels(built["verification"],
                                       _labels(built["verification"].rows,
                                               dataset_id="ds"))
    assert not q and bound.trusted


def test_a_dataset_without_an_id_refuses_labels():
    """d. dataset id 없음 → fail-closed."""
    ds = sv.distribution_dataset([])
    assert ds.dataset_id is None
    with pytest.raises(sv.LabelBindingError):
        sv.bind_verified_labels(ds, {})


# ── 3. DB distribution CLI ───────────────────────────────────────────────

FAKE_DB = '''
import sys, types
rows = ROWS_JSON
mod = types.ModuleType("psycopg")
class _Cur:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): self._q = a[0] if a else ""
    def fetchone(self): return {"a": "public.edit_sessions", "b": "public.edit_review_events"}
    def fetchall(self): return rows
class _Conn:
    read_only = False
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _Cur()
def connect(*a, **k): return _Conn()
mod.connect = connect
sys.modules["psycopg"] = mod
rowsmod = types.ModuleType("psycopg.rows")
rowsmod.dict_row = object()
sys.modules["psycopg.rows"] = rowsmod
sys.argv = ARGV
import runpy
runpy.run_path(SCRIPT, run_name="__main__")
'''


def _run_db_cli(tmp_path, rows):
    """psycopg 를 가짜로 바꿔 --dsn 경로만 실행한다(실 DB 미접속)."""
    script = tmp_path / "run.py"
    script.write_text(
        FAKE_DB.replace("ROWS_JSON", repr(rows))
        .replace("ARGV", json.dumps(["shadow_report.py", "--dsn",
                                     "postgresql://fake/db", "--json"]))
        .replace("SCRIPT", json.dumps(str(SERVER / "scripts" / "shadow_report.py"))))
    return subprocess.run([sys.executable, str(script)], cwd=str(SERVER),
                          capture_output=True, text=True)


DB_ROW = {"id": "s0", "edit_type": "BACKGROUND_ONLY", "status": "pass",
          "source_kind": "editor_asset", "created_at": None, "completed_at": None,
          "output_id": "o0", "edit_qc_result": {"decision": "pass",
                                                "vision": {"meta": {"status": "ok"}}},
          "review_decision": None, "has_pattern_or_logo": False}


def test_the_db_path_produces_a_report(tmp_path):
    r = _run_db_cli(tmp_path, [DB_ROW])
    assert "TypeError" not in (r.stdout + r.stderr), r.stderr[-400:]
    assert r.returncode == 0, r.stderr[-400:]
    out = json.loads(r.stdout)
    assert out["reportKind"] == "distribution_only"
    assert out["manifestTrust"] != "trusted"
    ev = out["pipelines"]["editor_vary"]
    assert {ev["verdict"]["enforceReady"]} | {
        t["verdict"]["enforceReady"] for t in ev["byEditTypeDetail"].values()} == {False}
    assert ev["samples"] == 1


def test_the_db_path_handles_an_empty_result(tmp_path):
    r = _run_db_cli(tmp_path, [])
    assert r.returncode == 0, r.stderr[-400:]
    out = json.loads(r.stdout)
    assert out["total"] == 0 and out["reportKind"] == "distribution_only"


# ── 4. label note 계약 ───────────────────────────────────────────────────

def test_a_label_note_is_preserved_through_bind(shadow_dataset):
    built = shadow_dataset(n=2)
    ds = built["verification"]
    records = list(_labels(ds.rows, note="흐릿함").values())
    bound, q = sv.bind_verified_labels(ds, ba.effective_labels(records))
    assert not q
    assert bound.rows[0]["label_note"] == "흐릿함"


def test_every_declared_label_field_can_actually_be_written(shadow_dataset):
    """선언만 있고 절대 기록되지 않는 필드를 두지 않는다."""
    built = shadow_dataset(n=2)
    ds = built["verification"]
    bound, _ = sv.bind_verified_labels(ds, _labels(ds.rows, note="n"))
    assert set(sv.LABEL_FIELDS) <= set(bound.rows[0])


def test_label_evidence_was_dropped_from_the_contract():
    assert "label_evidence" not in sv.LABEL_FIELDS


def test_a_note_longer_than_the_contract_is_already_bounded(shadow_dataset):
    built = shadow_dataset(n=2)
    ds = built["verification"]
    bound, _ = sv.bind_verified_labels(ds, _labels(ds.rows, note="가" * 900))
    assert len(bound.rows[0]["label_note"]) <= 500


# ── 5. 정리 ──────────────────────────────────────────────────────────────

def test_the_old_alias_is_gone():
    assert not hasattr(sv, "verify_manifest_for_report")


def test_labels_bound_is_a_real_invariant():
    import inspect
    assert "labels_bound" in inspect.getsource(sv.VerifiedDataset.integrity_problems)
