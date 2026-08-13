"""역할(slot)이 붙은 상품 참조 — 패턴 소스 선택이 Detail 을 잃지 않게 (순수, IO 없음).

왜 필요한가(2026-07-31 재조사): 워커는 상품 자산을 로드하는 즉시 `InlineImage` 바이트 배열로
납작하게 만들었고, 그 뒤로는 어느 사진이 Front 이고 어느 사진이 Detail 인지 알 방법이 없었다.
당시 소비자였던 원단 2패스는 "앞에서 두 장"을 골랐는데, 실제 슬롯 순서는
`Front → Back → Detail → Fit` 이라 **정상적으로 세 장을 올린 셀러일수록 Detail 이 빠졌다**.

지금의 소비자는 deterministic hybrid composite 다 — 패턴의 바탕색·색 순서·반복 주기를 원본
사진에서 **추출**하므로, 어느 사진이 그 정본인지(slot 권위)가 결과의 상한을 결정한다.

`InlineImage` 를 `ProductReference(slot, asset_id, image)` 로 감싸 배선 끝까지 역할을 들고 간다.
기존 생성·QC 가 쓰는 bare 바이트 목록은 여기서 파생한다(`[r.image for r in refs]`).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from .gemini_image import InlineImage

# 패턴 소스 우선순위. **업로드 슬롯 순서와 다르다.**
#  · Detail: 패턴 색·색 순서·반복 간격·선 굵기·질감의 기준 (근접 촬영이라 고주파가 살아 있다)
#  · Front : 전체 형태·봉제 구조·패턴의 전체 배치 기준
#  · Back  : 뒷면 구조와 패턴 연속성
#  · Fit   : 착용 핏 참고일 뿐 — 사람이 입은 사진은 조명·주름·색 왜곡이 커서 원단 기준이 못 된다
PATTERN_SOURCE_PRIORITY: tuple[str, ...] = ("Detail", "Front", "Back", "Fit")

_UNKNOWN_PRIORITY = len(PATTERN_SOURCE_PRIORITY)


@dataclass(frozen=True)
class ProductReference:
    """상품 사진 1장 + 그 사진의 역할. frozen — 중간 단계가 역할을 덮어쓰지 못하게."""

    slot: str
    asset_id: str
    image: InlineImage


def role_priority(slot: str | None) -> int:
    """`PATTERN_SOURCE_PRIORITY` 내 순위. 모르는 슬롯은 맨 뒤 — 새 슬롯이 생겨도 조용히 버리지 않는다."""
    try:
        return PATTERN_SOURCE_PRIORITY.index(slot or "")
    except ValueError:
        return _UNKNOWN_PRIORITY


def order_by_role(refs: Sequence[ProductReference]) -> tuple[ProductReference, ...]:
    """`Detail → Front → Back → Fit` 정렬 (중복 제거 없음, 같은 순위는 입력 순서 유지)."""
    return tuple(sorted(refs, key=lambda r: role_priority(r.slot)))


def select_pattern_sources(
    refs: Sequence[ProductReference], *, limit: int = 2
) -> tuple[ProductReference, ...]:
    """패턴 합성이 소비할 소스를 `Detail → Front → Back → Fit` 순으로 중복 없이 최대 limit 개.

    Detail 이 있으면 항상 첫 번째다 — 근접 원단컷이 색·간격·선 그룹의 정본이고,
    Front 는 전체 배치·구조 확인용 보조 소스다.

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
