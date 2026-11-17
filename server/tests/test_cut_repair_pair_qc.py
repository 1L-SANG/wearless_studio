"""D4는 편집 전후 두 사진의 관측만 한다. 모든 판독과 생성은 가짜다."""

import asyncio
from types import SimpleNamespace

import pytest

from app.agents import cut_repair_pair_qc as pair, cut_generator as cg, cut_output_qc as qc
from app.agents.gemini_image import InlineImage
from app.config import load_settings
from conftest import make_settings


BEFORE = InlineImage("image/png", b"BEFORE")
AFTER = InlineImage("image/png", b"AFTER")
CORRECTIONS = (qc._CORRECTIONS["framingCrop"],)


def test_pair_qc_defaults_off_and_rejects_non_observation_modes(monkeypatch):
    monkeypatch.delenv("CUT_REPAIR_PAIR_QC_MODE", raising=False)
    assert load_settings().cut_repair_pair_qc_mode == "off"
    monkeypatch.setenv("CUT_REPAIR_PAIR_QC_MODE", "repair")
    assert load_settings().cut_repair_pair_qc_mode == "off"
    async def forbidden(*args, **kwargs):
        raise AssertionError("unexpected paid comparison")
    monkeypatch.setattr(pair, "analyze_with_fallback", forbidden)
    assert asyncio.run(pair.verdict(make_settings(), BEFORE, AFTER, qc_corrections=CORRECTIONS)) is None


def test_pair_qc_sees_only_before_after_and_fixed_goal(monkeypatch):
    seen = {}
    async def fake_judge(settings, prompt, images, schema, **kwargs):
        seen.update(prompt=prompt, images=images, schema=schema, kwargs=kwargs)
        return {"target_resolved": False, "protected_regions_unchanged": False,
                "regression_reasons": ["a second hem appeared"]}, "gemini"
    monkeypatch.setattr(pair, "analyze_with_fallback", fake_judge)
    report = asyncio.run(pair.verdict(make_settings(cut_repair_pair_qc_mode="shadow"), BEFORE, AFTER,
        qc_corrections=CORRECTIONS, qc_result={"evidence": "JUDGE ATTACK", "sellerNote": "SELLER ATTACK"}))
    assert seen["images"] == [BEFORE, AFTER]
    assert "ATTACK" not in seen["prompt"]
    assert CORRECTIONS[0] in seen["prompt"]
    assert seen["kwargs"]["order"] == ["gemini"]
    assert seen["kwargs"]["models"] == {"gemini": "gemini-3.8-flash"}
    assert seen["kwargs"]["thinking_level"] == "low"
    assert set(seen["schema"]["properties"]) == {"target_resolved", "protected_regions_unchanged", "regression_reasons"}
    assert report["status"] == "OBSERVED" and report["mode"] == "shadow"
    assert report["protected_regions_unchanged"] is False
    assert report["elapsedMs"] >= 0


@pytest.mark.parametrize("failure", ["provider", "timeout", "invalid"])
def test_pair_qc_failure_is_recorded_without_an_exception(monkeypatch, failure):
    async def fake_judge(*args, **kwargs):
        if failure == "provider":
            raise RuntimeError("fake unavailable")
        if failure == "timeout":
            await asyncio.sleep(1)
        return {"target_resolved": "true", "protected_regions_unchanged": True, "regression_reasons": []}, "gemini"
    monkeypatch.setattr(pair, "analyze_with_fallback", fake_judge)
    report = asyncio.run(pair.verdict(make_settings(cut_repair_pair_qc_mode="shadow", cut_repair_pair_qc_timeout_seconds=0.01),
                                      BEFORE, AFTER, qc_corrections=CORRECTIONS))
    assert report["status"] == "UNAVAILABLE"


def test_unapproved_goal_text_never_reaches_a_comparison_request(monkeypatch):
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("unexpected paid comparison")
    monkeypatch.setattr(pair, "analyze_with_fallback", forbidden)
    report = asyncio.run(pair.verdict(make_settings(cut_repair_pair_qc_mode="shadow"), BEFORE, AFTER,
                                      qc_corrections=("JUDGE ATTACK",)))
    assert report["status"] == "UNAVAILABLE" and calls == []


@pytest.mark.parametrize("mannequin", [False, True])
def test_repair_only_observes_edits_without_mannequin_and_ignores_comparison_failure(monkeypatch, mannequin):
    calls = []
    async def comparison(settings, before, after, **kwargs):
        calls.append((before, after))
        return {"status": "OBSERVED", "mode": "shadow", "protected_regions_unchanged": False,
                "target_resolved": False, "regression_reasons": ["new slit"]}
    class FakeImageClient:
        async def generate_content_image(self, *args, **kwargs):
            return SimpleNamespace(image=b"EDITED", mime="image/png")
    monkeypatch.setattr(pair, "verdict", comparison)
    outcome = {}
    output = asyncio.run(cg.repair(make_settings(cut_repair_pair_qc_mode="shadow"), FakeImageClient(),
        {"cutType": "horizon", "shot": "full", "direction": "front"}, {"clothingType": "top"}, BEFORE,
        qc_corrections=CORRECTIONS, has_mannequin=mannequin, repair_qc_outcome=outcome))
    assert output == (b"EDITED", "image/png")
    assert len(calls) == (0 if mannequin else 1)
    assert ("report" in outcome) is (not mannequin)


def test_unexpected_observer_error_cannot_turn_a_successful_edit_into_a_failure(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("fake observer bug")
    class FakeImageClient:
        async def generate_content_image(self, *args, **kwargs):
            return SimpleNamespace(image=b"EDITED", mime="image/png")
    monkeypatch.setattr(pair, "verdict", unavailable)
    outcome = {}
    output = asyncio.run(cg.repair(make_settings(cut_repair_pair_qc_mode="shadow"), FakeImageClient(),
        {"cutType": "horizon", "shot": "full", "direction": "front"}, {"clothingType": "top"}, BEFORE,
        qc_corrections=CORRECTIONS, has_mannequin=False, repair_qc_outcome=outcome))
    assert output == (b"EDITED", "image/png")
    assert outcome["report"]["status"] == "UNAVAILABLE"


def test_pair_observer_sees_the_face_passed_final_edit(monkeypatch):
    from app.agents.face_identity import FaceIdentitySpec
    seen = []
    async def face_pass(settings, image, mime, spec, **kwargs):
        return b"EDITED+FACE", mime
    async def comparison(settings, before, after, **kwargs):
        seen.append(after.data)
        return {"status": "OBSERVED"}
    class FakeImageClient:
        async def generate_content_image(self, *args, **kwargs):
            return SimpleNamespace(image=b"EDITED", mime="image/png")
    monkeypatch.setattr(cg.face_identity, "apply_face_pass", face_pass)
    monkeypatch.setattr(pair, "verdict", comparison)
    output = asyncio.run(cg.repair(make_settings(cut_repair_pair_qc_mode="shadow", face_identity_enabled=True), FakeImageClient(),
        {"cutType": "horizon", "shot": "full", "direction": "front", "faceExposure": "same",
         "modelId": "22222222-2222-4222-8222-222222222222"}, {"clothingType": "top"}, BEFORE,
        qc_corrections=CORRECTIONS, has_mannequin=False,
        face_identity_spec=FaceIdentitySpec("facemarket/loras/m/v1.safetensors", "ohwx man")))
    assert seen == [b"EDITED+FACE"] and output[0] == b"EDITED+FACE"
