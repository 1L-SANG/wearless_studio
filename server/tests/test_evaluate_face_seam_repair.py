"""The local evaluator may load API credentials, never production connections."""
import importlib.util
from pathlib import Path

import pytest


def evaluator():
    script = Path(__file__).resolve().parents[1] / "scripts/evaluate_face_seam_repair.py"
    spec = importlib.util.spec_from_file_location("local_seam_eval", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evaluator_limits_environment_and_refuses_versioned_image_outputs(tmp_path, monkeypatch):
    module = evaluator()
    script = Path(module.__file__)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=do-not-load\nOPENAI_API_KEY=synthetic-key\n")
    module.load_api_env(env)
    assert "DATABASE_URL" not in module.os.environ
    assert module.os.environ["OPENAI_API_KEY"] == "synthetic-key"
    with pytest.raises(ValueError, match="output_must_be_outside_git_or_ignored"):
        module.validate_output_dir(script.parents[2] / "docs" / "unsafe-face-results")
    assert module.validate_output_dir(tmp_path / "local-results").is_dir()


def test_cache_provenance_cannot_be_overwritten_by_output(tmp_path):
    module = evaluator()
    cache = tmp_path / "cache"
    for output in (cache, cache / "cut", tmp_path):
        with pytest.raises(ValueError, match="output_overlaps_cache"):
            module.validate_cache_paths(output, cache)
    module.validate_cache_paths(tmp_path / "new-run", cache)


def test_cannot_attribute_response_to_a_different_plan_cache(tmp_path):
    import asyncio
    from types import SimpleNamespace
    module = evaluator()
    with pytest.raises(ValueError, match="conflicting_plan_response_caches"):
        asyncio.run(module.evaluate(SimpleNamespace(plans_dir=tmp_path / "plans", responses_dir=tmp_path / "responses")))


def test_cached_response_must_match_mask_and_prompt(tmp_path):
    from PIL import Image
    from types import SimpleNamespace
    module = evaluator()
    crop = SimpleNamespace(current=Image.new("RGB", (32, 32)), edit_mask=Image.new("RGBA", (1024, 1024), "white"))
    fingerprint = module.request_fingerprint(crop, "Repair left collar")
    module.save_json(tmp_path / "request.json", fingerprint)
    module.check_cached_request(tmp_path, fingerprint)
    with pytest.raises(ValueError, match="cached_request_mismatch"):
        module.check_cached_request(tmp_path, module.request_fingerprint(crop, "Repair right collar"))
    crop.edit_mask.putpixel((500, 500), (255, 255, 255, 0))
    with pytest.raises(ValueError, match="cached_request_mismatch"):
        module.check_cached_request(tmp_path, module.request_fingerprint(crop, "Repair left collar"))
