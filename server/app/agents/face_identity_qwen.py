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

#: ★ 적재 시간의 정체(2026-09-11 실측, H100 80GB · 224 vCPU · 가중치 워밍 상태):
#:     CPU 에서 합치기  from_pretrained 1.6s · load_lora 2.1s · **fuse 189.5s** · to(cuda) 21.4s = 214.7s
#:     GPU 에서 합치기  from_pretrained 1.9s · to(cuda) 9.0s · load_lora 2.4s · **fuse 0.2s** = 13.5s
#:   콜드스타트의 대부분은 다운로드가 아니라 CPU fuse_lora 였다(16배).
#:   경로 A 5컷 SFace(old→new): +0.009 / +0.014 / +0.001 / +0.022 / −0.007, 게이트 5/5 통과.
#:   ±0.02 규칙은 **열화를 막으려는** 것이고 초과한 한 컷이 개선 방향이라 채택했다(사용자 결정).
#:   → 기본값은 GPU 합치기. FACE_RENDER_GPU_FUSE=false 가 탈출구(결과 픽셀을 예전과 똑같이).
CROP = 1024
#: 베이스 모델 — 레시피 해시(agents/face_recipe.py)에 들어가는 값이라 이름 있는 상수로 둔다.
RENDER_MODEL_ID = "Qwen/Qwen-Image-Edit-2509"
RENDER_STEPS = 25
RENDER_GUIDANCE = 4.0
RENDER_NEGATIVE = ""


def load_base_pipeline(model_id: str = RENDER_MODEL_ID, device: str = "cuda", *,
                       cpu_offload: bool = False, gpu_fuse: bool = True):
    """LoRA 없는 베이스 파이프라인. 서비스가 **기동 때** 올려 두는 것이다 — 첫 요청이 수 분을 물지 않게.

    GPU 합치기 경로(기본)면 여기서 GPU 로 올려 둔다. 이후 QwenLocalBackend(base_pipe=...) 가 LoRA 만 붙인다
    (실측 load_lora 2.4s + fuse 0.2s). 오프로드·CPU 합치기 경로는 CPU 에 둔 채로 넘기고 백엔드가 기존 순서대로 처리한다.
    """
    import torch
    from diffusers import QwenImageEditPlusPipeline

    pipe = QwenImageEditPlusPipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16)
    pipe.set_progress_bar_config(disable=True)
    if gpu_fuse and not cpu_offload:
        pipe.to(device)
    return pipe


class QwenLocalBackend:
    """render(control, prompt, seed) -> 1024² PIL.Image. 파이프라인은 첫 호출에 1회 로드.

    base_pipe 를 받으면(load_base_pipeline 결과) 베이스를 다시 받지 않고 LoRA 만 붙인다."""

    def __init__(
        self,
        lora_path: str,
        *,
        model_id: str = RENDER_MODEL_ID,
        device: str = "cuda",
        steps: int = RENDER_STEPS,
        guidance_scale: float = RENDER_GUIDANCE,
        negative_prompt: str = RENDER_NEGATIVE,
        lora_scale: float = 1.0,
        cpu_offload: bool = False,
        gpu_fuse: bool = True,
        base_pipe=None,
    ):
        """cpu_offload=True 면 enable_model_cpu_offload() — 48GB 급 카드(A40)에서 bf16 전체(≈55GB)를
        한 번에 못 올릴 때. transformer(≈40GB)만 GPU 에 올라가 1024² 추론이 돈다(느리다)."""
        self.lora_path = lora_path
        self.base_pipe = base_pipe
        self.model_id = model_id
        self.device = device
        self.steps = steps
        self.guidance_scale = guidance_scale
        self.negative_prompt = negative_prompt
        self.lora_scale = lora_scale
        self.cpu_offload = cpu_offload
        self.gpu_fuse = gpu_fuse
        self._pipe = None
        self._lock = threading.Lock()

    def pipeline(self):
        with self._lock:
            if self._pipe is None:
                if self.base_pipe is not None:
                    pipe, self.base_pipe = self.base_pipe, None      # 미리 올린 베이스 — 한 번만 쓴다
                else:
                    pipe = load_base_pipeline(self.model_id, self.device,
                                              cpu_offload=self.cpu_offload, gpu_fuse=self.gpu_fuse)
                if self.gpu_fuse and not self.cpu_offload:
                    # 기본. 먼저 GPU 로 올리고 합친다 — 적재 214.7초 → 13.5초(위 실측).
                    # (베이스는 load_base_pipeline 이 이미 GPU 에 올렸다.)
                    pipe.load_lora_weights(self.lora_path, adapter_name="identity")
                    pipe.set_adapters(["identity"], adapter_weights=[self.lora_scale])
                    pipe.fuse_lora(lora_scale=self.lora_scale)
                else:
                    # 탈출구(gpu_fuse=False)와 오프로드 경로: CPU 에서 합치고 나서 올린다.
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
