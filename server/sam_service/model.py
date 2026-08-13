"""One SAM2 model per process, loaded on first use.

Loading takes ~6s and holds the weights in memory, so it must happen once — not per request
and not per view. Two requests arriving together must not both start a load, hence the lock.

The load runs in a worker thread: it is synchronous, CPU-bound and slow, and doing it on the
event loop would stall the health endpoint exactly when a scheduler is deciding whether this
task is alive.
"""

from __future__ import annotations

import asyncio

from sam_service.segmentation import Sam2Segmenter, SegmentationUnavailable

_segmenter: Sam2Segmenter | None = None
_load_error: str | None = None
_lock = asyncio.Lock()


async def get_segmenter(model_id: str | None = None) -> Sam2Segmenter:
    """The process-wide segmenter. Raises `SegmentationUnavailable`.

    A previous failure is remembered: if the weights are missing, every subsequent request
    fails immediately with the original reason instead of re-attempting a load that takes
    seconds and will not succeed.
    """
    global _segmenter, _load_error
    if _segmenter is not None:
        return _segmenter
    if _load_error is not None:
        raise SegmentationUnavailable(_load_error)
    async with _lock:
        if _segmenter is not None:               # another request won the race
            return _segmenter
        if _load_error is not None:
            raise SegmentationUnavailable(_load_error)
        try:
            _segmenter = await asyncio.to_thread(
                Sam2Segmenter, model_id) if model_id else await asyncio.to_thread(
                Sam2Segmenter)
        except SegmentationUnavailable as e:
            _load_error = str(e)
            raise
        except Exception as e:                   # noqa: BLE001
            _load_error = f"SAM2 model load failed: {e!r}"
            raise SegmentationUnavailable(_load_error) from e
        return _segmenter


def is_loaded() -> bool:
    return _segmenter is not None


def load_failure() -> str | None:
    return _load_error


def reset_for_tests() -> None:
    """Test-only. Production never unloads — the whole point is that it stays resident."""
    global _segmenter, _load_error
    _segmenter, _load_error = None, None
