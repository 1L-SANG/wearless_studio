"""역할(slot)이 붙은 상품 참조 — 원단 보정이 Detail 을 잃지 않게 (순수, IO 없음).

왜 필요한가(2026-07-31 재조사): 워커는 상품 자산을 로드하는 즉시 `InlineImage` 바이트 배열로
납작하게 만들었고, 그 뒤로는 어느 사진이 Front 이고 어느 사진이 Detail 인지 알 방법이 없었다.
그래서 원단 2패스가 "앞에서 두 장"(`prod_imgs[:2]`)을 골랐는데, 실제 슬롯 순서는
`Front → Back → Detail → Fit` 이라 **정상적으로 세 장을 올린 셀러일수록 Detail 이 빠졌다**.
원단 프롬프트는 Detail 을 패턴 스케일의 기준으로 지목하는데, 그 사진이 입력에 없으면 모델은
패턴을 기억이 아니라 상상으로 채운다.

`InlineImage` 를 `ProductReference(slot, asset_id, image)` 로 감싸 배선 끝까지 역할을 들고 간다.
기존 생성·QC 가 쓰는 bare 바이트 목록은 여기서 파생한다(`[r.image for r in refs]`) — 계약을
바꾸는 대신 잃어버린 정보만 되살리는 게 이번 변경의 범위다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from .gemini_image import InlineImage

# 원단 참조 우선순위. **업로드 슬롯 순서와 다르다.**
#  · Detail: 패턴 색·색 순서·반복 간격·선 굵기·질감의 기준 (근접 촬영이라 고주파가 살아 있다)
#  · Front : 전체 형태·봉제 구조·패턴의 전체 배치 기준
#  · Back  : 뒷면 구조와 패턴 연속성
#  · Fit   : 착용 핏 참고일 뿐 — 사람이 입은 사진은 조명·주름·색 왜곡이 커서 원단 기준이 못 된다
FABRIC_PRIORITY: tuple[str, ...] = ("Detail", "Front", "Back", "Fit")

_UNKNOWN_PRIORITY = len(FABRIC_PRIORITY)


@dataclass(frozen=True)
class ProductReference:
    """상품 사진 1장 + 그 사진의 역할. frozen — 중간 단계가 역할을 덮어쓰지 못하게."""

    slot: str
    asset_id: str
    image: InlineImage


def role_priority(slot: str | None) -> int:
    """`FABRIC_PRIORITY` 내 순위. 모르는 슬롯은 맨 뒤 — 새 슬롯이 생겨도 조용히 버리지 않는다."""
    try:
        return FABRIC_PRIORITY.index(slot or "")
    except ValueError:
        return _UNKNOWN_PRIORITY


def order_by_role(refs: Sequence[ProductReference]) -> tuple[ProductReference, ...]:
    """`Detail → Front → Back → Fit` 정렬 (중복 제거 없음, 같은 순위는 입력 순서 유지)."""
    return tuple(sorted(refs, key=lambda r: role_priority(r.slot)))


def select_fabric_references(
    refs: Sequence[ProductReference], *, limit: int = 2
) -> tuple[ProductReference, ...]:
    """원단 2패스에 넣을 참조를 `Detail → Front → Back → Fit` 순으로 중복 없이 최대 limit 개.

    `limit` 을 두는 이유는 가슴/untuck 2패스와 같다 — 편집은 과제가 하나일 때만 반영된다.
    사진을 전부 넣으면 "이 패턴으로 고쳐라"가 다시 흐려진다.

    같은 asset 이 여러 슬롯에 걸려 있으면 **더 높은 우선순위 슬롯으로 한 번만** 남긴다.
    같은 바이트를 두 번 넣으면 두 자리 중 하나를 한 사진이 낭비해, 실제로 다른 각도를 보여줄
    참조가 밀려난다.
    """
    selected: list[ProductReference] = []
    seen: set[str] = set()
    for ref in order_by_role(refs):
        if ref.asset_id in seen:
            continue
        seen.add(ref.asset_id)
        selected.append(ref)
        if len(selected) >= limit:
            break
    return tuple(selected)


def reference_event_payload(
    selected: Sequence[ProductReference], *, all_refs: Sequence[ProductReference]
) -> dict:
    """job_events 용 선택 근거 — **메타데이터만**.

    이미지 바이트·base64·서명 URL·R2 키는 절대 넣지 않는다(이벤트는 디버그 로그라 보존 기간이
    길고 그대로 노출된다). `priority` 는 실제 전달 순서(1-based)라, 나중에 "Detail 이 정말 첫
    참조였는지"를 로그만으로 재현할 수 있다.
    """
    return {
        "refs": [
            {"slot": r.slot, "asset_id": r.asset_id, "priority": i + 1}
            for i, r in enumerate(selected)
        ],
        # 고위험 패턴 상품인데 Detail 이 없으면 애초에 재현 상한이 낮다 — 그 사실을 남긴다.
        "detail_missing": not any(r.slot == "Detail" for r in all_refs),
    }
