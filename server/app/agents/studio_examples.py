"""Display metadata is trusted only from the published server registries."""


def is_minimal_example(entry):
    return isinstance(entry, dict) and entry.get("cutType") == "styling" and entry.get("studioStyle") == "minimal"


def is_minimal_example_id(example_id):
    if not isinstance(example_id, str) or not example_id:
        return False
    if str(example_id).startswith("ss_"):
        from .space_set_assets import load_space_set_registry
        _, sets = load_space_set_registry()
        return any(is_minimal_example(member) for group in sets.values() for member in group["members"]
                   if member["exampleId"] == example_id)
    from .cut_generator import load_example_asset_registry
    _, entries = load_example_asset_registry()
    return is_minimal_example(entries.get(example_id))


def studio_style_for_set(members):
    minimal = sum(is_minimal_example(member) for member in members)
    if minimal and minimal != len(members):
        raise ValueError("mixed_studio_style_set")
    return "minimal" if minimal else None


def slot_allows_cut_type(recipe, block):
    if block.get("cutType") == "styling" and is_minimal_example_id(block.get("exampleId")):
        return "horizon" in recipe["allowedCutTypes"]
    return block.get("cutType") in recipe["allowedCutTypes"]
