"""One isolated replay of the landmark extractor, to recover the error three layers eat.

`panel_landmarks_invalid` carries the string "기하 추출 실패: VisionError" and nothing else.
Following it back:

  _call_gemini      raises VisionError(f"Gemini {status}: {body[:300]}")   <- the real cause
  analyze_with_fallback
                    catches it, logs provider/error_type/category WITHOUT the message
                    (deliberate: provider messages can carry the request URL and key),
                    then raises a fresh VisionError("상품 분석에 실패했어요...") with no
                    `from`, so the original is not even chained
  _read_source_geometry
                    catches Exception and keeps only type(exc).__name__

So the status code, the body, and the category all exist at the moment of failure and none
of them survive to the database. This script calls the SAME production path once and records
what the bottom layer actually said.

Vision only. No image generation, no mannequin job, no DB write, no R2 write. The captured
report redacts anything key-shaped before it is written.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path

from scripts._env import load_env

load_env()

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.agents import hybrid_landmarks, vision_llm          # noqa: E402
from app.agents.gemini_image import InlineImage              # noqa: E402
from app.config import load_settings                         # noqa: E402
from app.r2 import R2Client                                  # noqa: E402

OUT = SERVER / "ab_out/controlled_real_garments/20260807-batch1/vision_replay.json"

#: the front photo of the product whose job died at landmark extraction
PROJECT_ID = "c7f00166-92a1-4be2-8d47-338808fc4eca"

_SECRET = re.compile(r"(key=|api[_-]?key\"?\s*[:=]\s*\"?)[A-Za-z0-9_\-]{12,}", re.I)


def redact(text: str) -> str:
    """Provider errors quote the request URL, and the key rides in its query string."""
    return _SECRET.sub(r"\1<redacted>", str(text))[:1200]


async def fetch_front_bytes(settings):
    """The exact bytes production fed the extractor, read from R2. Read-only."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                """select id, r2_key, mime_type, original_filename, byte_size
                   from assets where project_id = %s and deleted_at is null
                   order by created_at""", (PROJECT_ID,))
            rows = cur.fetchall()
    front = next((r for r in rows if "앞면" in (r["original_filename"] or "")), rows[0])
    data = R2Client(settings).get_bytes(front["r2_key"])
    return front, data


async def main() -> int:
    settings = load_settings()
    front, data = await fetch_front_bytes(settings)
    image = InlineImage(front["mime_type"], data)

    captured: list = []
    real_call = vision_llm._call_gemini

    async def capturing_call(s, model, prompt, images, schema, timeout, **kw):
        """Same call, but the bottom-layer error is written down before it is swallowed."""
        t0 = time.time()
        try:
            out = await real_call(s, model, prompt, images, schema, timeout, **kw)
        except BaseException as exc:                      # noqa: BLE001 — diagnostic
            captured.append({"outcome": "raised", "model": model,
                             "exceptionType": type(exc).__name__,
                             "message": redact(exc),
                             "category": vision_llm._failure_category(exc),
                             "elapsedS": round(time.time() - t0, 2)})
            raise
        captured.append({"outcome": "returned", "model": model,
                         "elapsedS": round(time.time() - t0, 2),
                         "topLevelKeys": sorted(out) if isinstance(out, dict) else None,
                         "garmentVisible": (out or {}).get("garment_visible"),
                         "confidence": (out or {}).get("confidence"),
                         "landmarksPresent": sorted(
                             k for k in ("shoulder_l", "shoulder_r", "hem_l", "hem_r",
                                         "sleeve_l_end", "sleeve_r_end")
                             if isinstance((out or {}).get(k), list))})
        return out

    logging.basicConfig(level=logging.WARNING)
    vision_llm._call_gemini = capturing_call
    vision_llm._PROVIDERS["gemini"] = (capturing_call,
                                       vision_llm._PROVIDERS["gemini"][1],
                                       vision_llm._PROVIDERS["gemini"][2])
    result = {"replayCalls": 1, "imageGenerationCalls": 0,
              "asset": {"assetId": str(front["id"]), "mime": front["mime_type"],
                        "filename": front["original_filename"],
                        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
              "config": {"order": settings.analysis_model_order,
                         "geminiModel": settings.model_text_gemini,
                         "openaiKeySet": bool(settings.openai_api_key),
                         "timeoutS": settings.analysis_timeout_seconds,
                         "promptVersion": hybrid_landmarks.PROMPT_VERSION}}
    try:
        raw = await hybrid_landmarks.extract_geometry(settings, image)
        result["extractGeometry"] = "succeeded"
        lm, inv, reason = hybrid_landmarks.validate_geometry(
            raw, aspect_hw=1.0)
        result["validateGeometry"] = {"failureReason": reason,
                                      "landmarkKeys": sorted(lm or {}),
                                      "inventoryKeys": sorted(inv or {})}
    except BaseException as exc:                          # noqa: BLE001 — diagnostic
        result["extractGeometry"] = "raised"
        result["surfacedException"] = {"type": type(exc).__name__,
                                       "message": redact(exc),
                                       "chainedCause": type(exc.__cause__).__name__
                                       if exc.__cause__ else None}
    finally:
        vision_llm._call_gemini = real_call
    result["providerLayer"] = captured

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
