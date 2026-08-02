"""Phase 3 P0-C 9/N — provenance 계약 한 벌.

정본이 둘이면 검증은 통과 도장을 찍는 절차가 된다. 실제로 수집기는 nested
run/case 를 쓰는데 manifest 는 옛 평면 필드만 봐서, run/case 가 아예 없는 legacy 도
run 이 섞인 dataset 도 validForCalibration=true 를 받았다.
"""

import copy
import importlib.util
import json
import pathlib

import pytest

from app import shadow_provenance as sp
from app import shadow_report as sr
from app.agents import cut_variator, edit_intent_vision
from app.agents.gemini_image import InlineImage
from app.config import load_settings

SERVER = pathlib.Path(__file__).resolve().parents[1]

# trusted 는 중앙 verifier 만 만든다 — 실제 파일·manifest 를 통과시킨 결과다.
from _shadow_trusted import trusted as _trusted  # noqa: E402
from app import shadow_verification as _sv  # noqa: E402


def _mod(name, rel):
    spec = importlib.util.spec_from_file_location(name, SERVER / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = _mod("sc_prov", "scripts/shadow_collect.py")
SM = _mod("sm_prov", "scripts/shadow_manifest.py")


@pytest.fixture(scope="module")
def fresh():
    s = load_settings()
    src = sorted((SERVER.parent / "public" / "assets" / "fit-examples").glob("*.jpg"))[0]
    raw = src.read_bytes()
    img = InlineImage("image/jpeg", raw)
    rows = []
    for i, (name, ch) in enumerate(SC.VARY_CASES):
        prep = cut_variator.prepare(s, img, ch, None)
        vp = SC.vision_prepared(ch)
        rows.append({"id": name, "output_id": f"o{i}", "case": name, "source": src.name,
                     "source_kind": "editor_asset",
                     "edit_type": SC.editor_vary.edit_type_for(ch),
                     "provenance": SC._provenance(
                         prep, case_name=name, changes=ch, attempt=1,
                         source_bytes=raw, output_bytes=b"x" + bytes([i]),
                         vision_meta={"promptSha256": vp.prompt_sha256,
                                      "provider": "p", "status": "ok"})})
    return rows


def _cases():
    return SC.normalized_cases()


def _mut(rows, fn):
    r = copy.deepcopy(rows)
    fn(r)
    return r


# ── 1. 공통 validator ──────────────────────────────────────────────────────

def test_a_fresh_multi_case_dataset_is_valid(fresh):
    assert sp.validate_dataset(fresh, expected_cases=_cases()) == []


def test_legacy_flat_only_rows_are_invalid(fresh):
    rows = _mut(fresh, lambda rs: [
        r.__setitem__("provenance", {k: v for k, v in r["provenance"].items()
                                     if k not in ("run", "case")}) for r in rs])
    problems = sp.validate_dataset(rows, expected_cases=_cases())
    assert "missing_run_fingerprint" in problems
    assert "missing_case_fingerprint" in problems


def test_mixed_run_fingerprints_are_invalid(fresh):
    rows = _mut(fresh, lambda rs: rs[1]["provenance"]["run"].__setitem__(
        "generationModel", "other"))
    assert "mixed_run_fingerprint" in sp.validate_dataset(rows)


def test_the_same_case_with_two_prompts_is_invalid(fresh):
    rows = copy.deepcopy(fresh)
    dup = copy.deepcopy(rows[0])
    dup["id"] = "dup"
    dup["provenance"]["case"]["generationPromptSha256"] = "0" * 64
    rows.append(dup)
    assert any(p.startswith("inconsistent_case_fingerprint:")
               for p in sp.validate_dataset(rows))


def test_a_case_set_mismatch_is_invalid(fresh):
    rows = _mut(fresh, lambda rs: [r["provenance"]["run"].__setitem__(
        "caseSetSha256", "0" * 64) for r in rs])
    assert "case_set_mismatch" in sp.validate_dataset(rows, expected_cases=_cases())


def test_an_unknown_case_name_is_invalid(fresh):
    rows = _mut(fresh, lambda rs: rs[0]["provenance"]["case"].__setitem__(
        "case", "ghost"))
    assert "unknown_case:ghost" in sp.validate_dataset(rows, expected_cases=_cases())


@pytest.mark.parametrize("field", sp.CASE_KEYS)
def test_a_missing_nested_case_field_is_invalid(fresh, field):
    rows = _mut(fresh, lambda rs: [r["provenance"]["case"].pop(field, None)
                                   for r in rs])
    assert any(p.startswith("missing_case_field:") or p == "case_set_mismatch"
               or p.startswith("unknown_case")
               for p in sp.validate_dataset(rows, expected_cases=_cases()))


@pytest.mark.parametrize("field", sp.RUN_KEYS)
def test_a_missing_nested_run_field_is_invalid(fresh, field):
    rows = _mut(fresh, lambda rs: [r["provenance"]["run"].pop(field, None)
                                   for r in rs])
    assert f"missing_run_field:{field}" in sp.validate_dataset(rows)


@pytest.mark.parametrize("field", ("sourceSha256", "outputSha256", "qcPolicyVersion"))
def test_a_missing_row_field_is_invalid(fresh, field):
    rows = _mut(fresh, lambda rs: [r["provenance"].pop(field, None) for r in rs])
    assert f"missing_row_field:{field}" in sp.validate_dataset(rows)


def test_a_dataset_with_no_output_rows_is_not_silently_valid(fresh):
    """출력이 없으면 '문제 없음'이 아니라 '쓸 수 없음'이다 — 추측해 채우지 않는다."""
    rows = [{**r, "output_id": None} for r in fresh]
    assert sp.validate_dataset(rows) == ["no_output_rows"]


def test_failed_rows_do_not_break_a_valid_dataset(fresh):
    rows = [*copy.deepcopy(fresh), {"id": "failed", "case": "bg_only"}]
    assert sp.validate_dataset(rows, expected_cases=_cases()) == []


def test_changes_may_be_an_empty_list(fresh):
    """빈 changes 는 '비슷한 컷'의 정상값이라 누락으로 세면 안 된다."""
    rows = _mut(fresh, lambda rs: rs[0]["provenance"]["case"].__setitem__("changes", []))
    assert not any(p.startswith("missing_case_field:changes")
                   for p in sp.validate_dataset(rows))


# ── manifest 가 같은 validator 를 쓴다 ────────────────────────────────────

def _manifest(tmp_path, rows, name="s"):
    """manifest 는 이제 실제 파일까지 대조한다 — 픽스처도 파일을 만들어야 한다."""
    for i, r in enumerate(rows):
        prov = r.get("provenance") or {}
        if prov.get("outputSha256"):
            (tmp_path / f"{r['id']}.png").write_bytes(b"x" + bytes([i]))
    p = tmp_path / "samples.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return SM.build(str(p), dataset_id="ds", invalid_reasons=[], image_usd=0,
                    vision_usd=0, collected_at="t", command=None)


def test_manifest_accepts_a_fresh_dataset(tmp_path, fresh):
    m = _manifest(tmp_path, fresh)
    assert m["validForCalibration"] is True and m["provenanceProblems"] == []


def test_manifest_rejects_legacy_flat_only(tmp_path, fresh):
    rows = _mut(fresh, lambda rs: [
        r.__setitem__("provenance", {k: v for k, v in r["provenance"].items()
                                     if k not in ("run", "case")}) for r in rs])
    m = _manifest(tmp_path, rows, "legacy")
    assert m["validForCalibration"] is False
    assert "provenance_unverified" in m["invalidReasons"]


def test_manifest_rejects_mixed_runs(tmp_path, fresh):
    rows = _mut(fresh, lambda rs: rs[1]["provenance"]["run"].__setitem__(
        "generationModel", "other"))
    m = _manifest(tmp_path, rows, "mixed")
    assert m["validForCalibration"] is False
    assert "mixed_run_fingerprint" in m["provenanceProblems"]


def test_manifest_model_comes_from_the_nested_run(tmp_path, fresh):
    m = _manifest(tmp_path, fresh)
    assert m["model"] == fresh[0]["provenance"]["run"]["generationModel"]


# ── 2. Vision prompt 가 case fingerprint 에 들어간다 ──────────────────────

def test_each_case_has_its_own_vision_prompt_hash(fresh):
    shas = {r["provenance"]["case"]["visionPromptSha256"] for r in fresh}
    assert len(shas) == len(SC.VARY_CASES) >= 2


def test_vision_prepare_matches_the_execution_builder():
    changes = SC.VARY_CASES[0][1]
    prep = SC.vision_prepared(changes)
    assert prep.prompt_sha256 == edit_intent_vision.prompt_sha256(prep.prompt)
    assert prep.template_sha256 == edit_intent_vision.template_sha256()
    assert prep.prompt_sha256 != prep.template_sha256


def test_observe_uses_the_prepared_prompt():
    import inspect
    src = inspect.getsource(edit_intent_vision.observe)
    assert "prep = prepared or prepare(" in src and "prompt = prep.prompt" in src
    assert '"promptSha256": prep.prompt_sha256' in src


def test_prepare_makes_no_provider_call(monkeypatch):
    monkeypatch.setattr(edit_intent_vision, "analyze_with_fallback",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("호출됨")))
    assert SC.vision_prepared(SC.VARY_CASES[0][1]).prompt_sha256


@pytest.mark.parametrize("field", ("visionPromptSha256", "editType"))
def test_resume_refuses_a_changed_case_condition(fresh, tmp_path, field):
    rows = _mut(fresh, lambda rs: rs[0]["provenance"]["case"].__setitem__(field, "zz"))
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    with pytest.raises(SystemExit) as e:
        SC._assert_resumable(p, load_settings())
    code = {"visionPromptSha256": "vision_prompt_mismatch",
            "editType": "case_edit_type_mismatch"}[field]
    assert code in str(e.value)


def test_resume_accepts_normal_multi_case(fresh, tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in fresh))
    SC._assert_resumable(p, load_settings())


# ── 3. backfill preflight ─────────────────────────────────────────────────

def test_backfill_uses_the_common_validator_and_has_no_fallback():
    src = (SERVER / "scripts" / "shadow_collect.py").read_text(encoding="utf-8")
    body = src[src.index("async def vision_backfill"):src.index("async def run(args)")]
    assert "sp.validate_dataset(" in body
    assert "case_changes.get(" not in body          # 미상 case → [] 폴백 금지
    for token in ("editType", "generationPromptSha256", "visionPromptSha256",
                  "결과 이미지가 바뀌었어요", "원본 이미지가 바뀌었어요"):
        assert token in body, token
    # 모든 거부가 provider 호출 전에 끝난다.
    assert body.index("observe_and_decide(") > body.rindex("_refuse(")


# ── 4. Vision 실패 provenance ─────────────────────────────────────────────

def test_failure_meta_keeps_the_prompt_hash():
    prep = SC.vision_prepared(SC.VARY_CASES[0][1])
    meta = edit_intent_vision.failure_meta(TimeoutError("x"), prep)
    assert meta["promptSha256"] == prep.prompt_sha256
    assert meta["templateSha256"] == prep.template_sha256
    assert meta["status"] == "timeout"


def test_failure_meta_only_has_whitelisted_keys():
    prep = SC.vision_prepared(SC.VARY_CASES[0][1])
    meta = edit_intent_vision.failure_meta(RuntimeError("x"), prep)
    assert set(meta) == set(edit_intent_vision.FAILURE_META_KEYS)


def test_failure_meta_leaks_nothing():
    prep = SC.vision_prepared(SC.VARY_CASES[0][1])
    meta = edit_intent_vision.failure_meta(
        RuntimeError("https://h/v1?key=SECRET body " + prep.prompt[:80]), prep)
    flat = json.dumps(meta)
    for leak in ("SECRET", "https://", "key=", prep.prompt[:40]):
        assert leak not in flat


def test_the_collector_passes_prepared_to_failure_meta():
    src = (SERVER / "scripts" / "shadow_collect.py").read_text(encoding="utf-8")
    assert "edit_intent_vision.failure_meta(e, prep)" in src


# ── 5. blocked reason union ───────────────────────────────────────────────

def _rows(n=2, **kw):
    return [{"id": f"s{i}", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY",
             "edit_qc_result": {"decision": "pass",
                                "vision": {"meta": {"status": "ok"}}}, **kw}
            for i in range(n)]


def test_manifest_and_label_reasons_are_unioned():
    out = sr.report(_sv.unverified_dataset(
        _rows(), {"validForCalibration": False}, ["provenance_unverified"]),
        quarantined=[{"reason": "dataset_mismatch"},
                     {"reason": "output_hash_mismatch"}])
    assert set(out["calibrationBlockedReasons"]) >= {
        "label_dataset_mismatch", "label_output_hash_mismatch", "provenance_unverified"}


def test_duplicate_reasons_are_collapsed():
    out = sr.report(_sv.distribution_dataset(_rows()),
                    quarantined=[{"reason": "dataset_mismatch"}] * 3)
    assert set(out["calibrationBlockedReasons"]) >= set(["label_dataset_mismatch"])


def test_a_combined_failure_gets_a_combined_status():
    out = sr.report(_sv.unverified_dataset(_rows(), {'validForCalibration': False}, ["x"]),
                    quarantined=[{"reason": "dataset_mismatch"}])
    ev = out["pipelines"]["editor_vary"]
    assert ev["verdict"]["status"] == "blocked_by_manifest_and_labels"
    assert set(ev["verdict"]["blockers"]) >= {b for b in ev["verdict"]["blockers"]
                                              if "manifest" in b or "라벨" in b}


def test_either_source_alone_still_blocks():
    only_manifest = sr.report(_sv.unverified_dataset(_rows(), {'validForCalibration': False}, ["x"]),)
    only_labels = sr.report(_sv.distribution_dataset(_rows()), quarantined=[{"reason": "dataset_mismatch"}])
    for out, status in ((only_manifest, "blocked_by_manifest"),
                        (only_labels, "blocked_by_labels")):
        ev = out["pipelines"]["editor_vary"]
        assert out["calibrationUsable"] is False
        assert ev["verdict"]["status"] == status
        assert {ev["verdict"]["enforceReady"],
                *(t["verdict"]["enforceReady"]
                  for t in ev["byEditTypeDetail"].values())} == {False}


def test_a_clean_run_has_no_blocked_reasons():
    out = sr.report(_sv.distribution_dataset(_rows(2, human_label="fidelity_pass")),
                    )
    # manifest 없는 리포트는 언제나 분포다 — 사유에 manifest_absent 만 남는다.
    assert set(out["calibrationBlockedReasons"]) == {"manifest_absent"}
