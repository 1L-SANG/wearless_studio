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

    def render(self, control: Image.Image, prompt: str, seed: int,
               base: Image.Image | None = None, gen_mask: Image.Image | None = None,
               negative_prompt: str | None = None) -> Image.Image:
        """base·gen_mask 를 둘 다 주면 **마스크 밖 latent 를 매 스텝 원본으로 되돌린다**.

        왜: 지금은 1024² 크롭 전체를 새로 그리고 서버가 타원 알파로 되붙인다. 그림자 있는 배경에서
        목 옆에 직사각형 조각이 남고(붙이기 경계) hz_d7 에서 그림자 얼룩이 생겼다.
        2026-09-15 실험(v7·seed 42·5컷): 잠그면 5/5 에서 조각이 사라지고 신원은 ±0.02 안
        (0.755→0.744 / 0.674→0.685 / 0.693→0.700 / 0.726→0.705 / 0.757→0.754),
        머리 밖 생성본과 크롭의 차이 8.2~24.9 → 1.1~3.3, 렌더 시간 동일(~62s, A100).

        둘 중 하나만 오면 잠그지 않는다 — 반만 있는 상태로 추측하지 않는다.

        negative_prompt 는 보정 단계가 주는 컷별 값이다. None 이면 인스턴스 기본값(= 지금까지의 동작).
        """
        import torch

        pipe = self.pipeline()
        generator = torch.Generator(self.device).manual_seed(int(seed))
        extra = {}
        if base is not None and gen_mask is not None:
            extra = _mask_lock(pipe, base, gen_mask, generator, self.device)
        return pipe(
            image=[control.convert("RGB")],
            prompt=prompt,
            negative_prompt=(self.negative_prompt if negative_prompt is None else negative_prompt),
            num_inference_steps=self.steps,
            true_cfg_scale=self.guidance_scale,
            height=CROP,
            width=CROP,
            generator=generator,
            **extra,
        ).images[0]


def _mask_lock(pipe, base: Image.Image, gen_mask: Image.Image, generator, device: str) -> dict:
    """마스크 밖 latent 를 매 스텝 원본 궤적으로 되돌린다(diffusers 0.40 QwenImageEditPlusPipeline).

    gen_mask 밖은 스텝마다 "base 크롭의 latent 를 **다음** 시그마만큼 노이즈에 섞은 값"으로 덮어쓴다
    (QwenImageEditInpaintPipeline 과 같은 혼합). 그래서 모델은 1024² 사각형을 통째로 다시 그리지 않고
    **손대지 않은 주변 안으로** 머리를 그려 넣는다. 초기 노이즈는 파이프라인이 뽑는 것과 똑같이 만든다
    — VAE encode 는 argmax 라 generator 를 건드리지 않으므로, 같은 시드의 잠금 없는 렌더와 시작점이 같다.

    ★ 정본은 ~/Downloads/comfy_swap_test/reference_code/pod_mask_lock.patch 다. 상수·식은 실측이라
      그대로 옮겼다 — 바꾸려면 키트 10컷을 다시 돌려야 한다.
    """
    import numpy as np
    import torch
    from diffusers.utils.torch_utils import randn_tensor

    dtype = pipe.transformer.dtype
    c = pipe.transformer.config.in_channels // 4
    lat = 2 * (CROP // (pipe.vae_scale_factor * 2))
    noise = randn_tensor((1, 1, c, lat, lat), generator=generator, device=torch.device(device), dtype=dtype)
    noise = pipe._pack_latents(noise, 1, c, lat, lat)
    with torch.no_grad():
        pix = pipe.image_processor.preprocess(base.convert("RGB"), CROP, CROP).unsqueeze(2)
        x0 = pipe._encode_vae_image(image=pix.to(device=device, dtype=dtype), generator=generator)
    x0 = pipe._pack_latents(x0, 1, c, lat, lat)
    cell = np.asarray(gen_mask.convert("L").resize((lat, lat), Image.BOX), np.float32) > 0.0
    m = torch.from_numpy(cell.astype(np.float32)).to(device=device, dtype=dtype)
    m = pipe._pack_latents(m.view(1, 1, 1, lat, lat).expand(1, c, 1, lat, lat).contiguous(), 1, c, lat, lat)

    def lock(p, i, t, kw):
        # step_index 는 scheduler.step() 안에서 이미 올라갔다 — 여기서 읽는 시그마가 **다음** 시그마다.
        sigma = p.scheduler.sigmas[p.scheduler.step_index].to(device=device, dtype=dtype)
        ref = (1.0 - sigma) * x0 + sigma * noise
        return {"latents": m * kw["latents"] + (1.0 - m) * ref}

    return {"latents": noise, "callback_on_step_end": lock,
            "callback_on_step_end_tensor_inputs": ["latents"]}
