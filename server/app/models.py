"""Pydantic 계약 모델 (common_data_contract.md §2·§3.7).

컬럼 snake_case ↔ API camelCase 변환은 여기(alias_generator) 책임 (계약 §1).
FastAPI는 기본적으로 response_model을 alias(camelCase)로 직렬화한다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
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


class ProjectPatch(CamelModel):
    """patchProject 수용 화이트리스트 (계약 §6): 이 3개만. adjustCount·status는 서버 전용."""

    compose_mode: ComposeMode | None = None
    copywriting: bool | None = None
    selected_mannequin_id: str | None = None
