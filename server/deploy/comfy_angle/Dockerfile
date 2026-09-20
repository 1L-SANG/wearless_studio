# 각도 교체(옆·뒷모습 머리 바꾸기) Serverless 워커.
#
# RunPod 공식 worker-comfyui 를 바탕으로 **커스텀 노드만** 굽는다. 우리 워크플로가 쓰는
# LanPaint 는 커스텀 노드라 네트워크 볼륨에 올릴 수 없다(RunPod 문서: 볼륨은 모델 전용).
#
# 가중치(약 28GB)는 **볼륨**에 둔다 — 이미지에 구우면 30GB 짜리를 CI 에서 빌드·푸시해야 하고
# (GitHub Actions 러너 디스크가 모자란다) 모델을 바꿀 때마다 다시 굽는다. 볼륨은
# /runpod-volume/models/... 로 마운트되고 ComfyUI 가 알아서 읽는다.
#
#   models/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors   ~20GB
#   models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors           ~8GB
#   models/vae/qwen_image_vae.safetensors                              ~250MB
#   models/loras/bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors ~200MB
#
# 이름은 app/agents/face_angle_swap.py 의 UNET·CLIP·VAE·BFS_LORA 와 **글자까지 같아야** 한다.
# 하나라도 어긋나면 워크플로가 모델 없음으로 죽고 컷은 graph_error 로 끝난다.
#
# 빌드·푸시:
#   docker build -t <레지스트리>/wearless-comfy-angle:<태그> server/deploy/comfy_angle
#   docker push  <레지스트리>/wearless-comfy-angle:<태그>
# 그 이미지로 Serverless 엔드포인트를 만들고 볼륨을 붙인 뒤, 엔드포인트 id 를
# FACE_ANGLE_ENDPOINT_ID 로 넣는다.

FROM runpod/worker-comfyui:5.10.0-base

# LanPaint — 워크플로의 LanPaint_KSampler. 2026-09-20 실측이 이 노드로 나왔다.
RUN comfy-node-install https://github.com/scraed/LanPaint
