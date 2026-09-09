"""Immutable, garment-scoped approved mannequin authority. No I/O or asset approval.

Callers resolve ownership/provenance and trusted visible essentials before binding.
The core validates exact bindings, not whether the owner's visual facts are true.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import io
import json
import re

from PIL import Image

from .gemini_image import InlineImage

VERSION = "approved_mannequin_v2"
GARMENT_AXES = ("color", "fit", "length", "structure", "material")
GLOBAL_AXES = ("camera", "crop", "identity", "expression", "hair", "body", "anatomy", "capture", "light", "pose", "background")
_ROLES = {"targetSeller", "matchingSeller", "approvedMannequin", "modelFace", "modelBody", "example", "capture"}


class ContractError(ValueError):
    """Preflight hold with safe, server-owned messages."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ContractError("wearshot_v2:" + code)


def _identifier(value: object) -> None:
    _require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) is not None, "invalid_identifier")


def _text(value: object) -> None:
    _require(isinstance(value, str) and 0 < len(value) <= 2000 and value == value.strip()
             and not re.search(r"[\x00-\x1f\x7f]", value), "invalid_text")


def _tuple(value: object, member_type: type) -> None:
    _require(type(value) is tuple and all(type(v) is member_type for v in value), "immutable_tuple_required")


def image_sha256(image: InlineImage) -> str:
    _require(type(image) is InlineImage and type(image.data) is bytes and bool(image.data), "invalid_image")
    _require(image.mime in {"image/png", "image/jpeg", "image/webp"}, "invalid_image_mime")
    try:
        with Image.open(io.BytesIO(image.data)) as decoded:
            _require(Image.MIME.get(decoded.format) == image.mime, "image_mime_mismatch")
            _require(getattr(decoded, "n_frames", 1) == 1, "animated_image")
            decoded.verify()
        with Image.open(io.BytesIO(image.data)) as decoded:
            decoded.load()
    except Exception:
        raise ContractError("wearshot_v2:invalid_image_bytes") from None
    return sha256(image.data).hexdigest()


@dataclass(frozen=True)
class BoundReference:
    key: str
    role: str
    image: InlineImage
    garment_id: str | None = None
    evidence_ordinal: int | None = None
    asset_id: str | None = None

    def __post_init__(self):
        _identifier(self.key)
        _require(self.key != "repairBase" and self.key != "candidate", "reserved_key")
        _require(self.role in _ROLES, "invalid_role")
        image_sha256(self.image)
        if self.role in {"targetSeller", "matchingSeller", "approvedMannequin"}:
            _identifier(self.garment_id)
        else:
            _require(self.garment_id is None, "unexpected_garment_scope")
        if self.role in {"targetSeller", "matchingSeller"}:
            _require(type(self.evidence_ordinal) is int and self.evidence_ordinal > 0, "invalid_seller_ordinal")
        else:
            _require(self.evidence_ordinal is None, "unexpected_ordinal")
        if self.asset_id is not None:
            _identifier(self.asset_id)

    def to_dict(self) -> dict:
        return dict(key=self.key, role=self.role, garmentId=self.garment_id,
                    evidenceOrdinal=self.evidence_ordinal, assetId=self.asset_id,
                    mime=self.image.mime, sha256=image_sha256(self.image))


@dataclass(frozen=True)
class EssentialDetail:
    """Caller declares visibility; invisible details are recorded, never certified."""
    code: str
    value: str
    evidence_keys: tuple[str, ...]
    visible: bool = True

    def __post_init__(self):
        _require(isinstance(self.code, str) and 0 < len(self.code) <= 128
                 and re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", self.code) is not None, "unsupported_essential")
        _require(type(self.visible) is bool, "invalid_detail_visibility")
        _text(self.value)
        _tuple(self.evidence_keys, str)
        _require(bool(self.evidence_keys) and len(set(self.evidence_keys)) == len(self.evidence_keys), "invalid_evidence_keys")
        for key in self.evidence_keys:
            _identifier(key)

    def to_dict(self):
        return dict(code=self.code, value=self.value, evidenceKeys=list(self.evidence_keys), visible=self.visible)


@dataclass(frozen=True)
class GarmentBinding:
    garment_id: str
    mannequin_key: str
    seller_keys: tuple[str, ...]
    essentials: tuple[EssentialDetail, ...] = ()
    out_of_frame_axes: tuple[str, ...] = ()

    def __post_init__(self):
        _identifier(self.garment_id)
        _identifier(self.mannequin_key)
        _tuple(self.seller_keys, str)
        _tuple(self.essentials, EssentialDetail)
        _tuple(self.out_of_frame_axes, str)
        _require(len(set(self.out_of_frame_axes)) == len(self.out_of_frame_axes)
                 and set(self.out_of_frame_axes) <= set(GARMENT_AXES), "invalid_out_of_frame_axes")
        _require(bool(self.seller_keys) and len(set(self.seller_keys)) == len(self.seller_keys), "invalid_seller_keys")
        for key in self.seller_keys:
            _identifier(key)
        _require(len({d.code for d in self.essentials}) == len(self.essentials), "duplicate_essential")
        _require(all(set(d.evidence_keys) <= set(self.seller_keys) for d in self.essentials), "unbound_detail_evidence")

    def to_dict(self):
        return dict(garmentId=self.garment_id, mannequinKey=self.mannequin_key,
                    sellerKeys=list(self.seller_keys), essentials=[d.to_dict() for d in self.essentials],
                    outOfFrameAxes=list(self.out_of_frame_axes))


@dataclass(frozen=True)
class FrameLock:
    shot: str
    face_visibility: str
    description: str

    def __post_init__(self):
        _require(self.shot in {"full", "medium", "close"}, "invalid_shot")
        _require(self.face_visibility in {"full", "partial", "hidden"}, "invalid_face_visibility")
        _text(self.description)


@dataclass(frozen=True, kw_only=True)
class WearshotContract:
    target: GarmentBinding
    matching: tuple[GarmentBinding, ...]
    expected_matching_ids: tuple[str, ...]
    references: tuple[BoundReference, ...]
    example_key: str
    model_face_key: str
    frame_lock: FrameLock
    model_body_key: str | None = None
    capture_key: str | None = None
    variation_axis: str = "pose"
    capture_profile: str = "soft"
    fingerprint: str = field(init=False)

    def __post_init__(self):
        _require(type(self.target) is GarmentBinding and type(self.frame_lock) is FrameLock, "invalid_binding")
        _tuple(self.matching, GarmentBinding)
        _tuple(self.references, BoundReference)
        _tuple(self.expected_matching_ids, str)
        _require(self.variation_axis in {"pose", "background"}, "invalid_variation")
        _require(self.capture_profile in {"clean", "soft"}, "invalid_capture")
        ids = [g.garment_id for g in self.garments]
        _require(len(set(ids)) == len(ids), "duplicate_garment")
        _require(len(set(self.expected_matching_ids)) == len(self.expected_matching_ids)
                 and set(self.expected_matching_ids) == {g.garment_id for g in self.matching}, "matching_selection_mismatch")
        refs = {r.key: r for r in self.references}
        _require(len(refs) == len(self.references), "duplicate_reference_key")
        used = []

        def bind(key, role, garment_id=None):
            _identifier(key)
            ref = refs.get(key)
            _require(ref is not None and ref.role == role and ref.garment_id == garment_id, "reference_binding_mismatch")
            used.append(key)

        for g in self.garments:
            bind(g.mannequin_key, "approvedMannequin", g.garment_id)
            ordinals = []
            for key in g.seller_keys:
                bind(key, "targetSeller" if g is self.target else "matchingSeller", g.garment_id)
                ordinals.append(refs[key].evidence_ordinal)
            _require(len(set(ordinals)) == len(ordinals), "duplicate_seller_ordinal")
        bind(self.example_key, "example")
        bind(self.model_face_key, "modelFace")
        if self.model_body_key is not None:
            bind(self.model_body_key, "modelBody")
        if self.capture_key is not None:
            bind(self.capture_key, "capture")
        _require(len(used) == len(set(used)) and set(used) == set(refs), "extraneous_or_shared_reference")
        object.__setattr__(self, "fingerprint", sha256(json.dumps(self._metadata(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())

    @property
    def garments(self):
        return (self.target, *self.matching)

    @property
    def attribute_keys(self) -> tuple[str, ...]:
        local = tuple(f"garment:{g.garment_id}:{a}" for g in self.garments for a in
                      (*(a for a in GARMENT_AXES if a not in g.out_of_frame_axes),
                       *("detail:" + d.code for d in g.essentials if d.visible)))
        global_keys = tuple(a for a in GLOBAL_AXES if not (a in {"identity", "expression", "hair"} and self.frame_lock.face_visibility == "hidden")
                            and not (a == "body" and self.model_body_key is None))
        return (*local, *global_keys, "variation")

    @property
    def all_attribute_keys(self) -> tuple[str, ...]:
        local = tuple(f"garment:{g.garment_id}:{a}" for g in self.garments for a in
                      (*GARMENT_AXES, *("detail:" + d.code for d in g.essentials)))
        return (*local, *GLOBAL_AXES, "variation")

    def _metadata(self):
        return dict(contractVersion=VERSION, target=self.target.to_dict(), matching=[g.to_dict() for g in self.matching],
                    expectedMatchingIds=list(self.expected_matching_ids), references=[r.to_dict() for r in self.references],
                    exampleKey=self.example_key, modelFaceKey=self.model_face_key, modelBodyKey=self.model_body_key,
                    captureKey=self.capture_key, frameLock=dict(shot=self.frame_lock.shot, faceVisibility=self.frame_lock.face_visibility,
                                                             description=self.frame_lock.description),
                    variationAxis=self.variation_axis, captureProfile=self.capture_profile)

    def to_dict(self):
        return {**self._metadata(), "fingerprint": self.fingerprint}


def bind_contract(*, target: GarmentBinding, matching: tuple[GarmentBinding, ...],
                  expected_matching_ids: tuple[str, ...], references: tuple[BoundReference, ...],
                  example_key: str, model_face_key: str, frame_lock: FrameLock,
                  model_body_key: str | None = None, capture_key: str | None = None,
                  variation_axis: str = "pose", capture_profile: str = "soft") -> WearshotContract:
    return WearshotContract(target=target, matching=matching, expected_matching_ids=expected_matching_ids,
                            references=references, example_key=example_key, model_face_key=model_face_key,
                            frame_lock=frame_lock, model_body_key=model_body_key, capture_key=capture_key,
                            variation_axis=variation_axis, capture_profile=capture_profile)


@dataclass(frozen=True)
class RepairPlan:
    contract_fingerprint: str
    base_image: InlineImage
    base_sha256: str
    failed_axes: tuple[str, ...]
    approved_axes: tuple[str, ...]

    def validate(self, contract: WearshotContract, base_image: InlineImage | None = None):
        _tuple(self.failed_axes, str)
        _tuple(self.approved_axes, str)
        failed, approved = set(self.failed_axes), set(self.approved_axes)
        _require(bool(failed) and len(failed) == len(self.failed_axes) and len(approved) == len(self.approved_axes)
                 and not failed & approved and failed | approved <= set(contract.attribute_keys), "invalid_repair_axes")
        _require("variation" not in failed or contract.variation_axis in failed, "variation_axis_not_repairable")
        _require(self.contract_fingerprint == contract.fingerprint, "repair_contract_mismatch")
        _require(self.base_sha256 == image_sha256(self.base_image), "repair_base_mismatch")
        if base_image is not None:
            _require(self.base_sha256 == image_sha256(base_image) and base_image.mime == self.base_image.mime, "repair_base_mismatch")

    def to_dict(self):
        return dict(contractFingerprint=self.contract_fingerprint, baseSha256=self.base_sha256,
                    failedAxes=list(self.failed_axes), approvedAxes=list(self.approved_axes))


def make_repair_plan(contract: WearshotContract, base_image: InlineImage,
                     failed_axes: tuple[str, ...], approved_axes: tuple[str, ...]) -> RepairPlan:
    _tuple(failed_axes, str)
    _tuple(approved_axes, str)
    if "variation" in failed_axes and contract.variation_axis not in failed_axes:
        failed_axes = (*failed_axes, contract.variation_axis)
    plan = RepairPlan(contract.fingerprint, base_image, image_sha256(base_image), failed_axes, approved_axes)
    plan.validate(contract)
    return plan
