import pytest

from app.agents.page_template_assembler import assemble_v4


SELLER = "  Cotton 셔츠를 추천해요.  \n\n  두 번째 줄\n세 번째 줄\n네 번째 줄  \n"
AI = "AI 첫 줄\n\nAI 둘째 줄\nAI 셋째 줄\nAI 넷째 줄"


def bodies(template_id, copywriting, analysis, body=AI):
    storyboard = [{"id": "intro", "taxonomyVersion": 4, "source": "text", "sectionId": "intro",
                   "pageSectionKey": f"{template_id}.intro", "pageSectionKind": "intro",
                   "pageDesignId": "T01-D07", "slotKey": None}]
    blocks = assemble_v4(storyboard, [], [{"blockId": "intro", "texts": [{"role": "body", "text": body}]}],
                         {"name": "코튼 셔츠", "colors": []}, copywriting, analysis=analysis)
    return [element for block in blocks for element in block["elements"] if element.get("copyRole") == "body"]


@pytest.mark.parametrize("template_id", ["emo", "show"])
@pytest.mark.parametrize("copywriting", [True, False])
@pytest.mark.parametrize("original", [SELLER, "앞줄\r\n\r\n뒤쪽  \t줄\r\n"])
def test_seller_md_priority_preserves_exact_original(template_id, copywriting, original):
    analysis = {"mdComment": original}
    elements = bodies(template_id, copywriting, analysis)
    assert [element["text"] for element in elements] == [original]
    assert not elements[0].get("aiDraft")
    assert elements[0]["paragraphCount"] == 1
    assert analysis["mdComment"] == original


@pytest.mark.parametrize("template_id", ["emo", "show"])
@pytest.mark.parametrize("copywriting", [True, False])
@pytest.mark.parametrize("analysis", [{}, {"mdComment": None}, {"mdComment": ""}, {"mdComment": " \t\n "}])
@pytest.mark.parametrize("body", [AI, ""])
def test_absent_md_retains_existing_ai_or_empty_behavior(template_id, copywriting, analysis, body):
    elements = bodies(template_id, copywriting, analysis, body)
    expected = ["AI 첫 줄", "AI 둘째 줄", "AI 셋째 줄\nAI 넷째 줄"] if copywriting and body else [""]
    assert [element["text"] for element in elements] == expected
    assert all(element.get("aiDraft") for element in elements)
    if not copywriting or not body:
        assert elements[0]["placeholder"] == "MD 코멘트"
