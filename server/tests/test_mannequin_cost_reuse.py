"""마네킹 생성 비용 절감(2026-09-23 오너 원칙: "생성 비용을 최대한 아껴야 한다. 이미 만든 결과가
있으면 그대로 쓴다").

운영(2026-09-23): 회색 니트 상의(상품) + 코디 카펜터 팬츠 잡이 5번 연속 실패했다. 사유는
final_fit_rejected ×2, final_edit_preservation_rejected ×1, OpenAI 500 ×2. 상품은 이미지 QC 를
통과했는데 매칭 바지 판정이 identityPass 를 떨어뜨렸고, 실패마다 산 이미지(~$0.20)와 QC 를 버렸다.

  A. 매칭(코디) 아이템 문제만 남으면 경고로 남기고 출고한다. 상품 쪽 관문은 그대로다.
  B. 실패한 잡의 유료 최선본을 초안으로 남기고, 같은 입력의 다음 잡이 편집 경로로 이어 쓴다.
  C. 과금 없는 일시 장애(500·503·연결 실패)는 같은 호출을 한 번만 다시 보낸다.
  D. 배포 드레인 취소(CancelledError)는 worker_shutdown 으로 닫고 예약을 푼다.

유료 호출·이미지 생성은 전부 가짜다.
"""
import asyncio
import contextlib
import types
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app import repo
from app.agents import gemini_image, image_qc, mannequin_fit_qc as fq
from app.agents.gemini_image import GeminiError, InlineImage
from app.workers import mannequin_job as job
from conftest import make_settings
import test_mannequin_final_untuck_qc as harness
from test_mannequin_quality_repair import rated


def _png(color=(120, 120, 120)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 6), color).save(buf, "PNG")
    return buf.getvalue()


PNG_A = _png((10, 20, 30))
PNG_B = _png((200, 10, 10))


@pytest.fixture(autouse=True)
def _no_transient_sleep(monkeypatch):
    # 실제 3초 대기 없이 재시도 경로만 검증한다.
    monkeypatch.setattr(job, "_TRANSIENT_RETRY_DELAY_SECONDS", 0)


# ═══ A. 매칭 아이템은 상품 출고를 막지 않는다 ═══════════════════════════════════

def _spec():
    return fq.declared_axis_spec({"category": "top", "gender": "women",
                                  "axes": {"fit": "slim", "length": "long"}})


def _axis(axis, target, ok=True, visible=True):
    return {"axis": axis, "target": target, "pass": ok, "visible": visible,
            "observedLandmark": "seen"}


def test_fit_qc_without_matching_keeps_schema_prompt_and_shape_unchanged():
    schema = fq.qc_schema(_spec())
    assert schema["required"] == ["identityPass", "axisPass", "mismatches"]
    assert "matchingIdentityPass" not in schema["properties"]
    prompt = fq.build_prompt(1, False, _spec())
    assert "matchingIdentityPass" not in prompt and "MATCHING" not in prompt
    v = fq.validate({"identityPass": True, "mismatches": [],
                     "axisPass": [_axis("fit", "slim"), _axis("length", "long")]}, _spec())
    assert set(v) == {"identityPass", "axisPass", "mismatches"}


def test_fit_qc_with_matching_splits_product_identity_from_matching_identity():
    schema = fq.qc_schema(_spec(), matching=True)
    assert "matchingIdentityPass" in schema["required"]
    prompt = fq.build_prompt(1, True, _spec())
    assert "MATCHING BOTTOM" in prompt
    assert "identityPass judges ONLY the main product" in prompt
    assert "matchingIdentityPass" in prompt
    # 예전 규칙("must contain it and preserve its visible identity" → identityPass)은 없다.
    assert "If a matching bottom photo is provided" not in prompt
    raw = {"identityPass": True, "matchingIdentityPass": False, "mismatches": ["pocket stitch"],
           "axisPass": [_axis("fit", "slim"), _axis("length", "long")]}
    v = fq.validate(raw, _spec(), matching=True)
    assert v["identityPass"] is True and v["matchingIdentityPass"] is False
    # 매칭 판정이 빠지거나 형식이 틀려도 상품 판정은 버리지 않는다(경고 전용 → None).
    v = fq.validate({**raw, "matchingIdentityPass": "no"}, _spec(), matching=True)
    assert v["matchingIdentityPass"] is None


def test_fit_qc_labels_a_matching_top_as_top():
    prompt = fq.build_prompt(1, True, _spec(), match_role="top")
    assert "MATCHING TOP" in prompt and "MATCHING BOTTOM" not in prompt


def test_fit_qc_verdict_sends_matching_schema_only_with_a_match_image(monkeypatch):
    seen = []

    async def analyze(settings, prompt, images, schema, **kwargs):
        seen.append(schema)
        return ({"identityPass": True, "matchingIdentityPass": True, "mismatches": [],
                 "axisPass": [_axis("fit", "slim"), _axis("length", "long")]}, "gemini")

    monkeypatch.setattr(fq, "analyze_with_fallback", analyze)
    profile = {"category": "top", "gender": "women", "axes": {"fit": "slim", "length": "long"}}
    gen = InlineImage("image/png", b"gen")
    with_match = asyncio.run(fq.verdict(make_settings(), [gen], gen, profile,
                                        InlineImage("image/png", b"m")))
    without = asyncio.run(fq.verdict(make_settings(), [gen], gen, profile))
    assert "matchingIdentityPass" in seen[0]["properties"]
    assert "matchingIdentityPass" not in seen[1]["properties"]
    assert with_match["matchingIdentityPass"] is True
    assert "matchingIdentityPass" not in without


@pytest.mark.parametrize("pants_qc,clothing_type,expect_match", [
    ("enforce", "top", True),      # 상의 상품 + 하의 매칭 + 바지 QC → 첨부(판정은 분리)
    ("off", "top", False),         # 바지 QC 꺼짐 → 매칭 참조 없음
    ("enforce", "bottom", False),  # 하의 상품이면 매칭은 상의 → 붙이지 않는다
])
def test_axis_qc_attaches_matching_reference_only_through_pants_ref(
        monkeypatch, pants_qc, clothing_type, expect_match):
    seen = []

    async def fake_fit(settings, prods, gen_img, fit_profile, match_image=None):
        seen.append(match_image)
        return {"identityPass": True, "mismatches": [],
                "axisPass": [_axis("fit", "slim"), _axis("length", "long")]}

    async def emit(*args, **kwargs):
        return None

    monkeypatch.setattr(job.mannequin_fit_qc, "verdict", fake_fit)
    monkeypatch.setattr(job, "_emit", emit)
    match = InlineImage("image/png", b"match")
    asyncio.run(job._apply_axis_qc(
        pool=None, gemini=None, s=make_settings(mannequin_axis_qc="shadow", mannequin_pants_qc=pants_qc),
        job_id="j", candidate="A", attempt=1, model="m",
        res=SimpleNamespace(image=b"img", mime="image/png"), prod_imgs=[match],
        match_img=match, fit_profile={"category": "top", "gender": "women",
                                      "axes": {"fit": "slim", "length": "long"}},
        profile_hash="h", calls_spent=1, clothing_type=clothing_type))
    assert seen == [match if expect_match else None]


def test_edit_accepted_can_downgrade_matching_only_to_a_warning():
    ok = harness.scored(95)
    matching_only = harness.scored(95, matching_critical=["matching bottom structure changed"])
    product_bad = harness.scored(95, critical=["logo altered"],
                                 matching_critical=["matching bottom structure changed"])
    assert image_qc.edit_accepted(ok) and image_qc.edit_accepted(ok, matching_blocks=False)
    # 기본값은 지금처럼 매칭도 거절한다(중간 편집은 편집 전으로 되돌리면 되므로).
    assert not image_qc.edit_accepted(matching_only)
    assert image_qc.edit_accepted(matching_only, matching_blocks=False)
    # 상품 치명 오류는 어떤 경우에도 거절.
    assert not image_qc.edit_accepted(product_bad, matching_blocks=False)


def _fit_verdicts(monkeypatch, *, final_identity=True, final_matching=False, calls=None):
    async def fake_fit(settings, prods, gen_img, fit_profile, match_image=None):
        if calls is not None:
            calls.append((gen_img.data, match_image))
        final = gen_img.data == b"final"
        return {"identityPass": final_identity if final else True,
                "matchingIdentityPass": final_matching if final else True,
                "mismatches": ["Generated pants are missing the angled front pocket stitching"]
                if final and not final_matching else [],
                "axisPass": [_axis("length", "basic")]}

    monkeypatch.setattr(job.mannequin_fit_qc, "verdict", fake_fit)


def test_final_fit_matching_identity_failure_is_a_warning_not_final_fit_rejected(monkeypatch):
    """운영 사례의 재현: 상품 수정은 성공, 최종 핏 QC 가 코디 바지 스티치만 지적 → 출고."""
    calls = []
    _fit_verdicts(monkeypatch, final_identity=True, final_matching=False, calls=calls)
    result, seen = harness.run_worker(
        monkeypatch, pants_mode="enforce",
        settings_overrides={"mannequin_max_attempts": 1, "mannequin_axis_qc": "enforce"},
        generated=(b"first", b"final"),
        p2={b"first": rated(logo_graphic="critical"), b"final": rated()})
    assert seen.puts == [b"final"]
    assert result["qc_scores"]["matching_review_only"] is True
    assert "final_fit_matching_identity" in result["qc_scores"]["matching_warning_phases"]
    assert result["qc_scores"]["outcome"] == "needs_review"
    # 상의 상품 + 하의 매칭 + 바지 QC enforce → 최종 핏 판정에 매칭 참조가 붙는다.
    assert calls[-1] == (b"final", harness.MATCHING)


def test_final_fit_product_identity_failure_still_rejects(monkeypatch):
    _fit_verdicts(monkeypatch, final_identity=False, final_matching=True)
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError, match="final_fit_rejected"):
        harness.run_worker(
            monkeypatch, pants_mode="enforce", captures=seen,
            settings_overrides={"mannequin_max_attempts": 1, "mannequin_axis_qc": "enforce"},
            generated=(b"first", b"final"),
            p2={b"first": rated(logo_graphic="critical"), b"final": rated()})
    assert seen.puts == []


def test_final_matching_review_unavailable_is_a_warning(monkeypatch):
    """수정본의 매칭 판정이 비어도(matching_fidelity None) 상품이 통과면 잡을 버리지 않는다."""
    final = {**rated(), "matching_fidelity": None}
    result, seen = harness.run_worker(
        monkeypatch, pants_mode="enforce", settings_overrides={"mannequin_max_attempts": 1},
        generated=(b"first", b"final"),
        p2={b"first": rated(logo_graphic="critical"), b"final": final})
    assert seen.puts == [b"final"]
    assert "final_matching_unavailable" in result["qc_scores"]["matching_warning_phases"]


def test_no_matching_item_keeps_the_existing_final_repair_contract(monkeypatch):
    """매칭이 없으면 동작이 그대로다 — 경고 표시도 없다."""
    result, seen = harness.run_worker(
        monkeypatch, has_match=False, settings_overrides={"mannequin_max_attempts": 1},
        generated=(b"first", b"final"),
        p2={b"first": rated(logo_graphic="critical"), b"final": rated()})
    assert seen.puts == [b"final"]
    assert "matching_review_only" not in result["qc_scores"]
    assert not any(e.get("status") == "matching_warning" for e in seen.events)


# ═══ B. 유료 최선본 초안 보존·재사용 ═════════════════════════════════════════════

def test_draft_sink_keeps_the_better_paid_candidate():
    s = make_settings(image_qc="enforce")
    sink = job._PaidDraftSink(s)
    first = SimpleNamespace(image=b"first", mime="image/png")
    repaired = SimpleNamespace(image=b"repaired", mime="image/png")
    worse = SimpleNamespace(image=b"worse", mime="image/png")
    sink.offer(first, job.merge_qc_scores(rated(logo_graphic="critical"), None), "generated")
    sink.offer(repaired, job.merge_qc_scores(rated(), None), "repaired")
    sink.offer(worse, job.merge_qc_scores(rated(color="critical"), None), "edited")
    assert sink.best[0] is repaired and sink.best[2] == "repaired"
    sink.offer(None, None, "none")  # 이미지 없는 제안은 무시
    assert sink.best[0] is repaired


def test_run_candidate_feeds_every_paid_image_to_the_draft_sink(monkeypatch):
    sink = job._PaidDraftSink(make_settings(image_qc="enforce"))
    with pytest.raises(job.MannequinQualityError):
        harness.run_worker(
            monkeypatch, has_match=False, settings_overrides={"mannequin_max_attempts": 1},
            generated=(b"first", b"final"),
            p2={b"first": rated(logo_graphic="critical"),
                b"final": {**rated(), "target_resolved": False}},
            candidate_kwargs={"draft_sink": sink})
    # 수정본이 보존 판정에 떨어졌어도 1차본보다 나으면 초안 후보는 수정본이다.
    assert sink.best is not None and sink.best[0].image == b"final"


def test_draft_fingerprint_changes_with_any_input():
    base = dict(clothing_type="top", base_asset_id="bw", product_assets=[("Front", "p1")],
                match_asset_id="m1", match_item_id="item-1",
                fit_profile={"category": "top", "axes": {"fit": "slim"}},
                source_mirrored=False, prompt_version="v1")
    fp = job.mannequin_draft_fingerprint(**base)
    assert fp == job.mannequin_draft_fingerprint(**deepcopy(base))
    for change in (
        {"product_assets": [("Front", "p2")]},
        {"match_asset_id": "m2"},
        {"fit_profile": {"category": "top", "axes": {"fit": "regular"}}},
        {"base_asset_id": "bm"},
        {"prompt_version": "v2"},
        {"source_mirrored": True},
    ):
        assert job.mannequin_draft_fingerprint(**{**base, **change}) != fp, change
    key = job.mannequin_draft_key("u1", "p1", fp)
    assert key == f"users/u1/projects/p1/ai/mannequin-drafts/{fp}"


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


class _Storage:
    def __init__(self):
        self.objects = {
            "bw.png": b"base", "prod.png": b"product", "prod2.png": b"product-2",
            "match.png": b"match",
        }
        self.puts = []

    def get_bytes(self, key):
        return self.objects[key]

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append({"key": key, "data": data, "mime": mime, "cache": cache})
        self.objects[key] = data


class _DraftLedger:
    """ai_output_cleanup_intents 의 초안 행을 흉내낸다(키 → 보존 중 여부)."""

    def __init__(self):
        self.rows = {}

    async def save(self, conn, *, job_id, r2_key, retain_seconds):
        self.rows[r2_key] = {"job_id": job_id, "retain": retain_seconds, "live": True}
        return "intent-" + job_id

    async def get_live(self, conn, r2_key, **kwargs):
        row = self.rows.get(r2_key)
        return {"id": "x", "job_id": row["job_id"], "r2_key": r2_key} if row and row["live"] else None

    async def expire(self, conn, r2_key):
        if r2_key in self.rows:
            self.rows[r2_key]["live"] = False


PROFILE = {"category": "top", "gender": "women", "source": "seller",
           "axes": {"fit": "slim"}, "version": 1}


def _wire_job(monkeypatch, *, ledger, storage, runner, product_asset="prod", profile=PROFILE,
              draft_reuse="on", cancelled=lambda: False):
    calls = {"success": [], "failure": [], "emits": []}

    async def get_product(conn, project_id):
        return {"name": "니트", "clothing_type": "top",
                "colors": [{"isBase": True, "images": [{"id": product_asset, "slot": "Front"}]}]}

    async def get_analysis(conn, project_id):
        return {"targetGenders": ["women"], "fit": "regular", "fitProfile": profile}

    async def get_asset_for_user(conn, user_id, asset_id):
        return {"bw": {"id": "bw", "mime_type": "image/png", "r2_key": "bw.png"},
                "prod": {"id": "prod", "mime_type": "image/png", "r2_key": "prod.png"},
                "prod2": {"id": "prod2", "mime_type": "image/png", "r2_key": "prod2.png"},
                }.get(asset_id)

    async def finalize_success(conn, **kwargs):
        calls["success"].append(kwargs)
        return {"cuts": kwargs["candidates"], "available": 5}

    async def finalize_failure(conn, **kwargs):
        calls["failure"].append(kwargs)
        return True

    async def is_job_cancelled(conn, job_id):
        return cancelled()

    async def emit(pool, job_id, event_type, payload):
        calls["emits"].append((event_type, dict(payload)))

    async def no_refs(*args, **kwargs):
        return [], []

    for name, fn in (
        ("get_product", get_product), ("get_analysis", get_analysis),
        ("get_asset_for_user", get_asset_for_user), ("is_job_cancelled", is_job_cancelled),
        ("finalize_mannequin_success", finalize_success),
        ("finalize_mannequin_failure", finalize_failure),
        ("save_mannequin_draft_intent", ledger.save),
        ("get_live_mannequin_draft_intent", ledger.get_live),
        ("expire_mannequin_draft_intent", ledger.expire),
    ):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(job, "_emit", emit)
    monkeypatch.setattr(job, "_load_style_refs", no_refs)
    monkeypatch.setattr(job, "_run_candidate", runner)
    settings = make_settings(base_mannequin_women_asset_id="bw", base_mannequin_men_asset_id="bm",
                             r2_bucket="bucket", mannequin_prompt_version="v1",
                             mannequin_draft_reuse=draft_reuse)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=storage, gemini=None))
    return app, calls


def _job(job_id, profile=PROFILE, mode="generate"):
    return {"id": job_id, "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
            "credits_reserved": 2,
            "payload": {"mode": mode, "fitProfileSnapshot": {
                "version": 1, "profile": profile, "adjustedAxes": []}}}


def _failing_runner(image=PNG_A, reason="final_fit_rejected", seen=None):
    async def runner(**kwargs):
        if seen is not None:
            seen.append(kwargs)
        kwargs["draft_sink"].offer(
            SimpleNamespace(image=image, mime="image/png"),
            job.merge_qc_scores(rated(), None), "repaired")
        raise job.MannequinQualityError(reason)
    return runner


def _passing_runner(seen):
    async def runner(**kwargs):
        seen.append(kwargs)
        return {"asset_id": "a1", "bucket": "bucket", "key": "cut.png", "mime": "image/png",
                "size": 1, "width": 1, "height": 1, "candidate": kwargs["candidate"],
                "base_fit": kwargs["base_fit"], "qc_scores": None, "generation_metadata": {}}
    return runner


def _statuses(calls, status):
    return [p for kind, p in calls["emits"] if p.get("status") == status]


def test_quality_failure_saves_the_best_paid_image_as_a_ttl_draft(monkeypatch):
    ledger, storage = _DraftLedger(), _Storage()
    app, calls = _wire_job(monkeypatch, ledger=ledger, storage=storage, runner=_failing_runner())
    asyncio.run(job.run_mannequin_job(app, _job("job-1")))

    assert calls["failure"][0]["code"] == "mannequin_quality_failed"
    [put] = storage.puts
    assert put["key"].startswith("users/u1/projects/p1/ai/mannequin-drafts/")
    assert put["data"] == PNG_A and put["cache"] == "private, no-store"
    row = ledger.rows[put["key"]]
    assert row["live"] and row["retain"] == 24 * 3600 and row["job_id"] == "job-1"
    [saved] = _statuses(calls, "draft_saved")
    assert saved["reason"] == "quality:final_fit_rejected" and saved["stage"] == "repaired"
    assert saved["qcScores"]["product_fidelity"] == 95


def test_next_job_with_the_same_inputs_reuses_the_draft_via_the_edit_path(monkeypatch):
    ledger, storage = _DraftLedger(), _Storage()
    app, _ = _wire_job(monkeypatch, ledger=ledger, storage=storage, runner=_failing_runner())
    asyncio.run(job.run_mannequin_job(app, _job("job-1")))
    draft_key = storage.puts[0]["key"]

    seen = []
    app, calls = _wire_job(monkeypatch, ledger=ledger, storage=storage,
                           runner=_passing_runner(seen))
    asyncio.run(job.run_mannequin_job(app, _job("job-2")))

    [kwargs] = seen
    assert kwargs["generation_path"] == "edit"
    assert kwargs["parent_cut_img"].data == PNG_A
    assert kwargs["parent_cut_img"].mime == "image/png"
    assert "MAIN PRODUCT — identity" in kwargs["adjust_directives"]
    assert "MAIN PRODUCT — fit" in kwargs["adjust_directives"]  # 선언 축도 다시 지시
    assert _statuses(calls, "draft_reused")
    metadata = calls["success"][0]["candidates"][0]["generation_metadata"]
    assert metadata["generationPath"] == "edit" and metadata["draftReused"] is True
    assert metadata["parentCutId"] is None and metadata["editDepth"] == 1
    # 새 컷이 생겼으니 초안은 곧바로 정리 대상이 된다.
    assert ledger.rows[draft_key]["live"] is False


@pytest.mark.parametrize("mismatch", ["profile", "product"])
def test_draft_is_not_reused_when_any_input_differs(monkeypatch, mismatch):
    ledger, storage = _DraftLedger(), _Storage()
    app, _ = _wire_job(monkeypatch, ledger=ledger, storage=storage, runner=_failing_runner())
    asyncio.run(job.run_mannequin_job(app, _job("job-1")))

    seen = []
    other = {**PROFILE, "axes": {"fit": "regular"}}
    app, calls = _wire_job(
        monkeypatch, ledger=ledger, storage=storage, runner=_passing_runner(seen),
        product_asset="prod2" if mismatch == "product" else "prod",
        profile=other if mismatch == "profile" else PROFILE)
    asyncio.run(job.run_mannequin_job(
        app, _job("job-2", profile=other if mismatch == "profile" else PROFILE)))
    assert seen[0]["generation_path"] == "fresh"
    assert seen[0]["parent_cut_img"] is None
    assert not _statuses(calls, "draft_reused")


def test_a_draft_is_reused_only_once_and_a_failed_reuse_saves_no_new_draft(monkeypatch):
    ledger, storage = _DraftLedger(), _Storage()
    app, _ = _wire_job(monkeypatch, ledger=ledger, storage=storage, runner=_failing_runner())
    asyncio.run(job.run_mannequin_job(app, _job("job-1")))
    draft_key = storage.puts[0]["key"]

    seen = []
    app, calls = _wire_job(monkeypatch, ledger=ledger, storage=storage,
                           runner=_failing_runner(image=PNG_B, seen=seen))
    asyncio.run(job.run_mannequin_job(app, _job("job-2")))
    assert seen[0]["generation_path"] == "edit"
    assert len(storage.puts) == 1, "이어 쓴 잡은 새 초안을 만들지 않는다(편집 누적 방지)"
    assert ledger.rows[draft_key]["live"] is False, "쓴 초안은 소모 처리된다"

    seen = []
    app, _ = _wire_job(monkeypatch, ledger=ledger, storage=storage, runner=_passing_runner(seen))
    asyncio.run(job.run_mannequin_job(app, _job("job-3")))
    assert seen[0]["generation_path"] == "fresh"


def test_undecodable_draft_bytes_fall_back_to_fresh_generation(monkeypatch):
    ledger, storage = _DraftLedger(), _Storage()
    app, _ = _wire_job(monkeypatch, ledger=ledger, storage=storage,
                       runner=_failing_runner(image=b"not-an-image"))
    asyncio.run(job.run_mannequin_job(app, _job("job-1")))
    seen = []
    app, calls = _wire_job(monkeypatch, ledger=ledger, storage=storage,
                           runner=_passing_runner(seen))
    asyncio.run(job.run_mannequin_job(app, _job("job-2")))
    assert seen[0]["generation_path"] == "fresh"
    assert not _statuses(calls, "draft_reused")


def test_draft_reuse_off_neither_saves_nor_reuses(monkeypatch):
    ledger, storage = _DraftLedger(), _Storage()
    app, calls = _wire_job(monkeypatch, ledger=ledger, storage=storage,
                           runner=_failing_runner(), draft_reuse="off")
    asyncio.run(job.run_mannequin_job(app, _job("job-1")))
    assert storage.puts == [] and ledger.rows == {}
    assert calls["failure"][0]["code"] == "mannequin_quality_failed"


def test_explicit_seller_adjust_edit_still_takes_priority_over_a_draft(monkeypatch):
    ledger, storage = _DraftLedger(), _Storage()
    app, _ = _wire_job(monkeypatch, ledger=ledger, storage=storage, runner=_failing_runner())
    asyncio.run(job.run_mannequin_job(app, _job("job-1")))

    parent = {"id": "A-3", "r2_key": "parent.png", "mime_type": "image/png",
              "generation_metadata": {"profileCategory": "top", "profileGender": "women",
                                      "matchItemId": None, "editDepth": 0}}
    storage.objects["parent.png"] = PNG_B

    async def get_parent(conn, user_id, project_id):
        return parent

    seen = []
    app, calls = _wire_job(monkeypatch, ledger=ledger, storage=storage,
                           runner=_passing_runner(seen))
    monkeypatch.setattr(repo, "get_mannequin_edit_parent", get_parent)
    regenerate = _job("job-2", mode="regenerate")
    regenerate["payload"]["fitProfileSnapshot"]["adjustedAxes"] = ["fit"]
    asyncio.run(job.run_mannequin_job(app, regenerate))
    assert seen[0]["parent_cut_img"].data == PNG_B, "셀러가 조정한 부모 컷이 우선"
    assert not _statuses(calls, "draft_reused")


def test_draft_repo_functions_use_the_cleanup_ledger_with_a_ttl():
    executed = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))

        async def fetchone(self):
            return {"id": "intent-1", "job_id": "j", "r2_key": "k"}

    class Conn:
        def cursor(self):
            return Cursor()

    conn = Conn()
    assert asyncio.run(repo.save_mannequin_draft_intent(
        conn, job_id="j", r2_key="k", retain_seconds=86400)) == "intent-1"
    sql, params = executed[-1]
    assert "insert into ai_output_cleanup_intents" in sql
    assert "now() + (%s * interval '1 second')" in sql and "on conflict (r2_key)" in sql
    assert params == ("j", "k", 86400)
    asyncio.run(repo.get_live_mannequin_draft_intent(conn, "k"))
    sql, params = executed[-1]
    assert "status = 'pending'" in sql and "not_before > now()" in sql and params == ("k", 60)
    asyncio.run(repo.expire_mannequin_draft_intent(conn, "k"))
    sql, params = executed[-1]
    assert sql.startswith("update ai_output_cleanup_intents set not_before = now()")
    # 커서가 없는 연결(테스트 대역·미배선)은 아무것도 하지 않는다 → 새 생성으로 폴백.
    assert asyncio.run(repo.save_mannequin_draft_intent(
        object(), job_id="j", r2_key="k", retain_seconds=1)) is None
    assert asyncio.run(repo.get_live_mannequin_draft_intent(object(), "k")) is None


# ═══ C. 과금 없는 일시 장애 1회 재시도 ═══════════════════════════════════════════

class _Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
        self.calls += 1
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(image=out, mime="image/png")


def _call_with_retry(provider):
    events = []

    async def emit(pool, job_id, kind, payload):
        events.append(payload)

    async def run():
        job._emit, original = emit, job._emit
        try:
            return await job._generate_with_transient_retry(
                provider, "m", "p", [], "1K", aspect_ratio="2:3", pool=None, job_id="j",
                candidate="A", phase="generation")
        finally:
            job._emit = original

    return run, events


def test_unbilled_5xx_is_retried_exactly_once():
    provider = _Provider([GeminiError("OpenAI 500", transient=True), b"ok"])
    run, events = _call_with_retry(provider)
    assert asyncio.run(run()).image == b"ok"
    assert provider.calls == 2
    assert events[0]["status"] == "transient_retry" and events[0]["phase"] == "generation"


def test_second_transient_failure_is_not_retried_again():
    provider = _Provider([GeminiError("500", transient=True), GeminiError("500", transient=True)])
    run, _ = _call_with_retry(provider)
    with pytest.raises(GeminiError):
        asyncio.run(run())
    assert provider.calls == 2


@pytest.mark.parametrize("error", [
    GeminiError("read timeout", billable=True),
    GeminiError("502", billable=True, transient=True),  # 과금 가능성이 있으면 transient 무효
    GeminiError("400 bad request"),
    GeminiError("응답에 이미지 없음"),
])
def test_billed_or_permanent_errors_are_never_resent(error):
    provider = _Provider([error, b"never"])
    run, events = _call_with_retry(provider)
    with pytest.raises(GeminiError):
        asyncio.run(run())
    assert provider.calls == 1 and events == []


def test_generation_500_is_retried_inside_the_same_budget_slot(monkeypatch):
    result, seen = harness.run_worker(
        monkeypatch, has_match=False, settings_overrides={"mannequin_max_attempts": 1},
        generated=(GeminiError("OpenAI 500", transient=True), b"before"),
        p2={b"before": rated()})
    assert seen.image_calls == ["generate", "generate"]
    assert seen.puts == [b"before"]
    assert any(e.get("status") == "transient_retry" for e in seen.events)


def test_final_repair_500_is_retried_instead_of_discarding_paid_work(monkeypatch):
    result, seen = harness.run_worker(
        monkeypatch, has_match=False, settings_overrides={"mannequin_max_attempts": 1},
        generated=(b"first", GeminiError("OpenAI 500", transient=True), b"final"),
        p2={b"first": rated(logo_graphic="critical"), b"final": rated()})
    assert seen.image_calls == ["generate", "generate", "generate"]
    assert seen.puts == [b"final"]
    assert result["qc_scores"]["quality_repair_used"] is True


def test_final_repair_billable_failure_still_ends_without_resend(monkeypatch):
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError, match="final_generation_failed"):
        harness.run_worker(
            monkeypatch, has_match=False, captures=seen,
            settings_overrides={"mannequin_max_attempts": 1},
            generated=(b"first", GeminiError("lost response", billable=True), b"never"),
            p2={b"first": rated(logo_graphic="critical")})
    assert seen.image_calls == ["generate", "generate"]


def _settings():
    return SimpleNamespace(gemini_api_key="k", openai_api_key="o", vertex_project=None,
                           vertex_location="global")


@pytest.mark.parametrize("model", ["gpt-image-2", "gemini-3-pro-image"])
@pytest.mark.parametrize("status,billable,transient", [
    (500, False, True), (503, False, True), (502, True, False), (504, True, False),
    (400, False, False),
])
def test_provider_errors_carry_the_transient_flag(monkeypatch, model, status, billable, transient):
    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            return SimpleNamespace(status_code=status, text="err", headers={}, json=lambda: {})

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    monkeypatch.setattr(gemini_image.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: None)
    with pytest.raises(GeminiError) as raised:
        asyncio.run(gemini_image.GeminiImageClient(_settings()).generate_content_image(
            model, "p", [InlineImage("image/png", b"x")], "1K", aspect_ratio="2:3"))
    assert raised.value.billable is billable
    assert raised.value.transient is transient


@pytest.mark.parametrize("error,transient", [
    (httpx.ConnectError("offline"), True), (httpx.ReadTimeout("slow"), False)])
def test_openai_transport_errors_carry_the_transient_flag(monkeypatch, error, transient):
    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            raise error

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: None)
    with pytest.raises(GeminiError) as raised:
        asyncio.run(gemini_image.GeminiImageClient(_settings()).generate_content_image(
            "gpt-image-2", "p", [InlineImage("image/png", b"x")], "1K", aspect_ratio="2:3"))
    assert raised.value.transient is transient


# ═══ D. 배포 드레인 취소 → worker_shutdown 종결 ══════════════════════════════════

def _cancel_mid_generation(monkeypatch, *, offer_image=False, user_cancelled=False):
    ledger, storage = _DraftLedger(), _Storage()

    async def runner(**kwargs):
        if offer_image:
            kwargs["draft_sink"].offer(SimpleNamespace(image=PNG_A, mime="image/png"),
                                       job.merge_qc_scores(rated(), None), "generated")
        runner.started.set()
        await asyncio.Event().wait()  # 생성 호출이 도는 중

    app, calls = _wire_job(monkeypatch, ledger=ledger, storage=storage, runner=runner,
                           cancelled=lambda: user_cancelled)

    async def scenario():
        runner.started = asyncio.Event()
        task = asyncio.create_task(job.run_mannequin_job(app, _job("job-1")))
        await runner.started.wait()
        task.cancel()  # dispatcher.stop() 드레인 시간 초과
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    return calls, storage, ledger


def test_drain_cancellation_finalizes_worker_shutdown_and_releases_the_reservation(monkeypatch):
    calls, storage, _ = _cancel_mid_generation(monkeypatch)
    [failure] = calls["failure"]
    assert failure["code"] == "worker_shutdown"
    assert failure["metadata"] == {"error": "worker_shutdown"}
    assert failure["reserved"] == 2 and failure["settle_key"] == "credit:job:job-1:settle"
    assert calls["success"] == []
    assert storage.puts == []  # 산 이미지가 없으면 초안도 없다


def test_drain_cancellation_keeps_an_already_paid_image_as_a_draft(monkeypatch):
    calls, storage, ledger = _cancel_mid_generation(monkeypatch, offer_image=True)
    assert calls["failure"][0]["code"] == "worker_shutdown"
    [put] = storage.puts
    assert put["data"] == PNG_A and ledger.rows[put["key"]]["live"]
    assert _statuses(calls, "draft_saved")[0]["reason"] == "worker_shutdown"


def test_drain_cancellation_after_user_cancel_does_not_overwrite_the_cancel_route(monkeypatch):
    calls, _, _ = _cancel_mid_generation(monkeypatch, user_cancelled=True)
    assert calls["failure"] == [] and calls["success"] == []
