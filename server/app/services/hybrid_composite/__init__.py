"""Deterministic hybrid composite — 스트라이프 상품의 최종 표면을 원본 사진에서 합성한다.

왜 이 경로인가(2026-08-01 paid live 실측): whole-image generative 원단 재생성은 같은 실셔츠
3-arm 평가에서 전패했다 — LLM QC 는 80/85/83 auto-pass 를 줬지만 blind visual 은 3/3 FAIL
(파랑/갈색 줄 그룹 소실, 색 순서·선 폭·실루엣 변형). 생성 모델에게 "정확히 그려라"를 더 세게
말하는 방향은 측정으로 기각됐다.

그래서 역할을 나눈다:
  · Gemini = geometry carrier(마네킹·핏·실루엣·주름·조명)만.
  · 의류 내부의 바탕색·색 순서·선 폭·반복 주기 = 원본 Front/Detail 에서 **결정론적으로**
    추출·합성. 합성 뒤에는 어떤 image-generation/edit 호출도 없다.
  · 합성 불가/저신뢰/metric 실패 = typed `needs_review`. silent fallback 없음.

stage 경계 (required_architecture):
  1 source_validation — 입력 ROI·반복수·선명도 gate, fail-closed
  2 stripe_model      — 축·주기·팔레트·순서·폭 추출 (1D periodic albedo)
  3 panel_map         — carrier 의 garment mask·panel·landmark, construction 대조
  4 warp_composite    — panel 별 결정론적 warp + shading transfer + protected blend
  5 deterministic_qc  — 합성 결과 재측정, typed critical (LLM QC 로 뒤집을 수 없음)
"""

from .types import (  # noqa: F401
    COMPOSITE_FAILURE_REASONS,
    EXTRACTOR_VERSION,
    PIPELINE_VERSION,
    CompositeFailure,
    CompositeSuccess,
    StripeModel,
)
from .texture_projection import (  # noqa: F401
    PROJECTION_VERSION,
    ProjectionPlan,
    plan_periodic_projection,
)
