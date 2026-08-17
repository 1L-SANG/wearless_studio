"""Fail-closed compiler for the owner-confirmed GPT styling prompt.

This is a structural replay of the immutable 2026-08-15 ``existing_exact`` +
``adjacent_v4`` prompt.  Fixed prose lives in one hash-pinned template.  Only the
scenario evidence, cut lock, outfit lock and six bounded-pose values are dynamic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re


_TEMPLATE = Path(__file__).resolve().parents[2] / "prompts" / "cut_generate_confirmed_gpt_v1.txt"
_TEMPLATE_SHA256 = "d06ff48fa1185efadbda85d2f543b2340577716e72be9b105d6e3b8e407134fd"
_TOKEN_RE = re.compile(r"\$\{([a-z_]+)\}")
_CODE_RE = re.compile(r"[a-z0-9_]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_QC_CORRECTIONS = 5

_JUDGEABILITY_REASONS = frozenset(
    {
        "clear_enough",
        "blur",
        "occlusion",
        "hanger_distortion",
        "fold_distortion",
        "mixed_light",
        "background_interference",
        "partial_crop",
    }
)
_SLOTS = frozenset(
    {
        "FRONT",
        "FRONT_DETAIL",
        "SIDE",
        "SIDE_DETAIL",
        "BACK",
        "BACK_DETAIL",
        "SHARED_DETAIL",
    }
)


class ConfirmedGptPromptError(ValueError):
    """The requested cut cannot use the exact confirmed GPT prompt contract."""


class InputRole(StrEnum):
    SELECTED_MANNEQUIN_CUT = "SELECTED MANNEQUIN CUT"
    MODEL_FACE_DIRECTION_SHEET = "MODEL FACE DIRECTION SHEET"
    MODEL_FULL_BODY_DIRECTION_SHEET = "MODEL FULL-BODY DIRECTION SHEET"
    SOLD_PRODUCT_LABELED_EVIDENCE_GRID = "SOLD PRODUCT LABELED EVIDENCE GRID"
    MATCHING_GARMENT_EVIDENCE = "MATCHING GARMENT EVIDENCE"
    SERVICE_EXAMPLE_REFERENCE = "SERVICE EXAMPLE REFERENCE"


_ROLE_AUTHORITY = {
    InputRole.SELECTED_MANNEQUIN_CUT: (
        "is the selected resolved garment-local color and fit reference defined by the fixed "
        "common policy below; it owns no person or body identity, pose, camera, matching garment, "
        "scene, lighting or global color grade"
    ),
    InputRole.MODEL_FACE_DIRECTION_SHEET: (
        "owns only the selected model's facial identity across directions; it owns no pose, "
        "outfit, camera or scene"
    ),
    InputRole.MODEL_FULL_BODY_DIRECTION_SHEET: (
        "owns only the same selected model's stable height impression and body proportions; it "
        "owns no face, pose, direction, outfit, camera or scene"
    ),
    InputRole.SOLD_PRODUCT_LABELED_EVIDENCE_GRID: (
        "is final sold-product identity, construction, seam, closure, pattern-topology, "
        "pattern-placement and permanent-detail pixel truth; its labels, panel judgeability and "
        "HARD/UNCERTAIN fact map limit what may be asserted or reconstructed outside the fixed "
        "mannequin garment-local color and fit authority"
    ),
    InputRole.MATCHING_GARMENT_EVIDENCE: (
        "is final truth for the directly selected matching garment; the example cannot replace "
        "or redesign it"
    ),
    InputRole.SERVICE_EXAMPLE_REFERENCE: (
        "owns bounded pose semantics plus camera geometry—height, obliqueness, distance, "
        "framing/crop, headroom, subject scale, negative space, perspective and "
        "foreshortening—and scene art direction: overall mood/tone, place type, time/weather, "
        "lighting principle, spatial feel and background density; the selected capture arm alone "
        "owns output capture rendering/processing and lens rendering, and the example owns no "
        "person, body or garment identity"
    ),
}


@dataclass(frozen=True)
class ConfirmedGptScope:
    family_mode: str
    reference_scope: str
    pose: str
    reference_direction_compatible: bool
    space_group_id: str | None
    selected_mannequin: bool
    example_source: str


@dataclass(frozen=True)
class SellerEvidencePanel:
    panel: int
    slot: str
    detail: str
    surface_authority: str
    judgeability: str
    limits: tuple[str, ...]
    provided: bool


@dataclass(frozen=True)
class CutLock:
    shot: str
    user_direction: str
    direction_description: str
    face_exposure: str
    requested_framing: str


@dataclass(frozen=True)
class SellerFact:
    code: str
    value: str


@dataclass(frozen=True)
class SellerUncertainty:
    code: str
    value: str
    reason: str


@dataclass(frozen=True)
class OutfitLock:
    fixed_inner: str | None
    fixed_footwear: str
    matching_attached: bool


@dataclass(frozen=True)
class PoseSemantics:
    action: str
    body_direction: str
    weight_and_support: str
    key_contacts: str
    gaze: str
    rough_framing: str


@dataclass(frozen=True)
class ConfirmedGptPromptInput:
    scope: ConfirmedGptScope
    ordered_roles: tuple[InputRole, ...]
    seller_evidence: tuple[SellerEvidencePanel, ...]
    cut_lock: CutLock
    visible_surface_plan: str
    hard_facts: tuple[SellerFact, ...]
    uncertainties: tuple[SellerUncertainty, ...]
    outfit: OutfitLock
    pose_semantics: PoseSemantics


@lru_cache(maxsize=1)
def load_confirmed_gpt_template() -> str:
    raw = _TEMPLATE.read_bytes()
    observed = sha256(raw).hexdigest()
    if observed != _TEMPLATE_SHA256:
        raise ConfirmedGptPromptError(
            f"confirmed_gpt_template_drift:{observed}!={_TEMPLATE_SHA256}"
        )
    # Repository text files carry one POSIX trailing LF; the immutable provider
    # prompt did not.  Remove exactly that repository-only terminator.
    return raw.decode("utf-8").removesuffix("\n")


def _line(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfirmedGptPromptError(f"{field}_required_single_trimmed_line")
    if _CONTROL_RE.search(value):
        raise ConfirmedGptPromptError(f"{field}_contains_control_character")
    if "${" in value or "[[" in value or "]]" in value:
        raise ConfirmedGptPromptError(f"{field}_contains_prompt_token")
    return value


def _validate_scope(scope: ConfirmedGptScope) -> None:
    if not isinstance(scope, ConfirmedGptScope):
        raise ConfirmedGptPromptError("confirmed_gpt_scope_required")
    if scope.family_mode != "styling/direct":
        raise ConfirmedGptPromptError("confirmed_gpt_requires_styling_direct")
    if scope.reference_scope != "all":
        raise ConfirmedGptPromptError("confirmed_gpt_requires_all_scope")
    if scope.pose != "auto":
        raise ConfirmedGptPromptError("confirmed_gpt_requires_auto_pose")
    if scope.reference_direction_compatible is not True:
        raise ConfirmedGptPromptError("confirmed_gpt_requires_direction_compatible_reference")
    if scope.space_group_id is not None:
        raise ConfirmedGptPromptError("confirmed_gpt_excludes_space_groups")
    if scope.selected_mannequin is not True:
        raise ConfirmedGptPromptError("confirmed_gpt_requires_selected_mannequin")
    if scope.example_source != "service":
        raise ConfirmedGptPromptError("confirmed_gpt_requires_service_example")


def _role_manifest(roles: tuple[InputRole, ...], *, matching_attached: bool) -> str:
    try:
        normalized = tuple(InputRole(role) for role in roles)
    except (TypeError, ValueError) as exc:
        raise ConfirmedGptPromptError("confirmed_gpt_unknown_input_role") from exc
    expected = (
        InputRole.SELECTED_MANNEQUIN_CUT,
        InputRole.MODEL_FACE_DIRECTION_SHEET,
        InputRole.MODEL_FULL_BODY_DIRECTION_SHEET,
        InputRole.SOLD_PRODUCT_LABELED_EVIDENCE_GRID,
        *((InputRole.MATCHING_GARMENT_EVIDENCE,) if matching_attached else ()),
        InputRole.SERVICE_EXAMPLE_REFERENCE,
    )
    if normalized != expected:
        raise ConfirmedGptPromptError(
            "confirmed_gpt_input_order_mismatch:"
            + ",".join(role.value for role in normalized)
        )
    return "\n".join(
        f"{ordinal}. {role.value} — {_ROLE_AUTHORITY[role]}"
        for ordinal, role in enumerate(normalized, 1)
    )


def _seller_evidence_map(
    panels: tuple[SellerEvidencePanel, ...], *, direction: str
) -> str:
    if not isinstance(panels, tuple) or not 1 <= len(panels) <= 4:
        raise ConfirmedGptPromptError("confirmed_gpt_requires_one_to_four_evidence_panels")
    if not all(isinstance(panel, SellerEvidencePanel) for panel in panels):
        raise ConfirmedGptPromptError("confirmed_gpt_invalid_evidence_panel")
    if not all(type(panel.panel) is int for panel in panels):
        raise ConfirmedGptPromptError("confirmed_gpt_invalid_evidence_panel_index")
    if [panel.panel for panel in panels] != list(range(1, len(panels) + 1)):
        raise ConfirmedGptPromptError("confirmed_gpt_evidence_panels_must_be_contiguous")
    lines: list[str] = []
    for panel in panels:
        if panel.slot not in _SLOTS:
            raise ConfirmedGptPromptError(f"confirmed_gpt_invalid_evidence_slot:{panel.slot}")
        detail = _line(panel.detail, f"seller_evidence_{panel.panel}_detail")
        expected_authority = (
            "DOMINANT"
            if panel.slot == "SHARED_DETAIL" or panel.slot.startswith(direction.upper())
            else "CONTEXT"
        )
        if panel.surface_authority != expected_authority:
            raise ConfirmedGptPromptError(
                f"confirmed_gpt_surface_authority_mismatch:{panel.panel}"
            )
        if panel.judgeability not in {"USABLE", "UNCERTAIN", "MISSING"}:
            raise ConfirmedGptPromptError(
                f"confirmed_gpt_invalid_judgeability:{panel.panel}"
            )
        if not isinstance(panel.limits, tuple) or not panel.limits:
            raise ConfirmedGptPromptError(f"confirmed_gpt_evidence_limits_required:{panel.panel}")
        if not all(isinstance(limit, str) for limit in panel.limits):
            raise ConfirmedGptPromptError(f"confirmed_gpt_invalid_evidence_limits:{panel.panel}")
        if (
            len(set(panel.limits)) != len(panel.limits)
            or not set(panel.limits).issubset(_JUDGEABILITY_REASONS)
        ):
            raise ConfirmedGptPromptError(f"confirmed_gpt_invalid_evidence_limits:{panel.panel}")
        if not isinstance(panel.provided, bool):
            raise ConfirmedGptPromptError(f"confirmed_gpt_invalid_provided_flag:{panel.panel}")
        if (panel.judgeability == "MISSING") == panel.provided:
            raise ConfirmedGptPromptError(
                f"confirmed_gpt_judgeability_provided_mismatch:{panel.panel}"
            )
        supplied = "seller pixels supplied" if panel.provided else "NOT PROVIDED — do not infer"
        lines.append(
            f"- Panel {panel.panel}: {panel.slot.replace('_', ' ')} ({detail}) — "
            f"surface authority: {panel.surface_authority} — judgeability: "
            f"{panel.judgeability} — limits: {', '.join(panel.limits)} — {supplied}"
        )
    return "\n".join(lines)


def _cut_lock(lock: CutLock) -> str:
    if not isinstance(lock, CutLock):
        raise ConfirmedGptPromptError("confirmed_gpt_cut_lock_required")
    if lock.shot not in {"full", "medium"}:
        raise ConfirmedGptPromptError("confirmed_gpt_invalid_shot")
    # 오너가 반복 선택한 B/H 기준선은 front 컷만 검증됐다. side/back을 같은
    # 프로필로 확장하면 실험에 없던 조건을 서비스가 임의로 추가하는 셈이라 닫는다.
    if lock.user_direction != "front":
        raise ConfirmedGptPromptError("confirmed_gpt_requires_front_direction")
    direction_description = _line(lock.direction_description, "direction_description")
    face_exposure = _line(lock.face_exposure, "face_exposure")
    requested_framing = _line(lock.requested_framing, "requested_framing")
    return "\n".join(
        (
            "- family/mode: styling/direct",
            f"- shot: {lock.shot}",
            f"- user direction: {lock.user_direction}",
            f"- direction description: {direction_description}",
            f"- face exposure: {face_exposure}",
            f"- requested framing: {requested_framing}",
            "- neutral outfit/interaction guide: OFF and not attached",
            "- staging/direction profile prose: OFF and not attached",
            "- reference source: SERVICE ONLY; no linked shopping-mall/raw source is attached",
        )
    )


def _facts(
    hard_facts: tuple[SellerFact, ...],
    uncertainties: tuple[SellerUncertainty, ...],
) -> tuple[str, str]:
    if not isinstance(hard_facts, tuple) or not hard_facts:
        raise ConfirmedGptPromptError("confirmed_gpt_hard_facts_required")
    if not isinstance(uncertainties, tuple) or not uncertainties:
        raise ConfirmedGptPromptError("confirmed_gpt_uncertainties_required")
    codes: set[str] = set()
    hard_lines: list[str] = []
    for fact in hard_facts:
        if (
            not isinstance(fact, SellerFact)
            or not isinstance(fact.code, str)
            or not _CODE_RE.fullmatch(fact.code)
        ):
            raise ConfirmedGptPromptError("confirmed_gpt_invalid_hard_fact")
        if fact.code in codes:
            raise ConfirmedGptPromptError(f"confirmed_gpt_duplicate_fact:{fact.code}")
        codes.add(fact.code)
        hard_lines.append(f"- [{fact.code}] {_line(fact.value, f'hard_fact_{fact.code}')}")
    uncertain_lines: list[str] = []
    for fact in uncertainties:
        if (
            not isinstance(fact, SellerUncertainty)
            or not isinstance(fact.code, str)
            or not _CODE_RE.fullmatch(fact.code)
        ):
            raise ConfirmedGptPromptError("confirmed_gpt_invalid_uncertainty")
        if fact.code in codes:
            raise ConfirmedGptPromptError(f"confirmed_gpt_duplicate_fact:{fact.code}")
        codes.add(fact.code)
        value = _line(fact.value, f"uncertainty_{fact.code}")
        reason = _line(fact.reason, f"uncertainty_reason_{fact.code}")
        uncertain_lines.append(
            f"- [{fact.code}] {value} — uncertainty reason: {reason}"
        )
    return "\n".join(hard_lines), "\n".join(uncertain_lines)


def _outfit_lock(outfit: OutfitLock) -> str:
    if not isinstance(outfit, OutfitLock) or not isinstance(outfit.matching_attached, bool):
        raise ConfirmedGptPromptError("confirmed_gpt_outfit_lock_required")
    fixed_inner = "none" if outfit.fixed_inner is None else _line(
        outfit.fixed_inner, "fixed_inner"
    )
    footwear = _line(outfit.fixed_footwear, "fixed_footwear")
    matching = (
        "Reproduce the directly selected matching garment from MATCHING GARMENT EVIDENCE; "
        "never replace it with the example's bottoms."
        if outfit.matching_attached
        else "No matching garment is supplied. Use only a plain unbranded neutral basic where "
        "the complete outfit requires one; never copy the example garment."
    )
    return f"Fixed inner: {fixed_inner}. Fixed footwear: {footwear}. {matching}"


def _pose_semantics(pose: PoseSemantics) -> str:
    if not isinstance(pose, PoseSemantics):
        raise ConfirmedGptPromptError("confirmed_gpt_pose_semantics_required")
    values = (
        ("action", pose.action),
        ("bodyDirection", pose.body_direction),
        ("weightAndSupport", pose.weight_and_support),
        ("keyContacts", pose.key_contacts),
        ("gaze", pose.gaze),
        ("roughFraming", pose.rough_framing),
    )
    return "\n".join(
        f"- {key}: {_line(value, f'pose_{key}')}" for key, value in values
    )


def _render(template: str, values: dict[str, str]) -> str:
    expected = set(_TOKEN_RE.findall(template))
    if expected != set(values):
        missing = sorted(expected - set(values))
        extra = sorted(set(values) - expected)
        raise ConfirmedGptPromptError(
            f"confirmed_gpt_template_contract:missing={missing}:extra={extra}"
        )
    rendered = template
    for key, value in values.items():
        token = f"${{{key}}}"
        if rendered.count(token) != 1:
            raise ConfirmedGptPromptError(f"confirmed_gpt_template_token_count:{key}")
        rendered = rendered.replace(token, value)
    if _TOKEN_RE.search(rendered) or "[[" in rendered or "]]" in rendered:
        raise ConfirmedGptPromptError("confirmed_gpt_unresolved_prompt_token")
    return rendered


def compile_confirmed_gpt_prompt(
    request: ConfirmedGptPromptInput,
    *,
    qc_corrections: tuple[str, ...] = (),
) -> str:
    """Compile one exact confirmed-profile prompt or reject the request.

    No fallback or normalization is intentional: callers must first bind the exact
    ordered image packet and product-specific evidence contract.
    """

    if not isinstance(request, ConfirmedGptPromptInput):
        raise ConfirmedGptPromptError("confirmed_gpt_prompt_input_required")
    _validate_scope(request.scope)
    if not isinstance(request.cut_lock, CutLock):
        raise ConfirmedGptPromptError("confirmed_gpt_cut_lock_required")
    # Validate the proven B/H cut family before any direction-dependent seller
    # evidence is interpreted. This keeps unsupported side/back requests from
    # surfacing a misleading evidence-map error first.
    cut_lock = _cut_lock(request.cut_lock)
    outfit_lock = _outfit_lock(request.outfit)
    manifest = _role_manifest(
        request.ordered_roles, matching_attached=request.outfit.matching_attached
    )
    evidence = _seller_evidence_map(
        request.seller_evidence, direction=request.cut_lock.user_direction
    )
    hard_facts, uncertainties = _facts(request.hard_facts, request.uncertainties)
    values = {
        "input_role_manifest": manifest,
        "seller_evidence_map": evidence,
        "cut_lock": cut_lock,
        "visible_surface_plan": _line(
            request.visible_surface_plan, "visible_surface_plan"
        ),
        "hard_facts": hard_facts,
        "uncertainties": uncertainties,
        "outfit_lock": outfit_lock,
        "pose_semantics": _pose_semantics(request.pose_semantics),
    }
    rendered = _render(load_confirmed_gpt_template(), values)
    if not qc_corrections:
        return rendered
    if (
        not isinstance(qc_corrections, tuple)
        or not 1 <= len(qc_corrections) <= _MAX_QC_CORRECTIONS
    ):
        raise ConfirmedGptPromptError("confirmed_gpt_invalid_qc_corrections")
    corrections = [
        _line(value, f"qc_correction_{index}")
        for index, value in enumerate(qc_corrections, 1)
    ]
    return (
        rendered
        + "\n\nQC-VERIFIED SECOND-STAGE CORRECTIONS:\n"
        + "\n".join(f"- {value}" for value in corrections)
        + "\nChange only those failed axes. Preserve every other passed input authority, "
        "pose meaning, camera relationship, scene decision and capture characteristic."
    )
