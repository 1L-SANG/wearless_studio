"""Visible fidelity, independent requests and strict evidence contracts."""
import asyncio
import hashlib
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from app.agents import mannequin_specialist_qc as qc, vision_llm
from app.agents.gemini_image import InlineImage
from app.agents.product_reference import ProductReference


def png(color, size=(120, 180)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return InlineImage("image/png", output.getvalue())


FRONT, BACK, DETAIL, CANDIDATE, MATCH = [png(c) for c in ("red", "blue", "green", "white", "black")]
REFS = [ProductReference("Back", "b", BACK), ProductReference("Front", "f", FRONT),
        ProductReference("Detail", "d", DETAIL)]
SETTINGS = SimpleNamespace(mannequin_specialist_model="gpt-6-astra", analysis_timeout_seconds=30,
                           mannequin_specialist_timeout_seconds=120.0)


def passed(**changes):
    return {"verdict": "pass", "summary": "Visible features match.", "issues": [],
            "confirmedMatches": ["visible structure"], "visibilityLimitations": [],
            "materialUncertainties": [], **changes}


def evidence(index):
    return {"imageIndex": index, "region": {"left": .1, "top": .1, "right": .8, "bottom": .8},
            "observation": "A visibly supported comparison."}


def issue(kind="design_line_change", materiality="material"):
    return {"kind": kind, "materiality": materiality, "sourceEvidence": evidence(1),
            "candidateEvidence": evidence(2), "repairInstruction": "Restore the photographed front seam."}


MANIFEST = [{"kind": "source"}, {"kind": "candidate"}, {"kind": "matching"}]


def test_visibility_only_pass_is_accepted_and_never_becomes_a_repair():
    raw = passed(visibilityLimitations=["The back is not visible."])
    assert qc.validate_response(raw, MANIFEST, "details") == raw
    report = {"complete": True, "verdict": "pass", "roles": {
        role: {"status": "ok", "response": raw} for role in ("structure", "details", "appearance")}}
    assert qc.repair_accepted(report)
    assert qc.repair_instructions(report) == []


@pytest.mark.parametrize("kind", ["design_line_change", "construction_change"])
def test_evidenced_missing_or_added_front_seam_is_material_and_keeps_exact_repair(kind):
    item = issue(kind)
    raw = passed(verdict="fail", issues=[item])
    assert qc.validate_response(raw, MANIFEST, "details") == raw
    report = {"complete": False, "verdict": "fail", "roles": {"details": {"status": "ok", "response": raw}}}
    assert qc.blocking_issues(report) == [item]
    assert qc.repair_instructions(report) == [item["repairInstruction"]]
    assert not qc.repair_accepted(report)


def test_visible_material_uncertainty_requires_review_without_prescribing_an_edit():
    raw = passed(verdict="review", materialUncertainties=["Visible line may be an added seam."])
    assert qc.validate_response(raw, MANIFEST, "details") == raw
    with pytest.raises(ValueError):
        qc.validate_response({**raw, "verdict": "pass"}, MANIFEST, "details")


@pytest.mark.parametrize("change", [
    lambda row: row["sourceEvidence"].update(imageIndex=2),
    lambda row: row["sourceEvidence"].update(imageIndex=3),
    lambda row: row["candidateEvidence"].update(imageIndex=1),
    lambda row: row["candidateEvidence"].update(imageIndex=99),
    lambda row: row["candidateEvidence"].update(imageIndex=True),
    lambda row: row["sourceEvidence"]["region"].update(left=.9, right=.2),
    lambda row: row["sourceEvidence"]["region"].update(top=-.1),
    lambda row: row["sourceEvidence"]["region"].update(left=float("nan")),
    lambda row: row.update(repairInstruction=""),
    lambda row: row.update(repairInstruction="x" * 221),
])
def test_wrong_evidence_or_unbounded_repair_cannot_become_a_valid_defect(change):
    item = issue()
    change(item)
    with pytest.raises(ValueError):
        qc.validate_response(passed(verdict="fail", issues=[item]), MANIFEST, "details")


def test_schema_consistency_and_role_scope_are_enforced():
    for raw in (passed(extra=True), passed(verdict="fail"), passed(issues=[issue()]),
                passed(issues=[issue(materiality="minor")] * 4)):
        with pytest.raises(ValueError):
            qc.validate_response(raw, MANIFEST, "details")
    with pytest.raises(ValueError):
        qc.validate_response(passed(verdict="fail", issues=[issue()]), MANIFEST, "appearance")


def test_partial_or_failed_roles_never_approve_a_repair():
    rows = {role: {"status": "ok", "response": passed()} for role in ("structure", "details")}
    assert not qc.repair_accepted({"complete": True, "verdict": "pass", "roles": rows})
    rows["appearance"] = {"status": "error", "error_type": "VisionError"}
    assert not qc.repair_accepted({"complete": True, "verdict": "pass", "roles": rows})


def test_three_roles_run_independently_with_bounded_calls_and_real_crop_provenance(monkeypatch):
    calls = []

    async def run():
        all_started = asyncio.Event()

        async def transport(settings, model, prompt, images, schema, timeout, **kwargs):
            calls.append((model, prompt, images, schema, timeout, kwargs))
            if len(calls) == 3:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), 1)
            kwargs["metadata"].update(requested_model=model, returned_model=model,
                                      usage={"prompt_tokens": 100, "completion_tokens": 20}, finish_reason="stop")
            return passed(summary="PRIVATE ROLE RESULT MUST NEVER ENTER ANOTHER PROMPT")

        monkeypatch.setattr(vision_llm, "_call_gpt", transport)
        return await qc.judge(SETTINGS, REFS, CANDIDATE, clothing_type="bottom", match_image=MATCH,
                              fit_profile={"category": "bottom", "axes": {"fit": "wide"}})

    report = asyncio.run(run())
    assert report["verdict"] == "pass" and report["complete"]
    assert report["image_hash"] == hashlib.sha256(CANDIDATE.data).hexdigest()
    assert set(report["roles"]) == {"structure", "details", "appearance"}
    assert len(calls) == 3
    assert len({id(c[-1]["metadata"]) for c in calls}) == 3
    assert len({c[1] for c in calls}) == 3
    for model, prompt, images, schema, timeout, options in calls:
        assert model == "gpt-6-astra" and timeout == 120.0
        assert timeout != SETTINGS.analysis_timeout_seconds
        assert options["reasoning_effort"] == "medium"
        assert options["image_detail"] == "high" and options["max_completion_tokens"] == 1800
        assert schema["properties"]["issues"]["maxItems"] == 3
        assert "PRIVATE ROLE RESULT" not in prompt
        assert "Source Front" in prompt and "Source Back" in prompt and "Source Detail" in prompt
        assert "Matching garment identity" in prompt
        assert FRONT in images and BACK in images and DETAIL in images and CANDIDATE in images and MATCH in images
    for role in report["roles"].values():
        assert role["metadata"]["usage"]["prompt_tokens"] == 100
        crops = [m for m in role["images"] if "parentImageIndex" in m]
        assert {m["kind"] for m in crops} == {"source", "candidate"}
        for crop in crops:
            assert len(crop["parentPixelBox"]) == 4 and len(crop["parentNormalizedBox"]) == 4
            assert crop["parentSha256"] == role["images"][crop["parentImageIndex"] - 1]["sha256"]


def test_one_provider_error_is_review_and_not_retried_or_hidden(monkeypatch):
    count = 0

    async def transport(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            raise vision_llm.VisionError("secret must not appear in report")
        return passed()

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    report = asyncio.run(qc.judge(SETTINGS, REFS, CANDIDATE, clothing_type="top"))
    assert count == 3 and report["verdict"] == "review" and not report["complete"]
    assert not qc.repair_accepted(report)
    assert "secret" not in str(report)


def test_invalid_inputs_do_not_spend_calls(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid images must not reach transport")

    monkeypatch.setattr(vision_llm, "_call_gpt", forbidden)
    for refs, candidate in (([], CANDIDATE), (REFS, InlineImage("image/png", b"broken"))):
        report = asyncio.run(qc.judge(SETTINGS, refs, candidate, clothing_type="top"))
        assert report["verdict"] == "review" and not report["complete"]


def test_review_and_minor_issues_are_not_sent_as_material_repairs():
    raw = passed(issues=[issue(materiality="minor")], visibilityLimitations=["back hidden"])
    report = {"complete": True, "verdict": "pass", "roles": {"details": {"status": "ok", "response": raw}}}
    assert qc.repair_instructions(report) == []


def test_malformed_complete_report_does_not_approve_repair():
    report = {"complete": True, "verdict": "pass", "roles": {
        role: {"status": "ok", "response": {}} for role in ("structure", "details", "appearance")}}
    assert not qc.repair_accepted(report)


def test_one_error_does_not_erase_an_independent_material_failure(monkeypatch):
    count = 0

    async def transport(settings, model, prompt, images, schema, timeout, **kwargs):
        nonlocal count
        count += 1
        if "ASSIGNED ROLE: structure" in prompt:
            raise vision_llm.VisionError("unavailable")
        if "ASSIGNED ROLE: details" in prompt:
            item = issue()
            # Front, Back, Detail, source crop, candidate whole, two candidate crops.
            item["candidateEvidence"]["imageIndex"] = 5
            return passed(verdict="fail", issues=[item])
        return passed()

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    report = asyncio.run(qc.judge(SETTINGS, REFS, CANDIDATE, clothing_type="top"))
    assert count == 3 and report["verdict"] == "fail" and not report["complete"]
    assert len(qc.blocking_issues(report)) == 1
    assert not qc.repair_accepted(report)


def test_repair_image_gets_three_fresh_calls_and_never_borrows_another_images_scores(monkeypatch):
    calls = []

    async def transport(*args, **kwargs):
        calls.append(args)
        return passed()

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    first = asyncio.run(qc.judge(SETTINGS, REFS, CANDIDATE, clothing_type="bottom"))
    second = asyncio.run(qc.judge(SETTINGS, REFS, png("yellow"), clothing_type="bottom"))
    assert len(calls) == 6 and first["image_hash"] != second["image_hash"]
    for row in first["roles"].values():
        main = next(im for im in row["images"] if im["kind"] == "candidate" and
                    im.get("parentNormalizedBox") == [.18, .40, .85, .96])
        assert main["parentPixelBox"] == [22, 72, 102, 173]


def test_whole_garments_keep_lower_details_in_the_domain_crop(monkeypatch):
    async def transport(*args, **kwargs):
        return passed()

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    report = asyncio.run(qc.judge(SETTINGS, REFS, CANDIDATE, clothing_type="dress"))
    regions = [im["parentNormalizedBox"] for im in report["roles"]["details"]["images"]
               if im["kind"] == "source" and "parentNormalizedBox" in im]
    assert regions == [[0, .10, 1, 1]]


def test_other_source_slots_retain_their_real_view_label(monkeypatch):
    async def transport(*args, **kwargs):
        return passed()

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    refs = [ProductReference("Side", "side", FRONT)]
    report = asyncio.run(qc.judge(SETTINGS, refs, CANDIDATE, clothing_type="outer"))
    for row in report["roles"].values():
        assert row["images"][0]["slot"] == "Side"
        assert row["images"][0]["label"] == "Source Side photograph"


_REGULAR_FIT = {"category": "top", "axes": {"fit": "regular"}}
# Frozen before adding mirrored-source support. Default requests must preserve
# the live calibration packet, including its pre-existing role-specific fit block.
_UNMIRRORED_PROMPT_HASHES = {
    "structure": "1d1f0f9bfdbd2ea1837a2eeb467376c8f89a863216fce5749c01f0ee597357bd",
    "details": "a0b32dec5a90080b4ac08ca1250dca7e53bfbed5f2e883fee89270cf0b243330",
    "appearance": "f47b63851818423d5c22321e84a90f6c9c458ad294d3dd95e162e81c0f51d710",
}


def test_default_source_direction_preserves_frozen_role_prompts():
    for role, expected in _UNMIRRORED_PROMPT_HASHES.items():
        prompt, _, _ = qc._prepare(REFS, CANDIDATE, "top", MATCH, _REGULAR_FIT, role)
        assert hashlib.sha256(prompt.encode()).hexdigest() == expected


@pytest.mark.parametrize("flag", [False, None, "false", "true", 0, 1])
def test_non_true_mirrored_flag_preserves_default_requests_byte_for_byte(monkeypatch, flag):
    calls = []

    async def transport(settings, model, prompt, images, schema, timeout, **options):
        calls.append((model, prompt, images, schema, timeout, options))
        return passed()

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    default = asyncio.run(qc.judge(SETTINGS, REFS, CANDIDATE, clothing_type="top",
                                  match_image=MATCH, fit_profile=_REGULAR_FIT))
    explicit = asyncio.run(qc.judge(SETTINGS, REFS, CANDIDATE, clothing_type="top",
                                   match_image=MATCH, fit_profile=_REGULAR_FIT, source_mirrored=flag))
    assert default["complete"] and explicit["complete"] and len(calls) == 6
    assert {row["prompt_hash"] for row in explicit["roles"].values()} == set(_UNMIRRORED_PROMPT_HASHES.values())
    # Parallel scheduling may differ, so compare complete call payloads by prompt.
    before, after = sorted(calls[:3], key=lambda c: c[1]), sorted(calls[3:], key=lambda c: c[1])
    assert before == after
    for role in qc.ROLES:
        implicit = qc._prepare(REFS, CANDIDATE, "top", MATCH, _REGULAR_FIT, role)
        explicit_packet = qc._prepare(REFS, CANDIDATE, "top", MATCH, _REGULAR_FIT, role, source_mirrored=flag)
        assert explicit_packet == implicit


def test_true_mirrored_source_reaches_all_roles_without_losing_fit_or_source_pixels(monkeypatch):
    calls = {}

    async def transport(settings, model, prompt, images, schema, timeout, **options):
        role = next(role for role in qc.ROLES if f"ASSIGNED ROLE: {role}\n" in prompt)
        calls[role] = (prompt, images, schema, options)
        if role == "details":
            raise vision_llm.VisionError("one role unavailable")
        return passed()

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    report = asyncio.run(qc.judge(SETTINGS, REFS, CANDIDATE, clothing_type="top",
                                 fit_profile=_REGULAR_FIT, match_image=MATCH, source_mirrored=True))
    assert set(calls) == set(qc.ROLES)
    assert not report["complete"] and report["verdict"] == "review"
    for role, (prompt, images, schema, options) in calls.items():
        baseline, original_images, _ = qc._prepare(REFS, CANDIDATE, "top", MATCH, _REGULAR_FIT, role)
        assert "MIRRORED SOURCE PHOTOS:" in prompt
        assert "normal readable orientation" in prompt
        assert "SOURCE ORIENTATION FOR COMPARISON:" in prompt
        assert ("DECLARED FIT" in prompt) == ("DECLARED FIT" in baseline)
        assert images == original_images
        assert schema == qc.SCHEMA
        assert options["image_detail"] == "high" and options["max_completion_tokens"] == 1800
    assert "light, even ease at chest and waist" in calls["structure"][0]
