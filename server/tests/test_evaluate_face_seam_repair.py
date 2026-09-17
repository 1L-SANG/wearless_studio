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
