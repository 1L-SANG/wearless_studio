#!/bin/bash
# ComfyUI + LanPaint + Qwen-Image-Edit(2509 fp8, 2511 fp8mixed) 설치. 단계는 /root/setup_status.txt 에 남긴다(/healthz 로 보임).
# venv --system-site-packages: 이미지의 torch 2.8+cu128 를 그대로 쓰고(재설치 3GB 방지) PEP 668 시스템 pip 차단도 피한다.
set -uo pipefail
ST=/root/setup_status.txt
s() { echo "$(date +%H:%M:%S) $*" >> "$ST"; }
s start
nohup python3 /root/face_render/auth_proxy.py > /root/proxy.log 2>&1 &
cd /root
V=/root/cvenv
python3 -m venv --system-site-packages $V && $V/bin/pip install -q --upgrade pip && s venv_ok || { s venv_failed; exit 1; }
if [ ! -d ComfyUI ]; then
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git && s comfy_cloned || { s comfy_clone_failed; exit 1; }
fi
cd /root/ComfyUI
grep -v -E '^(torch|torchvision|torchaudio)([<>=~! ].*)?$' requirements.txt > /root/comfy_req.txt
$V/bin/pip install -q -r /root/comfy_req.txt && s comfy_reqs || s comfy_reqs_failed
if [ ! -d custom_nodes/LanPaint ]; then
  git clone --depth 1 https://github.com/scraed/LanPaint.git custom_nodes/LanPaint && s lanpaint_cloned || s lanpaint_clone_failed
fi
if [ -f custom_nodes/LanPaint/requirements.txt ]; then
  grep -v -E '^(torch|torchvision|torchaudio)([<>=~! ].*)?$' custom_nodes/LanPaint/requirements.txt > /root/lp_req.txt
  $V/bin/pip install -q -r /root/lp_req.txt && s lanpaint_reqs || s lanpaint_reqs_failed
fi
$V/bin/pip install -q "huggingface_hub>=0.25" hf_transfer && s hf_ready || s hf_failed
export HF_HUB_ENABLE_HF_TRANSFER=1
dl() {  # repo path dest
  $V/bin/python - "$1" "$2" "$3" <<'PY'
import os, sys, shutil
from huggingface_hub import hf_hub_download
repo, path, dest = sys.argv[1:4]
os.makedirs(dest, exist_ok=True)
target = os.path.join(dest, os.path.basename(path))
if not os.path.exists(target):
    p = hf_hub_download(repo, path, local_dir="/root/hf")
    shutil.move(p, target)
print("ok", target, os.path.getsize(target))
PY
}
M=/root/ComfyUI/models
dl Comfy-Org/Qwen-Image_ComfyUI split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors $M/text_encoders && s te_ok || s te_failed
dl Comfy-Org/Qwen-Image_ComfyUI split_files/vae/qwen_image_vae.safetensors $M/vae && s vae_ok || s vae_failed
dl Comfy-Org/Qwen-Image-Edit_ComfyUI split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors $M/diffusion_models && s edit2509_ok || s edit2509_failed
# BFS Best Face Swap (MIT) — Qwen Edit 2511 머리 교체 LoRA(Head V5, 공식 워크플로 변형 = merged rank16)
dl Alissonerdx/BFS-Best-Face-Swap bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors $M/loras && s bfs_ok || s bfs_failed
nohup $V/bin/python main.py --listen 127.0.0.1 --port 8188 > /root/comfy.log 2>&1 &
s comfy_started
dl Comfy-Org/Qwen-Image-Edit_ComfyUI split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors $M/diffusion_models && s edit2511_ok || s edit2511_failed
s all_done
