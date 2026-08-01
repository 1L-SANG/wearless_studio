"""Composite source reference 계약 — 패턴 합성이 소비할 원본 참조의 선택·순서·provenance.

P0(원단 2패스 시절)의 provenance 회귀 커버리지를 이관한 파일이다. 당시 결함: 워커가 자산을
바이트로 납작하게 만들며 슬롯을 버려, `Front + Back + Detail` 업로드에서 패턴 기준인 Detail 이
잘려 나갔다. 그 교훈은 경로가 generative 재생성에서 deterministic composite 로 바뀌어도
동일하게 적용된다 — **어느 원본이 패턴의 정본인지**는 slot 이 결정하고, 선택기는
`Detail → Front → Back → Fit` 권위 순서와 asset 중복 제거를 보장해야 한다.

여기 테스트는 순수 계약만 본다. 워커 → composite stage 로의 end-to-end 전달은
`test_hybrid_worker_integration.py` 가 잠근다.
"""

import pytest

from app.agents.gemini_image import InlineImage
from app.agents.product_reference import (
    ProductReference,
    order_by_role,
    reference_event_payload,
    select_pattern_sources,
)


def _ref(slot, asset_id=None, data=None):
    asset_id = asset_id or f"{slot.lower()}-asset"
    return ProductReference(
        slot=slot,
        asset_id=asset_id,
        image=InlineImage("image/jpeg", data or slot.lower().encode()),
    )


def test_product_reference_keeps_slot_asset_id_and_image():
    """참조는 bytes 가 아니라 (slot, asset_id, image) 로 다닌다 — 배선 도중 역할이 사라지지 않게."""
    image = InlineImage("image/jpeg", b"bytes")
    ref = ProductReference(slot="Detail", asset_id="a-1", image=image)
    assert (ref.slot, ref.asset_id, ref.image) == ("Detail", "a-1", image)
    with pytest.raises(Exception):  # frozen — 중간 단계가 역할을 덮어쓰지 못한다
        ref.slot = "Front"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("case", "slots", "expected"),
    [
        ("front_back_detail_fit", ["Front", "Back", "Detail", "Fit"], ["Detail", "Front"]),
        ("front_back_detail", ["Front", "Back", "Detail"], ["Detail", "Front"]),
        ("front_back", ["Front", "Back"], ["Front", "Back"]),
        ("front_only", ["Front"], ["Front"]),
        ("detail_only", ["Detail"], ["Detail"]),
        ("back_detail", ["Back", "Detail"], ["Detail", "Back"]),
        ("fit_front", ["Fit", "Front"], ["Front", "Fit"]),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_select_pattern_sources_orders_detail_front_back_fit(case, slots, expected):
    """`Detail → Front → Back → Fit` 우선순위로 최대 2개. 입력 슬롯 순서와 무관하다."""
    selected = select_pattern_sources([_ref(s) for s in slots])
    assert [r.slot for r in selected] == expected, case
    assert len(selected) <= 2


def test_select_pattern_sources_puts_detail_first_whenever_present():
    """Detail 이 있으면 **항상** 첫 번째 소스 — 패턴 색·간격의 기준이라 순서가 곧 권위다."""
    for slots in (
        ["Front", "Detail"], ["Front", "Back", "Detail"], ["Front", "Back", "Detail", "Fit"],
        ["Fit", "Back", "Detail"], ["Detail", "Front", "Back", "Fit"],
    ):
        selected = select_pattern_sources([_ref(s) for s in slots])
        assert selected[0].slot == "Detail", slots


def test_select_pattern_sources_dedupes_the_same_asset_across_slots():
    """같은 asset 이 여러 슬롯에 걸려 있으면 **한 번만**, 더 높은 우선순위 슬롯으로 남는다."""
    refs = [
        _ref("Front", "same-asset", b"one"),
        _ref("Detail", "same-asset", b"one"),
        _ref("Back", "back-asset", b"back"),
    ]
    selected = select_pattern_sources(refs)
    assert [r.asset_id for r in selected] == ["same-asset", "back-asset"]
    assert [r.slot for r in selected] == ["Detail", "Back"]
    assert len({r.asset_id for r in selected}) == len(selected)


def test_select_pattern_sources_respects_limit_and_empty_input():
    assert select_pattern_sources([]) == ()
    refs = [_ref(s) for s in ("Front", "Back", "Detail", "Fit")]
    assert [r.slot for r in select_pattern_sources(refs, limit=3)] == ["Detail", "Front", "Back"]
    assert [r.slot for r in select_pattern_sources(refs, limit=1)] == ["Detail"]


def test_order_by_role_keeps_unknown_slots_last_and_stable():
    """알 수 없는 슬롯은 버리지 않고 뒤로 — 새 슬롯이 생겨도 참조가 조용히 사라지지 않는다."""
    refs = [_ref("Mystery", "m1"), _ref("Front", "f"), _ref("Other", "o1"), _ref("Detail", "d")]
    assert [r.asset_id for r in order_by_role(refs)] == ["d", "f", "m1", "o1"]


def test_reference_event_payload_is_metadata_only():
    """이벤트에 남는 것은 slot/asset_id/priority 뿐 — 이미지 바이트·base64·URL 은 없다."""
    refs = [_ref("Front", "f", b"front-bytes"), _ref("Detail", "d", b"detail-bytes")]
    selected = select_pattern_sources(refs)
    payload = reference_event_payload(selected, all_refs=refs)
    assert payload["refs"] == [
        {"slot": "Detail", "asset_id": "d", "priority": 1},
        {"slot": "Front", "asset_id": "f", "priority": 2},
    ]
    assert payload["detail_missing"] is False
    blob = str(payload)
    assert "front-bytes" not in blob and "detail-bytes" not in blob


def test_reference_event_payload_flags_missing_detail():
    refs = [_ref("Front", "f"), _ref("Back", "b")]
    payload = reference_event_payload(select_pattern_sources(refs), all_refs=refs)
    assert payload["detail_missing"] is True
    assert [r["slot"] for r in payload["refs"]] == ["Front", "Back"]
