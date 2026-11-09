"""Explicit v4 fixtures for recipe, billing and worker regression tests."""
from copy import deepcopy


def v4_blocks(blocks):
    if not isinstance(blocks, list):
        return blocks
    out, last, run, run_usage, run_design, run_first = [], None, 0, None, None, False
    for index, raw in enumerate(blocks):
        if not isinstance(raw, dict) or raw.get("taxonomyVersion") == 4:
            out.append(deepcopy(raw))
            continue
        block = deepcopy(raw)
        if block.get("source") != "mine" and not block.get("cutType"):
            block["cutType"] = {"productOverview": "product", "detail": "product", "fit": "horizon"}.get(block.get("contentRole"), "horizon" if block.get("sectionRole") == "studio" else "styling")
            block.setdefault("shot", "detail" if block.get("contentRole") == "detail" else "ghost" if block["cutType"] == "product" else "full")
        if block.get("source") != "mine":
            block.setdefault("shot", "detail" if block.get("contentRole") == "detail" else "ghost" if block.get("cutType") == "product" else "full")
        sid = block.get("sectionId") or block.get("sectionRole") or block.get("cutType") or "photos"
        if sid != last:
            run += 1
            last = sid
            run_usage = "fit" if block.get("cutType") == "horizon" else "styling"
            run_design = "P04-D01" if block.get("cutType") == "horizon" else "P02-D01"
            run_first = run == 1 and (block.get("contentRole") in ("hero", "benefit") or block.get("sectionRole") in ("hooking", "benefit"))
        first = run_first
        block.update(id=block.get("id") or f"fixture-{index}", taxonomyVersion=4, source=block.get("source") or "ai",
            sectionId=block.get("sectionId") or f"{sid}-{run}", sectionLayout="stack", slotKey=None,
            pageSectionKey="emo.first" if first else f"emo.wear_{run}",
            pageSectionKind="first" if first else "wear",
            pageDesignId="P01-D09" if first else run_design)
        if not first:
            block["pageSectionUsage"] = run_usage
        out.append(block)
    return out
