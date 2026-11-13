import pytest

from app.agents.page_template_assembler import assemble_v4


SELLER = "  Cotton 셔츠를 추천해요.  \n\n  두 번째 줄\n세 번째 줄\n네 번째 줄  \n"
AI = "AI 첫 줄\n\nAI 둘째 줄\nAI 셋째 줄\nAI 넷째 줄"


def bodies(template_id, analysis, body=AI):
    storyboard = [{"id": "intro", "taxonomyVersion": 4, "source": "text", "sectionId": "intro",
                   "pageSectionKey": f"{template_id}.intro", "pageSectionKind": "intro",
                   "pageDesignId": "T06-D01" if template_id == "show" else "T01-D07", "slotKey": None}]
    blocks = assemble_v4(storyboard, [], [{"blockId": "intro", "texts": [{"role": "body", "text": body}]}],
                         {"name": "코튼 셔츠", "colors": []}, True, analysis=analysis)
    return [element for block in blocks for element in block["elements"] if element.get("copyRole") == "body"]


@pytest.mark.parametrize("template_id", ["emo", "show"])
@pytest.mark.parametrize("original", [SELLER, "앞줄\r\n\r\n뒤쪽  \t줄\r\n"])
def test_seller_md_priority_preserves_exact_original(template_id, original):
    analysis = {"mdComment": original}
    elements = bodies(template_id, analysis)
    assert [element["text"] for element in elements] == [original]
    assert not elements[0].get("aiDraft")
    assert elements[0].get("paragraphCount") == (1 if template_id == "emo" else None)
    if template_id == "show":
        assert [elements[0][key] for key in ("x", "y", "w")] == [112, 107, 776]
        assert elements[0]["style"]["align"] == "center"
    assert analysis["mdComment"] == original


def test_long_showroom_md_wraps_within_panel_padding_without_rewriting():
    original = "Cotton " * 27 + "!"
    [element] = bodies("show", {"mdComment": original})
    assert element["text"] == original
    assert len(original) == 190
    assert [element[key] for key in ("x", "y", "w", "h")] == [112, 107, 776, 160]
    assert element["style"]["lineHeight"] == 40
    assert element["style"]["align"] == "center"


@pytest.mark.parametrize("template_id", ["emo", "show"])
@pytest.mark.parametrize("analysis", [{}, {"mdComment": None}, {"mdComment": ""}, {"mdComment": " \t\n "}])
@pytest.mark.parametrize("body", [AI, ""])
def test_absent_md_retains_existing_ai_or_empty_behavior(template_id, analysis, body):
    elements = bodies(template_id, analysis, body)
    expected = ([body] if template_id == "show" else ["AI 첫 줄", "AI 둘째 줄", "AI 셋째 줄\nAI 넷째 줄"]) if body else [""]
    assert [element["text"] for element in elements] == expected
    assert all(element.get("aiDraft") for element in elements)
    if not body:
        assert elements[0]["placeholder"] == "MD 코멘트"
