"""실제(REAL) 모델은 **studio 섹션 컷만** 만든다 — 2026-09-14 사용자 결정.

왜: 얼굴 합성이 실측으로 검증된 곳이 studio 섹션뿐이다. 나머지까지 그리면 gpt-image 비용만
나간다. 푸는 방법은 identity_scope.REAL_ALLOWED_SECTION_ROLES 에 섹션을 더하는 것 하나뿐이다.

판정은 identity_scope 한 곳에 있고 세 자리가 그걸 쓴다:
  · 상세페이지 라우트 — **예약 전에** 걸러 예약 크레딧 = 실제 생성 컷 수
  · detail 워커      — 같은 판정으로 건너뜀(구 클라이언트·재시도 방어, 과금 0)
  · 에디터 라우트    — 생성 전에 거부(과금 0)
"""

import pathlib

import pytest

from app.agents import content_roles, identity_scope

REAL_MODEL = "11111111-1111-4111-8111-111111111111"
VIRTUAL_MODEL = "m_luna"

#: 섹션별 대표 블록 — canonicalize 를 거치지 않은 저장 shape 그대로 본다.
BLOCKS = {
    "hooking": {"source": "ai", "sectionRole": "hooking", "cutType": "styling", "shot": "full"},
    "styling": {"source": "ai", "sectionRole": "styling", "cutType": "styling", "shot": "full"},
    "studio": {"source": "ai", "sectionRole": "studio", "cutType": "horizon", "shot": "full"},
    "product": {"source": "ai", "sectionRole": "product", "cutType": "product", "shot": "ghost"},
}


def test_the_policy_is_one_constant():
    assert identity_scope.REAL_ALLOWED_SECTION_ROLES == ("studio",)
    # 섹션 이름이 바뀌면 정책이 조용히 비어 버린다 — 실제 섹션 목록에 들어 있는지 잠근다.
    for role in identity_scope.REAL_ALLOWED_SECTION_ROLES:
        assert role in content_roles.SECTION_ROLES


@pytest.mark.parametrize("section", ["hooking", "styling", "product"])
def test_a_real_model_cannot_make_cuts_outside_studio(section):
    block = BLOCKS[section]
    assert identity_scope.block_allowed(block, REAL_MODEL) is False
    code, message = identity_scope.block_rejection(block, REAL_MODEL)
    assert code == "real_model_studio_only"
    assert "스튜디오" in message
    # 인프라 단어는 셀러 문구에 없다
    for word in ("파드", "라이선스", "LoRA", "pod"):
        assert word not in message


def test_a_real_model_can_make_studio_cuts():
    assert identity_scope.block_allowed(BLOCKS["studio"], REAL_MODEL) is True
    assert identity_scope.block_rejection(BLOCKS["studio"], REAL_MODEL) is None


@pytest.mark.parametrize("section", list(BLOCKS))
def test_virtual_models_are_untouched(section):
    """가상 모델은 무변화 — 이 PR 은 REAL 경로만 좁힌다."""
    assert identity_scope.block_allowed(BLOCKS[section], VIRTUAL_MODEL) is True
    assert identity_scope.block_rejection(BLOCKS[section], VIRTUAL_MODEL) is None


def test_old_blocks_without_a_section_are_judged_by_their_recipe():
    """옛 블록엔 sectionRole 이 없다 — contentRole·cutType 에서 섹션을 추론한다."""
    assert identity_scope.block_allowed({"source": "ai", "cutType": "horizon"}, REAL_MODEL) is True
    for cut_type in ("styling", "mirror", "product"):
        assert identity_scope.block_allowed(
            {"source": "ai", "cutType": cut_type}, REAL_MODEL) is False


def test_a_block_whose_section_cannot_be_resolved_is_not_blocked():
    """섹션을 못 알아내면 막지 않는다 — 모르는 것을 이유로 셀러 컷을 지우지 않는다."""
    assert identity_scope.section_of({}) is None or identity_scope.section_of({}) in content_roles.SECTION_ROLES
    assert identity_scope.studio_only_block({"contentRole": "custom"}) is False


def test_the_two_reasons_stay_apart():
    """"스튜디오만 된다" 와 "이 예시는 가상 전용이다" 는 셀러가 할 수 있는 일이 다르다."""
    assert identity_scope.STUDIO_ONLY_CODE != identity_scope.MISMATCH_CODE
    # 가상 전용 공간세트는 studio 섹션이어도 막힌다 — 그때는 mismatch 코드다
    space_block = {"source": "ai", "sectionRole": "studio", "cutType": "horizon",
                   "spaceGroupId": "ssg1__hatchingroom_2161__sg_1"}
    rejection = identity_scope.block_rejection(space_block, REAL_MODEL)
    assert rejection is not None and rejection[0] == identity_scope.MISMATCH_CODE


# ── 세 자리가 같은 판정을 쓰는가 ─────────────────────────────────────────────
def test_the_route_filters_before_it_reserves():
    from app import routes

    text = pathlib.Path(routes.__file__).read_text(encoding="utf-8")
    body = text[text.index("async def generate_detail_page"):]
    body = body[:body.index("@router.get")]
    filter_at = body.index("identity_scope.block_allowed")
    reserve_at = body.index("reserve_credits")
    assert filter_at < reserve_at, "예약이 필터보다 먼저면 만들지도 않을 컷에 크레딧이 잡힌다"
    # 전부 걸러지면 잡을 만들지 않고 전용 코드로 끝낸다
    assert "identity_scope.block_rejection" in body


def test_the_worker_skips_with_the_same_judgement():
    from app.workers import detail_page_job

    text = pathlib.Path(detail_page_job.__file__).read_text(encoding="utf-8")
    assert "identity_scope.block_rejection" in text
    branch = text.split("identity_scope.block_rejection")[1][:600]
    assert '"status": "cut_skipped"' in branch


def test_the_editor_route_rejects_with_the_same_code():
    from app import routes

    text = pathlib.Path(routes.__file__).read_text(encoding="utf-8")
    assert "identity_scope.block_rejection(payload, selected_model_id)" in text


def test_the_frontend_rule_table_carries_the_section_rule():
    """프런트는 규칙을 다시 구현하지 않는다 — 서버가 내보낸 표에 섹션 규칙이 있어야 한다."""
    import json

    root = pathlib.Path(__file__).resolve().parents[2]
    data = json.loads((root / "src/data/identityScopes.json").read_text(encoding="utf-8"))
    section_rules = [r for r in data["rules"] if "sectionNotIn" in (r.get("when") or {})]
    assert len(section_rules) == 1
    rule = section_rules[0]
    assert rule["scope"] == identity_scope.VIRTUAL
    assert rule["when"]["sectionNotIn"] == list(identity_scope.REAL_ALLOWED_SECTION_ROLES)
    assert rule["code"] == identity_scope.STUDIO_ONLY_CODE
    assert rule["message"] == identity_scope.STUDIO_ONLY_MESSAGE
