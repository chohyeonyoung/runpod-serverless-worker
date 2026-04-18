#!/bin/bash

# ⭐ logs 폴더 생성
mkdir -p /workspace/logs
touch /workspace/logs/comfyui.log
echo "Worker Initiated"

# Network Volume의 ComfyUI를 사용
COMFYUI_DIR="/runpod-volume/runpod-slim/ComfyUI"
VENV_DIR="$COMFYUI_DIR/.venv-cu128"

# ⭐ Network Volume 마운트 대기 추가
echo "Waiting for Network Volume..."
ELAPSED=0
TIMEOUT=60
until [ -d "$COMFYUI_DIR" ]; do
    sleep 2
    ELAPSED=$((ELAPSED + 2))
    echo "Volume not ready... ${ELAPSED}s / ${TIMEOUT}s"
    if [ $ELAPSED -ge $TIMEOUT ]; then
        echo "ERROR: Network Volume mount timeout"
        exit 1
    fi
done
echo "Network Volume ready ✓"

# 경로 확인
echo "=== Path Check ==="
echo "COMFYUI_DIR: $COMFYUI_DIR"
ls "$COMFYUI_DIR" && echo "ComfyUI dir OK" || { echo "ERROR: no ComfyUI dir"; exit 1; }

# main.py 있는지 확인
if [ -f "$COMFYUI_DIR/main.py" ]; then
    echo "main.py found ✓"
else
    echo "ERROR: main.py not found in $COMFYUI_DIR"
    exit 1
fi

ls "$VENV_DIR/bin/activate" && echo "venv OK" || { echo "ERROR: no venv"; exit 1; }


echo "Activating venv..."
source "$VENV_DIR/bin/activate"

PYTHON="$VENV_DIR/bin/python3"
echo "Python: $PYTHON"
echo "Python version: $($PYTHON --version)"

# runpod, websocket 혹시 없으면 설치
# pip install requests runpod websocket-client -q
"$PYTHON" -m pip install requests runpod websocket-client -q

echo "Starting ComfyUI..."
cd "$COMFYUI_DIR"


$PYTHON main.py --listen 0.0.0.0 --port 8188 > /workspace/logs/comfyui.log 2>&1 &
COMFY_PID=$!  # ⭐ 여기 추가
echo "ComfyUI PID: $COMFY_PID"

# ComfyUI 뜰 때까지 대기 (최대 120초)
echo "Waiting for ComfyUI..."
TIMEOUT=180
ELAPSED=0
while ! curl -s http://127.0.0.1:8188/system_stats > /dev/null 2>&1; do
    sleep 3
    ELAPSED=$((ELAPSED + 3))
    echo "Elapsed: ${ELAPSED}s / ${TIMEOUT}s"
    
    # 프로세스 살아있는지 확인
    if ! kill -0 $COMFY_PID 2>/dev/null; then
        echo "ERROR: ComfyUI process died!"
        echo "=== ComfyUI logs ==="
        cat /workspace/logs/comfyui.log
        exit 1
    fi
    
    if [ $ELAPSED -ge $TIMEOUT ]; then
        echo "ERROR: Timeout!"
        echo "=== ComfyUI logs ==="
        cat /workspace/logs/comfyui.log
        exit 1
    fi
done

echo "ComfyUI ready!"



# ComfyUI 뜰 때까지 대기
# echo "Waiting for ComfyUI..."
# while ! curl -s http://127.0.0.1:3000/system_stats > /dev/null 2>&1; do
#     sleep 2
# done
# echo "ComfyUI ready!"

# Handler 실행
echo "Starting RunPod Handler..."
# $PYTHON -u /rp_handler.py
$PYTHON -u /rp_handler.py 2>&1
