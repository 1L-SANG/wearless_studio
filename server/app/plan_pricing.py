"""플랜별 크레딧 정책. 2026-09-11 오너 확정, 프론트 limits.js 미러."""
from typing import get_args

from .agents.cut_generator import load_virtual_model_registry
from .models import PlanTier

EXTENSION_MODEL_FEE = {"free": 19, "starter": 19, "seller": 10, "pro": 0}
FREE_MANNEQUIN_ADJUSTS = {"free": 1, "starter": 1, "seller": 1, "pro": 2}
BASIC_VIRTUAL_MODEL_IDS = frozenset({"mA", "mB"})


def plan_of(profile_plan: str | None) -> str:
    return profile_plan if profile_plan in get_args(PlanTier) else "free"


def extension_model_fee(plan: str | None, selected_model_id: str | None) -> int:
    if not selected_model_id or selected_model_id in BASIC_VIRTUAL_MODEL_IDS:
        return 0
    if selected_model_id not in load_virtual_model_registry():
        return 0
    return EXTENSION_MODEL_FEE[plan_of(plan)]


def free_mannequin_adjusts(plan: str | None) -> int:
    return FREE_MANNEQUIN_ADJUSTS[plan_of(plan)]


def mannequin_regenerate_cost(plan: str | None, done_count: int, base_cost: int) -> int:
    return 0 if done_count <= free_mannequin_adjusts(plan) else base_cost
