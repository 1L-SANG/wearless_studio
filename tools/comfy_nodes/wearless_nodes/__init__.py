"""Wearless Studio ComfyUI nodes — 서버(server/app)의 생성·QC 코드를 그대로 import해 쓴다.

전제:
  - 환경변수 WEARLESS_SERVER_DIR 가 저장소의 server/ 를 가리킬 것
    (미설정 시 흔한 경로 몇 곳을 자동 탐색)
  - GEMINI_API_KEY / OPENAI_API_KEY 는 프로세스 환경변수로 주입
    (워크플로 파일에는 절대 저장하지 않는다)
"""
import asyncio
import io
import json
import os
import sys
import threading
import types
from pathlib import Path

import numpy as np
import torch
from PIL import Image

# ---------------------------------------------------------------- server 경로

def _find_server_dir() -> Path | None:
    env = os.environ.get("WEARLESS_SERVER_DIR")
    cands = [env] if env else []
    cands += [
        r"D:\wearless_studio\server",
        r"C:\wearless_studio\server",
        str(Path.home() / "wearless_studio" / "server"),
    ]
    for c in cands:
        if c and (Path(c) / "app" / "agents" / "gemini_image.py").exists():
            return Path(c)
    return None


_SERVER = _find_server_dir()
_IMPORT_ERROR = None

if _SERVER and str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

# psycopg 은 image_usage 가 타입 하나 때문에 import 한다. DB 는 쓰지 않으므로 스텁으로 대체.
if "psycopg" not in sys.modules:
    try:
        import psycopg  # noqa: F401
    except Exception:
        _m = types.ModuleType("psycopg")
        _t = types.ModuleType("psycopg.types")
        _j = types.ModuleType("psycopg.types.json")
        _j.Json = lambda x: x
        _t.json = _j
        _m.types = _t
        sys.modules.update({"psycopg": _m, "psycopg.types": _t, "psycopg.types.json": _j})

try:
    from app.config import Settings, load_settings
    from app.agents.gemini_image import GeminiImageClient, InlineImage
    from app.agents import image_qc as _image_qc
    from app import image_usage as _image_usage

    _image_usage.configure(pool=None, persist=False)  # 실험 환경: 원장 기록 끔
except Exception as exc:  # noqa: BLE001
    _IMPORT_ERROR = exc
    Settings = None


def _settings():
    if _IMPORT_ERROR:
        raise RuntimeError(
            f"서버 코드 import 실패 (WEARLESS_SERVER_DIR={_SERVER}): {_IMPORT_ERROR}"
        )
    return load_settings()


# ---------------------------------------------------------------- 유틸

def _run(coro):
    """ComfyUI 이벤트 루프와 충돌하지 않도록 별도 스레드에서 코루틴 실행."""
    box = {}

    def runner():
        loop = asyncio.new_event_loop()
        try:
            box["v"] = loop.run_until_complete(coro)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
        finally:
            loop.close()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["v"]


def _to_inline(images) -> list:
    """ComfyUI IMAGE 텐서 [B,H,W,C] float(0~1) -> list[InlineImage]"""
    if images is None:
        return []
    out = []
    for i in range(images.shape[0]):
        arr = (images[i].detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")
        out.append(InlineImage("image/png", buf.getvalue()))
    return out


def _to_tensor(data: bytes):
    im = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.asarray(im).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None,]


def _blank(w=512, h=512):
    return torch.zeros((1, h, w, 3), dtype=torch.float32)


def _json_or(default, raw):
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return default


CAT = "Wearless"


# ---------------------------------------------------------------- 노드

class WearlessGenerateImage:
    """서버와 동일한 generate_content_image 호출 (Gemini / gpt-image 자동 분기)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": ("STRING", {"default": "gemini-3-pro-image"}),
                "image_size": (["1K", "2K", "4K"], {"default": "2K"}),
                "aspect_ratio": ("STRING", {"default": "2:3"}),
            },
            "optional": {
                "images": ("IMAGE",),
                "temperature": ("FLOAT", {"default": -1.0, "min": -1.0, "max": 2.0, "step": 0.05}),
                "timeout": ("FLOAT", {"default": 180.0, "min": 10.0, "max": 900.0}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "info")
    FUNCTION = "run"
    CATEGORY = CAT

    def run(self, prompt, model, image_size, aspect_ratio, images=None, temperature=-1.0, timeout=180.0):
        s = _settings()
        client = GeminiImageClient(s)
        res = _run(
            client.generate_content_image(
                model=model,
                prompt=prompt,
                images=_to_inline(images),
                image_size=image_size,
                temperature=None if temperature < 0 else temperature,
                aspect_ratio=aspect_ratio or None,
                timeout=timeout,
            )
        )
        info = json.dumps(
            {"model": model, "latency_ms": res.latency_ms, "mime": res.mime,
             "bytes": len(res.image), "usage": res.usage}, ensure_ascii=False
        )
        return (_to_tensor(res.image), info)


class WearlessImageQC:
    """서버의 image_qc.verdict — 상품사진 대비 동일성 판정(로고·프린트 포함)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "product_images": ("IMAGE",),
                "generated_image": ("IMAGE",),
                "scored": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "fit_profile_json": ("STRING", {"multiline": True, "default": ""}),
                "match_image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("STRING", "BOOLEAN", "STRING")
    RETURN_NAMES = ("verdict_json", "passed", "correction_prompt")
    FUNCTION = "run"
    CATEGORY = CAT

    def run(self, product_images, generated_image, scored, fit_profile_json="", match_image=None):
        s = _settings()
        gen = _to_inline(generated_image)[0]
        match = _to_inline(match_image)[0] if match_image is not None else None
        out = _run(
            _image_qc.verdict(
                s, _to_inline(product_images), gen,
                scored=scored,
                fit_profile=_json_or(None, fit_profile_json),
                match_image=match,
            )
        )
        return (json.dumps(out, ensure_ascii=False, indent=1),
                out.get("verdict") == "pass",
                out.get("correctionPrompt") or "")


class WearlessBestOf:
    """서버의 image_qc.best_of — 불합격 시 추가 생성 후 최선 후보 채택."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "product_images": ("IMAGE",),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": ("STRING", {"default": "gemini-3-pro-image"}),
                "image_size": (["1K", "2K", "4K"], {"default": "2K"}),
                "aspect_ratio": ("STRING", {"default": "2:3"}),
            },
            "optional": {"input_images": ("IMAGE",)},
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "qc_meta")
    FUNCTION = "run"
    CATEGORY = CAT

    def run(self, product_images, prompt, model, image_size, aspect_ratio, input_images=None):
        s = _settings()
        client = GeminiImageClient(s)
        src = _to_inline(input_images)

        async def gen_one():
            r = await client.generate_content_image(
                model=model, prompt=prompt, images=src,
                image_size=image_size, aspect_ratio=aspect_ratio or None,
            )
            return InlineImage(r.mime, r.image)

        async def pipeline():
            first = await gen_one()
            return await _image_qc.best_of(s, _to_inline(product_images), first, gen_one)

        chosen, meta, warns = _run(pipeline())
        return (_to_tensor(chosen.data),
                json.dumps({"meta": meta, "warnings": warns}, ensure_ascii=False, indent=1))


class WearlessCutGenerate:
    """서버의 cut_generator.generate — 컷 계약(cut_spec) 그대로 컷 생성."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "cut_spec_json": ("STRING", {"multiline": True, "default": '{"cutType":"model_full"}'}),
                "product_json": ("STRING", {"multiline": True, "default": '{"name":"","colors":[]}'}),
                "images": ("IMAGE",),
            },
            "optional": {
                "analysis_json": ("STRING", {"multiline": True, "default": ""}),
                "manifest": ("STRING", {"multiline": True, "default": ""}),
                "has_face": ("BOOLEAN", {"default": False}),
                "prompt_only": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "prompt")
    FUNCTION = "run"
    CATEGORY = CAT

    def run(self, cut_spec_json, product_json, images, analysis_json="", manifest="",
            has_face=False, prompt_only=False):
        from app.agents import cut_generator

        s = _settings()
        spec = _json_or({}, cut_spec_json)
        product = _json_or({}, product_json)
        analysis = _json_or(None, analysis_json)
        man = manifest.strip() or None

        text = cut_generator.build_prompt(
            spec, product, analysis=analysis, manifest=man, has_face=has_face
        )
        if prompt_only:
            return (_blank(), text)

        client = GeminiImageClient(s)
        data, _mime = _run(
            cut_generator.generate(
                s, client, spec, product, _to_inline(images),
                analysis=analysis, manifest=man, has_face=has_face,
            )
        )
        return (_to_tensor(data), text)


class WearlessMannequinPrompt:
    """서버의 render_mannequin_prompt — 마네킹 프롬프트를 그대로 렌더링(생성은 안 함)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "product_json": ("STRING", {"multiline": True, "default": '{"name":"","clothingType":"top","colors":[]}'}),
                "analysis_json": ("STRING", {"multiline": True, "default": "{}"}),
            },
            "optional": {
                "image_manifest": ("STRING", {"multiline": True, "default": ""}),
                "product_count": ("INT", {"default": 1, "min": 1, "max": 8}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "meta")
    FUNCTION = "run"
    CATEGORY = CAT

    def run(self, product_json, analysis_json, image_manifest="", product_count=1):
        from app.agents import mannequin as mq
        from app.agents.prompts import load_prompt_template, render_mannequin_prompt

        s = _settings()
        product = _json_or({}, product_json)
        analysis = _json_or({}, analysis_json)
        ctype = product.get("clothingType") or product.get("clothing_type") or "top"
        gender = mq.select_base_gender(analysis, ctype)
        ctx = mq.prompt_context(
            clothing_type=ctype, product_count=product_count, base_gender=gender,
            image_manifest=image_manifest,
            fit_profile=mq.effective_fit_profile(analysis, False),
        )
        text = render_mannequin_prompt(load_prompt_template(s), ctx, product, analysis)
        meta = json.dumps({
            "base_gender": gender,
            "has_logo_text": mq.has_logo_text(product, analysis),
            "has_fine_pattern": mq.has_fine_pattern(product, analysis),
        }, ensure_ascii=False)
        return (text, meta)


class WearlessStatus:
    """설치·연결 점검용."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ("STRING",)
    FUNCTION = "run"
    CATEGORY = CAT
    OUTPUT_NODE = True

    def run(self):
        info = {
            "server_dir": str(_SERVER) if _SERVER else None,
            "import_error": repr(_IMPORT_ERROR) if _IMPORT_ERROR else None,
            "gemini_key": bool(os.environ.get("GEMINI_API_KEY")),
            "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
        }
        if not _IMPORT_ERROR:
            try:
                s = _settings()
                info.update({
                    "model_image_high": s.model_image_high,
                    "model_image_signature": s.model_image_signature,
                    "garment_qc_mode": s.garment_qc_mode,
                    "mannequin_image_size": s.mannequin_image_size,
                })
            except Exception as e:  # noqa: BLE001
                info["settings_error"] = repr(e)
        return (json.dumps(info, ensure_ascii=False, indent=1),)


NODE_CLASS_MAPPINGS = {
    "WearlessStatus": WearlessStatus,
    "WearlessGenerateImage": WearlessGenerateImage,
    "WearlessImageQC": WearlessImageQC,
    "WearlessBestOf": WearlessBestOf,
    "WearlessCutGenerate": WearlessCutGenerate,
    "WearlessMannequinPrompt": WearlessMannequinPrompt,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "WearlessStatus": "Wearless: 상태 점검",
    "WearlessGenerateImage": "Wearless: 이미지 생성 (Gemini/GPT)",
    "WearlessImageQC": "Wearless: QC 판정",
    "WearlessBestOf": "Wearless: best-of 게이트",
    "WearlessCutGenerate": "Wearless: 컷 생성",
    "WearlessMannequinPrompt": "Wearless: 마네킹 프롬프트",
}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
