#!/bin/bash
echo "Worker Initiated"

# Network Volume의 ComfyUI를 사용
COMFYUI_DIR="/runpod-volume/runpod-slim/ComfyUI"
VENV_DIR="$COMFYUI_DIR/.venv-cu128"

echo "Activating venv..."
source "$VENV_DIR/bin/activate"

# runpod, websocket 혹시 없으면 설치
pip install requests runpod websocket-client -q

echo "Starting ComfyUI..."
cd "$COMFYUI_DIR"
python main.py --listen 0.0.0.0 --port 3000 > /workspace/logs/comfyui.log 2>&1 &

# ComfyUI 뜰 때까지 대기
echo "Waiting for ComfyUI..."
while ! curl -s http://127.0.0.1:3000/system_stats > /dev/null 2>&1; do
    sleep 2
done
echo "ComfyUI ready!"

# Handler 실행
echo "Starting RunPod Handler..."
python -u /rp_handler.py
