"""hybrid composite 공용 타입 — 순수 dataclass, IO/모델 호출 없음.

모든 stage 산출물에 버전을 박는다. 합성 결과의 provenance(어느 원본, 어느 알고리즘 버전)를
로그만으로 재현하는 것이 이 경로의 존재 이유 중 절반이다 — 나머지 절반이 픽셀 정확성.
"""

from dataclasses import dataclass, field

import numpy as np

# 알고리즘 버전 — 결과 metadata/event 에 기록된다. 수식·임계 변경 시 반드시 올린다.
EXTRACTOR_VERSION = "stripe_extractor_v1"
PANEL_MAP_VERSION = "panel_map_v1"
WARP_VERSION = "warp_composite_v2"
QC_VERSION = "hybrid_deterministic_qc_v2"
PIPELINE_VERSION = "hybrid_stripe_composite_v2"

# typed failure — failure_contract 의 최소 어휘. 자유 문자열 금지(오타 하나로 집계가 갈린다).
COMPOSITE_FAILURE_REASONS = frozenset({
    "reference_insufficient",       # 원본 ROI/반복수/선명도 미달
    "unsupported_pattern",          # 규칙 스트라이프 밖(체크·플라워 등) — 합성 시도 자체 금지
    "stripe_model_low_confidence",  # 축/주기/팔레트 추출 신뢰도 미달
    "mask_low_confidence",          # garment mask 신뢰도 미달
    "panel_landmarks_invalid",      # panel landmark 부재/기하 모순
    "geometry_carrier_mismatch",    # carrier 의 구조(칼라·단추·비율)가 원본과 다름
    "warp_invalid",                 # negative Jacobian / triangle flip / 과도 stretch
    "source_coverage_low",          # protected 내부의 source-derived 비율 미달
    "protected_component_missing", # 칼라·플래킷 등 보호 부위 source decal 부재
    "chroma_cast_excessive",        # source/carrier chroma 차가 같은 옷으로 설명 불가
    "interface_seam",               # painted 내부 경계가 계단 — 직선 이음매로 드러남
    "boundary_chroma_discontinuity",  # 경계 양쪽 색이 한 벌로 보이지 않음
    "drape_lost",                   # carrier 의 주름·음영이 평면화됨
    "pattern_metric_failed",        # 합성 결과 재측정이 source 모델과 불일치
    "protected_region_drift",       # 보호 영역 밖 픽셀이 carrier 에서 이탈
    "carrier_preflight_rejected",  # projection 전 carrier 구조/하의/프레임 부적격
    "vision_qc_rejected",          # deterministic 통과 후 Vision fidelity 불합격
    "vision_qc_unavailable",       # enforce 출고 전 Vision 관찰 불가
    "final_frame_qc_rejected",     # projection 뒤 canonical frame 회귀 — 원본 rollback 금지
    "final_qc_rejected",           # 결정론+Vision 이후 통합 출고 정책 불합격
})


@dataclass(frozen=True)
class StripeModel:
    """Stage 2 산출 — 원본 원단의 스트라이프 정체성 (deterministic).

    `period_profile_lab` 은 단순 평균색이 아니라 **한 주기의 Lab albedo 신호**(K×3)다.
    합성은 이 신호를 반복·투영하므로, 여기 담기지 않은 정보는 결과에 존재할 수 없다.
    """

    axis: str                      # "vertical" | "horizontal" — 원본 사진 기준 줄 방향
    period_px: float               # 원본 ROI 픽셀 단위 반복 주기
    period_profile_lab: np.ndarray  # (K, 3) float32 — 한 주기의 Lab albedo (조명 정규화 후)
    ground_color_lab: tuple        # 가장 넓은 run 의 대표 Lab
    color_sequence_lab: tuple      # run 순서대로의 대표 Lab (cyclic)
    line_width_ratios: tuple       # run 폭 / period (합 ≈ 1.0)
    n_periods_used: int            # 접합에 실제 사용한 주기 수
    confidence: float              # 0~1 — 축 분리도·period 합의·run 안정성의 최소값
    source_asset_id: str
    source_sha256: str
    source_roi: tuple              # (x0, y0, x1, y1) — 원본 픽셀 좌표
    extractor_version: str = EXTRACTOR_VERSION

    def summary(self) -> dict:
        """event/metadata 용 — ndarray 미포함(바이트 비기록 규율)."""
        return {
            "axis": self.axis,
            "period_px": round(self.period_px, 2),
            "n_colors": len(self.color_sequence_lab),
            "line_width_ratios": [round(w, 4) for w in self.line_width_ratios],
            "n_periods_used": self.n_periods_used,
            "confidence": round(self.confidence, 3),
            "source_asset_id": self.source_asset_id,
            "source_sha256": self.source_sha256,
            "source_roi": list(self.source_roi),
            "extractor_version": self.extractor_version,
        }


@dataclass(frozen=True)
class CompositeFailure:
    """typed 실패 — reason 은 반드시 COMPOSITE_FAILURE_REASONS 원소."""

    reason: str
    detail: str = ""            # 사람용 짧은 근거 (이미지 데이터·URL 금지)
    metrics: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.reason not in COMPOSITE_FAILURE_REASONS:
            raise ValueError(f"typed reason 이 아님: {self.reason!r}")


@dataclass(frozen=True)
class CompositeSuccess:
    """합성 성공 — 이미지 + provenance + 측정치."""

    image_bgr: np.ndarray          # 합성 결과 (BGR uint8)
    stripe_model_summary: dict
    panel_metrics: dict            # panel 별 warp/coverage 측정
    qc_metrics: dict               # deterministic QC 원시 측정
    source_coverage: float         # protected 내부 source-derived 픽셀 비율
    versions: dict = field(default_factory=lambda: {
        "pipeline": PIPELINE_VERSION,
        "extractor": EXTRACTOR_VERSION,
        "panel_map": PANEL_MAP_VERSION,
        "warp": WARP_VERSION,
        "qc": QC_VERSION,
    })
