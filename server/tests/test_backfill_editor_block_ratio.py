"""저장된 editor_blocks 지오메트리 백필 — 데이터 안전 가드 (codex 리뷰 반영, WS2).

백필은 프로덕션 데이터를 제자리 수정하므로 "안 고치는 경우"가 계약의 핵심이다:
dims 미상이면 손대지 않고, 사용자가 편집한 블록·다른 텍스트는 건드리지 않으며,
파손 JSON 에도 죽지 않아야 한다."""

import importlib
import sys

import pytest


@pytest.fixture()
def bf(monkeypatch):
    from scripts import _env

    monkeypatch.setattr(_env, "load_env", lambda: None)  # 테스트는 자격증명 없이 import
    sys.modules.pop("scripts.backfill_editor_block_ratio", None)
    module = importlib.import_module("scripts.backfill_editor_block_ratio")
    yield module
    sys.modules.pop("scripts.backfill_editor_block_ratio", None)


AID = "11111111-2222-4333-8444-555555555555"


def _old_block(**overrides):
    """구 page_assembler 가 만들던 AI 컷 블록."""
    block = {
        "id": "b0", "h": 660,
        "elements": [
            {"id": "b0e0", "type": "image", "cutType": "styling",
             "x": 60, "y": 50, "w": 880, "h": 560, "src": f"/v1/assets/{AID}/file"},
        ],
    }
    block.update(overrides)
    return block


def test_rewrites_only_when_real_dims_known(bf):
    block = _old_block()
    plan = bf._plan_block(block, {AID: (848, 1264)})
    assert plan is not None and (plan["w"], plan["h"]) == (880, 1312)


def test_skips_when_dims_unknown_instead_of_guessing(bf):
    # dims 를 모르는데 2:3 폴백을 적용하면 가로 이미지의 폭을 잘라먹는다 → 아예 손대지 않는다.
    for dims in ({}, {AID: (None, None)}, {AID: (0, 0)}):
        assert bf._plan_block(_old_block(), dims) is None


def test_skips_when_src_has_no_asset_id(bf):
    block = _old_block()
    block["elements"][0]["src"] = "https://cdn.example.com/external.png"
    assert bf._plan_block(block, {AID: (848, 1264)}) is None


def test_skips_user_edited_geometry(bf):
    # 사용자가 크기·위치를 바꿨으면 구 지문과 다르다 → 편집분 보존
    for override in ({"h": 900}, {"w": 500}, {"y": 120}):
        block = _old_block()
        block["elements"][0].update(override)
        assert bf._plan_block(block, {AID: (848, 1264)}) is None
    resized_block = _old_block(h=1000)
    assert bf._plan_block(resized_block, {AID: (848, 1264)}) is None


def test_skips_non_ai_image_without_cut_type(bf):
    block = _old_block()
    block["elements"][0].pop("cutType")
    assert bf._plan_block(block, {AID: (848, 1264)}) is None


def test_moves_generated_body_copy_but_not_other_text(bf):
    # 구 body 카피(x120,y560,w760,h40)만 따라 내리고, 같은 y 의 사용자 캡션은 그대로 둔다.
    block = _old_block()
    block["elements"] += [
        {"id": "b0e1", "type": "text", "x": 120, "y": 560, "w": 760, "h": 40,
         "style": {"size": 18, "color": "#4a4a45"}, "text": "생성 카피"},
        {"id": "b0e2", "type": "text", "x": 300, "y": 560, "w": 200, "h": 40,
         "style": {"size": 18, "color": "#4a4a45"}, "text": "내 캡션"},
    ]
    plan = bf._plan_block(block, {AID: (848, 1264)})
    bf._apply_block(block, plan)
    by_id = {e["id"]: e for e in block["elements"]}
    assert by_id["b0e1"]["y"] == 50 + 1312 - 50   # 이미지 하단 근처로 이동
    assert by_id["b0e2"]["y"] == 560              # 사용자 캡션은 불변


def test_block_height_contains_every_element(bf):
    block = _old_block()
    block["elements"].append(
        {"id": "b0e1", "type": "text", "x": 120, "y": 560, "w": 760, "h": 40, "text": "생성 카피"})
    plan = bf._plan_block(block, {AID: (848, 1264)})
    new_h = bf._apply_block(block, plan)
    assert new_h == block["h"]
    for el in block["elements"]:
        assert el["y"] + el["h"] <= block["h"]


def test_survives_malformed_blocks(bf):
    # 파손 JSON 이 섞여도 예외 없이 '대상 아님'으로 넘어가야 한다(전체 트랜잭션 롤백 방지).
    for bad in ("bad", None, 123, {}, {"h": 660}, {"h": 660, "elements": None},
                {"h": 660, "elements": [None, "x"]}):
        assert bf._plan_block(bad, {AID: (848, 1264)}) is None


def test_skips_element_with_existing_crop(bf):
    # 사용자가 크롭을 커밋한 요소는 crop{iw,ih} 가 프레임과 짝 — 프레임만 키우면 공백이 생긴다.
    block = _old_block()
    block["elements"][0]["crop"] = {"ox": 0, "oy": 0, "iw": 880, "ih": 560}
    assert bf._plan_block(block, {AID: (848, 1264)}) is None


def test_skips_image_without_id(bf):
    # id 가 없으면 대상 식별 불가 — 다른 id 없는 요소까지 함께 리사이즈된다.
    block = _old_block()
    block["elements"][0].pop("id")
    assert bf._plan_block(block, {AID: (848, 1264)}) is None


def test_body_move_requires_generated_style(bf):
    # 같은 자리의 사용자 라벨(스타일 다름)은 옮기지 않는다.
    block = _old_block()
    block["elements"] += [
        {"id": "t1", "type": "text", "x": 120, "y": 560, "w": 760, "h": 40,
         "style": {"size": 18, "color": "#4a4a45"}, "text": "생성 카피"},
        {"id": "t2", "type": "text", "x": 120, "y": 560, "w": 760, "h": 40,
         "style": {"size": 12, "color": "#000000"}, "text": "사용자 법적 고지"},
        {"id": "t3", "type": "text", "x": 120, "y": 560, "w": 760, "h": 40, "text": "스타일 없음"},
    ]
    plan = bf._plan_block(block, {AID: (848, 1264)})
    bf._apply_block(block, plan)
    by_id = {e["id"]: e for e in block["elements"]}
    assert by_id["t1"]["y"] == 50 + 1312 - 50
    assert by_id["t2"]["y"] == 560 and by_id["t3"]["y"] == 560


def test_asset_id_regex_rejects_non_uuid(bf):
    assert bf._asset_id("/v1/assets/------------------------------------/file") is None
    assert bf._asset_id("/v1/assets/not-a-uuid/file") is None
    assert bf._asset_id(f"/v1/assets/{AID}/file") == AID


def test_idempotent_after_apply(bf):
    block = _old_block()
    plan = bf._plan_block(block, {AID: (848, 1264)})
    bf._apply_block(block, plan)
    assert bf._plan_block(block, {AID: (848, 1264)}) is None  # 두 번째 실행은 대상 아님
