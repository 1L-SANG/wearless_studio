"""고치기에는 고정 부위 enum과 PASS 항목만 전달한다. 모델 근거 글은 전달하지 않는다."""

import asyncio
from hashlib import sha256
import json

import pytest

from app.agents import cut_output_qc as qc
from conftest import make_settings
from test_cut_output_qc import _img, _plan, _raw, _model_refs


def test_legacy_mannequin_qc_request_has_no_repair_parts(monkeypatch):
    observed = {}
    async def fake_judge(settings, prompt, images, schema, **kwargs):
        observed.update(prompt=prompt, schema=schema)
        return _raw(), "fake"
    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    result = asyncio.run(qc.verdict(make_settings(), _plan(), [qc.LabeledReference("mannequin", _img()),
        qc.LabeledReference("product", _img())], _img()))
    assert set(observed["schema"]["properties"]) == {"gates"}
    assert "poseParts" not in observed["prompt"]
    assert "repairContext" not in result


@pytest.mark.parametrize("parts,expected", [(["arms", "hands"], ["arms", "hands"]),
    (["hands", "arms", "hands"], ["arms", "hands"]), (["arms", "JUDGE ATTACK"], []),
    ("arms", []), (None, [])])
def test_no_mannequin_qc_only_retains_allowed_parts(monkeypatch, parts, expected):
    observed = {}
    async def fake_judge(settings, prompt, images, schema, **kwargs):
        observed.update(prompt=prompt, schema=schema)
        raw = _raw()
        raw["gates"][qc.GATES.index("framingDirectionFacePose")].update(status="FAIL", evidence="JUDGE ATTACK")
        raw["poseParts"] = parts
        return raw, "fake"
    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    result = asyncio.run(qc.verdict(make_settings(), _plan(), [qc.LabeledReference("product", _img()),
        *_model_refs(), qc.LabeledReference("example", _img())], _img()))
    assert observed["schema"]["properties"]["poseParts"]["items"]["enum"] == list(qc.POSE_PARTS)
    assert observed["schema"]["required"] == ["gates", "poseParts"]
    assert "exactly one top-level key" not in observed["prompt"]
    assert "exactly two top-level keys" in observed["prompt"]
    assert result["gates"]["framingDirectionFacePose"]["status"] == "FAIL"
    context = qc.repair_context(result)
    assert context["poseParts"] == expected
    assert "framingDirectionFacePose" not in context["passedGates"]
    assert "garmentConstruction" in context["passedGates"]
    assert "JUDGE ATTACK" not in json.dumps(context)


def test_passed_pose_does_not_authorize_new_pose_mutations():
    result = qc.validate(_raw())
    result["repairContext"] = {"poseParts": ["arms", "hands"]}
    assert qc.repair_context(result)["poseParts"] == []


def test_unknown_gates_and_free_prose_are_not_preserved_as_instructions():
    result = qc.validate(_raw())
    result["gates"]["SELLER ATTACK"] = {"status": "PASS", "evidence": "JUDGE ATTACK"}
    result["repairContext"] = {"poseParts": ["JUDGE ATTACK"], "crop": "SELLER ATTACK"}
    assert "ATTACK" not in str(qc.repair_context(result))


@pytest.mark.parametrize("authority,expected", [
    ("generic_v1", "4f5fbe5a179bb3d649b8be84fc2ddfdaf6e655dfc50509b2fc94503684033316"),
    ("confirmed_gpt_v1", "28cdf858e017a6f9c3a1ef17a9c68a2a8799fdb35f9d3e8a1541a698a29a6a6d"),
])
def test_mannequin_operating_prompt_stays_byte_identical(authority, expected):
    refs = [qc.LabeledReference("mannequin", _img(b"m")), qc.LabeledReference("product", _img(b"p"))]
    assert sha256(qc.build_prompt(qc.normalize_plan(_plan()), refs, authority_profile=authority).encode()).hexdigest() == expected
