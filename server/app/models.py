"""Pydantic 계약 모델 (common_data_contract.md §2·§3.7).

컬럼 snake_case ↔ API camelCase 변환은 여기(alias_generator) 책임 (계약 §1).
FastAPI는 기본적으로 response_model을 alias(camelCase)로 직렬화한다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel

ProjectStatus = Literal["draft", "generating", "done"]
PlanTier = Literal["basic", "plus", "seller"]
ComposeMode = Literal["simple", "basic", "extended"]


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class Account(CamelModel):
    name: str
    avatar: str
    credits: int  # = balance - reserved (§6)
    plan: PlanTier


class Project(CamelModel):
    id: str
    status: ProjectStatus
    title: str
    compose_mode: ComposeMode
    copywriting: bool
    selected_mannequin_id: str | None
    adjust_count: int
    created_at: datetime
    updated_at: datetime


class ProjectSummary(CamelModel):
    id: str
    title: str
    cover: str  # 대표 이미지 URL (없으면 '')
    clothing_type: str | None
    block_count: int
    status: ProjectStatus
    updated_at: datetime


class UploadUrlRequest(CamelModel):
    """POST /v1/assets/upload-url (§3 1단계)."""

    filename: str
    mime: str
    size: int
    project_id: str
    purpose: str = "upload"


class UploadUrlResponse(CamelModel):
    asset_id: str
    upload_url: str
    expires_at: datetime


class AssetCompleteRequest(CamelModel):
    """POST /v1/assets/{id}/complete (§3 3단계). 키 재유도용 컨텍스트."""

    project_id: str
    mime: str
    filename: str | None = None


class Asset(CamelModel):
    """업로드 완료 자산 — 프론트 ImageAsset 의 src/메타로 매핑된다 (계약 §3.1)."""

    id: str
    url: str  # 서빙 URL (= ImageAsset.src)
    mime_type: str
    byte_size: int | None


class Product(CamelModel):
    """상품의 물리적 사실 (계약 §3.1). colors·measurements는 프론트 소유 shape →
    JSONB 패스스루(list[dict])로 안전 라운드트립. 상단 스칼라만 엄격 검증."""

    id: str
    project_id: str
    name: str
    clothing_type: str | None = None
    colors: list[dict] = []
    measurements: list[dict] = []
    measurements_unknown: bool = False
    upload_complete: bool = False


class ProductPatch(CamelModel):
    """saveProduct patch. NOT NULL 컬럼(name·colors·measurements·*_unknown·*_complete)은
    명시적 null 거부(422). clothingType만 null 허용(초안)."""

    name: str | None = None
    clothing_type: str | None = None
    colors: list[dict] | None = None
    measurements: list[dict] | None = None
    measurements_unknown: bool | None = None
    upload_complete: bool | None = None

    @model_validator(mode="after")
    def _reject_explicit_null(self):
        for field in ("name", "colors", "measurements", "measurements_unknown", "upload_complete"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field}는 null일 수 없습니다.")
        return self


class ProjectPatch(CamelModel):
    """patchProject 수용 화이트리스트 (계약 §6): 이 3개만. adjustCount·status는 서버 전용."""

    compose_mode: ComposeMode | None = None
    copywriting: bool | None = None
    selected_mannequin_id: str | None = None

    @model_validator(mode="after")
    def _reject_explicit_null_on_non_nullable(self):
        # composeMode·copywriting은 NOT NULL 컬럼 — 명시적 null로 보내면 422 (500 방지).
        # 미전송(생략)은 허용, selectedMannequinId만 null 허용.
        for field in ("compose_mode", "copywriting"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field}는 null일 수 없습니다.")
        return self


# ---------- Phase 4 — 마네킹 job (계약 §3.3·§6) ----------


class MannequinCut(CamelModel):
    """마네킹컷 (계약 §3.3). id = `${candidate}-${version}` (DB UUID 아님). src는 안정 앱 URL."""

    id: str
    src: str
    candidate: str
    version: int
    base_fit: str
    fit_adjust: str | None = None
    length_adjust: str | None = None
    match_adjust: dict | None = None


class JobView(CamelModel):
    """GET /v1/jobs/{id} 폴링 스냅샷 (ai_pipeline_spec §4)."""

    id: str
    project_id: str
    kind: str
    status: str
    progress: int
    steps: list | None = None
    result: dict | None = None
    error_message: str | None = None
    credits_charged: int | None = None
    created_at: datetime
    updated_at: datetime
