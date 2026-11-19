"""Synthetic copy prompts: unit tests must not depend on owner tuning or prose."""
import json

import pytest


FIXTURE_GPT_CAP = 3456
FIXTURE_LUNA_CAP = 4321
FIXTURE_ARGON_THINKING = {"testOnlyThinking": "low"}


def write_page_copy_prompts(directory):
    directory.mkdir(parents=True, exist_ok=True)

    def write_json(name, value):
        (directory/name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def envelope(keys):
        return "{{rules}}\n---USER---\n{" + ",".join(
            json.dumps(key)+":{{"+key+"}}" for key in keys) + "}"

    providers = {
        "gpt": {"max_output_tokens": FIXTURE_GPT_CAP, "reasoning_effort": "low"},
        "gemini": {"max_output_tokens": 4567, "thinking_level": "low"},
        "claude": {"max_output_tokens": 5678, "effort": "low", "thinking": {"type": "adaptive"}},
    }
    settings = {"providers": providers, "models": {
        "gpt-6-luna": {"max_output_tokens": FIXTURE_LUNA_CAP, "reasoning_effort": "none"},
        "gemini-4-argon": {"thinking_config": FIXTURE_ARGON_THINKING},
    }}
    for version in ("v1", "v2"):
        write_json(f"settings_{version}.json", settings)
    for prefix in ("", "feature_"):
        (directory/f"{prefix}rules_v1.md").write_text("Synthetic test rules", encoding="utf-8")
        write_json(f"{prefix}examples_v1.json", {"examples": []})
        for provider in providers:
            (directory/f"{prefix}{provider}_v1.md").write_text(
                envelope(["facts", "plans", "examples", "slots"]), encoding="utf-8")
    for mode in ("single",):
        (directory/f"{mode}_rules_v2.md").write_text("Synthetic test rules", encoding="utf-8")
        keys = ["facts", "plans", "examples", "slots", "exemplars"]
        for provider in providers:
            (directory/f"{mode}_{provider}_v2.md").write_text(envelope(keys), encoding="utf-8")
    write_json("exemplars_v2.json", {"exemplars": []})
    return directory


@pytest.fixture
def page_copy_prompt_dir(tmp_path, monkeypatch):
    from app.agents import page_copy
    directory = write_page_copy_prompts(tmp_path/"copy-prompt-fixture")
    monkeypatch.setattr(page_copy, "PROMPT_DIR", directory)
    return directory
