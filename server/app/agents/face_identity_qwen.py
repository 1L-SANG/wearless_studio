"""face_identity 의 로컬 GPU 백엔드 — QwenImageEditPlusPipeline + 인물 LoRA.

API 서버 이미지에는 torch/diffusers 가 없다(test_canonical_pipeline 의 no-torch 가드). 이 모듈은
그 가드의 명시적 예외이며, torch/diffusers 는 render 시점에만 임포트한다 — 파드·개발 전용.
프로덕션은 face_identity_backend_url(HttpFaceBackend) 로 원격 GPU 를 쓴다.

설정은 학습 표본 샘플러(armA_v5.yaml sample)와 동일: 25 step · guidance 4 · negative "" · 1024².
QwenImageEditInpaintPipeline 은 쓰지 않는다(2026-09-07 실측 — 학습 조건 경로와 다름).
"""

from __future__ import annotations

import threading

from PIL import Image

CROP = 1024
RENDER_STEPS = 25
RENDER_GUIDANCE = 4.0
RENDER_NEGATIVE = ""


class QwenLocalBackend:
    """render(control, prompt, seed) -> 1024² PIL.Image. 파이프라인은 첫 호출에 1회 로드."""

    def __init__(
        self,
        lora_path: str,
        *,
        model_id: str = "Qwen/Qwen-Image-Edit-2509",
        device: str = "cuda",
        steps: int = RENDER_STEPS,
        guidance_scale: float = RENDER_GUIDANCE,
        negative_prompt: str = RENDER_NEGATIVE,
        lora_scale: float = 1.0,
        cpu_offload: bool = False,
    ):
        """cpu_offload=True 면 enable_model_cpu_offload() — 48GB 급 카드(A40)에서 bf16 전체(≈55GB)를
        한 번에 못 올릴 때. transformer(≈40GB)만 GPU 에 올라가 1024² 추론이 돈다(느리다)."""
        self.lora_path = lora_path
        self.model_id = model_id
        self.device = device
        self.steps = steps
        self.guidance_scale = guidance_scale
        self.negative_prompt = negative_prompt
        self.lora_scale = lora_scale
        self.cpu_offload = cpu_offload
        self._pipe = None
        self._lock = threading.Lock()

    def pipeline(self):
        with self._lock:
            if self._pipe is None:
                import torch
                from diffusers import QwenImageEditPlusPipeline

                pipe = QwenImageEditPlusPipeline.from_pretrained(self.model_id, torch_dtype=torch.bfloat16)
                pipe.set_progress_bar_config(disable=True)
                pipe.load_lora_weights(self.lora_path, adapter_name="identity")
                pipe.set_adapters(["identity"], adapter_weights=[self.lora_scale])
                pipe.fuse_lora(lora_scale=self.lora_scale)
                if self.cpu_offload:
                    pipe.enable_model_cpu_offload(device=self.device)
                else:
                    pipe.to(self.device)
                self._pipe = pipe
            return self._pipe

    def render(self, control: Image.Image, prompt: str, seed: int) -> Image.Image:
        import torch

        pipe = self.pipeline()
        generator = torch.Generator(self.device).manual_seed(int(seed))
        return pipe(
            image=[control.convert("RGB")],
            prompt=prompt,
            negative_prompt=self.negative_prompt,
            num_inference_steps=self.steps,
            true_cfg_scale=self.guidance_scale,
            height=CROP,
            width=CROP,
            generator=generator,
        ).images[0]
