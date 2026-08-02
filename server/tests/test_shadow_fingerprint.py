"""Phase 3 P0-C 9/N 최종 — resume fingerprint · Vision provenance · 경로 봉쇄 · blocked 계약.

여기 있는 테스트는 전부 "실제로 재현한 결함"에서 나왔다. 이전 구현은
generation prompt 를 run 동일성 키에 넣어 멀쩡한 multi-case 데이터셋을 거부했고,
Vision provenance 로 템플릿 해시를 적어 case 별 차이를 지웠으며, POST 는 경로를
검사하지 않았고, quarantine 집계는 도달할 수 없는 죽은 코드였다.
"""

import importlib.util
import json
import pathlib

import pytest

from app import blinded_audit as ba
from app import shadow_report as sr
from app import shadow_verification as _sv
from app.agents import cut_variator, edit_intent_vision
from app.agents.gemini_image import InlineImage
from app.config import load_settings
from app.services import edit_qc_scope, editor_vary

SERVER = pathlib.Path(__file__).resolve().parents[1]

# trusted 는 중앙 verifier 만 만든다 — 실제 파일·manifest 를 통과시킨 결과다.
from _shadow_trusted import trusted as _trusted  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, SERVER / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = _load("sc_fp", "scripts/shadow_collect.py")
BL = _load("bl_fp", "scripts/blinded_label.py")
REPORT_CLI = SERVER / "scripts" / "shadow_report.py"


@pytest.fixture(scope="module")
def env():
    s = load_settings()
    src = sorted((SERVER.parent / "public" / "assets" / "fit-examples").glob("*.jpg"))[0]
    raw = src.read_bytes()
    return s, src, raw, InlineImage("image/jpeg", raw)


def _rows(env, cases, vision_sha="a" * 64):
    s, src, raw, img = env
    out = []
    for name, changes in cases:
        prep = cut_variator.prepare(s, img, changes, None)
        out.append({"id": name, "output_id": "o", "case": name, "source": src.name,
                    "provenance": SC._provenance(
                        prep, case_name=name, changes=changes, attempt=1,
                        source_bytes=raw, output_bytes=b"x",
                        vision_meta={"promptSha256": vision_sha, "provider": "p",
                                     "status": "ok"})})
    return out


def _write(tmp_path, rows):
    p = tmp_path / "samples.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return p


# ── 1. run/case fingerprint 분리 ───────────────────────────────────────────

def test_normal_multi_case_prompts_differ(env):
    """case 마다 프롬프트가 다른 건 정상이다 — 이걸 거부하던 게 결함이었다."""
    rows = _rows(env, SC.VARY_CASES)
    shas = {r["provenance"]["case"]["generationPromptSha256"] for r in rows}
    assert len(shas) == len(SC.VARY_CASES) >= 2


def test_multi_case_dataset_resumes(env, tmp_path):
    SC._assert_resumable(_write(tmp_path, _rows(env, SC.VARY_CASES)), env[0])


def test_a_case_subset_also_resumes(env, tmp_path):
    SC._assert_resumable(_write(tmp_path, _rows(env, SC.VARY_CASES[:2])), env[0])


def _mutated(env, mutate, cases=None):
    rows = _rows(env, cases or SC.VARY_CASES)
    mutate(rows)
    return rows


@pytest.mark.parametrize("key,value", [
    ("generationModel", "other-model"),
    ("generationTemplateSha256", "0" * 64),
    ("visionTemplateSha256", "0" * 64),
    ("visionPromptTemplateVersion", "v9"),
    ("qcPolicyVersion", "v9"),
    ("caseSetSha256", "0" * 64),
    ("imageSize", "999K"),
    ("aspectRatio", "1:1"),
])
def test_a_changed_run_condition_is_refused(env, tmp_path, key, value):
    rows = _mutated(env, lambda rs: [r["provenance"]["run"].__setitem__(key, value)
                                     for r in rs])
    with pytest.raises(SystemExit) as e:
        SC._assert_resumable(_write(tmp_path, rows), env[0])
    # 공통 validator 가 먼저 잡으면 문제 코드로, 아니면 필드 이름으로 나온다.
    assert key in str(e.value) or "case_set_mismatch" in str(e.value)


def test_a_changed_case_prompt_is_refused(env, tmp_path):
    rows = _mutated(env, lambda rs: rs[0]["provenance"]["case"].__setitem__(
        "generationPromptSha256", "0" * 64))
    with pytest.raises(SystemExit) as e:
        SC._assert_resumable(_write(tmp_path, rows), env[0])
    assert "generation_prompt_mismatch" in str(e.value)


def test_a_removed_case_definition_is_refused(env, tmp_path):
    rows = _rows(env, SC.VARY_CASES[:1])
    rows[0]["provenance"]["case"]["case"] = "gone-case"
    with pytest.raises(SystemExit) as e:
        SC._assert_resumable(_write(tmp_path, rows), env[0])
    assert "unknown_case:gone-case" in str(e.value)


def test_a_changed_case_definition_is_refused(env, tmp_path):
    rows = _rows(env, SC.VARY_CASES[:1])
    rows[0]["provenance"]["case"]["changes"] = [{"type": "pose", "value": "다른 값"}]
    with pytest.raises(SystemExit) as e:
        SC._assert_resumable(_write(tmp_path, rows), env[0])
    assert "case_changes_mismatch" in str(e.value)


def test_rows_without_fingerprints_are_refused(tmp_path):
    p = tmp_path / "samples.jsonl"
    p.write_text(json.dumps({"id": "a", "output_id": "o"}) + "\n")
    with pytest.raises(SystemExit) as e:
        SC._assert_resumable(p)
    assert "missing_provenance" in str(e.value)


def test_failed_rows_are_not_provenance_evidence(env, tmp_path):
    """출력이 없는 row 는 무엇으로 만들어졌는지 증명하지 못한다."""
    p = tmp_path / "samples.jsonl"
    p.write_text(json.dumps({"id": "a", "case": "bg_only"}) + "\n")
    SC._assert_resumable(p)                       # 거부하지 않는다


def test_code_commit_alone_does_not_refuse(env, tmp_path, capsys):
    """보조 근거다 — 스냅샷이 모두 같으면 커밋 차이로 막지 않는다."""
    rows = _mutated(env, lambda rs: [r["provenance"].__setitem__("codeCommit", "0" * 40)
                                     for r in rs])
    SC._assert_resumable(_write(tmp_path, rows), env[0])
    assert "codeCommit 이 다릅니다" in capsys.readouterr().out


def test_mixed_run_conditions_in_one_file_are_refused(env, tmp_path):
    rows = _rows(env, SC.VARY_CASES[:2])
    rows[1]["provenance"]["run"]["generationModel"] = "other"
    with pytest.raises(SystemExit) as e:
        SC._assert_resumable(_write(tmp_path, rows), env[0])
    assert "mixed_run_fingerprint" in str(e.value)


def test_prepare_only_makes_no_provider_call(env, monkeypatch):
    """resume 검증은 prepare 까지만 — execute 가 불리면 실패한다."""
    def boom(*a, **k):
        raise AssertionError("provider 를 불렀다")
    monkeypatch.setattr(cut_variator, "execute", boom)
    monkeypatch.setattr(edit_intent_vision, "observe", boom)
    run, cases = SC._prepare_only(env[0])
    assert run["generationModel"] and len(cases) == len(SC.VARY_CASES)


# ── 2. Vision provenance ───────────────────────────────────────────────────

def _vision_prompt(changes):
    scope = editor_vary.semantic_scope(changes)
    return edit_intent_vision.build_prompt(
        edit_type=editor_vary.edit_type_for(changes),
        adjustments={"changes": changes},
        allowed_scope=edit_qc_scope.vision_scope(scope))


def test_different_cases_render_different_vision_prompts():
    a, b = SC.VARY_CASES[0][1], SC.VARY_CASES[1][1]
    assert edit_intent_vision.prompt_sha256(_vision_prompt(a)) != \
        edit_intent_vision.prompt_sha256(_vision_prompt(b))


def test_template_hash_is_not_the_rendered_prompt_hash():
    rendered = edit_intent_vision.prompt_sha256(_vision_prompt(SC.VARY_CASES[0][1]))
    assert rendered != edit_intent_vision.template_sha256()


def test_provenance_records_the_rendered_prompt_not_the_template(env):
    rows = _rows(env, SC.VARY_CASES[:1], vision_sha="b" * 64)
    prov = rows[0]["provenance"]
    assert prov["visionPromptSha256"] == "b" * 64
    assert prov["visionTemplateSha256"] == edit_intent_vision.template_sha256()


def test_observe_meta_carries_the_prompt_hash_from_the_same_builder():
    import inspect
    src = inspect.getsource(edit_intent_vision.observe)
    # prepare 결과를 그대로 쓴다 — 기록용으로 다시 조립하지 않는다.
    assert '"promptSha256": prep.prompt_sha256' in src
    assert '"templateSha256": prep.template_sha256' in src
    assert "prompt = prep.prompt" in src


def test_backfill_refuses_mismatched_conditions_before_calling_the_provider():
    src = (SERVER / "scripts" / "shadow_collect.py").read_text(encoding="utf-8")
    body = src[src.index("async def vision_backfill"):src.index("async def run(args)")]
    assert body.index("_refuse(") < body.index("observe_and_decide(")
    for k in ("visionTemplateSha256", "visionPromptTemplateVersion", "qcPolicyVersion"):
        assert k in body
    assert "결과 이미지가 바뀌었어요" in body and "원본 이미지가 바뀌었어요" in body


def test_backfill_records_lineage_not_a_silent_overwrite():
    src = (SERVER / "scripts" / "shadow_collect.py").read_text(encoding="utf-8")
    for k in ("visionBackfilledAt", "visionProvider", "visionStatus",
              "visionPromptSha256", "qcPolicyVersion"):
        assert f'prov["{k}"]' in src


def test_failure_meta_leaks_no_prompt_or_url():
    from app.agents.edit_intent_vision import failure_meta
    meta = failure_meta(RuntimeError("https://host/v1?key=SECRET body PROMPT"))
    flat = json.dumps(meta)
    for leak in ("SECRET", "https://", "PROMPT", "body"):
        assert leak not in flat


# ── 5. template hash 정본 ──────────────────────────────────────────────────

def test_generation_template_hash_exists_and_is_stable():
    a = cut_variator.template_sha256()
    assert a and a == cut_variator.template_sha256() and len(a) == 64


def test_no_fake_template_version_constant_is_reported(env):
    """null 을 "버전"이라고 적어 있는 것처럼 보고하지 않는다."""
    prov = _rows(env, SC.VARY_CASES[:1])[0]["provenance"]
    assert prov["generationTemplateSha256"] == cut_variator.template_sha256()
    assert "generationPromptTemplateVersion" not in prov


# ── 3. 경로 봉쇄 ───────────────────────────────────────────────────────────

@pytest.fixture
def sandbox(tmp_path):
    base = tmp_path / "ds"
    base.mkdir()
    (base / "ok.png").write_bytes(b"PNG")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"SECRET")
    (base / "link.png").symlink_to(outside / "secret.txt")
    return base, outside


def test_a_normal_name_resolves(sandbox):
    base, _ = sandbox
    assert BL.safe_resolve(base, "ok", BL._SAFE_ID).name == "ok.png"


@pytest.mark.parametrize("name", [
    "../../etc/hosts", "/etc/hosts", "..", ".", "a/b", "a\\b", "s\x00.png",
    "http://x/y.png", "", "-leading", "x" * 200,
])
def test_hostile_names_are_refused(sandbox, name):
    base, _ = sandbox
    with pytest.raises(BL.UnsafePath):
        BL.safe_resolve(base, name, BL._SAFE_ID)


def test_a_symlink_out_of_the_base_is_refused(sandbox):
    base, _ = sandbox
    with pytest.raises(BL.UnsafePath):
        BL.safe_resolve(base, "link", BL._SAFE_ID)


def test_a_refused_path_is_never_read(sandbox, monkeypatch):
    """거부는 '읽고 나서 버리기'가 아니라 '읽지 않기'여야 한다."""
    base, _ = sandbox
    reads = []
    real = pathlib.Path.read_bytes
    monkeypatch.setattr(pathlib.Path, "read_bytes",
                        lambda self, *a, **k: (reads.append(str(self)), real(self))[1])
    for name in ("../../etc/hosts", "link", "/etc/hosts"):
        with pytest.raises(BL.UnsafePath):
            BL.safe_resolve(base, name, BL._SAFE_ID)
    assert reads == []


def test_get_and_post_share_one_resolver():
    src = (SERVER / "scripts" / "blinded_label.py").read_text(encoding="utf-8")
    assert src.count("safe_resolve(") >= 4
    # 직접 이어 붙이던 경로가 남아 있으면 한쪽만 안전해진다.
    assert 'img = dataset_dir / f"{sid}.png"' not in src
    assert "src = src_dir / str(row.get" not in src


def test_a_missing_file_is_refused_like_an_unsafe_one(sandbox):
    base, _ = sandbox
    with pytest.raises(BL.UnsafePath):
        BL.safe_resolve(base, "nope", BL._SAFE_ID)


# ── 4. quarantine / blocked 계약 ───────────────────────────────────────────

def _sample_rows(n=3, **kw):
    return [{"id": f"s{i}", "source_kind": "editor_asset", "output_id": "o",
             "edit_type": "BACKGROUND_ONLY", "image_calls": 1, "vision_calls": 1,
             "edit_qc_result": {"decision": "pass",
                                "vision": {"meta": {"status": "ok"}}}, **kw}
            for i in range(n)]


def test_quarantine_blocks_the_whole_report():
    out = sr.report(_sv.distribution_dataset(_sample_rows(3, human_label="fidelity_pass")),
                    quarantined=[{"reason": "output_hash_mismatch"}])
    assert out["calibrationUsable"] is False
    assert set(out["calibrationBlockedReasons"]) >= set(["label_output_hash_mismatch"])
    ev = out["pipelines"]["editor_vary"]
    ready = [ev["verdict"]["enforceReady"]] + [
        t["verdict"]["enforceReady"] for t in ev["byEditTypeDetail"].values()]
    assert set(ready) == {False}
    assert ev["verdict"]["status"] == "blocked_by_labels"


def test_quarantine_counts_are_reported_by_reason():
    out = sr.report(_sv.distribution_dataset(_sample_rows(2)), quarantined=[
        {"reason": "dataset_mismatch"}, {"reason": "dataset_mismatch"},
        {"reason": "policy_version_unsupported"}])
    assert out["labelQuarantine"]["byReason"] == {
        "dataset_mismatch": 2, "policy_version_unsupported": 1}


def test_clean_labels_keep_the_normal_calculation():
    out = sr.report(_sv.distribution_dataset(_sample_rows(3, human_label="fidelity_fail")))
    cal = out["pipelines"]["editor_vary"]["calibrationConfusion"]
    assert cal["graded"] == 3 and cal["falsePass"] == 3
    assert "labelQuarantine" not in out


def test_review_decision_is_not_counted_as_a_fidelity_label():
    h = sr.human_label_coverage(_sample_rows(5, review_decision="accepted"))
    assert h["passLabeled"] == 0


def test_the_cli_reaches_the_blocked_report_instead_of_dying_early():
    src = REPORT_CLI.read_text(encoding="utf-8")
    assert "bind_verified_labels(" in src   # 라벨도 typed 변환이다
    assert "return 5 if blocked else 0" in src
    assert "return 4" in src                # 체인 손상은 리포트조차 만들지 않는다
    assert "byReason" in src
