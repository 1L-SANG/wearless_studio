"""Phase 3 P0-C 9/N 최종 — 도구 체인 무결성.

측정 도구가 스스로를 속이면 그 위의 모든 판단이 틀린다. 여기서 고정하는 건
"조건이 하나 바뀌었을 때 조용히 넘어가지 않는가"다.
"""

import importlib.util
import json
import pathlib

import pytest

from app import blinded_audit as ba
from app import shadow_report as sr

SERVER = pathlib.Path(__file__).resolve().parents[1]
from _shadow_trusted import trusted as _trusted  # noqa: E402
from app import shadow_verification as _sv  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, SERVER / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


COLLECT = _load("sc", "scripts/shadow_collect.py")
REPORT_CLI = SERVER / "scripts" / "shadow_report.py"
LABEL_CLI = SERVER / "scripts" / "blinded_label.py"


class _Prepared:
    model = "gemini-3-pro-image"
    prompt = "PROMPT BODY"
    image_size = "2K"
    aspect_ratio = "2:3"


CASE, CHANGES = COLLECT.VARY_CASES[0]
VMETA = {"promptSha256": "v" * 64, "provider": "p", "status": "ok"}


def prov(source_bytes=b"src", output_bytes=b"out"):
    return COLLECT._provenance(_Prepared(), case_name=CASE, changes=CHANGES,
                               attempt=1, source_bytes=source_bytes,
                               output_bytes=output_bytes, vision_meta=VMETA)


REQUIRED_PROVENANCE = ("sourceSha256", "outputSha256", "generationModel",
                       "generationPromptSha256", "generationTemplateSha256",
                       "visionPromptSha256", "visionTemplateSha256",
                       "qcPolicyVersion", "codeCommit", "imageSize",
                       "aspectRatio", "callAttemptIndex", "run", "case")


# ── 1. collector provenance ────────────────────────────────────────────────

def test_a_fresh_row_carries_every_required_provenance_field():
    """사후 보정 없이 그 자리에서 라벨을 만들 수 있어야 한다."""
    p = prov()
    assert [k for k in REQUIRED_PROVENANCE if p.get(k) is None] == []


def test_provenance_hashes_bind_the_actual_bytes():
    a = prov(b"s1", b"o1")
    b = prov(b"s1", b"o2")
    assert a["sourceSha256"] == b["sourceSha256"]
    assert a["outputSha256"] != b["outputSha256"]


def test_a_fresh_row_can_be_labeled_without_post_hoc_fixup():
    row = {"id": "s1", "case": "bg_only", "source": "a.jpg",
           "edit_type": "BACKGROUND_ONLY", "output_id": "o1",
           "provenance": prov()}
    rec = ba.make_label(sample=row, label="fidelity_pass", reviewer_id="me",
                        dataset_id="ds", now=1.0)
    assert rec["outputSha256"] == row["provenance"]["outputSha256"]


def test_resume_is_defined_and_callable():
    """호출은 있는데 정의가 없어 --resume 가 NameError 로 죽던 회귀."""
    assert callable(COLLECT._assert_resumable)
    src = (SERVER / "scripts" / "shadow_collect.py").read_text(encoding="utf-8")
    assert "def _assert_resumable" in src
    assert "_assert_resumable(samples_path, settings)" in src


def test_resume_accepts_a_missing_file(tmp_path):
    COLLECT._assert_resumable(tmp_path / "nope.jsonl")      # 새 데이터셋


def test_resume_refuses_rows_without_provenance(tmp_path):
    p = tmp_path / "samples.jsonl"
    p.write_text(json.dumps({"id": "a", "output_id": "o"}) + "\n")
    with pytest.raises(SystemExit) as e:
        COLLECT._assert_resumable(p)
    assert "missing_provenance" in str(e.value)


def test_resume_refuses_a_changed_generation_model(tmp_path):
    """codeCommit 은 보조 근거로 내려갔다 — 스냅샷 비교가 정본이다."""
    pr = prov()
    pr["run"]["generationModel"] = "other-model"
    p = tmp_path / "samples.jsonl"
    p.write_text(json.dumps({"id": "a", "output_id": "o", "provenance": pr}) + "\n")
    with pytest.raises(SystemExit) as e:
        COLLECT._assert_resumable(p)
    # 스텁 prepared 라 프롬프트 해시부터 어긋난다 — 어느 쪽이든 fail-closed 다.
    assert any(c in str(e.value) for c in
               ("generation_prompt_mismatch", "mixed_run_fingerprint", "generationModel"))


def test_resume_refuses_a_file_that_already_mixes_run_conditions(tmp_path):
    base = prov()
    other = json.loads(json.dumps(base))
    other["run"]["generationModel"] = "other-model"
    p = tmp_path / "samples.jsonl"
    p.write_text("".join(json.dumps({"id": i, "output_id": "o", "provenance": pr}) + "\n"
                         for i, pr in enumerate((base, other))))
    with pytest.raises(SystemExit) as e:
        COLLECT._assert_resumable(p)
    assert "mixed_run_fingerprint" in str(e.value)


def test_backfill_finds_images_next_to_the_samples_file():
    """--out 을 다시 조합하면 dataset 디렉터리가 빠져 전부 skip 된다."""
    src = (SERVER / "scripts" / "shadow_collect.py").read_text(encoding="utf-8")
    body = src[src.index("async def vision_backfill"):src.index("def _assert_resumable")
               if "def _assert_resumable" in src[src.index("async def vision_backfill"):]
               else len(src)]
    assert "out_dir = samples_file.parent" in src
    assert "out_dir = pathlib.Path(args.out)\n    rows" not in src


# ── 2. report CLI ──────────────────────────────────────────────────────────

def _rows(n, decision="pass", **kw):
    return [{"id": f"s{i}", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY", "image_calls": 1, "vision_calls": 1,
             "edit_qc_result": {"decision": decision,
                                "vision": {"meta": {"status": "ok"}}}, **kw}
            for i in range(n)]


def test_an_invalid_manifest_forces_every_verdict_to_blocked():
    out = sr.report(_rows(60, human_label="fidelity_pass"),
                    manifest_verification=_sv.unverified({'validForCalibration': False}, ["provenance_unverified"]),)
    ev = out["pipelines"]["editor_vary"]
    ready = [ev["verdict"]["enforceReady"]] + [
        t["verdict"]["enforceReady"] for t in ev["byEditTypeDetail"].values()]
    assert set(ready) == {False}
    assert ev["byEditTypeDetail"]["BACKGROUND_ONLY"]["verdict"]["status"] == \
        "blocked_by_manifest"


def test_a_valid_manifest_leaves_verdicts_alone():
    out = sr.report(_rows(60, human_label="fidelity_pass"),
                    manifest_verification=_trusted())
    v = out["pipelines"]["editor_vary"]["byEditTypeDetail"]["BACKGROUND_ONLY"]["verdict"]
    assert v["enforceReady"] is True


def test_quarantined_labels_are_reported_not_hidden():
    out = sr.report(_rows(2), quarantined=[{"reason": "output_hash_mismatch"},
                                           {"reason": "dataset_mismatch"}])
    q = out["labelQuarantine"]
    assert q["count"] == 2
    assert q["byReason"] == {"output_hash_mismatch": 1, "dataset_mismatch": 1}


def test_human_coverage_ignores_production_review_decisions():
    """reviewDecision 은 사용자 행동이다 — fidelity 커버리지로 세면 안 된다."""
    h = sr.human_label_coverage(_rows(10, review_decision="accepted"))
    assert h["passLabeled"] == 0 and h["labeledTotal"] == 0
    h2 = sr.human_label_coverage(_rows(10, human_label="fidelity_pass"))
    assert h2["passLabeled"] == 10


def test_the_cli_wires_labels_through_verify_and_a_blocked_report():
    """strict=True 로 죽던 걸 blocked report 로 바꿨다 — quarantine 이 도달 가능해졌다."""
    src = REPORT_CLI.read_text(encoding="utf-8")
    for token in ("--labels", "--manifest", "--dataset-id",
                  "ba.load_labels(", "ba.effective_labels(",
                  "strict=False", "manifest=manifest", "quarantined=quarantined",
                  "return 5 if blocked else 0"):
        assert token in src, token


def test_the_cli_stops_on_a_broken_chain_or_binding():
    src = REPORT_CLI.read_text(encoding="utf-8")
    assert "except ba.LabelChainError" in src and "return 4" in src
    assert "return 5 if blocked else 0" in src


# ── 3. labeling tool ───────────────────────────────────────────────────────

def test_dataset_id_is_never_guessed_from_the_directory_name():
    src = LABEL_CLI.read_text(encoding="utf-8")
    assert "def _resolve_dataset_id" in src
    assert "dataset_id = dataset_dir.name" not in src


def test_dataset_id_resolution_prefers_explicit_then_manifest(tmp_path):
    tool = _load("bl", "scripts/blinded_label.py")
    (tmp_path / "manifest.json").write_text(json.dumps({"datasetId": "from-manifest"}))
    assert tool._resolve_dataset_id(tmp_path, "explicit", None) == "explicit"
    assert tool._resolve_dataset_id(tmp_path, None, None) == "from-manifest"


def test_dataset_id_resolution_refuses_when_unknown(tmp_path):
    tool = _load("bl2", "scripts/blinded_label.py")
    with pytest.raises(SystemExit):
        tool._resolve_dataset_id(tmp_path, None, None)


def test_both_source_and_output_are_revalidated_before_saving():
    src = LABEL_CLI.read_text(encoding="utf-8")
    assert 'error": "output sha mismatch"' in src
    assert 'error": "source sha mismatch"' in src


def test_post_requires_nonce_origin_and_json():
    src = LABEL_CLI.read_text(encoding="utf-8")
    assert 'self.headers.get("X-QA-Nonce") != nonce' in src
    assert 'origin not in (f"http://127.0.0.1:{port}"' in src
    assert 'ctype != "application/json"' in src
    assert "secrets.token_urlsafe" in src


def test_the_server_binds_loopback_only():
    assert '("127.0.0.1", port)' in LABEL_CLI.read_text(encoding="utf-8")


# ── 4. chain verified inside the lock ──────────────────────────────────────

def test_append_verifies_the_existing_chain_under_the_lock():
    import inspect
    src = inspect.getsource(ba.append_label)
    assert "verify_chain(existing)" in src
    assert src.index("flock(fd, fcntl.LOCK_EX)") < src.index("verify_chain(existing)")


def test_appending_to_a_tampered_file_is_refused(tmp_path):
    path = tmp_path / "l.jsonl"
    sample = {"id": "s1", "case": "c", "source": "a.jpg", "edit_type": "T",
              "provenance": {"sourceSha256": "aa" * 32, "outputSha256": "bb" * 32}}
    mk = lambda t: ba.make_label(sample=sample, label="fidelity_pass",  # noqa: E731
                                 reviewer_id="me", dataset_id="d", now=t)
    ba.append_label(str(path), mk(1))
    ba.append_label(str(path), mk(2))
    lines = path.read_text().splitlines()
    row = json.loads(lines[0]); row["label"] = "fidelity_fail"
    lines[0] = json.dumps(row, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ba.LabelChainError):
        ba.append_label(str(path), mk(3))
