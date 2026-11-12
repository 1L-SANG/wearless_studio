import pytest

from app.agents.page_template_assembler import assemble_v4


@pytest.mark.parametrize("source,src,meta", [
    ("mine", "seller:square", {}),
    ("mine", "seller:landscape", {}),
    ("ai", "generated:portrait", {"width": 900, "height": 1200}),
])
def test_frame_preserves_contain_and_existing_geometry(source, src, meta):
    storyboard = [{"id": "frame-photo", "taxonomyVersion": 4, "source": source,
                   "ownImages": [src] if source == "mine" else [], "sectionId": "wear",
                   "pageSectionKey": "emo.wear_1", "pageSectionKind": "wear", "pageSectionUsage": "styling",
                   "pageDesignId": "WEAR-STYLING-FRAME-COLUMN-01", "slotKey": None}]
    blocks = assemble_v4(storyboard, [{"blockId": "frame-photo", "imageUrl": src, **meta}], [],
                         {"name": "코튼 셔츠", "colors": []}, False, analysis={})
    photo = next(element for element in blocks[0]["elements"] if element["type"] == "image" and not element.get("blur"))
    assert photo.get("fit") == "contain"
    assert photo["src"] == src
    assert [photo[key] for key in ("x", "y", "w", "h")] == [220, 301, 560, 747]
    assert "crop" not in photo
    assert "fit" not in next(element for element in blocks[0]["elements"] if element.get("blur"))
