#!/bin/bash
# RunPod 이미지의 /start.sh 가 부른다. 설치는 오래 걸리므로 백그라운드로 넘기고 바로 돌아간다(sshd 등 기동을 막지 않게).
nohup bash /root/face_render/comfy_setup.sh > /root/comfy_setup.log 2>&1 &
exit 0
