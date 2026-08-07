"""One source texture reading per generation run, shared by every candidate.

The source garment's stripe period is a property of the product photo, but the
worker re-derived it inside every candidate and every attempt. That path runs two
Vision landmark calls, and their jitter moves the source torso ROI, which moves
the measured period. Production recorded 30.0px for candidate A and 15.0px for
candidate B from the identical source SHA in one job.

Nothing here measures anything. This module only holds the reading and guarantees
it is taken at most once per run: the extraction stays exactly where it was, with
the same landmarks, ROI, scan, guided fallback and thresholds as before.

A failure is cached too. If the source cannot be read, every candidate must fail
the same way it does today rather than quietly re-rolling the dice for a luckier
answer — that re-roll is the behaviour this patch removes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

CONTEXT_VERSION = "source_texture_context_v1"

#: which measurement produced `source_period_px`, mirroring the worker's own
#: branch: the Front torso patch scan, or the guided correlation fallback.
PERIOD_FROM_FRONT_SCAN = "front_scan"
PERIOD_FROM_GUIDED = "guided"


def _freeze(value: Any) -> Any:
    """Deep read-only view. Candidates share this object; none may edit it."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _stable(value: Any) -> Any:
    """JSON-safe copy for hashing; mapping proxies and tuples are normalised."""
    if isinstance(value, Mapping):
        return {str(k): _stable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class SourceTextureContext:
    """The source-only half of the hybrid composite inputs, read once.

    Immutable per generation run — NOT a permanent product truth. The extractor
    behind these numbers is known to be ROI-sensitive and is the subject of a
    later patch; freezing it here only stops it from changing mid-run.
    """

    source_sha256: str
    source_landmarks: Mapping
    source_inventory: Mapping
    source_component_boxes_norm: Mapping
    source_torso_roi: tuple
    garment_axis: str
    source_period_px: float
    source_period_source: str
    source_model_confidence: float | None
    torso_span_px: float
    pattern_model_slot: str | None
    pattern_model_asset_id: str | None
    pattern_model_sha256: str | None
    pattern_model_roi: tuple | None
    detail_validation_ok: bool | None
    extractor_version: str = CONTEXT_VERSION

    def context_id(self) -> str:
        """Deterministic id over the source-only inputs — equal across candidates."""
        return hashlib.sha256(json.dumps({
            "sourceSha256": self.source_sha256,
            "sourceRoi": _stable(self.source_torso_roi),
            "axis": self.garment_axis,
            "periodPx": round(float(self.source_period_px), 4),
            "periodSource": self.source_period_source,
            "patternModelSha256": self.pattern_model_sha256,
            "patternModelRoi": _stable(self.pattern_model_roi),
            "extractorVersion": self.extractor_version,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def to_metadata(self) -> dict:
        """Internal observation only — no public API field is derived from this."""
        return {
            "contextVersion": self.extractor_version,
            "contextId": self.context_id(),
            "sourceSha256": self.source_sha256,
            "sourceRoi": list(self.source_torso_roi),
            "sourcePeriodPx": round(float(self.source_period_px), 3),
            "sourcePeriodSource": self.source_period_source,
            "sourceModelConfidence": (round(float(self.source_model_confidence), 4)
                                      if self.source_model_confidence is not None else None),
            "garmentAxis": self.garment_axis,
            "torsoSpanPx": round(float(self.torso_span_px), 3),
            "patternModelSlot": self.pattern_model_slot,
        }


#: the source reading happens in two places the worker deliberately keeps apart:
#: the pattern model and source landmarks run before carrier preflight, the torso
#: period runs after it. Hoisting the second one above preflight would change
#: which failure a rejected carrier reports, so each is memoised where it already
#: stands rather than moved.
SLOT_SOURCE_GEOMETRY = "source_geometry"
SLOT_SOURCE_PERIOD = "source_period"


@dataclass
class SourceTextureContextCache:
    """Run-level memo. Each source-only step is computed at most once per run.

    Lazy on purpose: lanes that never reach the hybrid path must not pay for a
    source reading they do not use, so this cannot be an eager step in the job.
    """

    _slots: dict = field(default_factory=dict)
    _resolved_source: Any = None
    _resolved_period: Any = None
    compute_counts: dict = field(default_factory=dict)
    reuse_counts: dict = field(default_factory=dict)
    period_disagreements: int = 0

    def computed(self, slot: str) -> bool:
        return slot in self._slots

    @property
    def compute_count(self) -> int:
        """Total source-only computations performed for this run."""
        return sum(self.compute_counts.values())

    async def get(self, slot: str, compute: Callable):
        """Return (value, was_reused). `compute` is awaited at most once per slot.

        A failed reading is memoised as well. Re-measuring after a failure would
        hand a later candidate a different answer from the same pixels, which is
        exactly the per-candidate drift this patch exists to remove.
        """
        if slot in self._slots:
            self.reuse_counts[slot] = self.reuse_counts.get(slot, 0) + 1
            return self._slots[slot], True
        value = await compute()
        self._slots[slot] = value
        self.compute_counts[slot] = self.compute_counts.get(slot, 0) + 1
        return value, False

    def record_period(self, **fields) -> None:
        """Freeze the resolved period once. Later candidates must not overwrite it.

        A second candidate reaching a DIFFERENT value here would mean the drift this
        patch removes had come back, so the first value wins and the disagreement is
        counted rather than silently replacing the record.
        """
        if self._resolved_period is None:
            self._resolved_period = {k: _freeze(v) for k, v in fields.items()}
            return
        for key, value in fields.items():
            # compare like against like — the stored side is already snapshotted
            if self._resolved_period.get(key) != _freeze(value):
                self.period_disagreements += 1
                return

    def record_source(self, **fields) -> None:
        """Same one-shot rule for the source-only identity fields.

        Snapshotted on the way IN, not on the way out. The worker hands over live
        dictionaries and then keeps editing them — `src_inv` loses its component
        boxes to a `.pop()` on the very next line — so holding the caller's object
        would let the record change after it was taken.
        """
        if self._resolved_source is None:
            self._resolved_source = {k: _freeze(v) for k, v in fields.items()}

    def context(self) -> "SourceTextureContext | None":
        """The assembled reading, once both halves exist. None before that."""
        src, period = self._resolved_source, self._resolved_period
        if not isinstance(src, dict) or not isinstance(period, dict):
            return None
        return SourceTextureContext(
            source_sha256=src["source_sha256"],
            source_landmarks=_freeze(src.get("source_landmarks") or {}),
            source_inventory=_freeze(src.get("source_inventory") or {}),
            source_component_boxes_norm=_freeze(src.get("source_component_boxes_norm") or {}),
            source_torso_roi=tuple(period["source_torso_roi"]),
            garment_axis=period["garment_axis"],
            source_period_px=float(period["source_period_px"]),
            source_period_source=period["source_period_source"],
            source_model_confidence=period["source_model_confidence"],
            torso_span_px=float(period["torso_span_px"]),
            pattern_model_slot=src.get("pattern_model_slot"),
            pattern_model_asset_id=src.get("pattern_model_asset_id"),
            pattern_model_sha256=src.get("pattern_model_sha256"),
            pattern_model_roi=(tuple(src["pattern_model_roi"])
                               if src.get("pattern_model_roi") else None),
            detail_validation_ok=src.get("detail_validation_ok"),
        )
