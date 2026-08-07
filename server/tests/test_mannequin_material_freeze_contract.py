from pathlib import Path

from app.agents import mannequin_fit_qc


SERVER_DIR = Path(__file__).resolve().parents[1]


def _prompt(name: str) -> str:
    return (SERVER_DIR / "prompts" / name).read_text(encoding="utf-8")


def _assert_material_evidence_is_frozen(text: str) -> None:
    normalized = " ".join(text.lower().replace("-", " ").split())
    for evidence in (
        "sheer",
        "mesh",
        "lace",
        "crochet",
        "open knit",
        "crinkle",
        "weave",
        "yarn",
        "edge thickness",
        "layer",
        "roughness",
        "gloss",
        "transmission",
        "fold",
        "compression",
        "self-shadow",
    ):
        assert evidence.replace("-", " ") in normalized


def test_fabric_pattern_repair_preserves_non_woven_material_evidence() -> None:
    prompt = _prompt("mannequin_fabric_v1.txt")

    _assert_material_evidence_is_frozen(prompt)
    assert "should read as woven cloth" not in prompt.lower()
    assert "change only the surface pattern" in prompt.lower()


def test_untuck_repair_freezes_visible_material_and_limits_new_folds() -> None:
    prompt = _prompt("mannequin_untuck_v1.txt")

    _assert_material_evidence_is_frozen(prompt)
    assert "every already-visible area" in prompt
    assert "only the fabric newly revealed" in prompt.lower()
    assert "only the fold redistribution physically required" in prompt.lower()


def test_fit_axis_repair_tail_preserves_material_physics() -> None:
    _assert_material_evidence_is_frozen(mannequin_fit_qc.EDIT_TAIL)
    assert "only where physically required by the requested fit-axis correction" in mannequin_fit_qc.EDIT_TAIL


def test_every_fit_axis_instruction_includes_material_freeze_tail_once() -> None:
    specs = [
        {
            "category": "top",
            "axis": "fit",
            "value": "slim",
            "observableTarget": "the torso has close but natural ease",
        }
    ]

    instruction = mannequin_fit_qc.build_edit_instruction(specs)

    assert instruction.endswith(mannequin_fit_qc.EDIT_TAIL)
    assert instruction.count("Preserve the garment's evidenced material identity") == 1


def test_live_adjust_prompt_preserves_optical_behavior_without_alpha_canvas() -> None:
    prompt = _prompt("mannequin_adjust_v2.txt").lower()

    assert "output canvas and background free of alpha" in prompt
    for evidence in ("opaque garment", "sheerness", "mesh", "lace", "crochet", "open knit"):
        assert evidence in prompt
    assert "current cut and its reference photos" in prompt
    assert "faded or ghosted overlay" in prompt
